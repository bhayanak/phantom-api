"""Typer CLI for phantom-api."""

from __future__ import annotations

import re
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from phantom_api import __version__
from phantom_api.constants import DEFAULT_HOST, DEFAULT_PORT
from phantom_api.models import MockSpec
from phantom_api.parsers import detect_and_parse
from phantom_api.parsers.base import ParserError

app = typer.Typer(
    name="phantom-api",
    help="Instant mock API server from an OpenAPI spec, JSON file, or Postman collection.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

_DELAY_RE = re.compile(r"^\s*(\d+)\s*(ms|s)?\s*$", re.IGNORECASE)

_METHOD_STYLES = {
    "GET": "green",
    "POST": "yellow",
    "PUT": "blue",
    "PATCH": "magenta",
    "DELETE": "red",
}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"phantom-api {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """phantom-api command line interface."""


def parse_delay(value: str) -> int:
    """Parse a delay string such as ``200ms``, ``1s``, or ``150`` into milliseconds."""
    match = _DELAY_RE.match(value)
    if not match:
        raise typer.BadParameter(f"Invalid delay {value!r}; use e.g. 200ms, 1s, or 150.")
    amount = int(match.group(1))
    unit = (match.group(2) or "ms").lower()
    return amount * 1000 if unit == "s" else amount


def _load_spec(spec: Path, source_type: str | None) -> MockSpec:
    try:
        return detect_and_parse(str(spec), source_type)
    except ParserError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _print_banner(spec: MockSpec, host: str, port: int, source: str, delay_ms: int) -> None:
    body = (
        f"[bold]http://{host}:{port}[/bold]\n"
        f"Source: {source} ({spec.source_type})\n"
        f"Routes: {spec.route_count()}   Delay: {delay_ms}ms"
    )
    console.print(Panel(body, title="phantom-api - Mock Server Running", border_style="magenta"))


def _print_routes(spec: MockSpec) -> None:
    table = Table(title=f"{spec.title} ({spec.source_type})", show_lines=False)
    table.add_column("Method", style="bold")
    table.add_column("Path")
    table.add_column("Status", justify="right")
    table.add_column("Summary", overflow="fold")
    for route in spec.routes:
        method = route.normalized_method()
        style = _METHOD_STYLES.get(method, "white")
        table.add_row(
            f"[{style}]{method}[/{style}]",
            route.path,
            str(route.status_code),
            route.summary,
        )
    console.print(table)


@app.command()
def serve(
    spec: Path = typer.Argument(..., exists=True, readable=True, help="Spec / JSON / collection."),
    type: str | None = typer.Option(
        None, "--type", "-t", help="Force source type: openapi, json, or postman."
    ),
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Bind host (localhost by default)."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Bind port."),
    delay: str = typer.Option("0ms", "--delay", "-d", help="Latency per request, e.g. 200ms."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Reload when the spec changes."),
    seed: int | None = typer.Option(None, "--seed", help="Seed for deterministic fake data."),
    cors: str = typer.Option("*", "--cors", help="Comma-separated allowed CORS origins."),
) -> None:
    """Start a mock server from SPEC."""
    from phantom_api.server.app import ServerConfig, create_app

    delay_ms = parse_delay(delay)
    mock_spec = _load_spec(spec, type)
    config = ServerConfig(
        delay_ms=delay_ms,
        cors_origins=[o.strip() for o in cors.split(",") if o.strip()],
        seed=seed,
    )
    fastapi_app = create_app(mock_spec, config)

    if watch:
        from phantom_api.server.hot_reload import attach_hot_reload

        attach_hot_reload(
            fastapi_app,
            spec,
            type,
            on_reload=lambda msg: console.print(f"[cyan]hot-reload:[/cyan] {msg}"),
        )

    _print_banner(mock_spec, host, port, spec.name, delay_ms)
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


@app.command()
def record(
    upstream: str = typer.Argument(..., help="Upstream base URL to proxy, e.g. https://api.x.com"),
    output: Path = typer.Option(Path("recordings"), "--output", "-o", help="Recordings dir."),
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Bind host."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Bind port."),
) -> None:
    """Proxy UPSTREAM and record every response to OUTPUT."""
    from phantom_api.recorder.proxy import create_proxy_app
    from phantom_api.recorder.storage import RecordingStorage

    storage = RecordingStorage(output)
    try:
        proxy_app = create_proxy_app(upstream, storage)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        Panel(
            f"Proxying [bold]{upstream}[/bold]\n"
            f"Listening on http://{host}:{port}\n"
            f"Saving recordings to {output}/",
            title="phantom-api - Recording",
            border_style="magenta",
        )
    )
    uvicorn.run(proxy_app, host=host, port=port, log_level="info")


@app.command()
def replay(
    recordings: Path = typer.Argument(
        ..., exists=True, file_okay=False, help="Recordings directory."
    ),
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Bind host."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Bind port."),
    delay: str = typer.Option("0ms", "--delay", "-d", help="Latency per request."),
) -> None:
    """Serve previously recorded responses from RECORDINGS."""
    from phantom_api.recorder.replayer import build_replay_spec, create_replay_app
    from phantom_api.server.app import ServerConfig

    delay_ms = parse_delay(delay)
    spec = build_replay_spec(recordings)
    if spec.route_count() == 0:
        console.print(f"[red]Error:[/red] no recordings found in {recordings}/")
        raise typer.Exit(code=1)

    replay_app = create_replay_app(recordings, ServerConfig(delay_ms=delay_ms))
    _print_banner(spec, host, port, str(recordings), delay_ms)
    uvicorn.run(replay_app, host=host, port=port, log_level="info")


@app.command()
def routes(
    spec: Path = typer.Argument(..., exists=True, readable=True, help="Spec file."),
    type: str | None = typer.Option(None, "--type", "-t", help="Force source type."),
) -> None:
    """List every route phantom-api would generate for SPEC."""
    mock_spec = _load_spec(spec, type)
    _print_routes(mock_spec)


@app.command()
def validate(
    spec: Path = typer.Argument(..., exists=True, readable=True, help="Spec file."),
    type: str | None = typer.Option(None, "--type", "-t", help="Force source type."),
) -> None:
    """Validate SPEC without starting a server."""
    mock_spec = _load_spec(spec, type)
    console.print(
        f"[green]OK[/green] {spec.name}: {mock_spec.source_type}, "
        f"{mock_spec.route_count()} route(s)."
    )
