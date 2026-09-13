---
name: mock-server
description: 'Create a running mock of any API with phantom-api, by whichever route fits what the user has: an OpenAPI/Swagger spec or Postman collection, a SOAP/XML or JSON-RPC service with no spec, a live system to record, hand-written example responses, or a stateful simulation. USE WHEN the user wants a mock/fake/stub server, a stand-in for a dependency they cannot reach, an offline replica of a live API, or asks to "mock <service>". Handles spec bundling, recording, sanitising captured traffic, deriving config and object graphs, serving locally or in Docker, and verifying the result.'
---

# Mock any API

Stand up a working mock with **phantom-api**, then verify it actually answers.

phantom-api is a published tool; do **not** assume its source is checked out.

- **PyPI:** <https://pypi.org/project/phantomapi-server/>
- **Docker Hub:** `fazorboy/phantom-api`

```bash
pip install 'phantomapi-server[mock]'   # the [mock] extra adds TLS for protocol mocks
phantom-api --version
```

---

## Step 1 — work out which route to take

The protocol barely matters. What decides the work is **where the answers come
from**. Ask only what you cannot infer:

| If the user has | Route | Command |
|---|---|---|
| An OpenAPI/Swagger spec or Postman collection | **A — spec** | `phantom-api serve` |
| A JSON file of example data | **A — spec** | `phantom-api serve data.json` |
| Access to the real system | **C — record** | `mock record` → `sanitize` → `derive` → `serve` |
| A few responses, or willingness to write them | **B — examples** | `mock init template` |
| A stateful client: sessions, paging, change feeds | **D — model** | `mock derive --model` |
| A reason to use real answers under their control | **E — forward** | `forward` responder |

**Most real mocks combine routes.** Rules match in order, so serve the tricky
operations from a model, the long tail from a recording, and forward the rest.

### Questions worth asking (and only these)

1. **What are we mocking, and do you have a spec for it?** — decides A vs the rest.
2. **Can you reach the real thing, and are you allowed to send traffic to it?** —
   decides C. Never assume permission.
3. **Does the client log in, page through results, or poll for changes?** — if
   yes, replay alone will not be enough; plan for D.
4. **Local or Docker? Which port?** — default local, `8443` for `mock`, `3000`
   for `serve`.

Everything else can be discovered from the spec or the traffic.

---

## Step 0 (shortcut) — the two worked examples

If the user asks for **Morpheus** or **vCenter**, both ship complete. Clone the
repo and serve them directly; do not rebuild them from scratch.

```bash
git clone https://github.com/<owner>/phantom-api && cd phantom-api

# Morpheus, REST — from a spec (631 paths) or from a recording (76 endpoints)
phantom-api serve examples/morpheus/morpheus-mock.json
phantom-api mock serve examples/morpheus/phantom.yaml

# vCenter, SOAP — recorded corpus plus a stateful model
phantom-api mock serve examples/vcenter/phantom.yaml
```

Use these as the reference when building a new mock: Morpheus is the REST
example, vCenter the SOAP-and-stateful one.

---

## Route A — from a specification

Works for OpenAPI 3.x, Swagger 2.0, Postman v2.1, or a plain JSON file (which
becomes CRUD endpoints).

```bash
phantom-api validate spec.yaml          # check it parses first
phantom-api routes spec.yaml            # see what you will get
phantom-api serve spec.yaml --port 3000 --watch
```

Useful flags: `--delay 200ms`, `--seed 42` (deterministic fake data),
`--host 0.0.0.0` (opt in before exposing beyond localhost).

### Multi-file specs

If `$ref`s point at other files, bundle first. Check the repo for its own build
step (`Rakefile`, `package.json`, `make bundle`) and prefer it; otherwise:

```bash
npx @redocly/cli bundle openapi.yaml --dereferenced -o bundled.json
python3 inline_example_refs.py bundled.json bundled.inlined.json
phantom-api serve bundled.inlined.json
```

`inline_example_refs.py` ships next to this SKILL.md. Redocly leaves `$ref`s
*inside* example values; that script inlines them so examples serve verbatim
instead of as a dangling reference.

---

## Route B — from example responses

For a handful of operations, with no spec and no access to the real system.

```bash
phantom-api mock init template --out phantom.yaml
```

```yaml
mock:
  name: billing
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }
  protocols:
    - pack: soap            # or openapi, jsonrpc, raw
      paths: ["/**"]
  responders:
    - match: "*"
      responder: template
      operations:
        GetAccount:
          dispatch: match   # first | sequence | random | match
          responses:
            - when: "params.accountId == '0'"
              fault: { kind: not-found, message: "Unknown account" }
            - body: |
                <accountId>{{ params.accountId }}</accountId>
                <balance>{{ fake.pyint(100, 99999) }}</balance>
```

`dispatch: sequence` returns one response per call — how you mock a job that
answers `pending`, `pending`, then `complete`.

If the user already has saved responses on disk, do not write templates: drop
them into a corpus directory with a `manifest.json` and use Route C from
`derive` onward.

---

## Route C — from a recording

The highest-fidelity route, and the one to prefer whenever the real system is
reachable. Four commands.

### C1. Record

**Confirm authorisation before sending a single request.** The acknowledgement
flag is not a formality.

Two modes. Use `--endpoints` when you have credentials but no client to drive
traffic; use the proxy when you have a real client to point at the mock.

```bash
# By calling a list of paths
phantom-api mock record https://real.example.com \
  --endpoints endpoints.txt --out raw --follow 3 \
  --header "Authorization: Bearer $TOKEN" \
  --i-understand-this-hits-production

# By proxying a real client
phantom-api mock record https://real.example.com --out raw \
  --i-understand-this-hits-production
```

