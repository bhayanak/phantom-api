"""Turn a raw recording into a publishable corpus.

Recorded traffic from a live system is full of identifying data: host names, IP
and MAC addresses, hardware serials, licence keys, object names and free-form
configuration. None of it belongs in a repository, and none of it can be removed
by hand at any useful scale.

Every replacement is **stable**: the same input always maps to the same output,
and every occurrence across every file maps identically. Cross-references
between responses therefore still resolve -- an object referenced in one
response is still findable in another -- while no original identity survives.

Replacements are also **structure-preserving**. An address stays an address, a
UUID stays a UUID, a MAC stays a MAC. A corpus full of `REDACTED` is useless for
replay; a corpus full of plausible-but-false values is not.

Nothing here is protocol-specific. :class:`Rules` carries the element and field
names to scrub, and defaults to a set that covers most systems; extend it with a
rules file rather than editing this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

#: Namespace for deterministic UUID derivation. Arbitrary but fixed, so a value
#: pseudonymised today matches one pseudonymised next year.
_NS = uuid.UUID("6f1d0a1e-9f6a-5a2e-8c1b-0d5f2a7b4e33")

DEFAULT_DOMAIN = "lab.example.com"

#: Patterns applied to every file, longest and most specific first so an FQDN is
#: consumed before its bare host name would be.
PATTERNS: list[tuple[str, str]] = [
    (
        "fqdn",
        r"\b(?!(?:www|schemas|xmlsoap|w3)\.)"
        r"[a-zA-Z0-9][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)+\.[a-zA-Z]{2,}\b",
    ),
    (
        "uuid",
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    ),
    ("mac", r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"),
    # IPv6 must be a full eight-group address or contain a literal `::`.
    # Anything looser matches the colons inside a timestamp (`00:14:03`) and the
    # `:` in a JSON member (`"memoryMB":8192`) -- both silently corrupt the corpus.
    (
        "ipv6",
        r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
        r"|(?<![\w:])(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{0,4}::"
        r"(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4}(?![\w:])",
    ),
    # Bounded by non-dot, non-digit, so a four-part version such as `8.0.2.1` is
    # not mistaken for an address.
    (
        "ipv4",
        r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])",
    ),
    ("licence", r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b"),
    # A WWN must contain at least one hex letter, and must not sit inside a
    # longer number. Without both guards this matches the fractional part of
    # `89.2113888559807235` and rewrites it to something that is no longer a
    # number -- 16 consecutive decimal digits are far more often an id or a
    # timestamp than a world-wide name.
    (
        "wwn",
        r"(?<![0-9A-Za-z.])(?=[0-9a-fA-F]*[a-fA-F])"
        r"(?:2[0-9a-fA-F]{15}|1000[0-9a-fA-F]{12})(?![0-9A-Za-z.])",
    ),
]

#: Structural addresses that must survive verbatim or the protocol breaks.
#: Well-known IPv6 prefixes (`fe80::`, `ff02::`) survive too, because the pattern
#: above requires a group after the `::` -- a bare prefix is a constant like
#: 127.0.0.1, carries no identity, and rewriting it corrupts routing data.
IP_ALLOW = frozenset(
    {
        "0.0.0.0",
        "127.0.0.1",
        "255.255.255.0",
        "255.255.255.255",
        "255.0.0.0",
        "255.255.0.0",
        "224.0.0.0",
        "169.254.0.0",
        "1.0.0.0",
        "8.0.0.0",
    }
)

#: XML elements whose entire text identifies a machine, a site or a supplier.
DEFAULT_ELEMENTS: dict[str, str] = {
    "hostName": "host",
    "hostname": "host",
    "domainName": "domain",
    "dnsName": "host",
    "searchDomain": "domain",
    "model": "model",
    "vendor": "vendor",
    "manufacturer": "vendor",
    "serialNumber": "serial",
    "assetTag": "asset",
    "partNumber": "part",
    "identifierValue": "ident",
    "deviceName": "device",
    "diskName": "disk",
    "canonicalName": "disk",
    "displayName": "disk",
    "vendorUrl": "url",
    "productUrl": "url",
    "classId": "class",
    "instanceId": "instance",
}

#: JSON fields, same idea.
DEFAULT_FIELDS: tuple[str, ...] = (
    "host_name",
    "domain_name",
    "hostname",
    "dns_name",
    "model",
    "vendor",
    "serial_number",
    "asset_tag",
    "full_name",
)

#: Elements holding a filesystem or datastore path. A `[prefix]` is structural
#: and is kept; only the tail is rewritten.
#:
#: `path` is deliberately absent. In a traversal specification `<path>` names an
#: edge to follow, and rewriting it breaks every recorded request. Real paths are
#: caught by the inline `[prefix] tail` rule regardless of element name.
DEFAULT_PATH_ELEMENTS: tuple[str, ...] = (
    "vmPathName",
    "fileName",
    "swapFile",
    "filePath",
    "snapshotDirectory",
    "suspendDirectory",
    "logDirectory",
)

#: Text that is product-standard rather than site-specific, so it stays put.
DEFAULT_KEEP = frozenset(
    {
        "",
        "unknown",
        "localhost",
        "localhost.localdomain",
        "VMware, Inc.",
        "VMware",
        "vSwitch0",
        "VM Network",
        "Management Network",
        "vmk0",
        "vmk1",
    }
)

#: Object names shorter than this, or matching protocol vocabulary, are left
#: alone: an object innocently named "vm" would otherwise rewrite every `"vm"`
#: JSON key and every `<vm>` element in the corpus.
MIN_RENAME_LENGTH = 4

RESERVED_WORDS = frozenset(
    {
        "vm",
        "host",
        "name",
        "value",
        "key",
        "type",
        "obj",
        "val",
        "parent",
        "config",
        "summary",
        "runtime",
        "network",
        "datastore",
        "folder",
        "cluster",
        "guest",
        "info",
        "true",
        "false",
        "null",
        "root",
        "data",
        "item",
        "id",
        "self",
        "link",
        "state",
        "status",
        "time",
        "date",
    }
)

_SAFE_SCALAR = re.compile(r"^(?:-?\d+(?:\.\d+)?|true|false|TRUE|FALSE|0x[0-9a-fA-F]+)?$")

#: Fields whose *name* says the value is a credential. Matched on the name, in
#: any case and any compound form (`sshPasswordHash`, `api_key`, `clientSecret`),
#: because the value itself is unguessable by shape -- a password looks like any
#: other short string.
SECRET_NAME = re.compile(
    r"(pass(word|phrase)|secret|token|apikey|api_key|credential|privatekey"
    r"|private_key|accesskey|access_key|sharedkey|shared_key|salt|cert(ificate)?"
    r"|keystore|truststore)",
    re.IGNORECASE,
)

#: A fixed marker rather than a pseudonym: nothing downstream should treat a
#: recorded credential as usable, and a realistic-looking fake invites exactly
#: that. No angle brackets -- inside XML they parse as a nested element and
#: break the document.
SECRET_PLACEHOLDER = "[scrubbed]"

#: Fields holding free-form, user-supplied text. Unbounded by definition, and in
#: practice they carry cloud-init blocks, provisioning scripts and operator
#: notes -- complete with the passwords and internal addresses inside them.
#: Nothing in a mock needs their contents, so they go wholesale.
DEFAULT_FREEFORM: tuple[str, ...] = (
    "userData",
    "user_data",
    "cloudInit",
    "cloud_init",
    "cloudConfig",
    "script",
    "scriptContent",
    "customScript",
    "bootScript",
    "notes",
    "description",
    "comment",
    "rawData",
    "config",
    "customOptions",
)


@dataclass
class Rules:
    """What to scrub. Defaults suit most systems; extend rather than edit."""

    domain: str = DEFAULT_DOMAIN
    elements: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ELEMENTS))
    json_fields: tuple[str, ...] = DEFAULT_FIELDS
    path_elements: tuple[str, ...] = DEFAULT_PATH_ELEMENTS
    keep: frozenset[str] = DEFAULT_KEEP
    #: Supplier and site tokens, matched case-insensitively on non-alphanumeric
    #: boundaries so `ACME_NS204i` is caught and `acmetrics.enabled` is not.
    terms: tuple[str, ...] = ()
    #: Files whose `<name>` elements are protocol vocabulary (property paths,
    #: counter keys, role identifiers) rather than user-chosen labels.
    structural_files: tuple[str, ...] = ()
    #: Rename object names found in responses to generic ones.
    rename_objects: bool = True
    #: JSON collections whose members are real objects rather than catalogue
    #: entries, named in the singular. Their `name` is a label someone chose --
    #: in a real capture, host names and hardware serials -- while a catalogue's
    #: `name` is product vocabulary that must survive. Only nominated kinds are
    #: renamed, because nothing in the data distinguishes the two.
    rename_kinds: tuple[str, ...] = ()
    #: Scrub free-form key/value bags, keeping keys and replacing values.
    scrub_option_values: bool = True
    #: Replace the value of any field whose name looks like a credential.
    scrub_secrets: bool = True
    #: Fields whose entire contents are free-form text.
    freeform_fields: tuple[str, ...] = DEFAULT_FREEFORM

    @classmethod
    def load(cls, path: Path | None) -> Rules:
        if path is None:
            return cls()
        raw = _read_structured(path)
        base = cls()
        return cls(
            domain=raw.get("domain", base.domain),
            elements={**base.elements, **(raw.get("elements") or {})},
            json_fields=tuple({*base.json_fields, *(raw.get("json_fields") or [])}),
            path_elements=tuple({*base.path_elements, *(raw.get("path_elements") or [])}),
            keep=frozenset({*base.keep, *(raw.get("keep") or [])}),
            terms=tuple(sorted({*base.terms, *(raw.get("terms") or [])}, key=len, reverse=True)),
            structural_files=tuple(raw.get("structural_files") or base.structural_files),
            rename_objects=bool(raw.get("rename_objects", base.rename_objects)),
            rename_kinds=tuple(raw.get("rename_kinds") or base.rename_kinds),
            scrub_option_values=bool(raw.get("scrub_option_values", base.scrub_option_values)),
            scrub_secrets=bool(raw.get("scrub_secrets", base.scrub_secrets)),
            freeform_fields=tuple({*base.freeform_fields, *(raw.get("freeform_fields") or [])}),
        )

    def with_terms(self, extra: list[str]) -> Rules:
        merged = sorted({*self.terms, *extra}, key=len, reverse=True)
        return Rules(**{**self.__dict__, "terms": tuple(merged)})


def _read_structured(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        import yaml

        return yaml.safe_load(text) or {}
    return json.loads(text)


#: Fields whose contents are a product version. A dotted version such as
#: `8.0.2.1` is indistinguishable from an address by pattern alone, and rewriting
#: it breaks every client that negotiates on version. Their spans are masked
#: before the pattern pass and restored after.
_VERSION_CONTEXT = re.compile(
    r"<(\w*[Vv]ersion|build|buildNumber)(?:\s[^>]*)?>([^<]*)</\1>"
    r'|"(\w*[Vv]ersion|build|build_number)"\s*:\s*"([^"]*)"',
)


class Pseudonymiser:
    """Allocates stable, structure-preserving replacements."""

    def __init__(self, domain: str = DEFAULT_DOMAIN) -> None:
        self.domain = domain
        self.map: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def _next(self, kind: str) -> int:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return self._counters[kind]

    def alloc(self, kind: str, original: str) -> str:
        if original in self.map:
            return self.map[original]
        if kind in {"uuid", "hexuuid"}:
            new = str(uuid.uuid5(_NS, original))
        elif kind == "ipv4":
            n = self._next(kind)
            block, host = divmod(n - 1, 253)
            prefix = ["192.0.2", "198.51.100", "203.0.113"][block % 3]
            new = f"{prefix}.{host + 1}"  # RFC 5737 documentation ranges
        elif kind == "mac":
            n = self._next(kind)
            new = f"02:00:5e:{(n >> 16) & 0xFF:02x}:{(n >> 8) & 0xFF:02x}:{n & 0xFF:02x}"
        elif kind == "fqdn":
            new = f"host-{self._next(kind):03d}.{self.domain}"
        elif kind == "licence":
            digest = hashlib.sha256(original.encode()).hexdigest().upper()
            new = "-".join(digest[i * 5 : (i + 1) * 5] for i in range(5))
        elif kind == "wwn":
            new = f"20000000c9{self._next(kind):06x}"
        else:
            new = f"{kind}-{self._next(kind):04d}"
        self.map[original] = new
        return new

    def scrub_patterns(self, text: str) -> str:
        text, restore = _mask_versions(text)
        for kind, pattern in PATTERNS:

            def repl(m: re.Match[str], _k: str = kind) -> str:
                value = m.group(0)
                if _k == "ipv4" and (value in IP_ALLOW or value.startswith("0.")):
                    return value
                if _k == "fqdn" and value.endswith((".xsd", ".org", ".w3.org")):
                    return value
                return self.alloc(_k, value)

            text = re.sub(pattern, repl, text)
        for token, original in restore.items():
            text = text.replace(token, original)
        return text


def _mask_versions(text: str) -> tuple[str, dict[str, str]]:
    restore: dict[str, str] = {}

    def mask(m: re.Match[str]) -> str:
        token = f"\x00v{len(restore)}\x00"
        restore[token] = m.group(0)
        return token

    return _VERSION_CONTEXT.sub(mask, text), restore


@dataclass
class Report:
    """What a run did, and whether the result is safe to publish."""

    exchanges: int = 0
    renamed: int = 0
    replacements: int = 0
    orphans: list[str] = field(default_factory=list)
    unparseable: list[tuple[str, str]] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.orphans or self.unparseable or self.leaks)

    def problems(self) -> list[str]:
        lines = []
        for name in self.orphans:
            lines.append(f"unpaired (cannot be replayed): {name}")
        for name, reason in self.unparseable:
            lines.append(f"no longer parses: {name}: {reason}")
        for name in self.leaks:
            lines.append(f"still contains a forbidden term: {name}")
        return lines


# -- name harvesting ---------------------------------------------------------


def build_rename_table(source: Path, rules: Rules) -> dict[str, str]:
    """Derive generic names for every object named in the recordings.

    Object names are the most obviously identifying thing in a corpus and the
    least mechanical, so they are harvested from the responses themselves rather
    than guessed.
    """
    if not rules.rename_objects:
        return {}

    prefixes = {
        "Datacenter": "DC",
        "ClusterComputeResource": "Cluster",
        "ComputeResource": "Cluster",
        "HostSystem": "esx",
        "VirtualMachine": "VM",
        "Datastore": "DS",
        "Folder": "Folder",
        "Network": "Net",
        "ResourcePool": "RP",
    }
    counters: dict[str, int] = {}
    renames: dict[str, str] = {}

    files = sorted(source.rglob("*.resp.xml")) + sorted(source.rglob("*.xml"))
    for path in dict.fromkeys(files):
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in re.findall(r"<returnval>(.*?)</returnval>", text, re.S):
            kind_match = re.search(r'<obj type="([^"]+)">([^<]+)</obj>', block)
            name_match = re.search(r"<name>name</name>\s*<val[^>]*>([^<]*)</val>", block)
            if not (kind_match and name_match):
                continue
            original = name_match.group(1)
            if not _renameable(original, renames):
                continue
            prefix = prefixes.get(kind_match.group(1), kind_match.group(1))
            counters[prefix] = counters.get(prefix, 0) + 1
            n = counters[prefix]
            if prefix == "esx":
                renames[original] = f"esx-{n:02d}.{rules.domain}"
            elif prefix == "VM":
                renames[original] = f"VM-{n:04d}"
            else:
                renames[original] = f"{prefix}-{n:02d}"

        # Nested specs (port groups, switches) are not top-level objects.
        for original in re.findall(r"<spec(?:\s[^>]*)?><name>([^<]+)</name>", text):
            if not _renameable(original, renames) or original in rules.keep:
                continue
            counters["PG"] = counters.get("PG", 0) + 1
            renames[original] = f"PG-{counters['PG']:02d}"

    if rules.rename_kinds:
        _harvest_json_names(source, rules, renames, counters)

    # Longest first, so a compound name is consumed before its prefix.
    return dict(sorted(renames.items(), key=lambda kv: -len(kv[0])))


def _harvest_json_names(
    source: Path, rules: Rules, renames: dict[str, str], counters: dict[str, int]
) -> None:
    """Collect the names of real objects from JSON recordings.

    Only collections the caller nominated are considered: a `server` name is a
    host name or a hardware serial, while an `instanceType` name is the product's
    own vocabulary and renaming it would make the corpus useless.
    """
    wanted = {kind.lower() for kind in rules.rename_kinds}

    def walk(node: Any, kind: str) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            if (
                kind in wanted
                and isinstance(name, str)
                and isinstance(node.get("id"), (int, str))
                and _renameable(name, renames)
                and name not in rules.keep
            ):
                label = kind[:1].upper() + kind[1:]
                counters[label] = counters.get(label, 0) + 1
                renames[name] = f"{label}-{counters[label]:02d}"
            for key, value in node.items():
                walk(value, singular(key).lower())
        elif isinstance(node, list):
            for item in node:
                walk(item, kind)

    for path in sorted(source.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            walk(json.loads(path.read_text(encoding="utf-8")), "")
        except (ValueError, OSError):
            continue


def singular(word: str) -> str:
    """`securityGroups` -> `securityGroup`, so a collection names its kind."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _renameable(original: str, seen: dict[str, str]) -> bool:
    return bool(
        original
        and original not in seen
        and len(original) >= MIN_RENAME_LENGTH
        and original.lower() not in RESERVED_WORDS
    )


