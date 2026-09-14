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
  <a href="https://github.com/bhayanak/phantom-api/actions/workflows/ci.yml">
    <img src="https://img.shields.io/badge/Coverage-%E2%89%A595%25-brightgreen" alt="Test coverage gate">
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
  <strong>Mock any API in seconds — from an OpenAPI spec, a Postman collection,
  a recording of the real thing, or a model you drive.</strong>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#mock-any-protocol">Any protocol</a> ·
  <a href="#features">Features</a> ·
  <a href="#cli">CLI</a> ·
  <a href="docs/building-mocks.md">Full guide</a> ·
  <a href="#comparison">Comparison</a>
</p>

---

Nobody should have to wait for a backend, or borrow a datacenter, to write code.
**phantom-api** turns any API into a running mock: point it at a spec, at a recording,
or at a model — no cloud account, no Java, no boilerplate.

It does this in two layers:

- **Spec-driven** (`phantom-api serve`) — an OpenAPI/Swagger spec, a JSON file or a
  Postman collection becomes a REST mock with realistic fake data. One command.
- **Protocol mocks** (`phantom-api mock`) — for systems that have no spec, do not speak
  REST, hold a session, or stream changes. SOAP, JSON-RPC and REST, replayed from
  recordings or simulated from a stateful model.

## Install

```bash
pip install phantomapi-server            # spec-driven mocks
pip install 'phantomapi-server[mock]'    # adds TLS for protocol mocks
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

## Mock any protocol

When there is no spec — SOAP, JSON-RPC, a stateful session, a change feed —
use `phantom-api mock`.

Pick a route by **where the answers come from**, not by protocol. Everything
below the protocol pack is shared, so these all work for SOAP, REST, JSON-RPC or
anything you add a pack for:

| You have | Route | Effort |
|---|---|---|
| An OpenAPI/Swagger spec or Postman collection | `phantom-api serve` | minutes |
| A few saved or hand-written responses | `template` responder | ~an hour |
| Access to the real system | record → sanitise → derive → serve | ~an hour |
| Domain knowledge and a stateful client | `model` responder + domain pack | a day or two |
| Access, and a reason not to fake it | `forward` responder | minutes |

Full walkthroughs for each: **[docs/building-mocks.md](docs/building-mocks.md)**.
Two complete worked examples: **[examples/](examples/)**.

### Replay a recording

The fastest route to a working mock of something that has no contract. Record the
real system once, make it safe to commit, and let the recording write the config:

```bash
phantom-api mock record https://real.example.com --out ./raw \
  --i-understand-this-hits-production

phantom-api mock sanitize ./raw --out ./corpus     # stable pseudonyms; fails if unsafe
phantom-api mock derive ./corpus -o phantom.yaml   # protocols, paths, sessions
phantom-api mock serve phantom.yaml
```

Wire-accurate, and nothing about it was guessed — `derive` reads the protocols,
path patterns and session transport off the recorded traffic.

For a corpus you already have, replay is a single command:

```bash
phantom-api mock serve --corpus ./corpus --port 8443 --tls self-signed
```

### Simulate a stateful system

When the client logs in, walks a graph and polls for changes, replay is not
enough. A **domain pack** gives the mock a model that stays self-consistent and
evolves over time:

```yaml
# phantom.yaml
mock:
  name: vcenter
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }
  protocols:
    - pack: soap
      paths: ["/sdk", "/pbm/sdk"]
      session:
        "/sdk":     { transport: cookie,      name: vmware_soap_session }
        "/pbm/sdk": { transport: soap-header, name: vcSessionCookie }
  model:
    pack: vcenter
    scenario: scenario/vcenter-scenario.yaml
  responders:
    - match: { operation: ["Login", "Retrieve*"] }
      responder: model      # must stay self-consistent
    - match: "*"
      responder: corpus     # everything else, replayed
      corpus: corpus
```

```bash
phantom-api mock serve examples/vcenter/phantom.yaml
```

Then drive the simulation from a test:

```bash
# Power off a VM. The model changes first, then the event is emitted -- always
# that order, so a client can trust its own reconciliation.
curl -sk -X POST https://localhost:8443/__phantom/events \
  -H 'Content-Type: application/json' \
  -d '{"event":"VmPoweredOffEvent","target":"vm-23"}'

