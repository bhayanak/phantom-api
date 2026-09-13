"""Tests for recording by calling a list of endpoints."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from phantom_api.cli import app
from phantom_api.mock.record.crawl import crawl, expand_collections, parse_endpoints

runner = CliRunner()

LIST_BODY = json.dumps({"servers": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]})
DETAIL_BODY = json.dumps({"server": {"id": 1, "name": "a", "cores": 4}})


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock")


# -- the endpoint list -------------------------------------------------------


def test_a_plain_list_of_paths_defaults_to_get():
    parsed = parse_endpoints("/api/servers\n/api/instances\n")
    assert [(m, p) for m, p, _ in parsed] == [
        ("GET", "/api/servers"),
        ("GET", "/api/instances"),
    ]


def test_a_method_may_be_given_explicitly():
    assert parse_endpoints("POST /api/search")[0][:2] == ("POST", "/api/search")


def test_comments_and_blank_lines_are_ignored():
    text = "# a heading\n\n/api/servers   # trailing note\n\n"
    assert len(parse_endpoints(text)) == 1
    assert parse_endpoints(text)[0][1] == "/api/servers"


def test_labels_are_filesystem_safe():
    label = parse_endpoints("/api/instance-types?max=5")[0][2]
    assert " " in label, "the method stays readable"
    assert "?" not in label and "/" not in label.split(None, 1)[1]


def test_an_empty_list_yields_nothing():
    assert parse_endpoints("# only comments\n\n") == []


# -- crawling ----------------------------------------------------------------


def test_every_endpoint_is_recorded(tmp_path):
    report = crawl(
        "http://mock",
        parse_endpoints("/api/servers\n/api/instances\n"),
        tmp_path / "raw",
        client=client_for(lambda r: httpx.Response(200, text=LIST_BODY)),
    )

    assert report.recorded == 2
    manifest = json.loads((tmp_path / "raw" / "manifest.json").read_text())
    assert len(manifest["exchanges"]) == 2
    assert manifest["sanitised"] is False, "recording never claims to be safe"


def test_the_recorded_method_is_kept(tmp_path):
    crawl(
        "http://mock",
        parse_endpoints("POST /api/search\n"),
        tmp_path / "raw",
        client=client_for(lambda r: httpx.Response(200, text="{}")),
    )
    entry = json.loads((tmp_path / "raw" / "manifest.json").read_text())["exchanges"][0]
    assert entry["method"] == "POST"


def test_the_query_string_is_part_of_the_recorded_identity(tmp_path):
    """Two pages of one collection differ only there."""
    crawl(
        "http://mock",
        parse_endpoints("/api/x?page=1\n/api/x?page=2\n"),
        tmp_path / "raw",
        client=client_for(lambda r: httpx.Response(200, text="{}")),
    )
    paths = [
        e["endpoint_path"]
        for e in json.loads((tmp_path / "raw" / "manifest.json").read_text())["exchanges"]
    ]
    assert paths == ["/api/x?page=1", "/api/x?page=2"]


def test_a_failing_endpoint_does_not_stop_the_rest(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="{}")

    report = crawl(
        "http://mock",
        parse_endpoints("/api/broken\n/api/fine\n"),
        tmp_path / "raw",
        client=client_for(handler),
    )

    assert report.recorded == 1
    assert report.failed[0][0] == "/api/broken"


def test_error_responses_are_recorded_because_clients_must_handle_them(tmp_path):
    report = crawl(
        "http://mock",
        parse_endpoints("/api/nope\n"),
        tmp_path / "raw",
        client=client_for(lambda r: httpx.Response(404, text='{"msg":"no"}')),
    )
    assert report.recorded == 1
    entry = json.loads((tmp_path / "raw" / "manifest.json").read_text())["exchanges"][0]
    assert entry["status"] == 404


def test_rate_limiting_stops_the_crawl(tmp_path):
    """Continuing past a 429 is rude and produces a corpus full of errors."""
    report = crawl(
        "http://mock",
        parse_endpoints("/api/a\n/api/b\n/api/c\n"),
        tmp_path / "raw",
        client=client_for(lambda r: httpx.Response(429, text="slow down")),
    )

    assert report.recorded == 0
    assert report.skipped == ["/api/a"]
    assert not report.ok


def test_headers_are_sent_with_every_call(tmp_path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, text="{}")

    crawl(
        "http://mock",
        parse_endpoints("/api/a\n/api/b\n"),
        tmp_path / "raw",
        headers={"Authorization": "Bearer t"},
        client=client_for(handler),
    )
    assert seen == ["Bearer t", "Bearer t"]


# -- following collections ---------------------------------------------------


def test_members_of_a_collection_are_followed(tmp_path):
    raw = tmp_path / "raw"
    crawl(
        "http://mock",
        parse_endpoints("/api/servers\n"),
        raw,
        client=client_for(lambda r: httpx.Response(200, text=LIST_BODY)),
    )

    report = expand_collections(
        "http://mock",
        raw,
        client=client_for(lambda r: httpx.Response(200, text=DETAIL_BODY)),
    )

    assert report.recorded == 2
    paths = [
        e["endpoint_path"] for e in json.loads((raw / "manifest.json").read_text())["exchanges"]
    ]
    assert "/api/servers/1" in paths and "/api/servers/2" in paths


def test_a_member_is_never_fetched_twice(tmp_path):
    """Paged views list the same members; fetching each once is the point."""
    raw = tmp_path / "raw"
    crawl(
        "http://mock",
        parse_endpoints("/api/servers?page=1\n/api/servers?page=2\n"),
        raw,
        client=client_for(lambda r: httpx.Response(200, text=LIST_BODY)),
    )

    report = expand_collections(
        "http://mock", raw, client=client_for(lambda r: httpx.Response(200, text=DETAIL_BODY))
    )

    assert report.recorded == 2, "two distinct members, not four"


def test_a_member_path_drops_the_collection_query(tmp_path):
    raw = tmp_path / "raw"
    crawl(
        "http://mock",
        parse_endpoints("/api/servers?page=1\n"),
        raw,
        client=client_for(lambda r: httpx.Response(200, text=LIST_BODY)),
    )
    expand_collections(
        "http://mock", raw, client=client_for(lambda r: httpx.Response(200, text=DETAIL_BODY))
    )

    paths = [
        e["endpoint_path"] for e in json.loads((raw / "manifest.json").read_text())["exchanges"]
    ]
    assert "/api/servers/1" in paths
    assert not any("?" in p and "/" in p.split("?", 1)[1] for p in paths)


def test_following_is_limited(tmp_path):
    raw = tmp_path / "raw"
    many = json.dumps({"things": [{"id": n} for n in range(20)]})
    crawl(
        "http://mock",
        parse_endpoints("/api/things\n"),
        raw,
        client=client_for(lambda r: httpx.Response(200, text=many)),
    )

    report = expand_collections(
        "http://mock", raw, limit=3, client=client_for(lambda r: httpx.Response(200, text="{}"))
    )
    assert report.recorded == 3


def test_nothing_to_follow_is_not_an_error(tmp_path):
    raw = tmp_path / "raw"
    crawl(
        "http://mock",
        parse_endpoints("/api/health\n"),
        raw,
        client=client_for(lambda r: httpx.Response(200, text='{"status":"ok"}')),
    )
    assert expand_collections("http://mock", raw).recorded == 0


def test_following_a_corpus_without_a_manifest_is_reported(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(FileNotFoundError):
        expand_collections("http://mock", tmp_path / "nope")


# -- the command line --------------------------------------------------------


def test_record_requires_acknowledgement(tmp_path):
    endpoints = tmp_path / "e.txt"
    endpoints.write_text("/api/a\n")
    result = runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://real.example.com",
            "--out",
            str(tmp_path / "raw"),
            "--endpoints",
            str(endpoints),
        ],
    )
    assert result.exit_code != 0
    assert "i-understand" in result.output


def test_record_refuses_loopback(tmp_path):
    result = runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://127.0.0.1:9000",
            "--out",
            str(tmp_path / "raw"),
            "--i-understand-this-hits-production",
        ],
    )
    assert result.exit_code != 0
    assert "loopback" in result.output


def test_record_rejects_a_missing_endpoint_list(tmp_path):
    result = runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://real.example.com",
            "--out",
            str(tmp_path / "raw"),
            "--endpoints",
            str(tmp_path / "absent.txt"),
            "--i-understand-this-hits-production",
        ],
    )
    assert result.exit_code != 0
    assert "endpoint list" in result.output


def test_record_rejects_an_empty_endpoint_list(tmp_path):
    endpoints = tmp_path / "e.txt"
    endpoints.write_text("# nothing here\n")
    result = runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://real.example.com",
            "--out",
            str(tmp_path / "raw"),
            "--endpoints",
            str(endpoints),
            "--i-understand-this-hits-production",
        ],
    )
    assert result.exit_code != 0
    assert "no endpoints" in result.output


def test_record_rejects_a_malformed_header(tmp_path):
    endpoints = tmp_path / "e.txt"
    endpoints.write_text("/api/a\n")
    result = runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://real.example.com",
            "--out",
            str(tmp_path / "raw"),
            "--endpoints",
            str(endpoints),
            "--header",
            "no-colon",
            "--i-understand-this-hits-production",
        ],
    )
    assert result.exit_code != 0
    assert "malformed header" in result.output


def stub_crawl(monkeypatch, recorded=2, failed=(), skipped=()):
    """Replace the network call so the command wiring can be tested on its own."""
    import phantom_api.mock.record.crawl as module

    seen: dict = {}

    def fake_crawl(base, endpoints, out, *, headers=None, verify=True, **kwargs):
        seen.update(base=base, endpoints=endpoints, out=out, headers=headers, verify=verify)
        report = module.CrawlReport(recorded=recorded)
        report.failed.extend(failed)
        report.skipped.extend(skipped)
        return report

    def fake_expand(base, corpus, **kwargs):
        seen["followed"] = kwargs.get("limit")
        return module.CrawlReport(recorded=1)

    monkeypatch.setattr(module, "crawl", fake_crawl)
    monkeypatch.setattr(module, "expand_collections", fake_expand)
    return seen


def record_cli(tmp_path, *extra):
    endpoints = tmp_path / "e.txt"
    endpoints.write_text("/api/a\n/api/b\n")
    return runner.invoke(
        app,
        [
            "mock",
            "record",
            "https://real.example.com",
            "--out",
            str(tmp_path / "raw"),
            "--endpoints",
            str(endpoints),
            "--i-understand-this-hits-production",
            *extra,
        ],
    )


def test_recording_endpoints_reports_what_it_captured(tmp_path, monkeypatch):
    seen = stub_crawl(monkeypatch)

    result = record_cli(tmp_path, "--header", "Authorization: Bearer t")

    assert result.exit_code == 0, result.output
    assert "Recorded 2" in result.output
    assert "sanitize" in result.output, "the next step must be named"
    assert seen["headers"] == {"Authorization": "Bearer t"}
    assert len(seen["endpoints"]) == 2


def test_following_collections_is_requested_when_asked(tmp_path, monkeypatch):
    seen = stub_crawl(monkeypatch)
    record_cli(tmp_path, "--follow", "5")
    assert seen["followed"] == 5


def test_insecure_disables_verification(tmp_path, monkeypatch):
    seen = stub_crawl(monkeypatch)
    record_cli(tmp_path, "--insecure")
    assert seen["verify"] is False


def test_recording_nothing_is_a_failure(tmp_path, monkeypatch):
    stub_crawl(monkeypatch, recorded=0, failed=[("/api/a", "refused")])

    result = record_cli(tmp_path)

    assert result.exit_code != 0
    assert "refused" in result.output


def test_rate_limiting_is_called_out(tmp_path, monkeypatch):
    stub_crawl(monkeypatch, recorded=1, skipped=["/api/b"])
    assert "rate limited" in record_cli(tmp_path).output
