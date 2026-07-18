<p align="center">
  <img src="logo.svg" alt="phantom-api" width="128" height="128">
</p>

<h1 align="center">phantom-api</h1>

<p align="center">
  <strong>Spin up a fully functional mock API server in seconds — from an OpenAPI spec, a JSON file, or a Postman collection.</strong>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#features">Features</a> ·
  <a href="#cli">CLI</a> ·
  <a href="#comparison">Comparison</a>
</p>

---

Frontend developers shouldn't have to wait for the backend. **phantom-api** turns any API
description into a running mock server with realistic fake data, latency simulation, error
injection, and record/replay — no cloud account, no Java, no boilerplate.

## Install

```bash
pip install phantomapi-server
```

Requires Python 3.10+.

## Quick start

```bash
# From an OpenAPI / Swagger spec
phantom-api serve petstore.yaml

# From a JSON file (auto-generates CRUD endpoints)
phantom-api serve users.json

# From a Postman collection
phantom-api serve collection.json --type postman
```

```
╭───────────────── phantom-api - Mock Server Running ─────────────────╮
│  http://127.0.0.1:3000                                              │
│  Source: petstore.yaml (openapi)                                    │
│  Routes: 4   Delay: 0ms                                             │
╰─────────────────────────────────────────────────────────────────────╯
```

## Features

| Feature | Description |
|---------|-------------|
| **From OpenAPI** | Parse OpenAPI 3.x / Swagger 2.0 → full mock server |
| **From JSON** | Drop a JSON file → instant CRUD endpoints |
| **From Postman** | Import Postman Collection v2.1 → mock all requests |
| **Fake data** | Schema-aware generation (format, pattern, and field-name aware) via Faker |
| **Custom delays** | Simulate latency globally or per request |
| **Error simulation** | Return 4xx/5xx by header or query param |
| **Record & replay** | Proxy a real API, record responses, replay offline |
| **Hot reload** | Watch the spec file and rebuild routes on change |
| **Admin dashboard** | Inspect routes and request stats at `/__admin` |

## CLI

```bash
phantom-api serve spec.yaml                     # start a mock server
phantom-api serve spec.yaml --port 4000         # custom port
phantom-api serve spec.yaml --host 0.0.0.0      # expose beyond localhost (opt-in)
phantom-api serve spec.yaml --delay 200ms       # add latency
phantom-api serve spec.yaml --watch             # hot reload on changes
phantom-api serve spec.yaml --seed 42           # deterministic fake data

phantom-api record https://api.example.com -o recordings/   # record
phantom-api replay recordings/                              # replay offline

phantom-api routes spec.yaml                    # list generated routes
phantom-api validate spec.yaml                  # validate without serving
```

### Per-request controls

| Control | Header | Query |
|---------|--------|-------|
| Latency (ms) | `X-Mock-Delay: 250` | `?__delay=250` |
| Force error | `X-Mock-Status: 503` | `?__status=503` |

```bash
curl "http://127.0.0.1:3000/pets?__status=503"   # → 503 error body
curl "http://127.0.0.1:3000/pets?__delay=500"    # → 500ms latency
```

### Admin endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/__admin/routes` | List all generated routes |
| GET | `/__admin/stats` | Request statistics and uptime |
| POST | `/__admin/reset` | Reset statistics |

## Fake data generation

phantom-api maps schema fragments to realistic values, in priority order:

1. **Spec examples** — any `example` in the schema or response is used verbatim.
2. **Semantic match** — `format: email` → an address, `format: uuid` → a UUID,
   a field named `first_name` → a first name, `pattern` → a matching string.
3. **Type fallback** — bounded integers, floats, booleans, arrays, and nested objects.

Pass `--seed` for reproducible output in CI.

## Comparison

| | phantom-api | Prism | WireMock | Postman Mock |
|--|:--:|:--:|:--:|:--:|
| Single-command start | ✅ | ✅ | ⚠️ | ⚠️ |
| No runtime beyond Python | ✅ | ❌ (Node) | ❌ (Java) | ❌ (cloud) |
| OpenAPI + Swagger | ✅ | ✅ | ⚠️ | ⚠️ |
| JSON → CRUD | ✅ | ❌ | ❌ | ❌ |
| Postman collections | ✅ | ❌ | ❌ | ✅ |
| Record & replay | ✅ | ⚠️ | ✅ | ❌ |
| Offline / no account | ✅ | ✅ | ✅ | ❌ |

## Security

- Binds to `127.0.0.1` by default; exposing the server requires an explicit `--host`.
- Specs are validated and never executed; `$ref` resolution is cycle-safe and refuses
  external references.
- Recordings use hash-based filenames confined to the output directory (no path traversal).
- Resource limits: max 10,000 routes and 64 MB spec files.

## Development

```bash
pip install -e ".[dev]"
ruff check src/ tests/
pytest --cov=src/phantom_api
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for details.

## License

[MIT](LICENSE)