`endpoints.txt` is one path per line, `# comments` allowed, optional leading
method:

```
/api/servers
/api/instances
GET /api/instance-types?max=5&offset=0
```

`--follow N` also fetches N members of every collection it records. **Do this.**
A list response and a detail response have different shapes, and without the
detail the corpus cannot answer what clients most often ask.

Include a deliberate failure (a bad id, a missing path) so the mock can exercise
the client's error handling.

### C2. Sanitise — never skip this

Recorded traffic contains host names, addresses, serials, licence keys, and
whatever people typed into free-form fields. A real capture during development of
this tool yielded an administrator password sitting in a `userData` field.

```bash
phantom-api mock sanitize raw --out corpus \
  --terms-file terms.txt \
  --mapping .capture/mapping.json
```

- Replacements are **stable and structure-preserving**, so cross-references
  still resolve and the corpus still replays.
- The command **exits non-zero** if anything is unpaired, no longer parses, or
  still contains a forbidden term. Treat a failure as blocking.
- `--mapping` is written outside the corpus with mode `0600`. **Never commit it.**
- `terms.txt` lists site and supplier tokens. Prefer the longest distinctive
  token: short generic ones match things they were never meant to.

Extend the rules per domain with `--rules rules.yaml`:

```yaml
elements: { tenantName: tenant }
json_fields: [account_name, owner_email]
freeform_fields: [provisioningScript]
terms: [acme]
```

### C3. Derive the config

Do not hand-write it. Everything here is read off the traffic:

```bash
phantom-api mock derive corpus --name myservice --out phantom.yaml
```

It detects protocols, collapses concrete paths into patterns, and works out how
each path carries its session. Review it, then:

```bash
phantom-api mock validate phantom.yaml
phantom-api mock serve phantom.yaml
```

**For many services this is where you stop.**

### C4. Check it has not gone stale

A stale corpus passes tests the real system would fail.

```bash
phantom-api mock drift corpus --target https://real.example.com \
  --header "Authorization: Bearer $TOKEN" --i-have-permission
```

Compares response *shape*, so timestamps and ids are not false positives. Exits
non-zero on divergence, so it can run on a schedule.

---

## Route D — from a model

Needed when the client holds a session, pages through results, or expects an
action to affect later reads. A corpus cannot change; a model can.

```bash
phantom-api mock derive corpus --name myservice --out phantom.yaml --model .
```

This recovers the object graph from the recording — entities, properties, and
the references between them — into `topology/inventory.json` and a scenario
describing its shape. Edit the scenario's counts to generate a larger world with
the same proportions.

Only relationships that **resolve** to an object seen elsewhere are recorded, so
a derived graph never invents an edge.

Going beyond that — operations that mutate state and emit events — means writing
a domain pack. Point the user at `docs/building-mocks.md` in the phantom-api
repo; the vCenter pack is the worked example.

---

## Route E — from the real system, live

Proxy upstream but under your control: inject latency and failures, record as
you go, or fill gaps in a growing corpus.

```yaml
responders:
  - match: "*"
    responder: corpus
    corpus: ./corpus
    on_miss: forward        # serve what is recorded, forward the rest
```

Same authorisation rule as recording.

---

## Step 3 — always verify before handing it over

Never report success without checking the mock answers.

```bash
phantom-api mock inspect ./corpus        # what can this corpus answer?
phantom-api mock log --url https://127.0.0.1:8443    # what was actually asked for
phantom-api mock verify Login --at-least 1           # assert it; non-zero if not
```

```bash
curl -sk https://127.0.0.1:8443/api/servers | head -c 200
```

For a spec-driven mock, `phantom-api routes spec.yaml | head` then curl one of
the listed routes.

---

## Docker

```bash
docker run --rm -p 3000:3000 -v "$PWD:/spec" fazorboy/phantom-api serve /spec/spec.yaml
docker run --rm -p 8443:8443 -v "$PWD:/spec" fazorboy/phantom-api \
  mock serve /spec/phantom.yaml --host 0.0.0.0
```

Mount read-only (`:ro`) unless recording. The mock binds `127.0.0.1` by default;
`--host 0.0.0.0` is required inside a container and is an explicit opt-in.

---

## Rules that are not negotiable

1. **Confirm authorisation before recording or drifting** against any system you
   do not own. Both commands require an explicit flag — do not pass it on the
   user's behalf without asking.
2. **Never commit an unsanitised capture**, and never commit the reversal
   mapping. Add `raw/` and `.capture/` to `.gitignore`.
3. **A failed `sanitize` is blocking.** It means the corpus is unsafe or
   unusable; do not work around it.
4. **Never put credentials in a file or a command that gets committed.** Read
   them from the environment (`read -rs`) and pass via `--header`.
5. **Do not claim a mock works without exercising it.** Serve it and make a
   request.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `no protocol pack matched` (400) | The request path is outside every `paths:` pattern. Check `mock validate`. |
| Corpus miss on a request you recorded | The query string is part of the identity; confirm it matches. Try `on_miss: forward` while building. |
| Empty or `null` fields everywhere | Serving a list response where a detail was needed. Re-record with `--follow`. |
| Client hangs after connecting | Often a handshake called twice with different headers. Check `mock log`. |
| TLS rejected | Most clients accept the self-signed certificate; if not, pass `--tls off` behind a proxy. |
| `sanitize` reports "in a field name" | A term is a JSON key. Renaming it would change the shape — either accept the term or rename the field deliberately via `elements`. |
