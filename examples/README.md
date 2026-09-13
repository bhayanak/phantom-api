# Examples

Two complete, working mocks of two real systems. They are deliberately
different: between them they cover both protocol families and every way of
getting a mock in the first place.

| | [morpheus](morpheus/) | [vcenter](vcenter/) |
|---|---|---|
| **Protocol** | REST / JSON | SOAP / XML, plus a REST surface |
| **Built from** | An OpenAPI spec **and** a recording | A recording, plus a simulated model |
| **Answers come from** | Generated data, or 76 recorded exchanges | 69 recorded exchanges and a live object graph |
| **Has a usable spec?** | Yes (631 paths) | No — SOAP services rarely publish one |
| **Stateful?** | No | Yes: sessions, paging cursors, mutations, events |
| **Best read for** | "I have a spec" / "I can record the real thing" | "My client holds state and expects the world to change" |

Both corpora were captured from real systems and sanitised with
`phantom-api mock sanitize`. Nothing in either folder identifies the machine it
came from.

## Which one is your situation?

**You have a specification.** Start with [morpheus](morpheus/). Point
`phantom-api` at the file and you have a server; nothing needs recording and
nothing needs sanitising.

```bash
phantom-api serve examples/morpheus/morpheus-mock.json
```

**You can reach the real system.** Both examples show the full route — record,
sanitise, derive, serve. Morpheus is the shorter read because REST is simpler:

```bash
phantom-api mock serve examples/morpheus/phantom.yaml
```

**Your client holds state.** Read [vcenter](vcenter/). A corpus cannot change,
and that is the whole problem it solves.

```bash
phantom-api mock serve examples/vcenter/phantom.yaml
```

**You have neither a spec nor access.** Write responses by hand with the
template responder. See [docs/building-mocks.md](../docs/building-mocks.md).

## What the two examples prove together

Every configuration and object graph in both folders was **generated**, by the
same commands, from the recordings alone:

```bash
phantom-api mock derive <corpus> --out phantom.yaml --model .
```

- On the **SOAP** corpus it recovers 153 objects across 14 kinds — matching the
  hand-built vCenter topology exactly on all 8 inventory types, plus 6 service
  singletons the hand-written file omitted.
- On the **REST** corpus it recovers 883 objects across 45 kinds, with counts
  that match the live appliance: 5 servers, 1 instance, 2 zones, 3 roles.

Nothing in the deriver knows what a datacenter or an instance is. That is the
point of having two examples rather than one: a single example cannot tell you
whether a generalisation is real.

## Running the tests against them

Both examples are exercised by the test suite, so they cannot silently rot:

```bash
pytest tests/mock -q
```

