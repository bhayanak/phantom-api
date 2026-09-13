"""End-to-end: the engine, the vCenter pack, and real captured requests.

The most valuable tests here replay requests recorded from a live endpoint. A
mock that answers a request someone actually sent is worth more than one that
answers a request we invented to match the implementation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phantom_api.mock.config import (
    ConfigError,
    ListenConfig,
    MockConfig,
    ModelConfig,
    ProtocolConfig,
    ResponderRule,
    SessionRule,
    load_config,
)
from phantom_api.mock.engine import create_mock_app
from phantom_api.mock.packs.vcenter.pack import VCenterPack

from .conftest import SOAP_ENVELOPE, VCENTER_MOCK

HEADERS = {"content-type": "text/xml; charset=utf-8", "SOAPAction": "urn:vim25/8.0"}
CORPUS = VCENTER_MOCK / "corpus"

pytestmark = pytest.mark.skipif(
    not (CORPUS / "manifest.json").exists(),
    reason="examples/vcenter corpus is not present",
)


def _config(scenario_file: Path) -> MockConfig:
    return MockConfig(
        name="vcenter-test",
        listen=ListenConfig(port=0, tls="off"),
        protocols=[
            ProtocolConfig(
                pack="soap",
                paths=["/sdk", "/pbm/sdk"],
                session={"/**": SessionRule(transport="cookie", name="vmware_soap_session")},
            )
        ],
        model=ModelConfig(pack="vcenter", scenario=scenario_file, seed=7),
        responders=[ResponderRule(match={}, responder="model")],  # type: ignore[arg-type]
        base_dir=scenario_file.parent,
    )


@pytest.fixture
def client(tmp_path, vcenter_scenario) -> TestClient:
    import yaml

    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(yaml.safe_dump(vcenter_scenario))
    return TestClient(create_mock_app(_config(scenario_file)))


def call(client: TestClient, body: str, cookie: str | None = None, path: str = "/sdk"):
    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    return client.post(path, content=SOAP_ENVELOPE.format(body=body), headers=headers)


def service_content(client: TestClient):
    return call(
        client,
        '<RetrieveServiceContent xmlns="urn:vim25">'
        '<_this type="ServiceInstance">ServiceInstance</_this></RetrieveServiceContent>',
    )


def authenticate(client: TestClient) -> str:
    response = service_content(client)
    cookie = response.headers["set-cookie"].split(";")[0]
    call(
        client,
        '<Login xmlns="urn:vim25"><_this type="SessionManager">SessionManager</_this>'
        "<userName>u</userName><password>p</password><locale>en_US</locale></Login>",
        cookie,
    )
    return cookie


class TestHandshake:
    def test_service_content_issues_a_session_cookie(self, client):
        """The client captures the cookie from the first response and reuses it."""
        response = service_content(client)
        assert response.status_code == 200
        assert "vmware_soap_session=" in response.headers["set-cookie"]

    def test_service_content_is_answered_twice(self, client):
        """The client calls it again after re-pinning SOAPAction from apiVersion."""
        first = service_content(client)
        api_version = re.search(r"<apiVersion>([^<]+)", first.text).group(1)
        second = client.post(
            "/sdk",
            content=SOAP_ENVELOPE.format(
                body='<RetrieveServiceContent xmlns="urn:vim25">'
                '<_this type="ServiceInstance">ServiceInstance</_this>'
                "</RetrieveServiceContent>"
            ),
            headers={**HEADERS, "SOAPAction": f"urn:vim25/{api_version[:3]}"},
        )
        assert second.status_code == 200

    def test_instance_uuid_comes_from_the_scenario(self, client):
        assert "11111111-2222-4333-8444-555555555555" in service_content(client).text

    def test_singleton_references_are_present(self, client):
        text = service_content(client).text
        for name in ("rootFolder", "sessionManager", "eventManager", "perfManager"):
            assert f"<{name} type=" in text


class TestAuthentication:
    def test_unauthenticated_currenttime_is_a_fault(self, client):
        """One condition, two shapes -- this one is a 500 with a SOAP fault."""
        cookie = service_content(client).headers["set-cookie"].split(";")[0]
        response = call(
            client,
            '<CurrentTime xmlns="urn:vim25">'
            '<_this type="ServiceInstance">ServiceInstance</_this></CurrentTime>',
            cookie,
        )
        assert response.status_code == 500
        assert "NotAuthenticated" in response.text

    def test_login_then_currenttime_succeeds(self, client):
        cookie = authenticate(client)
        response = call(
            client,
            '<CurrentTime xmlns="urn:vim25">'
            '<_this type="ServiceInstance">ServiceInstance</_this></CurrentTime>',
            cookie,
        )
        assert response.status_code == 200
        assert "CurrentTimeResponse" in response.text

    def test_fixed_credentials_reject_the_wrong_password(self, tmp_path, vcenter_scenario):
        import yaml

        vcenter_scenario["auth"] = {"mode": "fixed", "username": "root", "password": "hunter2"}
        scenario_file = tmp_path / "s.yaml"
        scenario_file.write_text(yaml.safe_dump(vcenter_scenario))
        client = TestClient(create_mock_app(_config(scenario_file)))
        cookie = service_content(client).headers["set-cookie"].split(";")[0]
        response = call(
            client,
            '<Login xmlns="urn:vim25"><_this type="SessionManager">SessionManager</_this>'
            "<userName>root</userName><password>wrong</password></Login>",
            cookie,
        )
        assert response.status_code == 500
        assert "InvalidLogin" in response.text

    def test_logout_ends_the_session(self, client):
        cookie = authenticate(client)
        call(
            client,
            '<Logout xmlns="urn:vim25">'
            '<_this type="SessionManager">SessionManager</_this></Logout>',
            cookie,
        )
        response = call(
            client,
            '<CurrentTime xmlns="urn:vim25">'
            '<_this type="ServiceInstance">ServiceInstance</_this></CurrentTime>',
            cookie,
        )
        assert response.status_code == 500


class TestCapturedRequests:
    """Replay requests recorded from a live endpoint through the model."""

    @pytest.mark.parametrize(
        ("recording", "kind"),
        [
            ("012-RetrieveProperties_Datacenter", "Datacenter"),
            ("013-RetrieveProperties_ClusterComputeResource", "ClusterComputeResource"),
            ("015-RetrieveProperties_HostSystem", "HostSystem"),
            ("017-RetrieveProperties_VirtualMachine", "VirtualMachine"),
            ("018-RetrieveProperties_Datastore", "Datastore"),
            ("019-RetrieveProperties_ResourcePool", "ResourcePool"),
            ("020-RetrieveProperties_Folder", "Folder"),
        ],
    )
    def test_real_traversal_returns_the_right_kind(self, client, recording, kind):
        cookie = authenticate(client)
        body = (CORPUS / "soap" / f"{recording}.req.xml").read_text()
        response = client.post("/sdk", content=body, headers={**HEADERS, "Cookie": cookie})
        assert response.status_code == 200
        kinds = set(re.findall(r'<obj type="([^"]+)">', response.text))
        assert kinds == {kind}, f"{recording} returned {kinds}"

    def test_response_is_well_formed_xml(self, client):
        from phantom_api.mock.protocols.xml_codec import parse_xml

        cookie = authenticate(client)
        body = (CORPUS / "soap" / "017-RetrieveProperties_VirtualMachine.req.xml").read_text()
        response = client.post("/sdk", content=body, headers={**HEADERS, "Cookie": cookie})
        parse_xml(response.text)

    def test_values_carry_an_xsi_type(self, client):
        """Clients resolve polymorphic fields from xsi:type; without it data vanishes."""
        cookie = authenticate(client)
        body = (CORPUS / "soap" / "012-RetrieveProperties_Datacenter.req.xml").read_text()
        response = client.post("/sdk", content=body, headers={**HEADERS, "Cookie": cookie})
        values = re.findall(r"<val\b[^>]*>", response.text)
        assert values
        assert all("xsi:type=" in v or "type=" in v for v in values)

    def test_unrequested_properties_are_reported_as_missing(self, client):
        cookie = authenticate(client)
        body = (CORPUS / "soap" / "012-RetrieveProperties_Datacenter.req.xml").read_text()
        response = client.post("/sdk", content=body, headers={**HEADERS, "Cookie": cookie})
        # effectiveRole is requested but not modelled, so it must come back as a
        # per-property failure rather than silently absent.
        assert "<missingSet>" in response.text


class TestPaging:
    def test_retrieve_ex_pages_and_continues(self, client):
        cookie = authenticate(client)
        body = (
            CORPUS / "soap" / "025-RetrievePropertiesEx_VirtualMachine_page.req.xml"
        ).read_text()
        first = client.post("/sdk", content=body, headers={**HEADERS, "Cookie": cookie})
        assert len(re.findall(r"<obj ", first.text)) == 2
        token = re.search(r"<token>([^<]+)</token>", first.text).group(1)

        second = call(
            client,
            '<ContinueRetrievePropertiesEx xmlns="urn:vim25">'
            '<_this type="PropertyCollector">propertyCollector</_this>'
            f"<token>{token}</token></ContinueRetrievePropertiesEx>",
            cookie,
        )
        assert len(re.findall(r"<obj ", second.text)) >= 1
        assert "<token>" not in second.text

    def test_an_invalid_token_faults(self, client):
        cookie = authenticate(client)
        response = call(
            client,
            '<ContinueRetrievePropertiesEx xmlns="urn:vim25">'
            '<_this type="PropertyCollector">propertyCollector</_this>'
            "<token>nonsense</token></ContinueRetrievePropertiesEx>",
            cookie,
        )
        assert response.status_code == 500


class TestChangeFeed:
    def _collector(self, client, cookie) -> str:
        response = call(
            client,
            '<CreateCollectorForEvents xmlns="urn:vim25">'
            '<_this type="EventManager">EventManager</_this><filter/>'
            "</CreateCollectorForEvents>",
            cookie,
        )
        pattern = r'<returnval type="EventHistoryCollector">([^<]+)<'
        return re.search(pattern, response.text).group(1)

    def test_the_model_changes_before_the_event_is_emitted(self, client):
        """A mock that reports a change it has not made teaches distrust."""
        authenticate(client)
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]
        assert vm["props"]["summary"]["runtime"]["powerState"] == "poweredOn"

        client.post("/__phantom/events", json={"event": "VmPoweredOffEvent", "target": vm["id"]})

        after = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]
        assert after["props"]["summary"]["runtime"]["powerState"] == "poweredOff"

    def test_events_carry_the_concrete_type_and_a_reference(self, client):
        cookie = authenticate(client)
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        client.post("/__phantom/events", json={"event": "VmPoweredOffEvent", "target": vm})

        token = self._collector(client, cookie)
        response = call(
            client,
            f'<ReadNextEvents xmlns="urn:vim25"><_this type="EventHistoryCollector">{token}</_this>'
            "<maxCount>10</maxCount></ReadNextEvents>",
            cookie,
        )
        assert '<returnval xsi:type="VmPoweredOffEvent">' in response.text
        assert f'<vm type="VirtualMachine">{vm}</vm>' in response.text

    def test_a_drained_collector_returns_an_empty_response_element(self, client):
        cookie = authenticate(client)
        token = self._collector(client, cookie)
        drained = call(
            client,
            f'<ReadNextEvents xmlns="urn:vim25"><_this type="EventHistoryCollector">{token}</_this>'
            "<maxCount>10</maxCount></ReadNextEvents>",
            cookie,
        )
        empty = '<ReadNextEventsResponse xmlns="urn:vim25"></ReadNextEventsResponse>'
        assert empty in drained.text

    def test_migration_moves_the_vm_between_hosts(self, client):
        authenticate(client)
        entities = client.get("/__phantom/inventory?kind=HostSystem").json()["entities"]
        target = entities[-1]["id"]
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        client.post(
            "/__phantom/events",
            json={"event": "VmMigratedEvent", "target": vm, "args": {"to": target}},
        )
        after = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]
        assert after["edges"]["host"] == [target]

    def test_removal_detaches_the_entity_everywhere(self, client):
        authenticate(client)
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        client.post("/__phantom/events", json={"event": "VmRemovedEvent", "target": vm})
        remaining = client.get("/__phantom/inventory?kind=VirtualMachine").json()
        assert all(e["id"] != vm for e in remaining["entities"])


class TestOptionsAndCatalogs:
    def test_https_port_option_is_answered(self, client):
        """The client rebuilds its canonical URL from this value."""
        cookie = authenticate(client)
        response = call(
            client,
            '<QueryOptions xmlns="urn:vim25"><_this type="OptionManager">VpxSettings</_this>'
            "<name>config.vpxd.rhttpproxy.httpsport</name></QueryOptions>",
            cookie,
        )
        assert "<key>config.vpxd.rhttpproxy.httpsport</key>" in response.text
        assert ">443<" in response.text

    def test_unknown_option_faults(self, client):
        cookie = authenticate(client)
        response = call(
            client,
            '<QueryOptions xmlns="urn:vim25"><_this type="OptionManager">VpxSettings</_this>'
            "<name>no.such.option</name></QueryOptions>",
            cookie,
        )
        assert response.status_code == 500

    def test_perf_counters_are_read_through_the_property_collector(self, client):
        cookie = authenticate(client)
        response = call(
            client,
            '<RetrieveProperties xmlns="urn:vim25">'
            '<_this type="PropertyCollector">propertyCollector</_this><specSet>'
            "<propSet><type>PerformanceManager</type><all>false</all>"
            "<pathSet>perfCounter</pathSet></propSet>"
            '<objectSet><obj type="PerformanceManager">PerfMgr</obj></objectSet>'
            "</specSet></RetrieveProperties>",
            cookie,
        )
        assert 'xsi:type="PerfCounterInfo"' in response.text

    def test_roles_are_read_through_the_property_collector(self, client):
        cookie = authenticate(client)
        response = call(
            client,
            '<RetrieveProperties xmlns="urn:vim25">'
            '<_this type="PropertyCollector">propertyCollector</_this><specSet>'
            "<propSet><type>AuthorizationManager</type><all>false</all>"
            "<pathSet>roleList</pathSet></propSet>"
            '<objectSet><obj type="AuthorizationManager">AuthorizationManager</obj></objectSet>'
            "</specSet></RetrieveProperties>",
            cookie,
        )
        assert 'xsi:type="AuthorizationRole"' in response.text


class TestControlPlane:
    def test_status_reports_the_model(self, client):
        payload = client.get("/__phantom/status").json()
        assert payload["model"]["pack"] == "vcenter"
        assert payload["model"]["entities"] > 0

    def test_log_records_what_was_asked_for(self, client):
        service_content(client)
        payload = client.get("/__phantom/log").json()
        assert payload["entries"][-1]["operation"] == "RetrieveServiceContent"

    def test_verify_turns_the_mock_into_an_oracle(self, client):
        service_content(client)
        result = client.post(
            "/__phantom/verify", json={"operation": "RetrieveServiceContent", "at_least": 1}
        ).json()
        assert result["satisfied"] is True

    def test_chaos_can_be_toggled_at_runtime(self, client):
        client.post("/__phantom/chaos", json={"fault_rate": 1.0})
        assert service_content(client).status_code == 500
        client.post("/__phantom/chaos", json={"fault_rate": 0.0})
        assert service_content(client).status_code == 200

    def test_mutate_edits_the_model_directly(self, client):
        vm = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]["id"]
        client.post(
            "/__phantom/mutate",
            json={"action": "set", "entity": vm, "path": "summary.config.numCpu", "value": 64},
        )
        after = client.get("/__phantom/inventory?kind=VirtualMachine").json()["entities"][0]
        assert after["props"]["summary"]["config"]["numCpu"] == 64


class TestPackGeneration:
    def test_topology_matches_the_scenario(self, vcenter_scenario):
        inventory = VCenterPack().build(vcenter_scenario, seed=1)
        kinds = inventory.kinds()
        assert kinds["Datacenter"] == 1
        assert kinds["ClusterComputeResource"] == 1
        assert kinds["HostSystem"] == 2
        assert kinds["VirtualMachine"] == 4

    def test_generation_is_deterministic(self, vcenter_scenario):
        first = VCenterPack().build(vcenter_scenario, seed=42).to_json()
        second = VCenterPack().build(vcenter_scenario, seed=42).to_json()
        assert first == second

    def test_every_vm_is_reachable_from_the_root(self, vcenter_scenario):
        inventory = VCenterPack().build(vcenter_scenario, seed=1)
        for vm in inventory.of_kind("VirtualMachine"):
            assert vm.refs("host"), f"{vm.id} has no host"
            assert vm.refs("parent"), f"{vm.id} has no parent folder"


class TestConfiguration:
    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yaml")

    def test_invalid_yaml_is_reported(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("mock: [unclosed")
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_config(path)

    def test_unset_environment_variable_refuses_to_start(self, tmp_path, monkeypatch):
        """Better than silently authenticating against the literal '${...}'."""
        monkeypatch.delenv("PHANTOM_TEST_SECRET", raising=False)
        path = tmp_path / "c.yaml"
        path.write_text("mock:\n  name: ${PHANTOM_TEST_SECRET}\n")
        with pytest.raises(ConfigError, match="not set"):
            load_config(path)

    def test_environment_variables_are_interpolated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_TEST_NAME", "from-env")
        path = tmp_path / "c.yaml"
        path.write_text("mock:\n  name: ${PHANTOM_TEST_NAME}\n")
        assert load_config(path).name == "from-env"

    def test_the_shipped_vcenter_config_is_valid(self):
        config = load_config(VCENTER_MOCK / "phantom.yaml")
        assert config.model.pack == "vcenter"
        assert any(p.pack == "soap" for p in config.protocols)


class TestCorpusReplayEndToEnd:
    def test_every_recorded_soap_request_replays(self):
        """The corpus must answer every request it was built from."""
        config = MockConfig(
            name="replay",
            listen=ListenConfig(port=0, tls="off"),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[
                ResponderRule(
                    match={},
                    responder="corpus",  # type: ignore[arg-type]
                    corpus=CORPUS,
                    on_miss="fault",
                )
            ],
            base_dir=CORPUS.parent,
        )
        client = TestClient(create_mock_app(config))
        requests = sorted((CORPUS / "soap").glob("*.req.xml"))
        assert requests
        failures = []
        for request_file in requests:
            response = client.post("/sdk", content=request_file.read_text(), headers=HEADERS)
            if response.status_code not in (200, 500) or "no recorded exchange" in response.text:
                failures.append(request_file.name)
        assert not failures, f"unreplayable: {failures}"