# -- the passes --------------------------------------------------------------


def scrub_names(text: str, renames: dict[str, str], *, is_json: bool) -> str:
    """Apply the rename table.

    In JSON, key positions are masked first and then names are replaced wherever
    they appear in a value -- including inside a longer string, because a host
    name turns up embedded in generated identifiers such as
    `ubuntu-<name>-tpx4cl`. Object names and field names share a namespace, so a
    blind replace happily renames a `"vm"` key and corrupts the document.
    """
    if is_json:
        restore: dict[str, str] = {}

        def mask(m: re.Match[str]) -> str:
            token = f"\x00n{len(restore)}\x00"
            restore[token] = m.group(0)
            return token

        text = re.sub(r'"(?:[^"\\]|\\.)*"\s*:', mask, text)
        for original, replacement in renames.items():
            text = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])",
                replacement,
                text,
            )
        for token, original_text in restore.items():
            text = text.replace(token, original_text)
        return text

    for original, replacement in renames.items():
        text = text.replace(f">{original}<", f">{replacement}<")
        text = text.replace(f'"{original}"', f'"{replacement}"')
        text = text.replace(f"[{original}]", f"[{replacement}]")
        text = text.replace(f"-{original}<", f"-{replacement}<")
    return text


def scrub_elements(text: str, pseudo: Pseudonymiser, rules: Rules) -> str:
    for element, kind in rules.elements.items():

        def repl(m: re.Match[str], _k: str = kind) -> str:
            value = m.group(2).strip()
            if value in rules.keep or not value:
                return m.group(0)
            return f"{m.group(1)}{pseudo.alloc(_k, value)}{m.group(3)}"

        text = re.sub(rf"(<{element}(?:\s[^>]*)?>)([^<]*)(</{element}>)", repl, text)
    return text