# What did my client actually ask for?
phantom-api mock log --url https://localhost:8443
```

### The four responders

They compose — serve most operations from a corpus, synthesise the rest from a
model, forward anything unknown, and record it as you go.

| Responder | Source of truth | Strength | Limit |
|---|---|---|---|
| `forward` | the real system | perfect | needs the real system |
| `corpus` | recorded traffic | wire-accurate, zero modelling | only answers what was recorded |
| `template` | declarative rules | fast to author, readable diffs | no consistency between responses |
| `model` | a domain model | consistent, generative, evolves | most work to build |

**→ [Build a mock server for anything](docs/building-mocks.md)** — a step-by-step
guide with two complete worked examples: a 1017-endpoint REST API from a spec,
and a stateful SOAP API captured from a live system.

## Features

### Spec-driven mocks

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

### Protocol mocks

| Feature | Description |
|---------|-------------|
| **Protocol packs** | SOAP 1.1/1.2, JSON-RPC 1.0/2.0, REST — or add your own |
| **Record & sanitise** | Capture a real system, then pseudonymise it so it is safe to commit |
| **Config from a recording** | `mock derive` reads protocols, paths and session transport off the wire |
| **Object graph from a recording** | `mock derive --model` recovers entities and their references |
| **Drift detection** | Replay a corpus against the real system and diff, so it cannot rot unnoticed |
| **Stateful model** | Typed entities and edges, dotted-path reads, partial-failure results |
| **Graph traversal** | Evaluate client-supplied traversal programs, with cycle safety |
| **Change feed** | Scripted or random evolution; the model mutates *before* the event is emitted |
| **Sessions & cursors** | Cookie, header or SOAP-header sessions; bounded server-side cursors |
| **Chaos** | Latency, faults in the target protocol's own terms, dropped connections |
| **Verification** | Assert which operations arrived, as a test oracle |
| **Control plane** | Inspect the model, inject events, toggle chaos at `/__phantom` |
| **Hardened XML** | `DOCTYPE`/`ENTITY` refused outright, plus size and depth caps |
| **Safe templates** | Expressions parsed to an AST, not `eval` — no imports, no dunders |

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
### Protocol mocks

```bash
phantom-api mock serve phantom.yaml             # serve from a config file
phantom-api mock serve --corpus ./corpus        # replay a corpus, no config
phantom-api mock record https://real.example.com --out ./corpus \
    --i-understand-this-hits-production         # proxy and record
phantom-api mock record https://real.example.com --out ./raw \
    --endpoints paths.txt --follow 3 -H "Authorization: Bearer $TOKEN" \
    --i-understand-this-hits-production         # record by calling, not proxying

phantom-api mock sanitize ./raw --out ./corpus  # strip identity; fails if unsafe
phantom-api mock derive ./corpus -o phantom.yaml # write a config from a recording
phantom-api mock derive ./corpus --model .      # ...and recover the object graph
phantom-api mock drift ./corpus --target https://real.example.com \
    --i-have-permission                         # has the real system moved on?

phantom-api mock init model -o phantom.yaml     # starter config (corpus|template|model)
phantom-api mock validate phantom.yaml          # check a config without binding a port
phantom-api mock inspect ./corpus               # what can this corpus answer?
phantom-api mock list-packs                     # available protocol and domain packs

phantom-api mock log --url https://localhost:8443       # what was asked for
phantom-api mock verify Login --at-least 1              # assert it; non-zero on failure
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

### Control plane (protocol mocks)

Mounted at `/__phantom`, loopback-only by default.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/__phantom/status` | Responders, sessions, cursors, model summary |
| GET | `/__phantom/log` | Every decoded operation, with timing and responder |
| POST | `/__phantom/verify` | Assert an operation arrived N times |
| GET | `/__phantom/inventory` | Dump the model |
| POST | `/__phantom/events` | Inject a change now |
| POST | `/__phantom/mutate` | Edit the model directly |
| POST | `/__phantom/chaos` | Toggle faults at runtime |
| POST | `/__phantom/reset` | Reset counters and the log |

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

[docker-compose.yml](docker-compose.yml) defines both kinds of mock:

```bash
SPEC_FILE=./openapi.json docker compose up --build phantom-api   # REST mock
docker compose --profile mock up examples/vcenter                    # protocol mock
```

The container reads `PHANTOM_API_HOST` and `PHANTOM_API_PORT` (default `0.0.0.0` / `3000`),
so the same environment variables configure the server locally too.

> Clients that build their URL without a port need the mock published on 443:
> `docker run -p 443:8443 ... phantom-api mock serve /spec/phantom.yaml`

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
python skills/mock-server/inline_example_refs.py bundled.json --base . -o mock.json

# 3. Serve it (locally or in Docker)
phantom-api serve mock.json --type openapi --seed 42
```

