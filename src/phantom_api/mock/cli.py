"""``phantom-api mock`` -- the v2 protocol-agnostic mock engine."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from phantom_api.constants import DEFAULT_HOST
from phantom_api.mock.record.sanitize import DEFAULT_DOMAIN

mock_app = typer.Typer(
    name="mock",
    help="Mock any protocol: SOAP, JSON-RPC or REST, replayed or simulated.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_MOCK_PORT = 8443


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _banner(config, responders: list[str], scheme: str) -> None:
    protocols = ", ".join(p.pack for p in config.protocols) or "auto"
    lines = [
        f"[bold]{scheme}://{config.listen.host}:{config.listen.port}[/bold]",
        f"Protocols: {protocols}",
    ]
    lines += [f"  - {line}" for line in responders]
    if config.control_plane.enabled:
        lines.append(f"Control:   {config.control_plane.prefix}/status")
    console.print(
        Panel("\n".join(lines), title=f"phantom-api mock - {config.name}", border_style="magenta")
    )


def _serve(config) -> None:
    from phantom_api.mock.engine import create_mock_app

    try:
        application = create_mock_app(config)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    engine = application.state.engine
    ssl_args: dict = {}
    scheme = "http"
    if config.listen.tls == "self-signed" or config.listen.tls_cert:
        from phantom_api.mock.tls import TlsUnavailable, generate_self_signed

        try:
            if config.listen.tls_cert and config.listen.tls_key:
                cert = config.resolve(config.listen.tls_cert)
                key = config.resolve(config.listen.tls_key)
            else:
                material = generate_self_signed(config.listen.host)
                cert, key = material.cert_path, material.key_path
                console.print("[dim]Generated an ephemeral self-signed certificate.[/dim]")
        except TlsUnavailable as exc:
            _fail(str(exc))
            return
        ssl_args = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        scheme = "https"

    if config.listen.host not in {"127.0.0.1", "localhost", "::1"}:
        console.print(
            "[yellow]Warning:[/yellow] binding to a non-loopback address. "
            "Confirm authentication is not set to accept-any."
        )

    _banner(config, engine.describe(), scheme)
    uvicorn.run(
        application, host=config.listen.host, port=config.listen.port, log_level="info", **ssl_args
    )


@mock_app.command()
def serve(
    config_file: Path | None = typer.Argument(None, help="Mock configuration file (phantom.yaml)."),
    corpus: Path | None = typer.Option(
        None, "--corpus", "-c", help="Replay a corpus with no configuration at all."
    ),
    host: str = typer.Option(DEFAULT_HOST, "--host", envvar="PHANTOM_API_HOST"),
    port: int = typer.Option(DEFAULT_MOCK_PORT, "--port", "-p", envvar="PHANTOM_API_PORT"),
    tls: str = typer.Option("off", "--tls", help="off or self-signed."),
    seed: int | None = typer.Option(None, "--seed", help="Seed for deterministic generation."),
    latency: int = typer.Option(0, "--latency-ms", help="Injected latency per request."),
    fault_rate: float = typer.Option(0.0, "--fault-rate", help="Probability of a forced fault."),
) -> None:
    """Serve a mock from CONFIG_FILE, or replay a corpus directly."""
    from phantom_api.mock.config import ConfigError, corpus_only_config, load_config

    if corpus is not None:
        if not (corpus / "manifest.json").exists():
            _fail(f"{corpus} does not look like a corpus (no manifest.json)")
        config = corpus_only_config(corpus, host=host, port=port, tls=tls)
    elif config_file is not None:
        try:
            config = load_config(config_file)
        except ConfigError as exc:
            _fail(str(exc))
            return
        if host != DEFAULT_HOST:
            config.listen.host = host
        if port != DEFAULT_MOCK_PORT:
            config.listen.port = port
    else:
        _fail("provide a configuration file or --corpus")
        return

    if seed is not None:
        config.model.seed = seed
    config.chaos.latency_ms = latency or config.chaos.latency_ms
    config.chaos.fault_rate = fault_rate or config.chaos.fault_rate
    _serve(config)


@mock_app.command()
def record(
    upstream: str = typer.Argument(..., help="Base URL of the real system."),
    out: Path = typer.Option(..., "--out", "-o", help="Corpus directory to write."),
    protocol: str = typer.Option("auto", "--protocol", help="soap, jsonrpc, openapi or auto."),
    endpoints: Path | None = typer.Option(
        None,
        "--endpoints",
        help="Record by calling these paths instead of proxying. One per line.",
    ),
    header: list[str] = typer.Option(
        [], "--header", "-H", help="Header sent with every call, as 'Name: value'. Repeatable."
    ),
    follow: int = typer.Option(
        0, "--follow", help="With --endpoints, also fetch up to N members of each collection."
    ),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS verification."),
    host: str = typer.Option(DEFAULT_HOST, "--host"),
    port: int = typer.Option(DEFAULT_MOCK_PORT, "--port", "-p"),
    tls: str = typer.Option("self-signed", "--tls"),
    i_understand: bool = typer.Option(
        False,
        "--i-understand-this-hits-production",
        help="Required: recording sends real traffic to UPSTREAM.",
    ),
) -> None:
    """Record real traffic into a corpus, by proxying or by calling endpoints.

    Without `--endpoints` this proxies: point a client at the mock and every
    exchange is captured. With `--endpoints` it calls the listed paths itself,
    which is what you want when you have credentials but no client to drive.

    Recorded traffic is *not* sanitised. Run `phantom-api mock sanitize` before
    committing or sharing a corpus.
    """
    from phantom_api.mock.config import (
        ListenConfig,
        MockConfig,
        ProtocolConfig,
        ResponderRule,
    )

    if not i_understand:
        _fail(
            "recording sends real traffic to a live system. "
            "Re-run with --i-understand-this-hits-production"
        )
    if any(blocked in upstream for blocked in ("localhost", "127.0.0.1", "0.0.0.0")):
        _fail("refusing to record from a loopback address")

    if endpoints is not None:
        _record_endpoints(upstream, endpoints, out, header, follow, insecure)
        return

    config = MockConfig(
        name=f"record:{upstream}",
        listen=ListenConfig(host=host, port=port, tls=tls),  # type: ignore[arg-type]
        protocols=[ProtocolConfig(pack=protocol, paths=["/**"])],
        responders=[ResponderRule(responder="forward", target=upstream, record_to=out)],
        base_dir=Path.cwd(),
    )
    console.print(f"[yellow]Recording unsanitised traffic into {out}/[/yellow]")
    _serve(config)


def _record_endpoints(
    upstream: str,
    endpoints: Path,
    out: Path,
    header: list[str],
    follow: int,
    insecure: bool,
) -> None:
    from phantom_api.mock.record.crawl import crawl, expand_collections, parse_endpoints

    if not endpoints.exists():
        _fail(f"no such endpoint list: {endpoints}")

    parsed = parse_endpoints(endpoints.read_text(encoding="utf-8"))
    if not parsed:
        _fail(f"{endpoints} lists no endpoints")

    headers: dict[str, str] = {}
    for item in header:
        name, _, value = item.partition(":")
        if not value:
            _fail(f"malformed header {item!r}; expected 'Name: value'")
        headers[name.strip()] = value.strip()

    console.print(f"[yellow]Recording unsanitised traffic into {out}/[/yellow]")
    report = crawl(upstream, parsed, out, headers=headers, verify=not insecure)

    if follow:
        expanded = expand_collections(
            upstream, out, headers=headers, verify=not insecure, limit=follow
        )
        report.recorded += expanded.recorded
        report.failed.extend(expanded.failed)

    console.print(f"[green]Recorded[/green] {report.recorded} exchange(s)")
    for path, reason in report.failed[:10]:
        console.print(f"  [red]![/red] {path}: {reason}")
    if report.skipped:
        console.print(f"  [yellow]stopped early: rate limited at {report.skipped[0]}[/yellow]")
    if not report.ok:
        _fail("nothing was recorded")
    console.print("Next: [bold]phantom-api mock sanitize[/bold] before sharing this corpus.")


@mock_app.command("list-packs")
def list_packs() -> None:
    """Show the protocol packs and domain packs available."""
    from phantom_api.mock.packs import available as domain_packs
    from phantom_api.mock.protocols import available as protocol_packs

    table = Table(title="Available packs")
    table.add_column("Kind", style="bold")
    table.add_column("Name")
    for name in protocol_packs():
        table.add_row("protocol", name)
    for name in domain_packs():
        table.add_row("domain", name)
    console.print(table)


@mock_app.command()
def inspect(
    corpus: Path = typer.Argument(..., exists=True, file_okay=False, help="Corpus directory."),
) -> None:
    """Summarise what a corpus can answer."""
    from phantom_api.mock.responders.corpus import CorpusResponder

    try:
        responder = CorpusResponder(corpus)
    except FileNotFoundError as exc:
        _fail(str(exc))
        return

    counts: dict[str, int] = {}
    for exchange in responder.exchanges:
        counts[exchange.operation] = counts.get(exchange.operation, 0) + 1

    table = Table(title=f"{corpus.name}: {len(responder.exchanges)} exchanges")
    table.add_column("Operation")
    table.add_column("Recorded", justify="right")
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        table.add_row(name, str(count))
    console.print(table)


@mock_app.command()
def validate(
    config_file: Path = typer.Argument(..., exists=True, help="Mock configuration file."),
) -> None:
    """Check a configuration without binding a port."""
    from phantom_api.mock.config import ConfigError, load_config
    from phantom_api.mock.engine import MockEngine

    try:
        config = load_config(config_file)
        engine = MockEngine(config)
    except (ConfigError, ValueError, KeyError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    console.print(f"[green]OK[/green] {config_file.name}: {config.name}")
    for line in engine.describe():
        console.print(f"  - {line}")


@mock_app.command()
def log(
    url: str = typer.Option(
        f"http://{DEFAULT_HOST}:{DEFAULT_MOCK_PORT}", "--url", help="Running mock base URL."
    ),
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """Show what a running mock has been asked for."""
    import httpx

    try:
        response = httpx.get(f"{url.rstrip('/')}/__phantom/log?limit={limit}", verify=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _fail(f"could not reach the mock at {url}: {exc}")
        return

    payload = response.json()
    table = Table(title=f"{payload['count']} requests total")
    for column in ("Operation", "Protocol", "Responder", "Status", "ms", "Bytes"):
        table.add_column(column, justify="right" if column in {"Status", "ms", "Bytes"} else "left")
    for entry in payload["entries"]:
        table.add_row(
            entry["operation"],
            entry["protocol"],
            entry["responder"],
            str(entry["status"]),
            str(entry["duration_ms"]),
            str(entry["bytes"]),
        )
    console.print(table)


@mock_app.command()
def init(
    kind: str = typer.Argument("corpus", help="corpus, template or model."),
    out: Path = typer.Option(Path("phantom.yaml"), "--out", "-o"),
) -> None:
    """Write a starter configuration for KIND."""
    if out.exists():
        _fail(f"{out} already exists")
    templates = {
        "corpus": _STARTER_CORPUS,
        "template": _STARTER_TEMPLATE,
        "model": _STARTER_MODEL,
    }
    if kind not in templates:
        _fail(f"unknown kind {kind!r}; choose corpus, template or model")
    out.write_text(templates[kind], encoding="utf-8")
    console.print(f"[green]Wrote[/green] {out}")


@mock_app.command()
def sanitize(
    corpus: Path = typer.Argument(..., help="Recorded corpus to sanitise."),
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the safe copy."),
    rules: Path | None = typer.Option(None, "--rules", help="YAML/JSON rules file."),
    term: list[str] = typer.Option(
        [], "--term", help="Forbidden token. Repeatable. Also used for the leak scan."
    ),
    terms_file: Path | None = typer.Option(
        None, "--terms-file", help="File of forbidden tokens, one per line."
    ),
    mapping: Path | None = typer.Option(
        None, "--mapping", help="Write the reversal map here (mode 0600, never commit)."
    ),
    domain: str = typer.Option(DEFAULT_DOMAIN, "--domain", help="Domain for fake hosts."),
) -> None:
    """Replace identifying data in CORPUS with stable, plausible substitutes.

    Recordings carry host names, addresses, serials and licence keys. This
    rewrites them consistently, so cross-references still resolve and replay
    still works, then refuses to pass if anything is unpaired, unparseable or
    still contains a forbidden term.
    """
    from phantom_api.mock.record.sanitize import Rules, sanitise_corpus

    if not (corpus / "manifest.json").exists():
        _fail(f"{corpus} has no manifest.json -- is it a recorded corpus?")

    try:
        rule_set = Rules.load(rules)
    except (OSError, ValueError) as exc:
        _fail(f"could not read rules from {rules}: {exc}")
        return
    rule_set = Rules(**{**rule_set.__dict__, "domain": domain})

    extra = list(term)
    if terms_file is not None:
        if not terms_file.exists():
            _fail(f"no such terms file: {terms_file}")
        extra += [
            line.strip()
            for line in terms_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if extra:
        rule_set = rule_set.with_terms(extra)

    report = sanitise_corpus(corpus, out, rules=rule_set, mapping_path=mapping)
    console.print(
        f"[green]Sanitised[/green] {report.exchanges} exchanges into {out} "
        f"({report.replacements} values replaced, {report.renamed} objects renamed)"
    )
    if mapping is not None:
        console.print(f"Reversal map written to {mapping} [dim](mode 0600 -- do not commit)[/dim]")

    if report.ok:
        console.print("[green]Checks passed[/green] -- paired, parseable, no known leaks.")
        return
    for problem in report.problems():
        console.print(f"  [red]![/red] {problem}")
    _fail(f"{len(report.problems())} problem(s) -- not safe to publish")


@mock_app.command()
def derive(
    corpus: Path = typer.Argument(..., help="Recorded corpus to read."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write config here."),
    name: str = typer.Option("recorded-service", "--name", help="Mock name."),
    port: int = typer.Option(DEFAULT_MOCK_PORT, "--port"),
    model: Path | None = typer.Option(
        None,
        "--model",
        help="Also derive the object graph into this directory (inventory + scenario).",
    ),
) -> None:
    """Write a configuration that serves CORPUS, derived from what it contains.

    Protocols, paths and session transport are read off the recorded traffic
    rather than guessed. With `--model`, the object graph is recovered too:
    entities, their properties, and the references between them.
    """
    from phantom_api.mock.record.derive import inspect_corpus, render_config

    try:
        shape = inspect_corpus(corpus)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
        return

    if not shape.exchanges:
        _fail(f"{corpus} contains no exchanges")

    # The corpus path is written into the config, and config paths resolve
    # relative to the config file -- not to the shell's working directory.
    anchor = (out.resolve().parent if out else Path.cwd()).resolve()
    try:
        corpus_ref = f"./{corpus.resolve().relative_to(anchor)}"
    except ValueError:
        corpus_ref = str(corpus.resolve())

    text = render_config(shape, name=name, corpus_path=corpus_ref, port=port)

    table = Table(title="What the recording covers", show_header=True)
    table.add_column("Protocol")
    table.add_column("Exchanges", justify="right")
    table.add_column("Operations", justify="right")
    table.add_column("Paths", justify="right")
    for protocol, count in shape.protocols.most_common():
        table.add_row(
            protocol,
            str(count),
            str(len(shape.operations.get(protocol, {}))),
            str(len(shape.paths.get(protocol, {}))),
        )
    console.print(table)

    if model is not None:
        _derive_model(corpus, model, name)

    if out is None:
        console.print(text)
        console.print("[dim]Pass --out to save this.[/dim]")
        return
    if out.exists():
        _fail(f"{out} already exists")
    out.write_text(text, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {out}")
    console.print(f"Try it: [bold]phantom-api mock serve {out}[/bold]")


def _derive_model(corpus: Path, out: Path, name: str) -> None:
    from phantom_api.mock.record.topology import derive_topology, render_scenario, slug

    topology = derive_topology(corpus)
    if not topology.entities:
        console.print("[yellow]No objects with identity were found in the recording.[/yellow]")
        console.print("[dim]Nothing to model -- the corpus can still be replayed.[/dim]")
        return

    (out / "topology").mkdir(parents=True, exist_ok=True)
    (out / "scenario").mkdir(parents=True, exist_ok=True)
    inventory = out / "topology" / "inventory.json"
    scenario = out / "scenario" / f"{slug(name)}-scenario.yaml"

    inventory.write_text(json.dumps(topology.as_dict(), indent=2), encoding="utf-8")
    scenario.write_text(render_scenario(topology, name=name), encoding="utf-8")

    table = Table(title="Object graph recovered from the recording", show_header=True)
    table.add_column("Kind")
    table.add_column("Objects", justify="right")
    for kind, count in list(topology.by_kind.items())[:12]:
        table.add_row(kind, str(count))
    console.print(table)

    edges = sum(len(t) for e in topology.entities.values() for t in e.edges.values())
    console.print(
        f"[green]Wrote[/green] {inventory} "
        f"({len(topology.entities)} objects, {edges} resolved references)"
    )
    console.print(f"[green]Wrote[/green] {scenario}")
    if topology.dangling:
        missed = sum(topology.dangling.values())
        console.print(
            f"[dim]{missed} reference(s) pointed outside the recording; "
            f"see `unresolved:` in the scenario.[/dim]"
        )


@mock_app.command()
def drift(
    corpus: Path = typer.Argument(..., help="Recorded corpus to replay."),
    target: str = typer.Option(..., "--target", help="Live base URL to compare against."),
    header: list[str] = typer.Option(
        [], "--header", "-H", help="Header sent with every call, as 'Name: value'. Repeatable."
    ),
    insecure: bool = typer.Option(
        False, "--insecure", help="Skip TLS verification against the target."
    ),
    acknowledge: bool = typer.Option(
        False,
        "--i-have-permission",
        help="Confirm you are authorised to send traffic to --target.",
    ),
) -> None:
    """Replay CORPUS against a live system and report where they disagree.

    A stale corpus passes tests the real system would fail. This resends only
    requests already in the corpus and compares response *shape*, ignoring
    timestamps, ids and other values that legitimately change every call.
    """
    from phantom_api.mock.record.drift import check_drift

    if not acknowledge:
        _fail(
            "drift sends recorded requests to a live system. "
            "Re-run with --i-have-permission to confirm you are authorised."
        )

    headers: dict[str, str] = {}
    for item in header:
        name, _, value = item.partition(":")
        if not value:
            _fail(f"malformed header {item!r}; expected 'Name: value'")
        headers[name.strip()] = value.strip()

    try:
        report = check_drift(corpus, target, verify=not insecure, headers=headers)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
        return

    console.print(report.summary())
    if report.skipped:
        console.print(f"[dim]Skipped {len(report.skipped)} exchange(s) with no endpoint.[/dim]")
    if not report.checked:
        _fail("nothing in this corpus could be replayed, so no comparison was made")
    if report.ok:
        console.print("[green]No drift[/green] -- the corpus still matches the live system.")
        return

    table = Table(title="Drift", show_header=True)
    table.add_column("Operation")
    table.add_column("Kind")
    table.add_column("Detail")
    for divergence in report.diverged:
        table.add_row(divergence.operation, divergence.kind, divergence.detail)
    console.print(table)
    raise typer.Exit(code=1)


@mock_app.command()
def verify(
    operation: str = typer.Argument(..., help="Operation name to assert on."),
    url: str = typer.Option(f"http://{DEFAULT_HOST}:{DEFAULT_MOCK_PORT}", "--url"),
    at_least: int = typer.Option(1, "--at-least"),
    at_most: int | None = typer.Option(None, "--at-most"),
) -> None:
    """Assert how many times a running mock saw OPERATION. Exits non-zero if not."""
    import httpx

    body = {"operation": operation, "at_least": at_least, "at_most": at_most}
    try:
        response = httpx.post(f"{url.rstrip('/')}/__phantom/verify", json=body, verify=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _fail(f"could not reach the mock at {url}: {exc}")
        return
    result = response.json()
    console.print(json.dumps(result, indent=2))
    if not result["satisfied"]:
        raise typer.Exit(code=1)


_STARTER_CORPUS = """\
# Replay recorded traffic. The fastest route to a working mock.
mock:
  name: my-service
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }
  protocols:
    - pack: soap
      paths: ["/**"]
  responders:
    - match: "*"
      responder: corpus
      corpus: ./corpus
      on_miss: fault
"""

_STARTER_TEMPLATE = """\
# Declarative canned responses with a dispatch strategy per operation.
mock:
  name: my-service
  listen: { host: 127.0.0.1, port: 8080 }
  protocols:
    - pack: soap
      paths: ["/**"]
  responders:
    - match: "*"
      responder: template
      operations:
        GetAccount:
          dispatch: match
          responses:
            - when: "params.accountId == '0'"
              fault: { kind: not-found, message: "Unknown account" }
            - body: |
                <accountId>{{ params.accountId }}</accountId>
                <balance>{{ fake.pyint(100, 99999) }}</balance>
                <asOf>{{ now.isoformat() }}</asOf>
"""

_STARTER_MODEL = """\
# A stateful simulation backed by a domain pack.
mock:
  name: my-service
  listen: { host: 127.0.0.1, port: 8443, tls: self-signed }
  protocols:
    - pack: soap
      paths: ["/sdk"]
      session:
        "/sdk": { transport: cookie, name: my_session }
  model:
    pack: vcenter          # see `phantom-api mock list-packs`
    scenario: ./scenario.yaml
    seed: 1234
  responders:
    - match: "*"
      responder: model
  control_plane: { enabled: true, bind: 127.0.0.1 }
"""