def scrub_json_fields(text: str, pseudo: Pseudonymiser, rules: Rules) -> str:
    for field_name in rules.json_fields:

        def repl(m: re.Match[str], _f: str = field_name) -> str:
            value = m.group(1)
            if value in rules.keep or not value or value == ".":
                return m.group(0)
            return f'"{_f}":"{pseudo.alloc(_f, value)}"'

        text = re.sub(rf'"{field_name}"\s*:\s*"([^"]*)"', repl, text)
    return text


def scrub_paths(text: str, pseudo: Pseudonymiser, rules: Rules) -> str:
    """Rewrite paths, keeping any `[prefix]` intact."""

    def rebuild(tail: str) -> str:
        folder = pseudo.alloc("dir", tail)
        last = tail.rsplit("/", 1)[-1]
        suffix = last.rsplit(".", 1)[-1] if "." in last else ""
        return f"{folder}/{folder}.{suffix}" if suffix else folder

    for element in rules.path_elements:

        def repl(m: re.Match[str]) -> str:
            value = m.group(2).strip()
            if not value:
                return m.group(0)
            prefix, sep, tail = value.partition("] ")
            if not sep:
                return f"{m.group(1)}{pseudo.alloc('path', value)}{m.group(3)}"
            return f"{m.group(1)}{prefix}] {rebuild(tail)}{m.group(3)}"

        text = re.sub(rf"(<{element}(?:\s[^>]*)?>)([^<]*)(</{element}>)", repl, text)

    # Paths also appear as plain values, so catch the `[prefix] tail` shape
    # wherever it occurs.
    return re.sub(
        r"\[([^\[\]<>\"]{1,64})\]\s+([^<\"]{1,200})",
        lambda m: f"[{m.group(1)}] {rebuild(m.group(2))}",
        text,
    )


