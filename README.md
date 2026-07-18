<p align="center">
  <img src="https://raw.githubusercontent.com/bhayanak/phantom-api/refs/heads/main/logo.svg" alt="phantom-api" width="128" height="128">
</p>

<h1 align="center">phantom-api</h1>

<p align="center">
  <a href="https://github.com/bhayanak/phantom-api/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/bhayanak/phantom-api/ci.yml?branch=main&label=CI" alt="CI status">
  </a>
  <a href="https://github.com/bhayanak/phantom-api/actions/workflows/release.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/bhayanak/phantom-api/release.yml?label=Release" alt="Release workflow status">
  </a>
  <a href="coverage.xml">
    <img src="https://img.shields.io/badge/Coverage-98.85%25-brightgreen" alt="Test coverage">
  </a>
  <a href="https://pypi.org/project/phantomapi-server/">
    <img src="https://img.shields.io/pypi/v/phantomapi-server" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/phantomapi-server/">
    <img src="https://img.shields.io/pypi/pyversions/phantomapi-server" alt="Python versions">
  </a>
  <a href="https://hub.docker.com/r/fazorboy/phantom-api">
    <img src="https://img.shields.io/docker/pulls/fazorboy/phantom-api" alt="Docker pulls">
  </a>
  <a href="https://hub.docker.com/r/fazorboy/phantom-api">
    <img src="https://img.shields.io/docker/image-size/fazorboy/phantom-api/latest" alt="Docker image size">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/bhayanak/phantom-api" alt="License">
  </a>
</p>

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

## Run in Docker

Run a mock server anywhere — CI, a shared dev box, or production-like environments —
without installing Python. The image binds to `0.0.0.0:3000` inside the container.

```bash
# Build the image
docker build -t phantom-api .

# Serve a spec by mounting it into /spec
docker run --rm -p 3000:3000 \
  -v "$PWD/openapi.json:/spec/openapi.json:ro" \
  phantom-api serve /spec/openapi.json --type openapi --seed 42
```

Or use the published image from Docker Hub:

```bash
docker run --rm -p 3000:3000 \
  -v "$PWD/openapi.json:/spec/openapi.json:ro" \
  fazorboy/phantom-api serve /spec/openapi.json
```

### docker compose

A ready-made [docker-compose.yml](docker-compose.yml) serves a mounted spec (defaulting to
the Morpheus mock). Point it at any single-file spec with `SPEC_FILE`:

```bash
SPEC_FILE=./openapi.json docker compose up --build
curl http://localhost:3000/__admin/routes
```

The container reads `PHANTOM_API_HOST` and `PHANTOM_API_PORT` (default `0.0.0.0` / `3000`),
so the same environment variables configure the server locally too.

> Multi-file specs (a root `openapi.yaml` that `$ref`s into `paths/` and `components/`)
> must first be bundled into a single self-contained file. See
> [Turn any OpenAPI repo into a mock](#turn-any-openapi-repo-into-a-mock).

## Turn any OpenAPI repo into a mock

Many API projects (for example
[HewlettPackard/morpheus-openapi](https://github.com/HewlettPackard/morpheus-openapi))
publish a **multi-file** OpenAPI spec plus example JSONs. To serve one:

```bash
# 1. Bundle the multi-file spec into a single dereferenced file (Redocly)
npx @redocly/cli bundle openapi.yaml -o bundled.json --dereferenced

# 2. Inline any example $refs that Redocly leaves inside example values
python skills/openapi-mock-server/inline_example_refs.py bundled.json --base . -o mock.json

# 3. Serve it (locally or in Docker)
phantom-api serve mock.json --type openapi --seed 42
```

The bundled **`openapi-mock-server`** skill automates this end to end — fetching the repo,
detecting its build tooling, bundling, inlining examples, and starting the server locally
or in a container. See [skills/openapi-mock-server/SKILL.md](skills/openapi-mock-server/SKILL.md).

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
