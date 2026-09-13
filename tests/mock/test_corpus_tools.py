"""Tests for corpus maintenance: sanitising, deriving a config, drift."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from phantom_api.cli import app
from phantom_api.mock.record.derive import (
    collapse,
    inspect_corpus,
    render_config,
    templatise,
)
from phantom_api.mock.record.drift import _shape, check_drift
from phantom_api.mock.record.sanitize import (
    Pseudonymiser,
    Rules,
    check_corpus,
    sanitise_corpus,
    scrub_names,
    scrub_option_values,
    scrub_paths,
    scrub_terms,
)

runner = CliRunner()

SOAP_RESPONSE = """<?xml version="1.0"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"><Body>
<RetrievePropertiesResponse><returnval>
<obj type="HostSystem">host-14</obj>
<propSet><name>name</name><val>esx-prod-07.corp.example.net</val></propSet>
<hostName>esx-prod-07</hostName>
<serialNumber>CZ3421XYZ9</serialNumber>
<vmPathName>[datastore1] Payroll/Payroll.vmx</vmPathName>
<ipAddress>10.24.8.51</ipAddress>
<macAddress>00:50:56:9a:3f:21</macAddress>
<uuid>4c4c4544-0043-5a10-8054-b7c04f325931</uuid>
</returnval></RetrievePropertiesResponse></Body></Envelope>
"""

SOAP_REQUEST = """<?xml version="1.0"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
<Header><vcSessionCookie>abc</vcSessionCookie></Header>
<Body><RetrieveProperties xmlns="urn:vim25"><_this type="PropertyCollector">p</_this>
</RetrieveProperties></Body></Envelope>
"""

REST_REQUEST = '{"method": "POST", "payload": {"filter": "all"}}'
REST_RESPONSE = '{"value": [{"name": "prod-db-01", "host_name": "db.corp.example.net"}]}'


def build_corpus(root: Path) -> Path:
    """A small two-protocol corpus in the on-disk layout the engine expects."""
    corpus = root / "corpus"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "rest").mkdir(parents=True)

    (corpus / "soap" / "001-RetrieveProperties.req.xml").write_text(SOAP_REQUEST)
    (corpus / "soap" / "001-RetrieveProperties.resp.xml").write_text(SOAP_RESPONSE)
    (corpus / "rest" / "002-list.req.json").write_text(REST_REQUEST)
    (corpus / "rest" / "002-list.resp.json").write_text(REST_RESPONSE)

    manifest = {
        "exchanges": [
            {
                "stem": "001-RetrieveProperties",
                "label": "RetrieveProperties",
                "transport": "soap",
                "status": 200,
                "endpoint_path": "/sdk",
                "request": "soap/001-RetrieveProperties.req.xml",
                "response": "soap/001-RetrieveProperties.resp.xml",
            },
            {
                "stem": "002-list",
                "label": "list",
                "transport": "rest",
                "status": 200,
                "endpoint_path": "/rest/vcenter/vm?action=list",
                "request": "rest/002-list.req.json",
                "response": "rest/002-list.resp.json",
            },
        ]
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    return corpus


# -- pseudonymisation --------------------------------------------------------


def test_replacements_are_stable_and_shaped_like_the_original():
    pseudo = Pseudonymiser()
    first = pseudo.alloc("ipv4", "10.24.8.51")
    assert pseudo.alloc("ipv4", "10.24.8.51") == first, "same input must map to same output"
    assert first.startswith(("192.0.2.", "198.51.100.", "203.0.113.")), "must stay an address"

    mac = pseudo.alloc("mac", "00:50:56:9a:3f:21")
    assert mac.startswith("02:00:5e:"), "must stay a locally-administered MAC"
    assert len(mac.split(":")) == 6


def test_structural_addresses_survive():
    """Rewriting a loopback or broadcast address corrupts routing data."""
    text = "<a>127.0.0.1</a><b>255.255.255.0</b><c>0.0.0.0</c>"
    assert Pseudonymiser().scrub_patterns(text) == text


def test_timestamps_and_json_numbers_are_not_mistaken_for_addresses():
    """The colons in a time and the `:` before a number are not an IPv6 address."""
    text = '<t>2024-03-11T00:14:03Z</t>{"memoryMB":8192,"cpu":4}'
    assert Pseudonymiser().scrub_patterns(text) == text


def test_version_numbers_are_not_mistaken_for_addresses():
    """`8.0.2.1` is a valid dotted quad, so only its context tells them apart."""
    text = '<version>8.0.2.1</version><apiVersion>8.0.2.0</apiVersion>{"build":"2.4.6.8"}'
    assert Pseudonymiser().scrub_patterns(text) == text


def test_an_address_next_to_a_version_is_still_replaced():
    out = Pseudonymiser().scrub_patterns("<version>8.0.2.1</version><ip>10.24.8.51</ip>")
    assert "8.0.2.1" in out
    assert "10.24.8.51" not in out


def test_ipv6_with_a_double_colon_is_replaced():
    out = Pseudonymiser().scrub_patterns("<a>2001:db8:85a3::8a2e:370:7334</a>")
    assert "2001:db8" not in out


# -- individual passes -------------------------------------------------------


def test_json_renames_only_apply_to_values():
    """An object named `vm` must not rewrite every `"vm"` key in the document."""
    text = '{"vm": "vm", "other": ["vm"]}'
    out = scrub_names(text, {"vm": "VM-0001"}, is_json=True)
    assert out.startswith('{"vm":'), "the key must survive"
    assert '"VM-0001"' in out


def test_vendor_terms_match_on_non_alphanumeric_boundaries():
    """`_` is a word character, so \\b would miss ACME_NS204i."""
    out = scrub_terms("<a>ACME_NS204i</a><b>acmetrics.enabled</b>", ("acme",))
    assert "ACME_NS204i" not in out
    assert "acmetrics.enabled" in out, "a substring inside another word is not the term"


def test_a_datastore_prefix_survives_but_the_path_does_not():
    """The prefix is structural; the folder and file names identify the customer."""
    out = scrub_paths(
        "<vmPathName>[shared-01] Payroll/Payroll.vmx</vmPathName>",
        Pseudonymiser(),
        Rules(),
    )
    assert "[shared-01]" in out
    assert "Payroll" not in out
    assert out.endswith(".vmx</vmPathName>"), "the extension must survive"


def test_traversal_specs_are_not_mistaken_for_file_paths():
    """`<path>` names an edge to follow; rewriting it breaks every request."""
    text = "<selectSet><path>childEntity</path></selectSet>"
    assert scrub_paths(text, Pseudonymiser(), Rules()) == text


def test_free_form_config_keeps_its_keys_and_loses_its_values():
    text = "<extraConfig><key>guestinfo.owner</key><value>Finance Dept</value></extraConfig>"
    out = scrub_option_values(text, Pseudonymiser())

    assert "<key>guestinfo.owner</key>" in out, "consumers match on the key"
    assert "Finance Dept" not in out


def test_numeric_config_values_are_left_alone():
    text = "<option><key>mem.min</key><value>2048</value></option>"
    assert scrub_option_values(text, Pseudonymiser()) == text


def test_rules_extend_the_defaults_rather_than_replacing_them(tmp_path):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"elements": {"tenantName": "tenant"}, "domain": "x.test"}))

    rules = Rules.load(rules_file)

    assert rules.elements["tenantName"] == "tenant"
    assert "hostName" in rules.elements, "built-in rules must survive"
    assert rules.domain == "x.test"


def test_rules_default_when_no_file_is_given():
    assert Rules.load(None).domain == "lab.example.com"


def test_rules_can_be_written_as_yaml(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("domain: y.test\nterms: [acme]\nrename_objects: false\n")

    rules = Rules.load(rules_file)

    assert rules.domain == "y.test"
    assert "acme" in rules.terms
    assert rules.rename_objects is False


def test_disabling_renaming_leaves_object_names_alone(tmp_path):
    corpus = build_corpus(tmp_path)
    rules = Rules(rename_objects=False)

    report = sanitise_corpus(corpus, tmp_path / "clean", rules=rules)

    assert report.renamed == 0


def test_longer_names_are_replaced_before_their_prefixes():
    """Otherwise `prod` would corrupt `prod-db-01` into `X-db-01`."""
    renames = {"prod-db-01": "VM-0001", "prod": "VM-0002"}
    out = scrub_names("<name>prod-db-01</name>", renames, is_json=False)
    assert out == "<name>VM-0001</name>"


def test_short_and_reserved_names_are_never_renamed(tmp_path):
    """An object innocently called `vm` would rewrite the whole corpus."""
    from phantom_api.mock.record.sanitize import build_rename_table

    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.resp.xml").write_text(
        SOAP_RESPONSE.replace("esx-prod-07.corp.example.net", "vm")
    )

    assert build_rename_table(corpus, Rules()) == {}


# -- whole-corpus ------------------------------------------------------------


def test_sanitise_removes_identity_and_keeps_the_corpus_usable(tmp_path):
    corpus = build_corpus(tmp_path)
    rules = Rules().with_terms(["corp"])

    report = sanitise_corpus(corpus, tmp_path / "clean", rules=rules)

    assert report.ok, report.problems()
    assert report.exchanges == 2

    soap = (tmp_path / "clean" / "soap" / "001-RetrieveProperties.resp.xml").read_text()
    for secret in ("esx-prod-07", "CZ3421XYZ9", "10.24.8.51", "00:50:56:9a:3f:21"):
        assert secret not in soap, f"{secret} survived"
    assert "<hostName>" in soap, "structure must survive"

    rest = (tmp_path / "clean" / "rest" / "002-list.resp.json").read_text()
    assert "db.corp.example.net" not in rest
    assert json.loads(rest)["value"], "must still be valid JSON"


def test_sanitise_writes_a_protected_mapping(tmp_path):
    corpus = build_corpus(tmp_path)
    mapping = tmp_path / "secret" / "mapping.json"

    sanitise_corpus(corpus, tmp_path / "clean", mapping_path=mapping)

    assert mapping.exists()
    assert mapping.stat().st_mode & 0o077 == 0, "must not be readable by others"
    assert "values" in json.loads(mapping.read_text())


def test_a_leaked_term_is_reported(tmp_path):
    """The scan is the last line of defence, so check it against raw recordings."""
    corpus = build_corpus(tmp_path)
    report = check_corpus(corpus, terms=("payroll",))

    assert not report.ok
    assert any("forbidden" in p for p in report.problems())


def test_a_clean_corpus_reports_no_leaks(tmp_path):
    corpus = build_corpus(tmp_path)
    assert check_corpus(corpus, terms=("nothing-here",)).ok


def test_an_unpaired_file_fails_the_check(tmp_path):
    """A request without its response cannot be replayed."""
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "900-orphan.req.xml").write_text(SOAP_REQUEST)

    report = sanitise_corpus(corpus, tmp_path / "clean", rules=Rules())
    # The orphan is not in the manifest, so it is not copied -- check the source.
    assert check_corpus(corpus).orphans == ["soap/900-orphan.req.xml"]
    assert report.exchanges == 2


def test_broken_syntax_is_detected(tmp_path):
    corpus = build_corpus(tmp_path)
    (corpus / "rest" / "002-list.resp.json").write_text('{"value": ')

    report = check_corpus(corpus)

    assert not report.ok
    assert any("no longer parses" in p for p in report.problems())


def test_missing_manifest_is_reported(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        sanitise_corpus(tmp_path / "empty", tmp_path / "out")


# -- deriving a config -------------------------------------------------------


def test_inspect_reads_protocols_and_operations_from_the_wire(tmp_path):
    shape = inspect_corpus(build_corpus(tmp_path))

    assert shape.exchanges == 2
    assert shape.protocols["soap"] == 1
    assert shape.protocols["openapi"] == 1
    # Read from the envelope, not from the recorded label.
    assert "RetrieveProperties" in shape.operations["soap"]


def test_soap_headers_are_attributed_to_the_path_that_carried_them(tmp_path):
    shape = inspect_corpus(build_corpus(tmp_path))
    assert shape.path_headers["/sdk"]["vcSessionCookie"] == 1


def test_instance_identifiers_collapse_to_a_wildcard():
    assert templatise("/api/vcenter/vm/vm-2433/guest") == "/api/vcenter/vm/*/guest"
    assert templatise("/rest/thing/42") == "/rest/thing/*"
    assert templatise("/rest/x?a=b") == "/rest/x", "a query is not part of the pattern"


def test_sibling_paths_collapse_to_the_narrowest_pattern():
    """Identifiers collapse first, so siblings often reduce to a single template."""
    assert sorted(collapse([f"/rest/a/{n}" for n in range(6)] + ["/api/b/1"])) == [
        "/api/b/*",
        "/rest/a/*",
    ]


def test_differing_paths_under_one_root_collapse_to_a_glob():
    paths = ["/rest/a/x", "/rest/b/y", "/rest/c/z", "/api/only"]
    assert sorted(collapse(paths)) == ["/api/only", "/rest/**"]


def test_derived_config_serves_what_was_recorded(tmp_path):
    shape = inspect_corpus(build_corpus(tmp_path))
    text = render_config(shape, name="demo", corpus_path="./corpus")

    import yaml

    parsed = yaml.safe_load(text)["mock"]
    assert parsed["name"] == "demo"
    packs = {p["pack"] for p in parsed["protocols"]}
    assert packs == {"soap", "openapi"}
    assert parsed["responders"][0]["responder"] == "corpus"

    soap = next(p for p in parsed["protocols"] if p["pack"] == "soap")
    assert soap["session"]["/sdk"]["transport"] == "soap-header"
    assert soap["session"]["/sdk"]["name"] == "vcSessionCookie"


def test_deriving_from_a_corpus_without_a_manifest_is_reported(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(FileNotFoundError):
        inspect_corpus(tmp_path / "nope")


def test_a_malformed_recording_falls_back_to_its_label(tmp_path):
    """One unreadable file must not stop the rest of the corpus being described."""
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.req.xml").write_text("<not xml")

    shape = inspect_corpus(corpus)

    assert shape.exchanges == 2
    assert "RetrieveProperties" in shape.operations["soap"], "the label is the fallback"


def test_a_recording_with_no_body_element_falls_back_to_its_label(tmp_path):
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.req.xml").write_text(
        '<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"><Header/></Envelope>'
    )
    assert "RetrieveProperties" in inspect_corpus(corpus).operations["soap"]


def test_a_missing_request_file_does_not_stop_the_scan(tmp_path):
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.req.xml").unlink()
    assert inspect_corpus(corpus).exchanges == 2


def test_faults_are_called_out_in_the_generated_config(tmp_path):
    corpus = build_corpus(tmp_path)
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest["exchanges"][0]["status"] = 500
    (corpus / "manifest.json").write_text(json.dumps(manifest))

    shape = inspect_corpus(corpus)
    text = render_config(shape, name="demo")

    assert shape.faults == 1
    assert "fault response(s)" in text


def test_an_empty_corpus_still_produces_a_usable_config():
    import yaml

    from phantom_api.mock.record.derive import CorpusShape

    parsed = yaml.safe_load(render_config(CorpusShape(), name="empty"))["mock"]

    assert parsed["protocols"][0]["pack"] == "raw"
    assert parsed["protocols"][0]["paths"] == ["/**"]


# -- drift -------------------------------------------------------------------


def test_shape_ignores_values_that_change_every_call():
    a = "<r><currentTime>2024-01-01T00:00:00Z</currentTime><name>x</name></r>"
    b = "<r><currentTime>2025-06-06T12:00:00Z</currentTime><name>y</name></r>"
    assert _shape(a) == _shape(b), "a new timestamp is not drift"


def test_shape_notices_a_removed_field():
    assert _shape("<r><a/><b/></r>") != _shape("<r><a/></r>")


def test_drift_reports_an_unreachable_target(tmp_path):
    corpus = build_corpus(tmp_path)
    report = check_drift(corpus, "http://127.0.0.1:1", timeout=0.3)

    assert not report.ok
    assert report.diverged[0].kind == "unreachable"


def _client_returning(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock")


def test_drift_is_quiet_when_the_corpus_still_matches(tmp_path):
    corpus = build_corpus(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        body = SOAP_RESPONSE if request.url.path == "/sdk" else REST_RESPONSE
        return httpx.Response(200, text=body)

    report = check_drift(corpus, "http://mock", client=_client_returning(handler))

    assert report.ok, [d.detail for d in report.diverged]
    assert report.matched == 2


def test_drift_reports_a_field_the_live_system_no_longer_returns(tmp_path):
    corpus = build_corpus(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sdk":
            return httpx.Response(200, text=SOAP_RESPONSE.replace("<hostName>esx-prod-07", "<x>y"))
        return httpx.Response(200, text=REST_RESPONSE)

    report = check_drift(corpus, "http://mock", client=_client_returning(handler))

    assert not report.ok
    assert report.diverged[0].kind == "shape"
    assert "hostName" in report.diverged[0].detail


def test_drift_reports_a_changed_status(tmp_path):
    corpus = build_corpus(tmp_path)
    handler = lambda request: httpx.Response(503, text="down")  # noqa: E731

    report = check_drift(corpus, "http://mock", client=_client_returning(handler))

    assert {d.kind for d in report.diverged} == {"status"}
    assert "recorded 200, live 503" in report.diverged[0].detail


def test_drift_summary_counts_everything(tmp_path):
    corpus = build_corpus(tmp_path)
    handler = lambda request: httpx.Response(200, text=SOAP_RESPONSE)  # noqa: E731

    report = check_drift(corpus, "http://mock", client=_client_returning(handler))

    assert report.summary() == "2 checked, 1 match, 1 diverged, 0 skipped"


def test_a_request_recorded_with_two_outcomes_accepts_either(tmp_path):
    """A login captured both succeeding and failing is not drift either way."""
    corpus = build_corpus(tmp_path)
    manifest = json.loads((corpus / "manifest.json").read_text())
    failure = dict(manifest["exchanges"][0])
    failure.update(
        {
            "stem": "003-RetrieveProperties-denied",
            "status": 500,
            "response": "soap/003-RetrieveProperties-denied.resp.xml",
        }
    )
    (corpus / "soap" / "003-RetrieveProperties-denied.resp.xml").write_text(
        "<Envelope><Body><Fault><faultstring>denied</faultstring></Fault></Body></Envelope>"
    )
    manifest["exchanges"].append(failure)
    (corpus / "manifest.json").write_text(json.dumps(manifest))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sdk":
            return httpx.Response(200, text=SOAP_RESPONSE)
        return httpx.Response(200, text=REST_RESPONSE)

    report = check_drift(corpus, "http://mock", client=_client_returning(handler))

    assert report.ok, [d.detail for d in report.diverged]
    assert report.checked == 2, "the duplicate request is sent once, not twice"


# -- the command line --------------------------------------------------------


def test_sanitize_command_writes_a_clean_corpus(tmp_path):
    corpus = build_corpus(tmp_path)
    result = runner.invoke(app, ["mock", "sanitize", str(corpus), "--out", str(tmp_path / "clean")])

    assert result.exit_code == 0, result.output
    assert "Checks passed" in result.output
    assert (tmp_path / "clean" / "manifest.json").exists()


def test_sanitize_command_removes_a_listed_term(tmp_path):
    corpus = build_corpus(tmp_path)
    terms = tmp_path / "terms.txt"
    terms.write_text("# comments and blanks are ignored\n\npayroll\n")

    result = runner.invoke(
        app,
        [
            "mock",
            "sanitize",
            str(corpus),
            "--out",
            str(tmp_path / "clean"),
            "--terms-file",
            str(terms),
        ],
    )

    assert result.exit_code == 0, result.output
    body = (tmp_path / "clean" / "soap" / "001-RetrieveProperties.resp.xml").read_text()
    assert "Payroll" not in body, "a listed term must not survive, in any case"


def test_sanitize_command_fails_on_a_corpus_it_would_break(tmp_path):
    """A non-zero exit is what stops an unusable corpus reaching a commit."""
    corpus = build_corpus(tmp_path)
    (corpus / "rest" / "002-list.resp.json").write_text('{"value": ')

    result = runner.invoke(app, ["mock", "sanitize", str(corpus), "--out", str(tmp_path / "clean")])

    assert result.exit_code != 0
    assert "no longer parses" in result.output


def test_sanitize_command_rejects_a_directory_that_is_not_a_corpus(tmp_path):
    (tmp_path / "nope").mkdir()
    result = runner.invoke(
        app, ["mock", "sanitize", str(tmp_path / "nope"), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code != 0
    assert "manifest.json" in result.output


def test_derive_command_prints_a_config_by_default(tmp_path):
    corpus = build_corpus(tmp_path)
    result = runner.invoke(app, ["mock", "derive", str(corpus)])

    assert result.exit_code == 0, result.output
    assert "mock:" in result.output
    assert "responder: corpus" in result.output


def test_derive_command_writes_a_config_that_validates(tmp_path):
    corpus = build_corpus(tmp_path)
    config = tmp_path / "phantom.yaml"

    written = runner.invoke(app, ["mock", "derive", str(corpus), "--out", str(config)])
    assert written.exit_code == 0, written.output

    validated = runner.invoke(app, ["mock", "validate", str(config)])
    assert validated.exit_code == 0, validated.output


def test_derive_command_refuses_to_overwrite(tmp_path):
    corpus = build_corpus(tmp_path)
    config = tmp_path / "phantom.yaml"
    config.write_text("existing")

    result = runner.invoke(app, ["mock", "derive", str(corpus), "--out", str(config)])

    assert result.exit_code != 0
    assert config.read_text() == "existing"


def test_drift_refuses_without_explicit_permission(tmp_path):
    """It sends traffic to a live system, so consent is not a default."""
    corpus = build_corpus(tmp_path)
    result = runner.invoke(app, ["mock", "drift", str(corpus), "--target", "https://x"])

    assert result.exit_code != 0
    assert "permission" in result.output


def test_drift_command_reports_divergence(tmp_path):
    corpus = build_corpus(tmp_path)
    result = runner.invoke(
        app,
        [
            "mock",
            "drift",
            str(corpus),
            "--target",
            "http://127.0.0.1:1",
            "--i-have-permission",
        ],
    )
    assert result.exit_code == 1
    assert "unreachable" in result.output


# -- secrets and free-form text ----------------------------------------------


def test_a_field_named_like_a_credential_is_scrubbed():
    """A password has no distinguishing shape, so the field name is the signal."""
    from phantom_api.mock.record.sanitize import scrub_secrets

    text = '{"sshPassword": "hunter2", "sshPasswordHash": "ab12", "name": "web-01"}'
    out = scrub_secrets(text, is_json=True)

    assert "hunter2" not in out
    assert "ab12" not in out
    assert '"web-01"' in out, "ordinary fields must survive"
    assert json.loads(out)["sshPassword"] == "[scrubbed]"


@pytest.mark.parametrize(
    "field",
    ["apiKey", "api_key", "clientSecret", "accessToken", "privateKey", "passphrase"],
)
def test_credential_field_names_are_recognised_in_any_form(field):
    from phantom_api.mock.record.sanitize import scrub_secrets

    out = scrub_secrets(f'{{"{field}": "s3cret"}}', is_json=True)
    assert "s3cret" not in out


def test_a_scrubbed_credential_is_never_a_plausible_fake():
    """Nothing downstream should be able to mistake it for a usable value."""
    from phantom_api.mock.record.sanitize import SECRET_PLACEHOLDER, scrub_secrets

    out = scrub_secrets('{"password": "x"}', is_json=True)
    assert SECRET_PLACEHOLDER in out


def test_credentials_in_xml_elements_are_scrubbed():
    from phantom_api.mock.record.sanitize import scrub_secrets

    out = scrub_secrets(
        "<config><password>hunter2</password><name>x</name></config>", is_json=False
    )
    assert "hunter2" not in out
    assert "<name>x</name>" in out


def test_free_form_text_is_replaced_wholesale():
    """A real capture had an administrator password inside cloud-init user data."""
    from phantom_api.mock.record.sanitize import Pseudonymiser, Rules, scrub_freeform

    text = '{"userData": "#cloud-config\\npassword: hunter2\\nnameserver 10.20.30.40"}'
    out = scrub_freeform(text, Pseudonymiser(), Rules(), is_json=True)

    assert "hunter2" not in out
    assert "10.20.30.40" not in out
    assert json.loads(out)["userData"].startswith("text-")


def test_free_form_xml_elements_are_replaced_too():
    from phantom_api.mock.record.sanitize import Pseudonymiser, Rules, scrub_freeform

    out = scrub_freeform(
        "<vm><script>rm -rf /</script></vm>", Pseudonymiser(), Rules(), is_json=False
    )
    assert "rm -rf" not in out
    assert "<script>" in out, "the element must survive"


def test_a_term_in_a_json_key_is_left_alone():
    """Renaming a key changes the shape clients parse, which no parse check sees."""
    text = '{"currentUsage": {"acme": 3}, "vendor": "acme"}'
    out = scrub_terms(text, ("acme",), is_json=True)

    assert json.loads(out)["currentUsage"]["acme"] == 3, "the key must survive"
    assert json.loads(out)["vendor"] != "acme", "the value must not"


def test_a_leak_in_a_key_is_reported_differently_from_one_in_a_value(tmp_path):
    from phantom_api.mock.record.sanitize import find_leaks

    corpus = build_corpus(tmp_path)
    (corpus / "rest" / "002-list.resp.json").write_text('{"acme": 1}')

    leaks = find_leaks(corpus, ("acme",))

    assert leaks and all("field name" in leak for leak in leaks)


def test_an_empty_request_body_is_not_a_parse_failure(tmp_path):
    """A GET has no body, and recording one still writes the file."""
    from phantom_api.mock.record.sanitize import find_unparseable

    corpus = build_corpus(tmp_path)
    (corpus / "rest" / "002-list.req.json").write_text("")

    assert find_unparseable(corpus) == []


def test_a_long_decimal_is_not_mistaken_for_a_world_wide_name():
    """`89.2113888559807235` is a CPU reading, not a WWN."""
    text = '{"cpuUsage": 89.2113888559807235, "wwn": "20000000c9abcdef"}'
    out = Pseudonymiser().scrub_patterns(text)

    assert json.loads(out)["cpuUsage"] == 89.2113888559807235
    assert json.loads(out)["wwn"] != "20000000c9abcdef"


def test_a_licence_key_keeps_its_shape():
    out = Pseudonymiser().alloc("licence", "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
    assert len(out.split("-")) == 5
    assert all(len(part) == 5 for part in out.split("-"))


def test_a_world_wide_name_keeps_its_shape():
    assert len(Pseudonymiser().alloc("wwn", "20000000c9abcdef")) == 16


def test_host_names_land_in_the_configured_domain():
    assert Pseudonymiser("x.test").alloc("fqdn", "real.corp.net").endswith(".x.test")


def test_schema_urls_are_not_treated_as_host_names():
    """Rewriting a namespace URL breaks every document that references it."""
    text = "<a xmlns='http://www.w3.org/2001/XMLSchema'>x</a>"
    assert "w3.org" in Pseudonymiser().scrub_patterns(text)


def test_product_standard_values_are_kept():
    from phantom_api.mock.record.sanitize import Rules, scrub_elements

    text = "<vendor>VMware, Inc.</vendor><hostName>real-host</hostName>"
    out = scrub_elements(text, Pseudonymiser(), Rules())

    assert "VMware, Inc." in out
    assert "real-host" not in out


def test_a_path_without_a_datastore_prefix_is_still_replaced():
    from phantom_api.mock.record.sanitize import Rules, scrub_paths

    out = scrub_paths("<fileName>/var/lib/secret.vmdk</fileName>", Pseudonymiser(), Rules())
    assert "secret" not in out


def test_checking_a_corpus_without_a_manifest_is_reported(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(FileNotFoundError):
        check_corpus(tmp_path / "nope")


def test_derive_names_a_single_protocol_when_only_one_is_present(tmp_path):
    corpus = build_corpus(tmp_path)
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest["exchanges"] = [e for e in manifest["exchanges"] if e["transport"] == "rest"]
    (corpus / "manifest.json").write_text(json.dumps(manifest))

    import yaml

    shape = inspect_corpus(corpus)
    parsed = yaml.safe_load(render_config(shape, name="rest-only"))["mock"]

    assert [p["pack"] for p in parsed["protocols"]] == ["openapi"]
    assert "session" not in parsed["protocols"][0], "REST paths need no session block"


def test_a_soap_path_without_a_header_is_given_a_cookie_rule(tmp_path):
    corpus = build_corpus(tmp_path)
    plain = SOAP_REQUEST.replace(
        "<Header><vcSessionCookie>abc</vcSessionCookie></Header>", "<Header/>"
    )
    (corpus / "soap" / "001-RetrieveProperties.req.xml").write_text(plain)

    import yaml

    shape = inspect_corpus(corpus)
    shape.session_operations.append("Login")
    soap = next(
        p
        for p in yaml.safe_load(render_config(shape, name="x"))["mock"]["protocols"]
        if p["pack"] == "soap"
    )
    assert soap["session"]["/sdk"]["transport"] == "cookie"


def test_an_unreadable_response_does_not_stop_the_scan(tmp_path):
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.req.xml").write_text("<broken")
    assert inspect_corpus(corpus).exchanges == 2


def test_object_names_are_renamed_by_kind(tmp_path):
    """Names are harvested from responses, so the mapping reflects what is there."""
    from phantom_api.mock.record.sanitize import build_rename_table

    corpus = tmp_path / "c"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "soap" / "a.resp.xml").write_text(
        "<R>"
        '<returnval><obj type="HostSystem">h-1</obj>'
        "<propSet><name>name</name><val>esx-prod-01.corp.net</val></propSet></returnval>"
        '<returnval><obj type="VirtualMachine">v-1</obj>'
        "<propSet><name>name</name><val>payroll-server</val></propSet></returnval>"
        '<returnval><obj type="Datacenter">d-1</obj>'
        "<propSet><name>name</name><val>London-DC</val></propSet></returnval>"
        "<spec><name>Production-PortGroup</name></spec>"
        "</R>"
    )

    renames = build_rename_table(corpus, Rules())

    assert renames["esx-prod-01.corp.net"].startswith("esx-01.")
    assert renames["payroll-server"] == "VM-0001"
    assert renames["London-DC"] == "DC-01"
    assert renames["Production-PortGroup"] == "PG-01"


def test_renaming_is_ordered_longest_first(tmp_path):
    """Otherwise a short name corrupts every longer name containing it."""
    from phantom_api.mock.record.sanitize import build_rename_table

    corpus = tmp_path / "c"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "soap" / "a.resp.xml").write_text(
        "<R>"
        '<returnval><obj type="VirtualMachine">v-1</obj>'
        "<propSet><name>name</name><val>prod</val></propSet></returnval>"
        '<returnval><obj type="VirtualMachine">v-2</obj>'
        "<propSet><name>name</name><val>prod-database-01</val></propSet></returnval>"
        "</R>"
    )

    keys = list(build_rename_table(corpus, Rules()))
    assert keys == sorted(keys, key=len, reverse=True)


def test_a_manifest_entry_with_a_missing_file_is_skipped(tmp_path):
    corpus = build_corpus(tmp_path)
    (corpus / "soap" / "001-RetrieveProperties.req.xml").unlink()

    report = sanitise_corpus(corpus, tmp_path / "clean")

    assert report.ok, report.problems()
    assert report.exchanges == 2


def test_sanitising_replaces_an_existing_destination(tmp_path):
    corpus = build_corpus(tmp_path)
    dest = tmp_path / "clean"
    dest.mkdir()
    (dest / "stale.json").write_text("{}")

    sanitise_corpus(corpus, dest)

    assert not (dest / "stale.json").exists(), "a stale file would be published too"


def test_a_json_field_that_is_only_a_dot_is_left_alone():
    from phantom_api.mock.record.sanitize import scrub_json_fields

    text = '{"domain_name":"."}'
    assert scrub_json_fields(text, Pseudonymiser(), Rules()) == text


def test_structural_files_keep_their_vocabulary(tmp_path):
    """Counter keys and role names are protocol vocabulary, not labels."""
    corpus = build_corpus(tmp_path)
    rules = Rules(structural_files=("RetrieveProperties",))

    sanitise_corpus(corpus, tmp_path / "clean", rules=rules)

    body = (tmp_path / "clean" / "soap" / "001-RetrieveProperties.resp.xml").read_text()
    assert "<hostName>" in body


def json_corpus(tmp_path, body):
    corpus = tmp_path / "jc"
    (corpus / "rest").mkdir(parents=True)
    (corpus / "rest" / "001.req.json").write_text("")
    (corpus / "rest" / "001.resp.json").write_text(body)
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "exchanges": [
                    {
                        "stem": "001",
                        "label": "list",
                        "transport": "rest",
                        "status": 200,
                        "method": "GET",
                        "endpoint_path": "/api/servers",
                        "request": "rest/001.req.json",
                        "response": "rest/001.resp.json",
                    }
                ]
            }
        )
    )
    return corpus


def test_only_nominated_collections_have_their_names_renamed(tmp_path):
    """A server name is a host name; an instance-type name is product vocabulary."""
    corpus = json_corpus(
        tmp_path,
        json.dumps(
            {
                "servers": [{"id": 1, "name": "ABC123XYZ9"}],
                "instanceTypes": [{"id": 2, "name": "Ubuntu 22.04"}],
            }
        ),
    )

    report = sanitise_corpus(corpus, tmp_path / "clean", rules=Rules(rename_kinds=("server",)))

    body = (tmp_path / "clean" / "rest" / "001.resp.json").read_text()
    assert report.ok, report.problems()
    assert "ABC123XYZ9" not in body, "a hardware serial must not survive"
    assert "Ubuntu 22.04" in body, "product vocabulary must survive"


def test_nothing_is_renamed_when_no_kinds_are_nominated(tmp_path):
    corpus = json_corpus(tmp_path, json.dumps({"servers": [{"id": 1, "name": "ABC123XYZ9"}]}))

    sanitise_corpus(corpus, tmp_path / "clean", rules=Rules())

    assert "ABC123XYZ9" in (tmp_path / "clean" / "rest" / "001.resp.json").read_text()


def test_a_renamed_object_is_replaced_inside_longer_strings(tmp_path):
    """A host name turns up embedded in generated identifiers."""
    corpus = json_corpus(
        tmp_path,
        json.dumps(
            {"servers": [{"id": 1, "name": "webhost-01", "osName": "ubuntu-webhost-01-tpx4cl"}]}
        ),
    )

    sanitise_corpus(corpus, tmp_path / "clean", rules=Rules(rename_kinds=("server",)))

    body = (tmp_path / "clean" / "rest" / "001.resp.json").read_text()
    assert "webhost-01" not in body
    assert json.loads(body)["servers"][0]["osName"].startswith("ubuntu-")


def test_renaming_never_touches_a_field_name(tmp_path):
    """A collection can hold an object whose name equals a field name."""
    corpus = json_corpus(
        tmp_path,
        json.dumps({"servers": [{"id": 1, "name": "status", "status": "running"}]}),
    )

    sanitise_corpus(corpus, tmp_path / "clean", rules=Rules(rename_kinds=("server",)))

    parsed = json.loads((tmp_path / "clean" / "rest" / "001.resp.json").read_text())
    assert "status" in parsed["servers"][0], "the field must still be there"


def test_an_object_without_an_identifier_is_not_renamed(tmp_path):
    corpus = json_corpus(tmp_path, json.dumps({"servers": [{"name": "no-identity-here"}]}))

    sanitise_corpus(corpus, tmp_path / "clean", rules=Rules(rename_kinds=("server",)))

    assert "no-identity-here" in (tmp_path / "clean" / "rest" / "001.resp.json").read_text()


def test_unreadable_json_does_not_stop_the_name_harvest(tmp_path):
    from phantom_api.mock.record.sanitize import build_rename_table

    corpus = json_corpus(tmp_path, json.dumps({"servers": [{"id": 1, "name": "webhost-01"}]}))
    (corpus / "rest" / "broken.json").write_text("{not json")

    assert "webhost-01" in build_rename_table(corpus, Rules(rename_kinds=("server",)))


@pytest.mark.parametrize(
    ("plural", "expected"),
    [("servers", "server"), ("policies", "policy"), ("addresses", "address")],
)
def test_collection_names_reduce_to_a_kind(plural, expected):
    from phantom_api.mock.record.sanitize import singular

    assert singular(plural) == expected


def test_the_credential_marker_is_safe_in_xml(tmp_path):
    """`<scrubbed>` parses as a nested element and breaks the document."""
    from xml.etree import ElementTree as ET

    from phantom_api.mock.record.sanitize import SECRET_PLACEHOLDER, scrub_secrets

    assert "<" not in SECRET_PLACEHOLDER and ">" not in SECRET_PLACEHOLDER

    out = scrub_secrets("<Login><password>hunter2</password></Login>", is_json=False)
    ET.fromstring(out)
    assert "hunter2" not in out