def scrub_option_values(text: str, pseudo: Pseudonymiser) -> str:
    """Pseudonymise free-form key/value config, keeping the keys.

    These bags are an open collection of guest- and tooling-supplied strings:
    deploy profiles, file paths, product names, ticket ids. Keys are a bounded
    vocabulary that consumers match on, so they stay. Values are unbounded and
    are the single richest source of accidental disclosure, so anything that is
    not a plain number or boolean is replaced.
    """

    def repl(m: re.Match[str]) -> str:
        value = m.group(2)
        if _SAFE_SCALAR.match(value.strip()):
            return m.group(0)
        return f"{m.group(1)}{pseudo.alloc('opt', value)}{m.group(3)}"

    return re.sub(r"(<value(?:\s[^>]*)?>)([^<]*)(</value>)", repl, text)


def scrub_terms(text: str, terms: tuple[str, ...], *, is_json: bool = False) -> str:
    """Final safety net: blank out supplier and site tokens wherever they hide.

    Boundaries are non-alphanumeric rather than `\\b`, because `_` is a word
    character: `ACME_NS204i` must match while `acmetrics.enabled` must not.

    In JSON, key positions are masked first. A site name can legitimately *be* a
    field name -- `{"currentUsage": {"acme": 3}}` -- and renaming the key changes
    the shape clients parse, which no syntax check would catch.
    """
    if not terms:
        return text

    restore: dict[str, str] = {}
    if is_json:

        def mask(m: re.Match[str]) -> str:
            token = f"\x00k{len(restore)}\x00"
            restore[token] = m.group(0)
            return token

        text = re.sub(r'"(?:[^"\\]|\\.)*"\s*:', mask, text)

    for term in terms:
        text = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            "Example",
            text,
            flags=re.IGNORECASE,
        )

    for token, original in restore.items():
        text = text.replace(token, original)
    return text


