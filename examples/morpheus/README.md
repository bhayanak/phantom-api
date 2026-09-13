# Morpheus — a REST API, two ways

Morpheus is a cloud management platform with a large REST API. This folder is
the worked example for **REST**, and it deliberately shows the same system built
from two different sources:

| | From a specification | From a recording |
|---|---|---|
| Source | [morpheus-mock.json](morpheus-mock.json) — OpenAPI 3.1 | [corpus/](corpus/) — 76 captured exchanges |
| Command | `phantom-api serve morpheus-mock.json` | `phantom-api mock serve phantom.yaml` |
| Covers | 631 paths, 1017 operations | 76 endpoints, exactly as they really answer |
| Data | Generated from schemas | The real system's own responses |
| Best for | Breadth — a client team building against the whole surface | Fidelity — tests that must match production behaviour |

Neither is better. A spec covers everything and is right about nothing in
particular; a recording covers a little and is right about all of it.

## Run it

```bash
# From the spec: the whole API surface, generated data
phantom-api serve examples/morpheus/morpheus-mock.json

# From the recording: fewer endpoints, real answers
phantom-api mock serve examples/morpheus/phantom.yaml
```

```bash
curl -sk https://127.0.0.1:8443/api/servers | jq '.servers[0].name'
curl -sk https://127.0.0.1:8443/api/instances/1 | jq '.instance.status'
curl -sk https://127.0.0.1:8443/api/does-not-exist          # a recorded 404
```

## What is here

```
morpheus/
├── morpheus-mock.json      OpenAPI 3.1 spec (9 MB, 631 paths)
├── phantom.yaml            config, written by `mock derive` from the corpus
├── corpus/                 76 sanitised request/response pairs
├── topology/
│   └── inventory.json      883 objects recovered from the corpus
├── scenario/
│   └── morpheus-scenario.yaml   the shape of that graph, as a recipe
└── tools/
    ├── endpoints.txt       the endpoint list that was recorded
    ├── rules.yaml          which collections hold named objects
    └── site-terms.txt      site tokens blanked during sanitisation
```

> A dereferenced `bundled.json` is not committed -- it is 18 MB, nothing reads
> it, and `npx @redocly/cli bundle` regenerates it. See the README's
> "Turn any OpenAPI repo into a mock" section.

Everything except the spec and `endpoints.txt` was **generated**, not written:

```bash
phantom-api mock record https://morpheus.example.com \
  --endpoints tools/endpoints.txt --out raw --follow 3 \
  --header "Authorization: Bearer $TOKEN" \
  --i-understand-this-hits-production

phantom-api mock sanitize raw --out corpus --terms-file tools/site-terms.txt
phantom-api mock derive corpus --name morpheus --out phantom.yaml --model .
```

## What the capture showed

All of these were found by recording a live appliance, and several of them
changed the tooling:

| Observation | Consequence |
|---|---|
| A virtual image's `userData` held a cloud-init block containing **an administrator password in plaintext**, plus internal DNS and NTP addresses | Free-form text fields are an unbounded disclosure surface. No pattern rule would have caught this, so the sanitiser now replaces such fields wholesale |
| `sshPassword`, `sshPasswordHash` and similar carried real values | A credential has no distinguishing shape, so it has to be detected by field *name*. They are replaced with a fixed marker, never a plausible fake |
| A CPU reading of `89.2113888559807235` matched the world-wide-name pattern | 16 consecutive digits are far more often a number than a WWN. The rule now requires a hex letter and refuses to match inside a longer number |
| A licence response used a site token as a **JSON key** | Renaming a key changes the shape clients parse. Term replacement now skips key positions, and the leak report says when a term survived there |
| Paged endpoints differ only by query string | The recorder stores the query as part of the endpoint's identity, or two pages become indistinguishable |
| A list response and a detail response for the same object have **different shapes** | Recording a collection is not enough; `--follow` fetches members so the corpus can answer what clients actually ask |

## The object graph

`phantom-api mock derive --model` recovered 883 objects across 45 kinds from the
recording alone, with no knowledge of Morpheus:

```
server 5 · instance 1 · zone 2 · group 1 · network 2
account 1 · user 1 · role 3 · virtualImage 1 · storageVolume 25
```

Those counts are exactly what the live appliance holds. Relationships were
recovered too — `server → account`, `instance → group` — and only where the
reference resolved to an object seen elsewhere in the recording, so the graph
never claims a relationship the traffic did not show.

The same command, unchanged, recovers vCenter's 147-object SOAP inventory. See
[../vcenter/](../vcenter/).

## Regenerating against your own appliance

```bash
export MORPH_URL=https://morpheus.example.com
read -rs MORPH_PASS      # never put a password in a file

TOKEN=$(curl -sk -X POST "$MORPH_URL/oauth/token?grant_type=password&scope=write" \
  -d client_id=morph-api -d username=admin --data-urlencode "password=$MORPH_PASS" \
  | jq -r .access_token)
```

Then run the three commands above. Sanitisation is not optional: this capture
proved a real appliance will hand you somebody's password without being asked.