The bundled **`mock-server`** skill automates this and every other route end to end —
choosing an approach from what you have, fetching and bundling a spec, recording and
sanitising a live system, deriving the config, and verifying the result.
See [skills/mock-server/SKILL.md](skills/mock-server/SKILL.md).

## Fake data generation

phantom-api maps schema fragments to realistic values, in priority order:

1. **Spec examples** — any `example` in the schema or response is used verbatim.
2. **Semantic match** — `format: email` → an address, `format: uuid` → a UUID,
   a field named `first_name` → a first name, `pattern` → a matching string.
3. **Type fallback** — bounded integers, floats, booleans, arrays, and nested objects.

Pass `--seed` for reproducible output in CI.

## Comparison

| | phantom-api | Prism | WireMock | MockServer | SoapUI | Postman Mock |
|--|:--:|:--:|:--:|:--:|:--:|:--:|
| Single-command start | ✅ | ✅ | ⚠️ | ✅ | ❌ | ⚠️ |
| No runtime beyond Python | ✅ | ❌ (Node) | ❌ (Java) | ❌ (Java) | ❌ (Java) | ❌ (cloud) |
| OpenAPI + Swagger | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | ⚠️ |
| JSON → CRUD | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Postman collections | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| SOAP | ✅ | ❌ | ⚠️ | ⚠️ | ✅ | ❌ |
| Record & replay | ✅ | ⚠️ | ✅ | ✅ | ❌ | ❌ |
| Sanitise recordings | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Chaos / fault injection | ✅ | ❌ | ⚠️ | ✅ | ⚠️ | ❌ |
| Request verification | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **Stateful domain model** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Client-driven graph traversal** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Evolving change feed** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Offline / no account | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

SoapUI and MockServer solve the stateless half well, and their structure informed
this design — SoapUI's operation/dispatch model and MockServer's expectations,
record-replay and chaos surface. Neither owns a **model of the system it is
pretending to be**, which is what graph-shaped, stateful, change-feed protocols
actually need.

## Security

- Binds to `127.0.0.1` by default; exposing the server requires an explicit `--host`.
- Specs are validated and never executed; `$ref` resolution is cycle-safe and refuses
  external references.
- **XML parsing is hardened**: `DOCTYPE` and `ENTITY` declarations are refused outright
  rather than configured away, with size and nesting caps on top.
- **Templates are not `eval`**: expressions are parsed to an AST and every node type that
  is not explicitly allowed is refused — no imports, no dunder access, no file or network
  reach.
- **Recording is deliberate**: proxying a live system requires an explicit acknowledgement
  flag, and loopback targets are refused.
- **Recordings are sanitised** before they can be published; the reversal mapping is written
  outside the corpus and git-ignored. CI asserts no unsanitised capture is ever committed.
- **Secrets come from the environment only** — an unset `${VAR}` is a startup error, not a
  silently wrong value.
- Recordings use path-safe filenames confined to the output directory.
- Bounded by default: sessions, cursors, event collectors, request log and inventory size
  all evict rather than grow.
- Resource limits: max 10,000 routes, 64 MB spec files, 10 MB request bodies.
- The control plane is loopback-only by default — it can mutate state, so it must never
  share an interface with the mocked service.
- **The container is scanned and gated**: zero critical or high findings at the time of
  writing, running as a non-root user with no package installer present. CI fails the
  build on any new critical or high finding rather than filing an alert nobody reads.

Full policy, threat model and reporting process: **[SECURITY.md](SECURITY.md)**.

Run the same checks CI runs, before you push:

```bash
make security        # dependencies, config, secrets, image, published corpora
```

## Development

```bash
pip install -e ".[dev]"
ruff check src/ tests/
ruff format --check src/ tests/
pytest --cov=src/phantom_api
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for details, and
[docs/building-mocks.md](docs/building-mocks.md) for the full guide to mocking
your own systems.

## License

[MIT](LICENSE)