def scrub_secrets(text: str, *, is_json: bool) -> str:
    """Replace the value of any field whose name says it is a credential.

    Detection is by name, because a credential has no distinguishing shape --
    a password looks like any other short string. The replacement is a fixed
    marker rather than a plausible fake, so nothing downstream can mistake a
    recorded secret for a usable one.
    """
    if is_json:

        def json_repl(m: re.Match[str]) -> str:
            if not SECRET_NAME.search(m.group(1)):
                return m.group(0)
            return f'"{m.group(1)}": "{SECRET_PLACEHOLDER}"'

        return re.sub(r'"([A-Za-z0-9_]+)"\s*:\s*"(?:[^"\\]|\\.)*"', json_repl, text)

    def xml_repl(m: re.Match[str]) -> str:
        if not SECRET_NAME.search(m.group(2)):
            return m.group(0)
        return f"{m.group(1)}{SECRET_PLACEHOLDER}{m.group(3)}"

    return re.sub(r"(<(\w+)(?:\s[^>]*)?>)[^<]*(</\2>)", xml_repl, text)


def scrub_freeform(text: str, pseudo: Pseudonymiser, rules: Rules, *, is_json: bool) -> str:
    """Replace free-form user-supplied text wholesale.

    These fields carry cloud-init blocks, provisioning scripts and operator
    notes. In a real capture that meant an administrator password and a set of
    internal DNS and NTP addresses, none of which any pattern rule would have
    caught. Nothing in a mock needs the contents, so the whole value goes.
    """
    for name in rules.freeform_fields:
        if is_json:
            text = re.sub(
                rf'"{re.escape(name)}"\s*:\s*"(?:[^"\\]|\\.)*"',
                lambda m, _n=name: f'"{_n}": "{pseudo.alloc("text", m.group(0))}"',
                text,
            )
        else:
            text = re.sub(
                rf"(<{re.escape(name)}(?:\s[^>]*)?>)([^<]*)(</{re.escape(name)}>)",
                lambda m: f"{m.group(1)}{pseudo.alloc('text', m.group(2))}{m.group(3)}",
                text,
            )
    return text


