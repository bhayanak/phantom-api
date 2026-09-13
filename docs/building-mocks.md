# Build a mock server for anything

A step-by-step guide. Follow it top to bottom and you will have a working mock
of your own system.

It is organised by **where your answers come from**, because that — not the
protocol — is what actually decides the work. Serving REST from a spec and
serving SOAP from a spec are the same job; serving REST from a spec and serving
REST from a recording are not.

| Route | You have | Effort | Works for |
|---|---|---|---|
| [A — from a specification](#route-a--from-a-specification) | OpenAPI, Swagger, or a Postman collection | minutes | REST/JSON |
| [B — from example responses](#route-b--from-example-responses) | A few saved responses, or none | ~an hour | any protocol |
| [C — from a recording](#route-c--from-a-recording) | Access to the real system | ~an hour | any protocol |
| [D — from a model](#route-d--from-a-model) | Knowledge of the domain | a day or two | any protocol |
| [E — from the real system, live](#route-e--from-the-real-system-live) | Access, and a reason not to fake it | minutes | any protocol |

**Most real mocks combine several.** Rules match in order, so you can answer the
tricky operations from a model, the long tail from a recording, and forward
anything you have not covered yet. That is the normal end state, not a
compromise.

Two complete examples ship with the repository and are referenced throughout:
[morpheus](../examples/morpheus/) (REST, from a spec) and
[vcenter](../examples/vcenter/) (SOAP, recorded and simulated). See
[examples/README.md](../examples/README.md) for how they differ.

---

## Contents

1. [Pick your route](#1-pick-your-route)
2. [Install](#2-install)
3. [Route A — from a specification](#route-a--from-a-specification)
4. [Route B — from example responses](#route-b--from-example-responses)
5. [Route C — from a recording](#route-c--from-a-recording)
6. [Route D — from a model](#route-d--from-a-model)
7. [Route E — from the real system, live](#route-e--from-the-real-system-live)
8. [The configuration file](#5-the-configuration-file)
9. [Protocol packs](#6-protocol-packs)
10. [The four responders](#7-the-four-responders)
11. [Recording and sanitising](#8-recording-and-sanitising)
12. [Building a domain pack](#9-building-a-domain-pack)
13. [The control plane](#10-the-control-plane)
14. [Chaos and fault injection](#11-chaos-and-fault-injection)
15. [Testing against your mock](#12-testing-against-your-mock)
16. [Running in CI and Docker](#13-running-in-ci-and-docker)
17. [Troubleshooting](#14-troubleshooting)

---

## 1. Pick your route

Answer these in order and stop at the first "yes".

| Question | Route | Why |
|---|---|---|
| Is there an OpenAPI / Swagger spec, or a Postman collection? | [A](#route-a--from-a-specification) | The spec already describes every response |
| Can you reach the real system to record it? | [C](#route-c--from-a-recording) | Recorded answers are correct by construction |
| Do you only need plausible answers for a handful of operations? | [B](#route-b--from-example-responses) | Writing five responses beats recording a thousand |
| Does the client hold a session, page through results, or poll for changes? | [D](#route-d--from-a-model) | Only a model can *change* |
| Do you need the real answers, but with failures injected? | [E](#route-e--from-the-real-system-live) | Forwarding keeps the answers honest |

### A note on protocols

Routes B through E do not care what the wire format is. A *protocol pack* turns
bytes into an operation name and a set of parameters, and turns a result back
into bytes; everything above that point is shared. Packs ship for `soap`,
`openapi`, `jsonrpc` and `raw`, and
[adding one](#adding-a-protocol-pack) is about a hundred lines.

So "how do I mock gRPC-web / XML-RPC / some in-house binary protocol?" has the
same answer as SOAP: write the pack once, then pick a route.

### What if I only have a WSDL?

Serve it as Route B or C. A WSDL describes *types*, not *answers* — it tells you
an operation returns a `VirtualMachine`, not what a realistic one contains. One
recorded response teaches the mock more than the whole schema, which is why
there is no "serve a WSDL" command.

---

## 2. Install

```bash
pip install phantomapi-server          # spec-driven mocks
pip install 'phantomapi-server[mock]'  # adds TLS for protocol mocks
```

Check what is available:

```bash
phantom-api --version
phantom-api mock list-packs
```

```
   Available packs
┏━━━━━━━━━━┳━━━━━━━━━┓
┃ Kind     ┃ Name    ┃
┡━━━━━━━━━━╇━━━━━━━━━┩
│ protocol │ jsonrpc │
│ protocol │ openapi │
│ protocol │ soap    │
│ domain   │ vcenter │
└──────────┴─────────┘
```

---

## Route A — from a specification

**Use when:** an OpenAPI/Swagger document or Postman collection exists.
**Worked example:** [morpheus](../examples/morpheus/) — 9 MB, 631 paths, 1017
operations.

**Goal:** mock a 1000-endpoint cloud management API so a client team can build
against it today.

**Source:** [examples/morpheus/morpheus-mock.json](../examples/morpheus/morpheus-mock.json) — an
OpenAPI 3.1 document (9 MB, 631 paths, 1017 operations).

### Step 1 — check the spec parses

```bash
phantom-api validate examples/morpheus/morpheus-mock.json
```

```
OK morpheus-mock.json: openapi, 1017 route(s).
```

If this fails, the spec is the problem, not the mock. Fix it there first.

### Step 2 — see what you will get

```bash
phantom-api routes examples/morpheus/morpheus-mock.json | head -20
```

```
                             Morpheus API (openapi)
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Method ┃ Path                        ┃ Status ┃ Summary                      ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ GET    │ /api/activity               │    200 │ Retrieves Activity           │
│ GET    │ /api/appliance-settings     │    200 │ Get Appliance Settings       │
│ PUT    │ /api/appliance-settings     │    200 │ Update Appliance Settings    │
```

### Step 3 — serve it

```bash
phantom-api serve examples/morpheus/morpheus-mock.json --port 3000 --seed 42
```

`--seed` makes the generated data identical on every run, which is what you want
in a test suite. Without it every restart invents new values.

### Step 4 — use it

```bash
curl -s localhost:3000/api/instances | head -c 200
curl -s localhost:3000/__admin/routes | head -c 200
curl -s localhost:3000/__admin/stats
```

### Step 5 — make it behave badly on purpose

```bash
# 200 ms on every request
phantom-api serve examples/morpheus/morpheus-mock.json --delay 200ms

# force a status per request, without restarting
curl -s -H 'X-Mock-Status: 503' localhost:3000/api/instances
curl -s 'localhost:3000/api/instances?__status=404'
```

### Step 6 — keep it in sync

```bash
phantom-api serve examples/morpheus/morpheus-mock.json --watch
```

The spec is re-read on change and the routes rebuilt, so you can edit the
contract and the mock follows.

**That is the whole of Route A.** No configuration file, no code.

> **Postman collections work the same way** — pass the collection file to
> `phantom-api serve` and saved example responses are served directly.

---

## Route B — from example responses

**Use when:** you need a handful of operations to answer plausibly, and either
have a few saved responses or can write them.
**Works for:** any protocol.

This is the fastest route that does not need the real system. Scaffold a config:

```bash
phantom-api mock init template --out phantom.yaml
```

Then describe what each operation should return. Responses are templates, so
they can vary per call without you writing code:

```yaml
mock:
  name: billing
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }
  protocols:
    - pack: soap          # or openapi, jsonrpc, raw
      paths: ["/**"]
  responders:
    - match: "*"
      responder: template
      operations:
        GetAccount:
          dispatch: match       # first | sequence | random | match
          responses:
            - when: "params.accountId == '0'"
              fault: { kind: not-found, message: "Unknown account" }
            - body: |
                <accountId>{{ params.accountId }}</accountId>
                <balance>{{ fake.pyint(100, 99999) }}</balance>
                <asOf>{{ now.isoformat() }}</asOf>
```

`dispatch: sequence` walks the list one response per call, which is how you mock
a long-running job that answers `pending`, `pending`, then `complete`.

Expressions are evaluated against an allowlist of AST nodes — no imports, no
dunder access, no `eval`. See
[the template responder](#template--declarative-canned-responses) for the full
context available.

**If you have saved responses on disk**, you do not need templates at all: drop
them into a corpus directory with a `manifest.json` and use Route C from
[step 4](#step-4--replay-and-you-already-have-a-working-mock) onward.

---

## Route C — from a recording

**Use when:** you can reach the real system.
**Works for:** any protocol.
**Worked example:** [vcenter](../examples/vcenter/) — SOAP over HTTPS, no spec,
stateful client.

Recording is the highest-fidelity route, because the answers are the real
system's own. The workflow is four commands:

```bash
phantom-api mock record --target https://real.example.com --out raw --i-have-permission
phantom-api mock sanitize raw --out corpus --terms-file terms.txt
phantom-api mock derive corpus --out phantom.yaml
phantom-api mock serve phantom.yaml
```

The worked example below is a SOAP system, but nothing in those four commands is
SOAP-specific.

**Goal:** mock a virtualisation management endpoint so an inventory collector
can be developed without a datacenter.

**Why it is harder:**

- SOAP over HTTPS, with no OpenAPI description anywhere.
- The client logs in and carries a session cookie.
- It does not request objects; it hands the server a *traversal program* —
  "start at the root, follow these edges, and give me these property paths on
  everything you find".
- It then polls a server-side event collector for changes, and reconciles those
  changes against what it read earlier.

None of that can be expressed as a spec. Here is how it was actually built.

### Step 1 — record the real system

Everything you need to know is on the wire. Capture it first; design later.

There are two ways to record. Proxying captures whatever a real client does, so
it needs a real client. Calling a list of endpoints needs only credentials:

```bash
phantom-api mock record https://real.example.com \
  --endpoints endpoints.txt --out raw --follow 3 \
  --header "Authorization: Bearer $TOKEN" \
  --i-understand-this-hits-production
```

`endpoints.txt` is one path per line, `#` comments allowed, with an optional
leading method. **Use `--follow`**: a list response and a detail response for the
same object have different shapes, and without the detail the corpus cannot
answer what clients most often ask. Include a deliberate failure too, so the mock
can exercise the client's error handling.

```bash
export VC_HOST=vcenter.example.com
export VC_USER='administrator@vsphere.local'
read -rs VC_PASSWORD && export VC_PASSWORD   # never put a password in a file

python3 examples/vcenter/tools/capture_vcenter.py
```

The bundled capture script is standard library only, so it runs on any jump host
that can reach the target. It records 66 exchanges across four protocol surfaces
and writes a request, a response and a metadata sidecar for each.

> If you cannot reach the real system, skip to
> [building a domain pack](#9-building-a-domain-pack) and work from
> documentation. It is slower and you will get details wrong — every finding in
> the table below came from the recording, not from the docs.

### Step 2 — read what the recording tells you

This is the step people skip, and it is the one that matters. Each of these was
a defect the mock would otherwise have shipped with:

| Finding | Consequence |
|---|---|
| `RetrieveServiceContent` is called **twice** — once with a default `SOAPAction`, then again after the client re-pins it from `about.apiVersion` | Answer only once and the client appears to hang |
| Sibling endpoints carry the **same session two different ways**: an HTTP cookie on `/sdk`, a `<vcSessionCookie>` SOAP header on `/pbm/sdk` | Hard-coding "session = cookie" breaks a third of the surface |
| An unauthenticated `RetrieveProperties` returns **HTTP 200 with a per-property `missingSet`**; an unauthenticated `CurrentTime` returns **HTTP 500 with a SOAP fault** | One condition, two shapes. Only a capture reveals this |
| Faults are **HTTP 500** with the document in the error body | Returning 200 silently disables client error handling |
| `xsi:type` is sometimes namespace-prefixed (`vim25:NotAuthenticated`) | The client resolves polymorphic fields from it; get it wrong and data vanishes |
| An empty result is an **empty response element**, not an omitted one | `<RetrievePropertiesResponse/>` is a valid, meaningful answer |
| Folders outnumber every other object type (48 of 147) | Traversal correctness matters far more than volume |

### Step 3 — sanitise before anything else

Recorded traffic is full of host names, addresses, serials, licence keys and
free-form config. None of it belongs in a repository, and none of it can be
removed by hand at any useful scale.

```bash
phantom-api mock sanitize raw --out corpus \
  --terms-file terms.txt \
  --mapping .capture/mapping.json
```

Every replacement is **stable** — the same input always maps to the same output,
everywhere — so cross-references between responses still resolve and replay
still works. Replacements are also structure-preserving: an address stays an
address, a UUID stays a UUID. A corpus full of `REDACTED` cannot be replayed.

The command exits non-zero if anything is unpaired, no longer parses, or still
contains a forbidden term, which is what makes it safe to put in CI. See
[section 8](#8-recording-and-sanitising) for what it covers and how to extend
the rules for your domain.

### Step 4 — replay, and you already have a working mock

```bash
phantom-api mock serve --corpus examples/vcenter/corpus --port 8443 --tls self-signed
```

That is a wire-accurate mock with no modelling at all. It only answers requests
that were recorded, which is enough for regression tests immediately.

Check what it can answer:

```bash
phantom-api mock inspect examples/vcenter/corpus
```

```
                       corpus: 69 exchanges
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Operation                       ┃ Recorded ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ RetrieveProperties              │       23 │
│ ReadNextEvents                  │        3 │
│ Login                           │        2 │
```

### Step 5 — let the recording write your config

You now have a working mock but no config file. Rather than write one by reading
the corpus yourself, derive it:

```bash
phantom-api mock derive corpus --name vcenter --out phantom.yaml
```

```
          What the recording covers
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ Protocol ┃ Exchanges ┃ Operations ┃ Paths ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│ soap     │        51 │         23 │     4 │
│ openapi  │        18 │         18 │    18 │
└──────────┴───────────┴────────────┴───────┘
```

Everything it writes was observed on the wire, not guessed. On this corpus it
independently works out the finding from
[step 2](#step-2--read-what-the-recording-tells-you) — that sibling endpoints
carry the session two different ways:

```yaml
  protocols:
    - pack: soap
      paths: ["/", "/pbm/sdk", "/sdk", "/sms/sdk"]
      session:
        "/sdk":     { transport: cookie, name: session }
        "/pbm/sdk": { transport: soap-header, name: vcSessionCookie }
        "/sms/sdk": { transport: soap-header, name: vcSessionCookie }
    - pack: openapi
      paths: ["/api/**", "/rest/**"]
```

Concrete paths are collapsed into patterns, so the mock keeps working when a
client asks for an object you did not happen to record. Review it, edit it, and
serve it:

```bash
phantom-api mock validate phantom.yaml
phantom-api mock serve phantom.yaml
```

**For many systems this is where you stop.** Continue only if your client needs
the mock to *change* — which is Route D.

---

## Route D — from a model

**Use when:** the client holds a session, pages through results, polls for
changes, or expects an action to affect later reads.
**Works for:** any protocol.

A corpus cannot do any of that: it is a fixed set of answers, and nothing in it
changes when a client asks for something to change. A model can.

The remaining steps build one for the vcenter example. The pieces are generic —
an object graph, projections that turn objects into protocol responses, and
mutations that change the graph and emit events — and
[section 9](#9-building-a-domain-pack) documents them on their own terms.

### Step 6 — recover the object graph

A corpus is fixed. To generate an inventory of *any* size, and to answer requests
that were never recorded, you need a model — and a model needs objects.

Those are already in the recording, so they are read out of it:

```bash
phantom-api mock derive corpus --name vcenter --out phantom.yaml --model .
```

This writes `topology/inventory.json` (entities, properties and the references
between them, in the form the model layer consumes) and a scenario describing the
shape of that graph. Edit the scenario's counts to generate a bigger world with
the same proportions.

Nothing in it is protocol- or product-specific. On the vCenter SOAP corpus it
recovers 153 objects across 14 kinds; on a REST corpus from a different product
it recovers 883 objects across 45 kinds, with counts matching the live system.

An edge is recorded **only when the reference resolves** to an object seen
elsewhere in the recording. References that point outside it are counted and
reported under `unresolved:`, which is usually a collection you forgot to record.

### Step 6b — the product-specific half

A derived graph gives you objects. Turning them into *answers* — and letting
operations change them — is what a domain pack does.

```bash
python3 examples/vcenter/tools/derive_scenario.py --corpus corpus --out .
```

```
inventory      147 objects {'Datacenter': 8, 'ClusterComputeResource': 8, ...}
type registry  28 elements
perf counters  732
roles          48
events         3000 instances, 9 distinct types
scenario       scenario/vcenter-scenario.yaml
```

The generated [scenario file](../examples/vcenter/scenario/vcenter-scenario.yaml)
describes the world in a form you can edit:

```yaml
service:
  version: "8.0.3"
  api_version: "8.0.3.0"
  instance_uuid: 11111111-2222-4333-8444-555555555555
  https_port: 443           # the client re-reads this and rebuilds its URL

auth:
  mode: accept-any          # development default; warns on start
  password: ${MOCK_VC_PASSWORD}

topology:
  datacenters:
    - name: DC-01
      clusters:
        - { name: Cluster-01, hosts: 8, vms_per_host: 25 }
  datastores:
    - { name: DS-01, type: VMFS, capacity_gb: 2048, free_gb: 1024 }

change_engine:
  mode: timeline
  timeline:
    - { at: 60s,  event: VmPoweredOffEvent, target: "vm:VM-0001" }
    - { at: 120s, event: VmMigratedEvent,   target: "vm:VM-0002", to: "host:esx-02" }
```

Want 2000 VMs instead of 200? Change `hosts` and `vms_per_host`.

### Step 7 — serve the model and the corpus together

[examples/vcenter/phantom.yaml](../examples/vcenter/phantom.yaml) routes each operation
to whichever responder suits it:

```yaml
mock:
  name: vcenter
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }

  protocols:
    - pack: soap
      paths: ["/sdk", "/pbm/sdk", "/sms/sdk"]
      session:
        "/sdk":     { transport: cookie,      name: vmware_soap_session }
        "/pbm/sdk": { transport: soap-header, name: vcSessionCookie }
    - pack: openapi
      paths: ["/rest/**", "/api/**"]

  model:
    pack: vcenter
    scenario: scenario/vcenter-scenario.yaml
    seed: 20260913

  responders:
    # Operations that must stay self-consistent come from the model.
    - match:
        operation: [RetrieveServiceContent, Login, Logout, "Retrieve*", "QueryPerf*"]
      responder: model
    # Everything else that was recorded, replayed verbatim.
    - match: "*"
      responder: corpus
      corpus: corpus
      on_miss: fault
```

```bash
phantom-api mock validate examples/vcenter/phantom.yaml
phantom-api mock serve   examples/vcenter/phantom.yaml
```

```
OK phantom.yaml: vcenter
  - model(108 entities, 20 projections)
  - corpus(corpus, 69 exchanges, on_miss=fault)
```

### Step 8 — drive the simulation

```bash
# What does the world look like?
curl -sk https://localhost:8443/__phantom/status

# Power off a VM, right now
curl -sk -X POST https://localhost:8443/__phantom/events \
  -H 'Content-Type: application/json' \
  -d '{"event":"VmPoweredOffEvent","target":"vm-23"}'

# The model changed before the event was emitted -- always that order
curl -sk 'https://localhost:8443/__phantom/inventory?kind=VirtualMachine'
```

### Step 9 — point your client at it

The client builds `https://{host}/sdk` with **no port**, so either publish the
mock on 443 or front it:

```bash
sudo phantom-api mock serve examples/vcenter/phantom.yaml --port 443
# or
docker run -p 443:8443 -v "$PWD/vcenter-mock:/spec:ro" \
  phantom-api mock serve /spec/phantom.yaml --host 0.0.0.0
```

TLS is required but unverified by most such clients, so the generated
self-signed certificate is enough — no CA setup.

---

## Route E — from the real system, live

**Use when:** you want the real system's answers, but under your control — to
inject latency and failures, to record while you work, or to fill the gaps in a
corpus that does not cover everything yet.
**Works for:** any protocol.

The `forward` responder proxies upstream and can record as it goes:

```yaml
  responders:
    - match: "*"
      responder: forward
      target: https://real.example.com
      record: ./corpus          # optional: build a corpus while you work
```

This is also the most useful `on_miss` setting while a corpus is still growing:
serve what you have recorded, forward anything else, and capture it for next
time.

```yaml
  responders:
    - match: "*"
      responder: corpus
      corpus: ./corpus
      on_miss: forward
```

Combine it with [chaos](#11-chaos-and-fault-injection) to test how your client
handles a real backend that is slow or failing — answers stay honest, only the
timing and the failures are yours.

> Forwarding sends traffic to a real system. Treat it exactly like recording:
> only against systems you are authorised to use.

### Keeping a corpus honest

A stale corpus is worse than no corpus, because it passes tests the real system
would fail. Replay yours against the real endpoint and diff the results:

```bash
phantom-api mock drift corpus --target https://real.example.com --i-have-permission
```

```
66 checked, 65 match, 1 diverged, 3 skipped
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Operation       ┃ Kind   ┃ Detail                  ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ RetrieveOptions │ shape  │ gone: perfInterval      │
└─────────────────┴────────┴─────────────────────────┘
```

It compares response *shape*, not bytes, so timestamps, ids and tokens that
legitimately change on every call are not reported as drift. It exits non-zero
when anything diverged, so it can run on a schedule against a staging system.

---

## 5. The configuration file

One file describes the whole mock. Rules are evaluated in order and the first
match wins, the same as nginx or Envoy.

```yaml
mock:
  name: my-service

  listen:
    host: 127.0.0.1        # loopback unless you mean otherwise
    port: 8443
    tls: self-signed       # self-signed | off
    tls_cert: ./cert.pem   # optional: bring your own
    tls_key: ./key.pem

  protocols:
    - pack: soap           # soap | jsonrpc | openapi | auto
      paths: ["/sdk", "/pbm/**"]
      session:
        "/sdk":     { transport: cookie,      name: my_session }
        "/pbm/**":  { transport: soap-header, name: sessionCookie }
    - pack: openapi
      paths: ["/rest/**"]
      spec: ./openapi.yaml   # optional: collapses /pets/42 onto GET /pets/{id}

  model:
    pack: vcenter
    scenario: ./scenario.yaml
    seed: 1234

  responders:
    - match: { operation: ["Login", "Retrieve*"] }
      responder: model
    - match: { protocol: openapi }
      responder: template
      operations: { ... }
    - match: "*"
      responder: corpus
      corpus: ./corpus
      on_miss: forward
    - match: "*"
      responder: forward
      target: https://real.example.com
      record_to: ./corpus

  chaos:
    latency_ms: 0
    latency_jitter_ms: 0
    fault_rate: 0.0
    drop_rate: 0.0

  control_plane:
    enabled: true
    bind: 127.0.0.1
```

**Matching.** A rule matches on any combination of `operation` (glob, or `re:`
for a regex), `protocol`, and `path`. `match: "*"` matches everything.

**Secrets.** `${VAR}` is read from the environment. An unset variable is a
startup error — better than a mock that silently authenticates against the
literal `${...}`.

**Paths** are resolved relative to the configuration file, so a whole mock
directory can be moved or mounted somewhere else.

---

## 6. Protocol packs

A pack is the only thing that knows a wire format. It answers three questions:
what operation is this, how do I serialise a result, and how does this protocol
express failure.

| Pack | Operation key | Session from |
|---|---|---|
| `soap` | first child of `<Body>` | cookie, header, or SOAP header element |
| `jsonrpc` | the `method` member | header |
| `openapi` | `METHOD /path` | header |
| `auto` | sniffed per request | — |

### The SOAP pack

Handles SOAP 1.1 and 1.2, and gets three things right that matter:

- **Faults are HTTP 500** with the fault document in the body.
- **`xsi:type` is emitted** on polymorphic elements and tolerated on input in
  both bare and namespace-prefixed forms.
- **`None` produces no element**, because an absent element and an empty one
  mean different things.

XML parsing is hardened: `DOCTYPE` and `ENTITY` declarations are refused
outright rather than configured away, with size and depth caps on top.

### Adding a protocol pack

Implement three methods and register it:

```python
from phantom_api.mock.protocols.base import register
from phantom_api.mock.types import MockError, Operation, RawRequest, RawResponse

class MyPack:
    name = "myproto"

    def matches(self, request: RawRequest) -> bool:
        return request.header("content-type") == "application/x-myproto"

    def decode(self, request, session=None) -> Operation:
        return Operation(name=..., protocol=self.name, params=..., request=request)

    def encode(self, result, op) -> RawResponse:
        ...

    def encode_error(self, error: MockError, op) -> RawResponse:
        ...

register(MyPack())
```

Nothing else changes. Every responder, the chaos pipeline, the control plane and
the verification API work with it immediately.

---

## 7. The four responders

Ordered by fidelity against effort.

### `forward` — proxy the real system

```yaml
- match: "*"
  responder: forward
  target: https://real.example.com
  record_to: ./corpus        # optional: this is how a corpus grows
```

### `corpus` — replay recordings

Wire-accurate, no modelling. Only answers what was recorded.

```yaml
- match: "*"
  responder: corpus
  corpus: ./corpus
  on_miss: fault             # fault | synthesize | forward | not-found
```

Matching is progressive: an exact fingerprint of the normalised request, then a
structural match that ignores values, then the operation name alone.
Normalisation strips session ids, timestamps and continuation tokens first —
otherwise nothing would ever match twice.

`on_miss: synthesize` defers to the next rule, which is how a corpus and a model
compose.

### `template` — declarative canned responses

For the long tail that needs a plausible answer and nothing more.

```yaml
- match: "*"
  responder: template
  operations:
    GetAccount:
      dispatch: match        # first | sequence | random | match
      responses:
        - when: "params.accountId == '0'"
          fault: { kind: not-found, message: "Unknown account" }
        - body: |
            <accountId>{{ params.accountId }}</accountId>
            <balance>{{ fake.pyint(100, 99999) }}</balance>
            <asOf>{{ now.isoformat() }}</asOf>
          delay_ms: 50
```

Context available in expressions: `params`, `headers`, `operation`, `session`,
`now`, `fake` (Faker), `random`.

Expressions are **not** `eval`. They are parsed to an AST and every node type
that is not explicitly allowed is refused — no imports, no dunder access, no
file or network reach.

### `model` — a stateful simulation

The one neither SoapUI nor MockServer offers, and the reason this exists.

```yaml
model:
  pack: vcenter
  scenario: ./scenario.yaml
  seed: 1234
responders:
  - match: "*"
    responder: model
```

---

## 8. Recording and sanitising

### Recording

```bash
phantom-api mock record https://real.example.com \
  --out ./corpus --protocol soap \
  --i-understand-this-hits-production
```

The acknowledgement flag is required, and loopback targets are refused. Recording
sends real traffic to a live system; that should be a deliberate act.

### Sanitising

**Recorded traffic is not publishable as-is.**

```bash
phantom-api mock sanitize raw --out corpus \
  --terms-file terms.txt \
  --mapping .capture/mapping.json
```

| Rule | Why |
|---|---|
| Stable pseudonyms (same input → same output, everywhere) | Cross-references between responses must still resolve |
| IPv4 → RFC 5737 ranges, MAC → `02:00:5e:*`, UUID → UUIDv5 | Shapes and lengths stay realistic, so replay still works |
| IPv6 rewritten too | EUI-64 addresses embed the real MAC |
| Open key/value bags: **values** replaced, **keys** kept | Keys are a bounded vocabulary consumers match on; values are where disclosure happens |
| Boundaries are non-alphanumeric, not `\b` | `ACME_NS204i` is caught; `hpet0.present` — a real config key — is not |
| Filesystem paths keep the `[prefix]`, lose the tail | The prefix is structural; the folder names are the customer's |
| JSON renames apply in value position only | Object names and field names share a namespace |
| Names shorter than four characters are never renamed | An object called `vm` would rewrite the whole corpus |
| Version fields are masked before the address pass | `8.0.2.1` is a valid dotted quad, and clients negotiate on it |

Four of those exist specifically because their absence caused real corruption:

- `<path>` is **not** treated as a filesystem path — in a traversal spec it names
  an edge to follow, and rewriting it broke every recorded request.
- The IPv6 pattern requires a literal `::` — a looser one matched the colons
  inside `00:14:03` and the `:` in `"memoryMB":8192`.
- JSON renames are value-position only — an object genuinely named `vm` rewrote
  every `"vm":` key and produced invalid JSON.
- Version strings are masked — otherwise `8.0.2.1` became an IP address and
  clients refused to negotiate.

### Extending the rules for your domain

The defaults cover host names, addresses, serials, licence keys and paths. Add
whatever your domain calls things:

```yaml
# rules.yaml
domain: lab.example.com
elements:
  tenantName: tenant        # <tenantName>Acme Ltd</tenantName> -> tenant-0001
  contractId: contract
json_fields: [account_name, owner_email]
terms: [acme, northwind]    # blanked wherever they appear, case-insensitively
structural_files: [Catalog, CounterList]   # <name> here is vocabulary, not a label
```

```bash
phantom-api mock sanitize raw --out corpus --rules rules.yaml
```

### Verification is part of the command

The run fails, with a non-zero exit, if any file is unpaired, no longer parses,
or still contains a forbidden term:

```
  ! unpaired (cannot be replayed): soap/900-GetThing.req.xml
  ! no longer parses: rest/012-list.resp.json: Expecting value: line 1 column 11
  ! still contains a forbidden term: soap/031-Login.resp.xml
Error: 3 problem(s) -- not safe to publish
```

To check a corpus you did not just produce:

```bash
phantom-api mock sanitize corpus --out /tmp/check --terms-file terms.txt
```

The reversal mapping is written **outside** the corpus, with mode `0600`, and
must never be committed. See the `corpus` job in
[.github/workflows/ci.yml](../.github/workflows/ci.yml) for the gate this
repository uses.

### Detecting a stale corpus

```bash
phantom-api mock drift corpus --target https://real.example.com --i-have-permission
```

Compares response shape against the live system and exits non-zero on any
divergence. See [Route E](#keeping-a-corpus-honest).

---

## 9. Building a domain pack

A domain pack is the product-specific half of a stateful mock. It supplies three
things; everything else is framework.

### The model

Protocol-free and dull on purpose:

```python
@dataclass
class Entity:
    id: str                        # "vm-42"
    kind: str                      # "VirtualMachine"
    props: dict[str, Any]          # nested, addressable by dotted path
    edges: dict[str, list[str]]    # named references to other entities
```

Two behaviours matter more than they look:

- `entity.resolve("summary.runtime.powerState")` returns `MISSING`, not `None`,
  when absent — so a read can report *partial* failure, which is what real
  systems do.
- Edges are named, not typed by target — so one traversal engine serves any
  product.

### 9.1 — build the inventory

```python
from phantom_api.mock.model.generator import Builder
from phantom_api.mock.packs.base import register

class MyPack:
    name = "myproduct"

    def build(self, scenario, seed):
        b = Builder(seed=seed)
        b.ids.prefix_for("Server", "srv")
        root = b.entity("Root", name="root")
        for spec in scenario["topology"]["servers"]:
            b.entity("Server", name=spec["name"], parent=root, parent_edge="children")
        return b.inventory
```

`Builder` seeds its own randomness, so generation is reproducible.

### 9.2 — write projections

One function per operation:

```python
from phantom_api.mock.types import MockError

def list_servers(ctx, op):
    return [
        {"id": e.id, "name": e.props["name"]}
        for e in ctx.inventory.of_kind("Server")
    ]

def get_server(ctx, op):
    entity = ctx.inventory.get(op.params.get("id"))
    if entity is None:
        raise MockError("no such server", kind="not-found", detail_type="NotFound")
    return {"id": entity.id, **entity.props}

class MyPack:
    def projections(self):
        return {"ListServers": list_servers, "GetServer": get_server}
```

`ctx` gives you `inventory`, `evolution`, `sessions`, `cursors`, `scenario` and
`catalogs`.

### 9.3 — map events to mutations

```python
def power_off(inventory, entity, args):
    entity.set("state.power", "off")
    return {"entity_id": entity.id, "power": "off"}

class MyPack:
    def mutations(self):
        return {"ServerPoweredOffEvent": power_off}
```

**The rule:** the mutation runs, *then* the event is recorded. A mock that
announces a change it has not made teaches its consumers to distrust their own
reconciliation logic.

### 9.4 — register it

Bundled packs register on import. Third-party packs ship as their own
distribution:

```toml
[project.entry-points."phantom_api.packs"]
myproduct = "my_package.pack:MyPack"
```

```bash
phantom-api mock list-packs   # yours now appears
```

### 9.5 — client-directed traversal (only if you need it)

If your protocol hands the server a traversal program:

```python
from phantom_api.mock.model.graph import ObjectSpec, TraversalSpec, traverse

specs = {
    "visitFolders": TraversalSpec("visitFolders", "Folder", "childEntity",
                                  ["visitFolders", "dcToHf"]),
    "dcToHf":       TraversalSpec("dcToHf", "Datacenter", "hostFolder", ["crToH"]),
    "crToH":        TraversalSpec("crToH", "ComputeResource", "host", []),
}
entities = traverse(
    inventory,
    ObjectSpec(start="root", select=list(specs)),
    specs,
    subtypes={"ComputeResource": {"ClusterComputeResource"}},
)
```

Pass `subtypes`. Clients write traversals against base types, and without it a
rule declared on `ComputeResource` never fires for a `ClusterComputeResource` —
a failure that looks like an empty inventory.

---

## 10. The control plane

Mounted at `/__phantom`, loopback-only by default. It can mutate state, so never
expose it on the same interface as the mock in a shared environment.

| Endpoint | Purpose |
|---|---|
| `GET /__phantom/status` | Uptime, responders, session and cursor counts, model summary |
| `GET /__phantom/log?limit=100` | Every decoded operation: name, responder, status, timing, size |
| `POST /__phantom/verify` | Assert an operation arrived N times. Exits non-zero from the CLI |
| `GET /__phantom/inventory?kind=X` | Dump the model |
| `POST /__phantom/events` | Inject a change now |
| `GET /__phantom/events?since=N` | Read the change log |
| `POST /__phantom/mutate` | Edit the model directly |
| `POST /__phantom/chaos` | Toggle faults at runtime |
| `GET /__phantom/sessions` | Live sessions and cursors |
| `POST /__phantom/reset` | Clear counters and the log |

The log answers the question that is painful against a real system: *what did my
client actually ask for?*

```bash
phantom-api mock log --url https://localhost:8443 -n 20
```

---

## 11. Chaos and fault injection

Statically:

```yaml
chaos:
  latency_ms: 50
  latency_jitter_ms: 200
  fault_rate: 0.05
  fault_kind: not-authenticated   # expressed in the target protocol's own terms
  drop_rate: 0.01
```

Or at runtime, mid-test:

```bash
curl -sk -X POST https://localhost:8443/__phantom/chaos \
  -H 'Content-Type: application/json' -d '{"fault_rate": 1.0}'
```

A protocol-specific fault beats a generic 500: `fault_kind: not-authenticated`
produces the exact shape an expired session produces, which exercises the
client's **recovery** path rather than just its error path.

---

## 12. Testing against your mock

### In-process, no port needed

```python
from pathlib import Path
from fastapi.testclient import TestClient
from phantom_api.mock import create_mock_app, load_config

def test_client_handles_a_powered_off_vm():
    app = create_mock_app(load_config(Path("examples/vcenter/phantom.yaml")))
    client = TestClient(app)

    vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]
    client.post("/__phantom/events",
                json={"event": "VmPoweredOffEvent", "target": vm["id"]})

    # ... drive your client, then assert it asked what you expected ...
    assert client.post("/__phantom/verify",
                       json={"operation": "RetrieveProperties"}).json()["satisfied"]
```

### As a test oracle from the shell

```bash
phantom-api mock verify RetrieveProperties --url https://localhost:8443 --at-least 1
echo $?   # non-zero if the assertion failed
```

### Replay the corpus as a regression gate

The strongest test available: assert the mock still answers every request it was
built from.

```python
for request_file in sorted((corpus / "soap").glob("*.req.xml")):
    response = client.post("/sdk", content=request_file.read_text(), headers=HEADERS)
    assert "no recorded exchange" not in response.text
```

---

## 13. Running in CI and Docker

```bash
docker run -p 3000:3000 -v "$PWD/openapi.json:/spec/openapi.json:ro" \
  phantom-api serve /spec/openapi.json

docker run -p 8443:8443 -v "$PWD/vcenter-mock:/spec:ro" \
  phantom-api mock serve /spec/phantom.yaml --host 0.0.0.0
```

```bash
docker compose up phantom-api                    # REST mock
docker compose --profile mock up examples/vcenter    # protocol mock
```

In a pipeline, start it in the background and wait for the control plane:

```yaml
- name: Start the mock
  run: |
    phantom-api mock serve mocks/phantom.yaml &
    for i in $(seq 1 20); do
      curl -skf https://127.0.0.1:8443/__phantom/status && break || sleep 1
    done
- name: Run the tests
  run: pytest tests/integration
- name: Assert the client called what it should have
  run: phantom-api mock verify Login --url https://127.0.0.1:8443
```

---

## 14. Troubleshooting

**"no responder configured for X"** — no rule matched. Add a `match: "*"`
catch-all at the end, or check your operation glob.

**"no recorded exchange for X"** — the corpus has not seen it. Use
`on_miss: synthesize` to fall through to the next responder, or
`on_miss: forward` with a `forward` rule after it.

**The model returns an empty inventory** — almost always a traversal problem.
Check that the `subtypes` map covers base types your client traverses against,
and confirm the edge names in your `TraversalSpec` match the edges your builder
created.

**The client hangs after connecting** — it is probably waiting on a second call
you have not answered. `phantom-api mock log` will show you the last operation
it managed to make.

**The client ignores data you are clearly sending** — a missing `xsi:type` on a
polymorphic element. Reflective clients skip fields they cannot resolve and log
a warning you will never see.

**TLS errors** — install the extra: `pip install 'phantomapi-server[mock]'`.

**"environment variable X is referenced but not set"** — working as intended.
Export it, or remove the reference.

**Control plane returns 403** — it is loopback-only. Set
`control_plane.bind` deliberately if you need otherwise.

---

## Where to look next

| | |
|---|---|
| Full worked example, captured data and tooling | [examples/vcenter/README.md](../examples/vcenter/README.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |
