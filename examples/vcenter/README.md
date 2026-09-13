# vcenter-mock

A working **vCenter simulator** built on `phantom-api mock`, plus the captured
reference data it was derived from.

This is the worked example for mocking a stateful, non-OpenAPI system. Everything
here was derived from a real vSphere 8.0.3 endpoint and then pseudonymised. The
simulator code lives in `src/phantom_api/mock/packs/vcenter/`; this directory
holds the data, the configuration and the tooling.

## Run it

```bash
pip install 'phantomapi-server[mock]'

# Model-backed for the operations that must stay consistent, corpus for the rest
phantom-api mock serve examples/vcenter/phantom.yaml

# Or pure replay, no modelling at all
phantom-api mock serve --corpus examples/vcenter/corpus --port 8443 --tls self-signed
```

```
╭─────────────── phantom-api mock - vcenter ───────────────╮
│  https://127.0.0.1:8443                                    │
│  Protocols: soap, openapi                                  │
│    - model(108 entities, 20 projections)                   │
│    - corpus(corpus, 69 exchanges, on_miss=fault)           │
│  Control:   /__phantom/status                              │
╰──────────────────────────────────────────────────────────╯
```

Drive the simulation:

```bash
curl -sk https://localhost:8443/__phantom/status
curl -sk 'https://localhost:8443/__phantom/inventory?kind=VirtualMachine'

curl -sk -X POST https://localhost:8443/__phantom/events \
  -H 'Content-Type: application/json' \
  -d '{"event":"VmPoweredOffEvent","target":"vm-23"}'

phantom-api mock log --url https://localhost:8443
```

A client that builds `https://{host}/sdk` with no port needs the mock on 443:

```bash
sudo phantom-api mock serve examples/vcenter/phantom.yaml --port 443
# or
docker run -p 443:8443 -v "$PWD/vcenter-mock:/spec:ro" \
  phantom-api mock serve /spec/phantom.yaml --host 0.0.0.0
```

Full walkthrough: [docs/building-mocks.md](../../docs/building-mocks.md).

## Why a vCenter simulator

Inventory collectors that read vCenter are painful to develop against. You need
a real vCenter, real ESXi hosts and real VMs before a single line of collection
logic can be exercised, and you cannot make a host fail on demand. The consumer
that drove this work does exactly that: it logs in over SOAP, walks the whole
inventory, then polls an event collector once a minute for changes.

Mocking it needs more than a spec-driven mock server, because:

- The protocol is SOAP/XML with no OpenAPI description.
- The client is stateful: session cookie, server-side cursors, event collectors.
- It asks for a **graph** — "walk the inventory from the root and give me these
  property paths on everything you find" — not a document.
- It expects a **continuous change feed** that stays consistent with the graph.

## Layout

```
examples/vcenter/
├── phantom.yaml     the mock configuration: protocols, sessions, responders
├── corpus/          replay corpus: 69 sanitised request/response pairs
│   ├── manifest.json
│   ├── soap/        vim25, PBM and SMS exchanges
│   └── rest/        Automation REST and vAPI exchanges
├── catalog/         extracted reference data, loaded by the pack
│   ├── vim-types.json          which xsi:type each element carried
│   ├── perf-counters.json      732 performance counters
│   ├── roles.json              48 authorisation roles
│   ├── event-types.json        event types observed in the capture
│   └── event-subscription.json 300 event ids a collector subscribes to
├── topology/
│   └── inventory.json          the object graph: 147 objects, typed and linked
├── scenario/
│   └── vcenter-scenario.yaml   the generative scenario
└── tools/
    ├── capture_vcenter.py      record a live endpoint (stdlib only)
    ├── vendor-terms.txt        supplier/site tokens to blank out
    └── derive_scenario.py      corpus -> catalogs, topology and scenario
```

> Sanitising and config derivation are no longer scripts here — they ship as
> `phantom-api mock sanitize` and `phantom-api mock derive`.

## The two modes this data supports

**Replay** uses `corpus/`. High fidelity, zero inference, but only answers
requests that were recorded. Good for regression tests and for proving the
simulator's parsing is right.

**Synthesis** uses `scenario/` + `catalog/`. Generates an inventory of any size
and answers requests that were never captured. Good for scale tests, fault
injection and a live change feed. The corpus is how you check the synthesiser
still produces wire-accurate output.

## What the capture told us

The recording is the specification. Things worth knowing before writing a line
of simulator code, all verified against the live endpoint:

| Observation | Consequence |
|---|---|
| `RetrieveServiceContent` is called **twice** — once with the default `SOAPAction`, then again after the client re-pins it from `about.apiVersion` | The handshake is not idempotent-looking; both must succeed |
| The session cookie is captured from the **first** response and reused; a failed login mid-stream replaces it | Cookie lifecycle bugs are easy to introduce and hard to see |
| PBM (`/pbm/sdk`) and SMS (`/sms/sdk`) reuse the vim25 session, but carry it in a `<vcSessionCookie>` **SOAP header**, not an HTTP `Cookie` | Three transports, two session mechanisms |
| An unauthenticated `RetrieveProperties` returns **HTTP 200 with a `missingSet`** per property, not a fault; an unauthenticated `CurrentTime` returns a **500 with a SOAP fault** | "Session expired" has two completely different shapes |
| Faults arrive as HTTP 500 with the fault in the *error* body | A mock that returns 200 for faults silently breaks client error handling |
| `xsi:type` appears on every polymorphic element, and sometimes **namespace-prefixed** (`vim25:NotAuthenticated`) | The serialiser must emit it, and the parser must tolerate both forms |
| Empty results are an **empty response element**, not an empty list | `<RetrievePropertiesResponse/>` is meaningful |
| Real topology: 8 datacenters, 8 clusters, 16 hosts, 25 VMs, 28 datastores, 48 folders, 6 networks | Folders outnumber every other object type — traversal correctness matters more than volume |
| 3000 events in 90 days, dominated by `UserLogoutSessionEvent`, `HostSyncFailedEvent`, `UserLoginSessionEvent` | A realistic change feed is mostly noise; interesting events are rare |

## Reproducing the capture

`capture_vcenter.py` uses the standard library only, so it runs on any jump host
that can reach the target. Credentials come from the environment and are never
written to disk.

```bash
export VC_HOST=vcenter.example.com
export VC_USER='administrator@vsphere.local'
read -rs VC_PASSWORD && export VC_PASSWORD   # never put this in a file
export VC_OUT=./raw

python3 tools/capture_vcenter.py
```

Then sanitise and derive:

```bash
phantom-api mock sanitize raw --out corpus \
  --terms-file tools/vendor-terms.txt \
  --mapping ../.capture/mapping.json

python3 tools/derive_scenario.py --corpus corpus --out .
```

The config in this folder was written by hand, but `phantom-api mock derive
corpus` reproduces it from the recording alone — including the detail that
`/pbm/sdk` and `/sms/sdk` carry the session in a SOAP header while `/sdk` uses a
cookie.

## Sanitisation

Recorded traffic from a live system is full of identifying data. `phantom-api
mock sanitize` rewrites all of it to stable pseudonyms, so cross-references
between responses still resolve while no original identity survives:

- Host names → `host-NNN.lab.example.com`, object names → `DC-NN`, `Cluster-NN`,
  `esx-NN.lab.example.com`, `VM-NNNN`, `DS-NN`, `PG-NN`
- IPv4 → RFC 5737 documentation ranges; IPv6 → pseudonyms (EUI-64 forms embed
  the real MAC)
- MAC → locally administered `02:00:5e:*`
- UUIDs → deterministic UUIDv5, so the same input always maps to the same output
- Licence keys, WWNs, serials, asset tags, disk identifiers, supplier and model
  strings
- Datastore paths keep the `[datastore]` prefix and lose the rest
- `extraConfig` values are pseudonymised wholesale — keys are a bounded
  vocabulary consumers match on, values are an unbounded disclosure surface
- A final supplier/site token pass, extendable with `--terms`
- Unpaired files are refused: a request without its response cannot be replayed

Two rules exist specifically because their absence caused real corruption, both
caught by the test suite rather than by review:

- `<path>` is **not** treated as a datastore path. In a traversal specification it
  names an edge to follow (`childEntity`, `hostFolder`), and rewriting it broke
  every recorded request — the mock returned exactly one object.
- The IPv6 pattern requires `::`. A looser one matched the colons inside
  `00:14:03` and corrupted every timestamp in the corpus.

Verified after every run: zero matches for any source identifier, every document
still parses as well-formed XML or JSON, and no file is left unreplayable. The
`corpus` job in [.github/workflows/ci.yml](../../.github/workflows/ci.yml) enforces
all three.

The reversal mapping is written **outside** this directory (`.capture/`, which is
git-ignored) and must never be committed.

> Re-recording against your own endpoint is the intended workflow. The committed
> corpus exists so the simulator has something to test against out of the box.
