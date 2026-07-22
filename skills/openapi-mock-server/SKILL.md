---
name: openapi-mock-server
description: 'Turn any OpenAPI/Swagger source into a running phantom-api mock server. USE WHEN the user wants a mock/fake API from a GitHub repo that publishes an OpenAPI spec (e.g. HewlettPackard/morpheus-openapi), from a spec URL, or from a local yaml/json file — including multi-file specs that need bundling (Redocly) and example $ref inlining. Handles cloning, detecting the repo build tooling, bundling, inlining example JSONs, and serving locally or in Docker.'
---

# OpenAPI → Mock Server

Turn an OpenAPI/Swagger source — or recorded live traffic — into a running mock API using
**phantom-api**. phantom-api serves spec examples verbatim and generates schema-aware fake
data when no example exists.

phantom-api is a published tool; do **not** assume its source code is checked out locally.

- **PyPI:** <https://pypi.org/project/phantomapi-server/>
- **Docker Hub:** `fazorboy/phantom-api` — <https://hub.docker.com/r/fazorboy/phantom-api>

## When to use

- "Mock the `<org>/<repo>` API" where the repo publishes an OpenAPI spec.
- "Spin up a fake server from this openapi.yaml / this URL."
- "Record this live API and give me an offline mock of it."
- "I need a stand-in for `<real API>` to develop/test against."

## Install phantom-api (pick one)

**pip / pipx** — provides the `phantom-api` command:

```bash
pip install phantomapi-server        # or: pipx install phantomapi-server
phantom-api --version
```

**Docker** — no Python needed; the image binds `0.0.0.0:3000` inside the container:

```bash
docker pull fazorboy/phantom-api
```

In every command below, either use the `phantom-api ...` CLI directly, or the Docker form
`docker run --rm -p 3000:3000 -v "$PWD:/spec" fazorboy/phantom-api <args...>`. Node/`npx` is
only needed for **bundling multi-file specs** (Redocly), not for phantom-api itself. The only
file this skill ships is the `inline_example_refs.py` helper next to this `SKILL.md`.

## Inputs (ask only if ambiguous)

1. **Source** — one of:
   - a GitHub repo URL that publishes an OpenAPI spec,
   - a direct spec URL,
   - a local yaml/json file, or
   - **record & replay** a live API (no spec needed — see Workflow D).
2. **Branch** (repos only) — default to the repo default branch unless the user names one
   (e.g. Morpheus uses `dev-9.0`).
3. **Run target** — `local` (default) or `docker`.
4. **Port** — default `3000`.

---

## Workflow A–C: from an OpenAPI spec

Keep generated artifacts in a dedicated folder (e.g. `<repo-name>/`) so the source tree and
the bundle stay together.

### 1. Acquire the spec

**GitHub repo** — shallow-clone the requested branch into a working folder:

```bash
git clone --depth 1 --branch <branch> https://github.com/<org>/<repo>.git <repo-name>
```

Then locate the OpenAPI entry point. Look for (in order): `openapi.yaml`, `openapi.json`,
`swagger.yaml`, or a spec referenced by `.redocly.yaml` / `redocly.yaml`. Multi-file specs
have a small root file whose `paths:` are `$ref`s into `paths/` and `components/` folders.

**Direct URL** — download it:

```bash
curl -fsSL <url> -o <repo-name>/openapi.yaml
```

**Local file** — use it directly; only bundle if it contains external `$ref`s.

### 2. Decide: single-file or multi-file?

- **Single self-contained file** (no external `$ref`, only `#/...` internal refs): skip to
  step 4 — phantom-api parses it directly (it resolves internal refs, cycle-safe).
- **Multi-file / external refs**: bundle first (step 3). Tell-tale signs: a `paths/` or
  `components/` directory, or `$ref: ../components/...` / `$ref: paths/...` in the root.

### 3. Bundle + inline examples (multi-file only)

**3a. Bundle with the repo's own tooling when present.** Check the README / `Rakefile` /
`package.json` / `.redocly.yaml`. Most repos (including morpheus-openapi) use Redocly:

```bash
cd <repo-name>
npx --yes @redocly/cli@latest bundle openapi.yaml -o bundled.json --dereferenced
```

If `npx`/`npm` fails with `EPERM` on the cache, point npm at a writable cache and retry:

```bash
export npm_config_cache="${TMPDIR:-/tmp}/npmcache-phantom" && mkdir -p "$npm_config_cache"
npx --yes @redocly/cli@latest bundle openapi.yaml -o bundled.json --dereferenced
```

If Node/npx is unavailable, install Redocly via another route the environment allows, or ask
the user. Do not hand-roll external-ref resolution unless bundling is truly impossible.