def sanitise_text(
    text: str,
    pseudo: Pseudonymiser,
    rules: Rules,
    renames: dict[str, str],
    *,
    is_json: bool,
    structural: bool = False,
) -> str:
    """Run every pass over one document, in the order that matters."""
    text = scrub_names(text, renames, is_json=is_json)
    if not structural:
        # Secrets and free-form blocks go first: they can contain anything, so
        # leaving them for a later pass means a later pass has to catch it.
        if rules.scrub_secrets:
            text = scrub_secrets(text, is_json=is_json)
        text = scrub_freeform(text, pseudo, rules, is_json=is_json)
        if is_json:
            text = scrub_json_fields(text, pseudo, rules)
        else:
            text = scrub_elements(text, pseudo, rules)
            text = scrub_paths(text, pseudo, rules)
            if rules.scrub_option_values:
                text = scrub_option_values(text, pseudo)
    text = pseudo.scrub_patterns(text)
    return scrub_terms(text, rules.terms, is_json=is_json)


# -- corpus level ------------------------------------------------------------


def sanitise_corpus(
    source: Path,
    dest: Path,
    *,
    rules: Rules | None = None,
    mapping_path: Path | None = None,
) -> Report:
    """Sanitise a recorded corpus into ``dest`` and verify the result."""
    rules = rules or Rules()
    pseudo = Pseudonymiser(rules.domain)
    report = Report()

    manifest_path = source / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if dest.exists():
        shutil.rmtree(dest)
    for bucket in ("soap", "rest"):
        (dest / bucket).mkdir(parents=True, exist_ok=True)

    renames = build_rename_table(source, rules)
    report.renamed = len(renames)

    for entry in manifest.get("exchanges", []):
        for key in ("request", "response"):
            relative = entry.get(key)
            if not relative:
                continue
            src_file = source / relative
            if not src_file.exists():
                continue
            is_json = src_file.suffix == ".json"
            structural = any(tag in src_file.name for tag in rules.structural_files)
            text = sanitise_text(
                src_file.read_text(encoding="utf-8", errors="replace"),
                pseudo,
                rules,
                renames,
                is_json=is_json,
                structural=structural,
            )
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        report.exchanges += 1

    manifest["sanitised"] = True
    manifest["pseudonym_domain"] = rules.domain
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report.replacements = len(pseudo.map)
    report.orphans = find_orphans(dest, manifest.get("exchanges", []))
    report.unparseable = find_unparseable(dest)
    report.leaks = find_leaks(dest, rules.terms)

    if mapping_path is not None:
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(
            json.dumps({"renames": renames, "values": pseudo.map}, indent=2),
            encoding="utf-8",
        )
        mapping_path.chmod(0o600)

    return report


