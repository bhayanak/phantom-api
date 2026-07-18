"""Inline leftover local ``$ref`` file pointers into a bundled OpenAPI spec.

`redocly bundle --dereferenced` inlines schemas and responses but leaves ``$ref`` pointers
that live inside ``example`` / ``examples.*.value`` fields untouched (they are treated as
literal data). This script walks a bundled spec and replaces every remaining
``{"$ref": "<local file>"}`` node with the actual contents of that file, producing a single
self-contained spec that phantom-api can serve verbatim.

Usage:
    python skills/openapi-mock-server/inline_example_refs.py bundled.json --base . -o mock.json

``--base`` is the root of the original spec source tree (used to resolve the relative ref
paths). JSON and YAML targets are both supported, and trailing commas are tolerated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with phantom-api's deps.
    yaml = None

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


class Inliner:
    def __init__(self, base: Path) -> None:
        self.base = base.resolve()
        self._cache: dict[str, Any] = {}
        self.missing: set[str] = set()
        self.repaired: set[str] = set()
        self.inlined = 0
        # Index every file under base by basename for fallback resolution.
        self._by_name: dict[str, list[Path]] = {}
        for path in self.base.rglob("*"):
            if path.is_file():
                self._by_name.setdefault(path.name, []).append(path)

    def _resolve_path(self, ref: str) -> Path | None:
        # Drop any JSON-pointer fragment; example refs point at whole files.
        file_part = ref.split("#", 1)[0]
        if not file_part:
            return None
        norm = re.sub(r"^(?:\.\./|\./)+", "", file_part)

        candidate = (self.base / norm).resolve()
        if candidate.is_file():
            return candidate

        matches = self._by_name.get(Path(norm).name, [])
        if len(matches) == 1:
            return matches[0]
        for match in matches:
            if match.as_posix().endswith(norm):
                return match
        return matches[0] if matches else None

    def _load(self, path: Path) -> Any:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        text = path.read_text(encoding="utf-8")
        value = self._parse(text, path)
        self._cache[key] = value
        return value

    def _parse(self, text: str, path: Path) -> Any:
        if path.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
            return yaml.safe_load(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = _TRAILING_COMMA.sub(r"\1", text)
            self.repaired.add(path.name)
            return json.loads(repaired)

    def transform(self, node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                target = self._resolve_path(ref)
                if target is None:
                    self.missing.add(ref)
                    return None
                self.inlined += 1
                return self._load(target)
            return {key: self.transform(value) for key, value in node.items()}
        if isinstance(node, list):
            return [self.transform(item) for item in node]
        return node


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inline local $ref files into a bundled spec.")
    parser.add_argument("spec", type=Path, help="Bundled spec file (JSON or YAML).")
    parser.add_argument(
        "--base", type=Path, default=Path("."), help="Root of the original spec source tree."
    )
    parser.add_argument(
        "-o", "--output", type=Path, required=True, help="Where to write the resolved spec."
    )
    args = parser.parse_args(argv)

    text = args.spec.read_text(encoding="utf-8")
    if args.spec.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        spec = yaml.safe_load(text)
    else:
        spec = json.loads(text)

    inliner = Inliner(args.base)
    resolved = inliner.transform(spec)
    args.output.write_text(json.dumps(resolved), encoding="utf-8")

    size_mb = args.output.stat().st_size / 1024 / 1024
    print(
        f"Inlined {inliner.inlined} ref(s); "
        f"{len(inliner.missing)} unresolved; {len(inliner.repaired)} repaired."
    )
    if inliner.missing:
        print("Unresolved refs:", ", ".join(sorted(inliner.missing)[:10]))
    print(f"Wrote {args.output} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