**3b. Inline leftover example `$ref`s.** Redocly `--dereferenced` inlines schemas and
responses but leaves `$ref`s that sit inside `example` / `examples.*.value` fields as
literal data. phantom-api does **not** resolve refs inside example values, so inline them
with the `inline_example_refs.py` helper that ships in this skill's folder (it tolerates
trailing commas and resolves refs against the source tree by relative path or basename).
Invoke it by its path inside this skill directory:

```bash
python "$SKILL_DIR/inline_example_refs.py" bundled.json --base . -o mock.json
```

(`$SKILL_DIR` is the folder containing this `SKILL.md`.) Expect output like
`Inlined N ref(s); 0 unresolved; K repaired.` If any refs are
**unresolved**, list them for the user — those endpoints will fall back to generated data.

### 4. Validate

```bash
phantom-api validate mock.json --type openapi
```

This prints the detected format and route count. If it errors with "too large", the spec
exceeds phantom-api's 64 MB cap — confirm the bundle isn't accidentally duplicated (bad
inlining of a recursive structure) before proceeding.

### 5. Serve

**Locally:**

```bash
phantom-api serve mock.json --type openapi --port 3000 --seed 42
```

**In Docker** (`fazorboy/phantom-api`; binds `0.0.0.0:3000` inside the container):

```bash
docker run --rm -p 3000:3000 -v "$PWD/mock.json:/spec/mock.json:ro" \
  fazorboy/phantom-api serve /spec/mock.json --type openapi --seed 42
```

Notes:
- `--seed <n>` makes generated (non-example) data deterministic — use it for repeatable tests.
- Locally the server binds `127.0.0.1` by default; pass `--host 0.0.0.0` (or set
  `PHANTOM_API_HOST`) only to expose it beyond localhost. The Docker image already does this.
- When running the server in a terminal that blocks port binds, run it unsandboxed.

---

## Workflow D: record a live API, then replay it

Use this when there is **no spec** but a reachable real API exists. phantom-api proxies the
upstream, saves every response, then serves those saved responses offline.

### 1. Record

Start the recording proxy pointed at the upstream base URL, then drive traffic through it
(point your app/tests at `http://127.0.0.1:3000`, or curl the paths you need). Every response
is written to the output directory.

```bash
phantom-api record https://api.example.com --output recordings/ --port 3000
# in another shell, exercise the endpoints you want captured:
curl -s http://127.0.0.1:3000/api/whoami
curl -s http://127.0.0.1:3000/api/things
# Ctrl+C the recorder when done.
```

Docker form (needs outbound network to the upstream and a writable volume for recordings):

```bash
docker run --rm -p 3000:3000 -v "$PWD/recordings:/spec/recordings" \
  fazorboy/phantom-api record https://api.example.com --output /spec/recordings
```

Only capture non-sensitive data; recordings are written verbatim to disk.

### 2. Replay (offline mock)

```bash
phantom-api replay recordings/ --port 3000 --seed 42
```

Docker form:

```bash
docker run --rm -p 3000:3000 -v "$PWD/recordings:/spec/recordings:ro" \
  fazorboy/phantom-api replay /spec/recordings
```

Replayed routes are derived from what you captured, so record every path/method you need a
mock for. `/__admin/routes` lists what the replay server ended up serving.

---

## Test a few endpoints (any workflow)

Discover real paths first (guessing wastes time — e.g. Morpheus exposes clouds as
`/api/zones`, not `/api/clouds`):

```bash
phantom-api routes mock.json --type openapi | grep -i "<keyword>"   # spec-based
curl -s http://127.0.0.1:3000/__admin/routes                        # any running server
```

Then exercise them and confirm they match the source data:

```bash
curl -s http://127.0.0.1:3000/<path> | python3 -m json.tool | head
curl -s http://127.0.0.1:3000/__admin/stats     # request counts
```

Per-request controls for testing:
- Force an error: `curl "http://127.0.0.1:3000/<path>?__status=503"`
- Add latency: `curl -H "X-Mock-Delay: 300" http://127.0.0.1:3000/<path>`

## Report back

Summarize: source (repo+branch / URL / local / recording), how it was prepared (bundled,
inlined, recorded), routes served, any unresolved example refs, the run command, and 2–3
verified GET endpoints with a snippet of their responses.

## Known gotchas

- **Examples come back as `{"$ref": "..."}`** → step 3b was skipped or a ref was unresolved.
- **npm `EPERM`** → set `npm_config_cache` to a writable dir (step 3a).
- **Non-strict JSON examples** (trailing commas) → the inliner repairs them automatically.
- **Wrong endpoint 404s** → the guessed path doesn't exist; list real routes with
  `phantom-api routes` or `GET /__admin/routes`.
- **Replay serves nothing / 404** → that path/method was never recorded; re-record it.
- **Port bind "operation not permitted"** → run the server unsandboxed.