def find_orphans(corpus: Path, exchanges: list[dict]) -> list[str]:
    """Files no manifest entry references, in either direction.

    A request without its response cannot be replayed, and the failure only
    shows up when a client asks for it.
    """
    referenced: set[str] = set()
    for entry in exchanges:
        referenced.add(entry.get("request", ""))
        referenced.add(entry.get("response", ""))
    orphans = []
    for bucket in ("soap", "rest"):
        directory = corpus / bucket
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            relative = f"{bucket}/{path.name}"
            if relative not in referenced:
                orphans.append(relative)
    return orphans


def find_unparseable(corpus: Path) -> list[tuple[str, str]]:
    """Files whose syntax a replacement broke.

    A rule that corrupts syntax is worse than no rule, and the damage is easy to
    miss by eye.

    An empty file is not broken: a GET has no request body, and recording one
    still writes the file so the request/response pairing holds.
    """
    broken: list[tuple[str, str]] = []
    for path in sorted(corpus.rglob("*.xml")):
        if not path.read_text(encoding="utf-8", errors="replace").strip():
            continue
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            broken.append((str(path.relative_to(corpus)), str(exc)))
    for path in sorted(corpus.rglob("*.json")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        try:
            json.loads(text)
        except ValueError as exc:
            broken.append((str(path.relative_to(corpus)), str(exc)))
    return broken


def find_leaks(corpus: Path, terms: tuple[str, ...]) -> list[str]:
    """Files where a forbidden token survived every pass.

    A term surviving in a *field name* is reported differently, because it needs
    a different decision: renaming a key changes the shape clients parse, so the
    choice is to accept the term or to rename the field deliberately.
    """
    if not terms:
        return []
    pattern = re.compile(
        "|".join(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])" for t in terms),
        re.IGNORECASE,
    )
    keys = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:')
    leaks = []
    for path in sorted(corpus.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not pattern.search(text):
            continue
        relative = str(path.relative_to(corpus))
        in_values = pattern.search(keys.sub('"":', text))
        leaks.append(relative if in_values else f"{relative} (in a field name)")
    return leaks


def check_corpus(corpus: Path, terms: tuple[str, ...] = ()) -> Report:
    """Verify an existing corpus without rewriting it."""
    manifest_path = corpus / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {corpus}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exchanges = manifest.get("exchanges", [])
    return Report(
        exchanges=len(exchanges),
        orphans=find_orphans(corpus, exchanges),
        unparseable=find_unparseable(corpus),
        leaks=find_leaks(corpus, terms),
    )
