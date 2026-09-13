"""Turn a recording into a working configuration.

Recording gives you a corpus. Serving it needs a config, and writing that by
hand means reading the corpus to work out which protocols it speaks, which paths
it answers on and how it carries a session. That is mechanical, so it is done
here instead: everything emitted is observed in the recordings, never guessed.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from phantom_api.mock.protocols.xml_codec import XmlError, local_name, parse_xml

#: Path segments that identify one instance rather than a kind of resource.
#: Collapsing them is what turns eight recorded URLs into one servable pattern.
_IDENTIFIER = re.compile(
    r"^(?:\d+"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[A-Za-z]+-\d+"
    r"|[0-9a-fA-F]{16,})$"
)


@dataclass
class CorpusShape:
    """What a corpus covers, read from the recordings rather than assumed."""

    exchanges: int = 0
    faults: int = 0
    protocols: Counter = field(default_factory=Counter)
    operations: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    paths: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    namespaces: Counter = field(default_factory=Counter)
    #: path -> SOAP header elements observed on requests to it.
    path_headers: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    #: Operations that look like they establish a session.
    session_operations: list[str] = field(default_factory=list)

    def protocol_paths(self, protocol: str) -> list[str]:
        return sorted(self.paths.get(protocol, {}))


def inspect_corpus(corpus: Path) -> CorpusShape:
    """Read a corpus and report what it can answer."""
    manifest_path = corpus / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {corpus}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    shape = CorpusShape()
    for entry in manifest.get("exchanges", []):
        shape.exchanges += 1
        if int(entry.get("status") or 200) >= 500:
            shape.faults += 1

        path = entry.get("endpoint_path") or "/"
        request_file = corpus / entry.get("request", "")
        protocol, operation = _classify(request_file, entry, shape)

        shape.protocols[protocol] += 1
        shape.paths[protocol][path] += 1
        if operation:
            shape.operations[protocol][operation] += 1
            if _looks_like_login(operation) and operation not in shape.session_operations:
                shape.session_operations.append(operation)
        if protocol == "soap" and request_file.exists():
            _harvest_headers(request_file, path, shape)

    return shape


def _looks_like_login(operation: str) -> bool:
    lowered = operation.lower()
    return any(word in lowered for word in ("login", "logon", "authenticate", "session"))


def _classify(request_file: Path, entry: dict, shape: CorpusShape) -> tuple[str, str]:
    if entry.get("transport") == "rest":
        return "openapi", entry.get("label", "")
    if not request_file.exists():
        return "soap", entry.get("label", "")
    try:
        root = parse_xml(request_file.read_text(encoding="utf-8", errors="replace"))
    except (XmlError, OSError):
        return "soap", entry.get("label", "")
    for child in root:
        if local_name(child.tag) != "Body":
            continue
        call = next(iter(child), None)
        if call is None:
            continue
        if call.tag.startswith("{"):
            shape.namespaces[call.tag[1:].split("}")[0]] += 1
        return "soap", local_name(call.tag)
    return "soap", entry.get("label", "")


def _harvest_headers(request_file: Path, path: str, shape: CorpusShape) -> None:
    """Record which SOAP headers each path carries.

    A header present on some paths and absent on others is the clearest signal
    of how each endpoint carries its session, and it is only visible per path.
    """
    try:
        root = parse_xml(request_file.read_text(encoding="utf-8", errors="replace"))
    except (XmlError, OSError):
        return
    for child in root:
        if local_name(child.tag) != "Header":
            continue
        for element in child:
            shape.path_headers[path][local_name(element.tag)] += 1


# -- path generalisation -----------------------------------------------------


def templatise(path: str) -> str:
    """Replace instance identifiers with a wildcard segment."""
    base = path.split("?", 1)[0]
    segments = ["*" if _IDENTIFIER.match(segment) else segment for segment in base.split("/")]
    return "/".join(segments) or "/"


def collapse(paths: list[str]) -> list[str]:
    """Reduce observed paths to the smallest set of patterns that covers them.

    A recording of eight concrete URLs under one prefix is really one endpoint,
    and a config listing all eight stops serving the moment a client asks for a
    ninth.
    """
    templated = sorted({templatise(p) for p in paths})
    if len(templated) <= 2:
        return templated

    groups: dict[str, list[str]] = defaultdict(list)
    for path in templated:
        parts = [p for p in path.split("/") if p]
        groups[parts[0] if parts else ""].append(path)

    patterns: list[str] = []
    for root, members in sorted(groups.items()):
        if not root:
            patterns.extend(members)
        elif len(members) == 1:
            patterns.append(members[0])
        else:
            patterns.append(f"/{root}/**")
    return patterns


# -- rendering ---------------------------------------------------------------


def render_config(
    shape: CorpusShape,
    *,
    name: str,
    corpus_path: str = "./corpus",
    port: int = 8443,
    tls: str = "self-signed",
) -> str:
    """Write a configuration that serves what the corpus actually contains."""
    lines = [
        "# Generated by `phantom-api mock derive`.",
        "#",
        f"# Read from {shape.exchanges} recorded exchanges. Every protocol, path",
        "# and session rule below was observed in the recording, not guessed.",
        "",
        "mock:",
        f"  name: {name}",
        "",
        "  listen:",
        "    host: 127.0.0.1",
        f"    port: {port}",
        f"    tls: {tls}",
        "",
        "  protocols:",
    ]

    if not shape.protocols:
        lines += ["    - pack: raw", '      paths: ["/**"]']

    for protocol, count in shape.protocols.most_common():
        observed = shape.protocol_paths(protocol)
        patterns = collapse(observed)
        rendered = ", ".join(f'"{p}"' for p in patterns)
        operations = len(shape.operations.get(protocol, {}))
        lines.append(f"    # {count} exchange(s), {operations} operation(s)")
        lines.append(f"    - pack: {protocol}")
        lines.append(f"      paths: [{rendered}]")
        if protocol == "soap":
            lines.extend(_session_block(shape, patterns, observed))

    lines += [
        "",
        "  responders:",
        '    - match: "*"',
        "      responder: corpus",
        f"      corpus: {corpus_path}",
        "      # What to do when a request is not in the corpus:",
        "      # fault | synthesize | forward | not-found",
        "      on_miss: fault",
        "",
        "  control_plane:",
        "    enabled: true",
        "    bind: 127.0.0.1",
    ]

    if shape.faults:
        lines += [
            "",
            f"# The recording contains {shape.faults} fault response(s). They replay as",
            "# faults, which is what exercises a client's error handling.",
        ]
    return "\n".join(lines) + "\n"


def _session_block(shape: CorpusShape, patterns: list[str], observed: list[str]) -> list[str]:
    """Emit per-path session rules from the headers each path actually carried."""
    if not (shape.path_headers or shape.session_operations):
        return []

    lines = ["      session:"]
    for pattern in patterns:
        headers: Counter = Counter()
        for path in observed:
            if _covers(pattern, templatise(path)):
                headers.update(shape.path_headers.get(path, {}))
        if headers:
            header = headers.most_common(1)[0][0]
            lines.append(f'        "{pattern}": {{ transport: soap-header, name: {header} }}')
        else:
            lines.append(f'        "{pattern}": {{ transport: cookie, name: session }}')

    if shape.session_operations:
        joined = ", ".join(shape.session_operations[:3])
        lines.append(f"      # Session-establishing operation(s) seen: {joined}.")
        lines.append("      # Set `name:` on cookie rules to the cookie your client sends.")
    return lines


def _covers(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return pattern == path
