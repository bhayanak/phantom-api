"""Write exchanges into the corpus layout that CorpusResponder reads back."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from phantom_api.mock.types import Operation

# Operation names arrive from the wire, so the filename keeps only characters
# that cannot be part of a path.
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_-]+")


def _target(op: Operation) -> str:
    if op.request is None:
        return ""
    return f"{op.request.path}?{op.request.query}" if op.request.query else op.request.path


class Recorder:
    """Append-only corpus writer.

    Sanitisation is *not* applied here: recording and publishing are separate
    decisions, and conflating them means a half-finished capture looks safe to
    commit. ``phantom-api mock record`` runs the sanitiser explicitly.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        (self.root / "soap").mkdir(parents=True, exist_ok=True)
        (self.root / "rest").mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.entries: list[dict] = []
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.entries = existing.get("exchanges", [])
        self._seq = len(self.entries)

    def record(self, op: Operation, status: int, response_body: str) -> dict:
        self._seq += 1
        transport = "rest" if op.protocol in {"openapi", "jsonrpc"} else "soap"
        suffix = "json" if transport == "rest" else "xml"
        label = _SAFE_LABEL.sub("_", op.name).strip("_") or "operation"
        stem = f"{self._seq:03d}-{label}"

        request_body = op.request.text if op.request else ""
        (self.root / transport / f"{stem}.req.{suffix}").write_text(request_body, encoding="utf-8")
        (self.root / transport / f"{stem}.resp.{suffix}").write_text(
            response_body, encoding="utf-8"
        )

        entry = {
            "stem": stem,
            "label": op.name,
            "transport": transport,
            "status": status,
            "method": op.request.method if op.request else "POST",
            # The query is part of the endpoint's identity: two pages of one
            # collection differ only there, and so do APIs that select the
            # operation with `?action=`. Dropping it makes them indistinguishable.
            "endpoint_path": _target(op),
            "request": f"{transport}/{stem}.req.{suffix}",
            "response": f"{transport}/{stem}.resp.{suffix}",
            "bytes": len(response_body),
        }
        self.entries.append(entry)
        self.flush()
        return entry

    def flush(self) -> None:
        self.manifest_path.write_text(
            json.dumps(
                {
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "sanitised": False,
                    "exchanges": self.entries,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self.entries)
