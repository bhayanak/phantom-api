# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-09-13

Mock any protocol, not just what a spec can describe.

### Added

- **`phantom-api mock`** — a protocol-agnostic mock engine, alongside the existing
  spec-driven `serve`. One declarative configuration file describes protocols,
  responders, chaos and the control plane; rules match in order, first one wins.
- **Protocol packs**: SOAP 1.1/1.2, JSON-RPC 1.0/2.0 and REST, with auto-detection.
  Adding a protocol means implementing three methods; everything else works unchanged.
- **Four composable responders**:
  - `corpus` — replay recorded traffic, matched by a fingerprint that ignores session
    ids, timestamps and continuation tokens.
  - `template` — declarative canned responses with `first`/`sequence`/`random`/`match`
    dispatch strategies.
  - `model` — a stateful domain model that stays self-consistent and evolves.
  - `forward` — proxy the real system, optionally recording as it goes.
- **Stateful model layer**: typed entities and named edges, dotted-path property reads
  with partial-failure results, client-directed graph traversal with cycle safety and
  subtype awareness, and bounded server-side cursors.
- **Change engine**: scripted timelines or weighted random churn. Every event mutates
  the model *before* it is recorded, so a client can trust its own reconciliation.
- **Domain packs**, discovered through the `phantom_api.packs` entry point. Ships with
  a `vcenter` pack covering a stateful SOAP inventory API.
- **Control plane** at `/__phantom`: status, request log, verification, inventory dump,
  event injection, direct model mutation, runtime chaos toggling. Loopback-only by default.
- **Record and sanitise** workflow: capture a live system, then pseudonymise it with
  stable, structure-preserving replacements so the corpus is safe to commit.
  `mock sanitize` ships the rules as a first-class command, extensible per domain
  through a rules file, and exits non-zero if any file is unpaired, no longer parses,
  or still contains a forbidden term.
- **`mock derive`** — write a runnable configuration from a recording. Protocols, path
  patterns and per-path session transport are read off the recorded traffic rather than
  guessed; on the bundled vCenter corpus it independently reproduces the hand-written
  config, including that sibling endpoints carry the session two different ways.
- **`mock derive --model`** — recover the object graph as well: entities, their
  properties, and the references between them, written as an inventory the model layer
  already consumes plus a scenario describing its shape. An edge is only recorded when
  the reference resolves to an object seen elsewhere, so a derived graph never claims a
  relationship the traffic did not show. Validated against two real systems over two
  protocols: it recovers vCenter's 147-object SOAP inventory exactly, and Morpheus's
  REST inventory with counts matching the live appliance.
- **`mock record --endpoints`** — record by *calling* a list of paths rather than by
  proxying, for when you have credentials but no client to drive the traffic.
  `--follow N` also fetches members of each collection, because a list response and a
  detail response are different shapes and clients ask for both.
- **`mock drift`** — replay a corpus against the live system and report where they
  disagree, so a corpus cannot rot unnoticed. Compares response *shape*, so timestamps,
  ids and tokens that change on every call are not false positives.
- **Chaos injection**: latency with jitter, dropped connections, and faults expressed in
  the target protocol's own terms rather than as generic 500s.
- **Sessions** carried as a cookie, an HTTP header, or a SOAP header element, per path.
- TLS with an ephemeral self-signed certificate, via the new `[mock]` extra.
- New CLI commands: `mock serve`, `mock record`, `mock sanitize`, `mock derive`,
  `mock drift`, `mock init`, `mock validate`, `mock inspect`, `mock list-packs`,
  `mock log`, `mock verify`.
- [docs/building-mocks.md](docs/building-mocks.md) — a step-by-step guide organised by
  where your answers come from: a specification, example responses, a recording, a
  model, or the live system. Each route works for any protocol.
- [examples/](examples/) — two complete worked mocks: `morpheus` (REST, from a spec)
  and `vcenter` (SOAP, recorded and simulated).
- CI gates the published corpus: every document must parse, the manifest must be marked
  sanitised, no file may be unreplayable, and no raw capture may be committed.

### Fixed

- The container set `PHANTOM_API_PORT=3000`, which overrode the port in a mock's own
  config file. `mock serve` therefore always bound 3000 while the documentation and the
  compose file published 8443, so the bundled `vcenter-mock` service was unreachable.
  The image no longer sets a port: `serve` uses its default and `mock serve` uses its
  config.
- The compose file and the Docker smoke test still referenced `vcenter-mock/` and
  `morpheus/`, which moved under `examples/`.

- Recorded exchanges whose endpoint carried a query string could never be replayed:
  matching compared the bare path on one side and the full target on the other. Some
  APIs select the operation entirely through the query (`?action=list`), so those
  recordings were unreachable. Found by `mock drift` on its first run.
- A URL-addressed request now matches on its full target before falling back to the
  operation-name fingerprint. Protocol packs strip the query when naming an operation,
  so every page of a collection was answered with the first page.
- Repeated SOAP parameters were silently discarded — a call carrying several `<privId>`
  elements kept only the last, because the decoder built its parameters with a dict
  comprehension.
