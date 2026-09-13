"""Proxy to the real system, optionally recording what comes back.

This is how a corpus grows: point the mock at the real service, let the client
drive, and every unmatched request becomes a new recorded exchange.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from phantom_api.mock.types import MockError, Operation, RawResponse

#: Hosts that must never be proxied to by accident.
BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class ForwardResponder:
    name = "forward"

    def __init__(
        self,
        target: str,
        *,
        record_to: Path | None = None,
        verify: bool = False,
        timeout: float = 60.0,
    ) -> None:
        if not target:
            raise ValueError("forward responder requires a target URL")
        self.target = target.rstrip("/")
        self.record_to = record_to
        self._client = httpx.Client(verify=verify, timeout=timeout, follow_redirects=False)
        self._recorder: Any = None
        if record_to is not None:
            from phantom_api.mock.record.recorder import Recorder

            self._recorder = Recorder(record_to)

    def respond(self, op: Operation) -> RawResponse:
        request = op.request
        if request is None:
            raise MockError("forward responder needs the original request", kind="internal")
        url = f"{self.target}{request.path}"
        if request.query:
            url = f"{url}?{request.query}"
        headers = {k: v for k, v in request.headers.items() if k not in {"host", "content-length"}}
        try:
            upstream = self._client.request(
                request.method, url, headers=headers, content=request.body
            )
        except httpx.HTTPError as exc:
            raise MockError(f"upstream unreachable: {exc}", kind="internal", status=502) from exc

        if self._recorder is not None:
            self._recorder.record(op, upstream.status_code, upstream.text)

        return RawResponse(
            status=upstream.status_code,
            headers={
                k.lower(): v
                for k, v in upstream.headers.items()
                if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            },
            body=upstream.content,
        )

    def close(self) -> None:
        self._client.close()
        if self._recorder is not None:
            self._recorder.flush()

    def describe(self) -> str:
        suffix = f", recording to {self.record_to}" if self.record_to else ""
        return f"forward({self.target}{suffix})"
