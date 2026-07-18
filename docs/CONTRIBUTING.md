# Contributing to phantom-api

Thanks for your interest in improving phantom-api!

## Development setup

```bash
git clone https://github.com/phantom-api/phantom-api
cd phantom-api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running checks

```bash
ruff check src/ tests/        # lint
ruff format src/ tests/       # format
pytest --cov=src/phantom_api  # tests + coverage
```

Coverage must stay at or above 95%.

## Project layout

| Path | Purpose |
|------|---------|
| `src/phantom_api/parsers/` | Convert source formats into a `MockSpec` |
| `src/phantom_api/generators/` | Schema-aware fake data + response building |
| `src/phantom_api/server/` | FastAPI app factory, routing, middleware, hot reload |
| `src/phantom_api/recorder/` | Proxy, storage, and replay |
| `src/phantom_api/output/` | Admin dashboard endpoints |
| `src/phantom_api/cli.py` | Typer CLI entry point |

## Adding a parser

1. Subclass `BaseParser` in `src/phantom_api/parsers/`.
2. Implement `_matches` (cheap detection) and `parse` (full conversion to `MockSpec`).
3. Register it in `parsers/__init__.py`.
4. Add fixtures and tests under `tests/parsers/`.

## Pull requests

- Keep changes focused and covered by tests.
- Run the full check suite before opening a PR.
- Update `CHANGELOG.md` under an `Unreleased` heading.
