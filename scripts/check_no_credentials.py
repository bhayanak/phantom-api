#!/usr/bin/env python3
"""Fail if a published corpus still holds a real credential.

Sanitisation already replaces credential-named fields, and its own verification
catches unpaired, unparseable and forbidden-term problems. This is the check for
what sanitisation cannot see: a corpus committed before a rule existed, or one
produced by an older version of the tool.

Detection is by field *name*, because a credential has no distinguishing shape --
a password looks like any other short string. That makes false positives the
main risk, and a check that cries wolf gets switched off, so values that cannot
be credentials are excluded deliberately:

- numbers, which is what a paging or continuation `token` holds;
- anything already carrying a redaction marker, whoever wrote it;
- very short values, which no real secret is.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_NAMES = (
    r"password|passphrase|secret|apikey|api_key|privatekey|private_key"
    r"|credential|authtoken|auth_token|accesstoken|access_token"
    r"|refreshtoken|refresh_token|bearertoken|sessionkey|session_key"
)

JSON_SECRET = re.compile(
    rf'"([a-zA-Z_]*(?:{_NAMES})[a-zA-Z_]*)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE
)

XML_SECRET = re.compile(rf"<(\w*(?:{_NAMES})\w*)>([^<]*)</\1>", re.IGNORECASE)

CORPORA = ("examples/vcenter/corpus", "examples/morpheus/corpus")

#: Markers meaning "already removed", from this tool or from a capture script.
REDACTED = re.compile(
    r"^(\[scrubbed\]|<scrubbed>|\*+REDACTED\*+|REDACTED|\*{3,}|x{3,})$", re.IGNORECASE
)

#: Shorter than any real secret, and where false positives start.
MIN_SECRET_LENGTH = 8


def is_credential(value: str) -> bool:
    value = value.strip()
    if not value or value.lower() in {"null", "none", "false", "true"}:
        return False
    if REDACTED.match(value):
        return False
    if value.isdigit():
        return False
    return len(value) >= MIN_SECRET_LENGTH


def findings(root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (JSON_SECRET, XML_SECRET):
            for match in pattern.finditer(text):
                name, value = match.group(1), match.group(2)
                if is_credential(value):
                    problems.append(f"{path}: {name} = {value[:30]!r}")
    return problems


def main() -> int:
    problems: list[str] = []
    checked = 0
    for corpus in CORPORA:
        root = Path(corpus)
        if not root.exists():
            continue
        checked += 1
        problems.extend(findings(root))

        manifest = root / "manifest.json"
        if manifest.exists():
            marked = json.loads(manifest.read_text(encoding="utf-8")).get("sanitised")
            if marked is not True:
                problems.append(f"{corpus}: manifest is not marked sanitised")

    if problems:
        print(f"{len(problems)} problem(s) found:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  ! {problem}", file=sys.stderr)
        return 1

    print(f"no credentials found in {checked} published corpora")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
