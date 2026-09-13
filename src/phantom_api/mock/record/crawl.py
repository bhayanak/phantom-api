"""Record by calling a list of endpoints.

Proxy recording needs a client to drive the traffic. Often there isn't one --
there are credentials, a base URL, and a list of paths worth capturing. This
walks that list and writes the same corpus layout the proxy recorder produces,
so everything downstream is identical.

Read-only by design: it issues GETs (and any explicitly listed method), never
guesses a mutating call, and stops at the first sign it is being rate-limited.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from phantom_api.mock.record.recorder import Recorder
from phantom_api.mock.types import Operation, RawRequest

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class CrawlReport:
    recorded: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.recorded > 0


def parse_endpoints(text: str) -> list[tuple[str, str, str]]:
    """Read an endpoint list: ``[METHOD] /path [# label]``, one per line.

    The method is optional and defaults to GET, so the common case is a plain
    list of paths.
    """
    endpoints: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isupper() and parts[0].isalpha():
            method, path = parts[0], parts[1].strip()
        else:
            method, path = "GET", line
        label = _SAFE.sub("_", path.strip("/")).strip("_") or "root"
        endpoints.append((method, path, f"{method} {label}"))
    return endpoints


def crawl(
    base_url: str,
    endpoints: list[tuple[str, str, str]],
    out: Path,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
    timeout: float = 60.0,
    pause: float = 0.0,
    client: httpx.Client | None = None,
) -> CrawlReport:
    """Call every endpoint once and record the result."""
    recorder = Recorder(out)
    report = CrawlReport()
    base = base_url.rstrip("/")

    owned = client is None
    active = client or httpx.Client(verify=verify, timeout=timeout)
    try:
        for method, path, label in endpoints:
            url = f"{base}{path if path.startswith('/') else '/' + path}"
            try:
                response = active.request(method, url, headers=headers or {})
            except httpx.HTTPError as exc:
                report.failed.append((path, str(exc)))
                continue

            if response.status_code == 429:
                report.skipped.append(path)
                break

            request = RawRequest(
                method=method,
                path=path.split("?", 1)[0],
                query=path.split("?", 1)[1] if "?" in path else "",
                headers={"content-type": "application/json"},
                body=b"",
            )
            op = Operation(
                name=label,
                protocol="openapi",
                params={},
                headers={},
                session_id=None,
                request=request,
                context={},
            )
            recorder.record(op, response.status_code, response.text)
            report.recorded += 1
            if pause:
                time.sleep(pause)
    finally:
        if owned:
            active.close()

    return report


def expand_collections(
    base_url: str,
    corpus: Path,
    *,
    headers: dict[str, str] | None = None,
    verify: bool = True,
    limit: int = 3,
    client: httpx.Client | None = None,
) -> CrawlReport:
    """Follow recorded collections to a few of their members.

    A list endpoint shows the summary shape; the detail endpoint shows the full
    one, and they are rarely the same. Without at least one detail response, a
    corpus silently cannot answer the request clients make most.
    """
    manifest_path = corpus / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {corpus}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    follow: list[tuple[str, str, str]] = []
    seen = {entry.get("endpoint_path", "") for entry in manifest.get("exchanges", [])}
    for entry in manifest.get("exchanges", []):
        path = entry.get("endpoint_path", "")
        body = corpus / entry.get("response", "")
        if not path or not body.exists() or entry.get("status") != 200:
            continue
        try:
            payload = json.loads(body.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for member_id in _collection_ids(payload)[:limit]:
            # The recorded path keeps its query so pages stay distinguishable,
            # but a member lives under the collection, not under the query.
            detail = f"{path.split('?', 1)[0].rstrip('/')}/{member_id}"
            # Paged views of one collection list the same members, so without
            # this the same detail response is fetched and stored repeatedly.
            if detail in seen:
                continue
            seen.add(detail)
            label = _SAFE.sub("_", detail.strip("/")).strip("_")
            follow.append(("GET", detail, f"GET {label}"))

    if not follow:
        return CrawlReport()
    return crawl(base_url, follow, corpus, headers=headers, verify=verify, client=client)


def _collection_ids(payload: object) -> list[str]:
    """Identifiers of the first list of objects that carries them."""
    if not isinstance(payload, dict):
        return []
    for value in payload.values():
        if not isinstance(value, list) or not value:
            continue
        ids = [
            str(item["id"])
            for item in value
            if isinstance(item, dict) and isinstance(item.get("id"), (int, str))
        ]
        if ids:
            return ids
    return []