- A parameter echoed back into a response was rebuilt as child elements, emitting
  `<#text>` — not a legal XML element name. Text with attributes now round-trips as
  `<object type="X">v</object>`.
- A structured `body:` in a template rendered as a Python repr rather than data, so a
  REST mock emitted single-quoted pseudo-JSON.
- `mock drift` skipped every exchange with no request body, which is every GET, then
  reported "no drift" — a vacuous pass. It now replays the recorded method and fails
  when nothing could be checked.
- The corpus verifier treated an empty request body as a parse failure, so a recording
  of any GET-based API could never pass.

### Security

- **The container image now has zero known vulnerabilities**, down from 3 critical and
  55 high. The base moved from Debian slim to Alpine: not for size, but because only 12
  of the 56 Debian findings had a fix available at all -- the rest are marked `affected`
  or `will_not_fix`, so no amount of patching clears them, and the scan could never have
  been a build gate. Both stages now `apk upgrade`, and the interpreter's `pip` and
  `setuptools` are removed from the runtime image: nothing there installs packages, and
  their vendored copies of `msgpack` and `pkg_resources` were the only findings left.
- **[SECURITY.md](SECURITY.md)** documents the threat model, the deliberate security
  properties, the reporting process, and what recorded data means for anyone publishing
  a corpus.
- CI gates on critical and high findings from Trivy across dependencies, the container
  image, and configuration, and uploads every result as SARIF so it appears under
  Security -> Code scanning. CodeQL runs on push and weekly; Dependabot raises version
  updates for pip, Docker and Actions.
- `make security` runs the same checks locally, so a clean run means a clean pipeline.
- A published corpus is checked for credential-bearing fields by
  `scripts/check_no_credentials.py`, which is itself tested in both directions --
  the failure mode of such a gate is crying wolf until somebody disables it.
- The container runs read-only with all capabilities dropped and
  `no-new-privileges` in the bundled compose file.

- **Sanitisation now covers credentials and free-form text.** Capturing a second real
  system found an administrator password sitting in a virtual image's `userData`, next
  to internal DNS and NTP addresses, and real password hashes in `sshPasswordHash`.
  Fields whose *name* says they hold a credential are replaced with a fixed marker
  rather than a plausible fake, and free-form text fields are replaced wholesale --
  no pattern rule would have caught either.
- Term replacement no longer rewrites JSON keys. A site token can legitimately *be* a
  field name, and renaming it changes the shape clients parse; the leak report now says
  when a term survived in a key so the choice is explicit.
- The world-wide-name pattern no longer matches inside a longer number. It was rewriting
  the fractional part of `89.2113888559807235` and producing invalid JSON.
- XML parsing refuses `DOCTYPE` and `ENTITY` declarations outright, with size and nesting
  caps on top.
- Template expressions are parsed to an AST with an explicit allowlist, not `eval`.
- Recording a live system requires an explicit acknowledgement flag and refuses loopback
  targets. `mock drift` requires the same acknowledgement, since it replays real requests
  against a real system.
- Sanitisation writes its reversal mapping outside the corpus with mode `0600`, and never
  to the published directory.
- Configuration secrets come from the environment only; an unset `${VAR}` is a startup
  error rather than a silently wrong value.
- Sessions, cursors, event collectors, the request log and the inventory are all bounded
  and evict rather than grow.

### Changed

- Coverage gate is 90% (was 95%), reflecting the larger surface area.
- The Docker image installs the `[mock]` extra and exposes 8443 alongside 3000.
- `docker-compose.yml` now defines both a spec-driven and a protocol mock service.

## [1.1.0] - 2026-XX-XX

### Added

- Official Docker image and `Dockerfile` (multi-stage, non-root) for running mock
  servers in containers.
- `docker-compose.yml` example that serves a mounted spec (defaults to the Morpheus mock).
- `PHANTOM_API_HOST` and `PHANTOM_API_PORT` environment variables for `serve`, `record`,
  and `replay` (the image binds to `0.0.0.0:3000` by default).
- CI now builds the Docker image; releases publish it to Docker Hub.

### Changed

- Raised the maximum spec size to 64 MB to support large dereferenced real-world specs.

## [1.0.0] - 2026-XX-XX

### Added

- Parse OpenAPI 3.x and Swagger 2.0 specs into mock servers.
- Parse JSON files into auto-generated CRUD endpoints.
- Parse Postman Collection v2.1 into mock routes.
- Schema-aware fake data generation via Faker (format-, pattern-, and name-aware).
- Custom delay simulation per request (global or via `X-Mock-Delay` / `?__delay`).
- Error injection via `X-Mock-Status` header or `?__status` query parameter.
- Record & replay mode (proxy real APIs, store responses, serve offline).
- Hot reload on spec file changes (`--watch`).
- Admin dashboard endpoints (`/__admin/routes`, `/__admin/stats`, `/__admin/reset`).
- FastAPI-powered server with async support.
- `serve`, `record`, `replay`, `routes`, and `validate` CLI commands.
