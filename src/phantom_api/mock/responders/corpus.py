"""Replay recorded traffic.

Wire-accurate and requires no modelling at all, which makes it the fastest route
to a working mock and the reference every other responder is checked against.
The cost is that it only answers what was recorded -- hence ``on_miss``.

Corpus layout (as produced by ``phantom-api mock record``):

    manifest.json
    soap/NNN-<label>.req.xml   soap/NNN-<label>.resp.xml
    rest/NNN-<label>.req.json  rest/NNN-<label>.resp.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from phantom_api.mock.protocols.xml_codec import XmlError, local_name, parse_xml
from phantom_api.mock.record.fingerprint import fingerprint
from phantom_api.mock.types import MockError, NotHandled, Operation, RawResponse


@dataclass(slots=True)
class Exchange:
    label: str
    operation: str
    status: int
    request_body: str
    response_body: str
    content_type: str
    endpoint_path: str
    transport: str
    method: str = "POST"


class CorpusResponder:
    name = "corpus"

    def __init__(self, corpus: Path, *, on_miss: str = "fault") -> None:
        self.corpus = corpus
        self.on_miss = on_miss
        self._by_key: dict[str, list[Exchange]] = {}
        self._by_target: dict[str, list[Exchange]] = {}
        self._cursor: dict[str, int] = {}
        self.exchanges: list[Exchange] = []
        self._load()

    # -- loading --------------------------------------------------------

    def _load(self) -> None:
        manifest_path = self.corpus / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"no manifest.json in corpus {self.corpus}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("exchanges", []):
            request_path = self.corpus / entry["request"]
            response_path = self.corpus / entry["response"]
            if not response_path.exists():
                continue
            request_body = request_path.read_text(encoding="utf-8") if request_path.exists() else ""
            exchange = Exchange(
                label=entry.get("label", entry["stem"]),
                operation=self._operation_of(entry, request_body),
                status=int(entry.get("status") or 200),
                request_body=request_body,
                response_body=response_path.read_text(encoding="utf-8"),
                content_type=(
                    "application/json"
                    if entry.get("transport") == "rest"
                    else "text/xml; charset=utf-8"
                ),
                endpoint_path=entry.get("endpoint_path", ""),
                transport=entry.get("transport", "soap"),
                # Older recordings predate the method being stored. A request
                # with no body was a GET; anything else was a POST.
                method=entry.get("method") or ("POST" if request_body.strip() else "GET"),
            )
            self.exchanges.append(exchange)
            if exchange.transport == "rest" and exchange.endpoint_path:
                self._by_target.setdefault(exchange.endpoint_path, []).append(exchange)
            for key in self._keys_for(exchange):
                self._by_key.setdefault(key, []).append(exchange)

    @staticmethod
    def _operation_of(entry: dict, request_body: str) -> str:
        """Prefer the operation the wire shows over the recorded label."""
        if entry.get("transport") == "soap" and request_body:
            try:
                root = parse_xml(request_body)
            except XmlError:
                return entry.get("label", "")
            for child in root:
                if local_name(child.tag) == "Body":
                    call = next(iter(child), None)
                    if call is not None:
                        return local_name(call.tag)
        if entry.get("transport") == "rest":
            try:
                payload = json.loads(request_body or "{}")
                method = payload.get("method", "GET")
            except ValueError:
                method = "GET"
            return f"{method} {entry.get('endpoint_path', '')}"
        return entry.get("label", "")

    def _keys_for(self, exchange: Exchange) -> list[str]:
        return fingerprint(exchange.operation, exchange.request_body).keys()

    # -- responding -----------------------------------------------------

    def respond(self, op: Operation) -> RawResponse:
        body = op.request.text if op.request else ""

        # For anything URL-addressed, the full target *is* the identity, and it
        # is checked before the operation-name fingerprint. The protocol pack
        # strips the query when naming an operation, so `/things` and
        # `/things?page=2` arrive with the same name -- and the unqueried
        # recording would otherwise answer for every page of the collection.
        if op.protocol != "soap" and op.request:
            target = self._target(op)
            exact = self._by_target.get(target)
            if exact:
                return self._render(self._pick(f"target:{target}", exact))

        for key in fingerprint(op.name, body).keys():  # noqa: SIM118 - Fingerprint, not a dict
            candidates = self._by_key.get(key)
            if candidates:
                return self._render(self._pick(key, candidates))

        # A REST path identifies its operation, so fall back to it when the
        # recording predates path templating. A SOAP path does not -- every
        # operation shares one endpoint -- so no such fallback exists there.
        if op.protocol != "soap" and op.request:
            for exchange in self.exchanges:
                if (
                    exchange.transport == "rest"
                    and exchange.endpoint_path.split("?", 1)[0] == op.request.path
                ):
                    return self._render(exchange)
        return self._miss(op)

    @staticmethod
    def _target(op: Operation) -> str:
        if op.request is None:
            return ""
        return f"{op.request.path}?{op.request.query}" if op.request.query else op.request.path

    def _pick(self, key: str, candidates: list[Exchange]) -> Exchange:
        """Cycle through duplicates so repeated polling sees varying answers."""
        index = self._cursor.get(key, 0)
        self._cursor[key] = index + 1
        return candidates[index % len(candidates)]

    @staticmethod
    def _render(exchange: Exchange) -> RawResponse:
        return RawResponse(
            status=exchange.status,
            headers={"content-type": exchange.content_type},
            body=exchange.response_body.encode(),
        )

    def _miss(self, op: Operation) -> RawResponse:
        if self.on_miss == "synthesize":
            raise NotHandled(f"no recorded exchange for {op.name}")
        if self.on_miss == "not-found":
            raise MockError(f"no recorded exchange for {op.name}", kind="not-found", status=404)
        if self.on_miss == "forward":
            raise NotHandled(f"forwarding {op.name}")
        raise MockError(
            f"no recorded exchange for {op.name}",
            kind="invalid-request",
            detail_type="NotFound",
        )

    def describe(self) -> str:
        return (
            f"corpus({self.corpus.name}, {len(self.exchanges)} exchanges, on_miss={self.on_miss})"
        )
