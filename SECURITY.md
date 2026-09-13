# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through
[GitHub's private vulnerability reporting](https://github.com/bhayanak/phantom-api/security/advisories/new).
That creates a draft advisory only the maintainers can see.

Include, as far as you can:

- what an attacker can do, not just what is wrong;
- the version or commit you tested;
- a reproduction — a spec, a config, or a request that triggers it.

You should get an acknowledgement within a few days. If a fix is warranted it
will be released with an advisory crediting you, unless you ask otherwise.

## Supported versions

| Version | Supported |
|---|---|
| 2.x | Yes |
| 1.x | Security fixes only, until 2.x has been out for six months |

## What this tool is, and what that means

phantom-api runs **fake** services so you can develop and test without the real
one. That shapes the threat model in two ways worth stating plainly.

**A mock is not a security control.** It authenticates nobody. `accept-any` is
the default auth mode and says so on startup. Never place a mock where a real
service is expected to enforce anything.

**A recording is a copy of production data.** The most likely way to be harmed
by this project is not a flaw in its code — it is committing a corpus that still
contains what the real system told you. See [Recorded data](#recorded-data).

## Security properties

These are deliberate, tested, and should be treated as regressions if broken.

### Network exposure

- Every server binds `127.0.0.1` unless `--host` says otherwise. Exposing a mock
  beyond the loopback interface is an explicit act.
- The control plane (`/__phantom`) binds loopback-only by default, separately
  from the mock itself. It can dump the model and mutate state, so it is not
  something to expose casually.
- The container image sets `PHANTOM_API_HOST=0.0.0.0`, because a container that
  binds loopback is unreachable. The isolation boundary there is the container's
  network, not the bind address.

### Parsing untrusted input

- **XML**: `DOCTYPE` and `ENTITY` declarations are refused outright before
  parsing, which closes XXE and billion-laughs. Documents are also capped at
  32 MB and 256 levels of nesting.
- **Expressions** in template configs are parsed to an AST and evaluated against
  an explicit allowlist of node types. There is no `eval`. Imports, dunder
  access, comprehensions and lambdas are refused, so a config cannot reach the
  filesystem, the network, or the interpreter.
- **Specs** are data, not code. No spec or config is ever executed.

### Credentials and configuration

- Secrets come from the environment. An unset `${VAR}` is a startup error rather
  than a silently empty value.
- Credentials are never written to the corpus, the config, or the logs.

### Acting on real systems

Two commands send traffic to systems you do not control, and both require an
explicit flag that this tool will never set for you:

- `mock record --i-understand-this-hits-production`
- `mock drift --i-have-permission`

Recording also refuses loopback targets, so it cannot be pointed at itself by
accident.

### Container

- Runs as a non-root user (uid 10001).
- Built on Alpine and patched at build time. The interpreter's `pip` and
  `setuptools` are removed from the runtime image: nothing there installs
  packages, and their vendored libraries were the only remaining scan findings.
- Scanned on every build; the pipeline fails on any critical or high finding.

## Recorded data

`mock record` captures whatever the real system returns. In testing against a
live appliance that included an administrator password in a cloud-init
`userData` field, password hashes, internal DNS and NTP addresses, and hardware
serial numbers used as object names.

**Never commit a raw capture.** The workflow exists for this reason:

```bash
phantom-api mock record ... --out raw          # raw/ is gitignored
phantom-api mock sanitize raw --out corpus \
  --rules rules.yaml --terms-file terms.txt
```

`mock sanitize` exits non-zero if any file is unpaired, no longer parses, or
still contains a forbidden term. **Treat a failure as blocking.** It also:

- replaces credential-named fields with a fixed marker, never a plausible fake;
- replaces free-form text fields (`userData`, scripts, notes) wholesale;
- pseudonymises addresses, UUIDs, MACs, serials and licence keys stably, so
  cross-references still resolve;
- writes its reversal mapping **outside** the corpus, mode `0600`.

Never commit that mapping. `raw/` and `.capture/` are gitignored, and CI fails
if either is tracked.

## How this repository is scanned

Every push and pull request runs:

| Check | Tool | Fails the build on |
|---|---|---|
| Dependency vulnerabilities | Trivy (filesystem), pip-audit | critical, high |
| Container vulnerabilities | Trivy (image) | critical, high |
| Dockerfile / compose misconfiguration | Trivy config | critical, high |
| Static analysis | CodeQL | — (reported to Security tab) |
| Secrets in the tree | Trivy secret scanner | any finding |
| Published corpora | `mock sanitize`, credential-field scan | any finding |
| Dependency updates | Dependabot | — (raises pull requests) |

Results are uploaded as SARIF, so they appear under
[Security → Code scanning](https://github.com/bhayanak/phantom-api/security/code-scanning).

Run the same checks locally before pushing:

```bash
make security          # everything below
make scan-deps         # Trivy filesystem + pip-audit
make scan-image        # build and scan the container
make scan-config       # Dockerfile and compose misconfiguration
make scan-secrets      # secrets in the working tree
make scan-corpora      # the published corpora are safe to ship
```
