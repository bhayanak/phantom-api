"""Projections not yet exercised end to end: events, performance, search, roles.

These go through the real engine and the real SOAP pack, so a passing test means
a client could actually make the call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from phantom_api.mock.config import (
    ListenConfig,
    MockConfig,
    ModelConfig,
    ProtocolConfig,
    ResponderRule,
    SessionRule,
)
from phantom_api.mock.engine import create_mock_app

from .conftest import SOAP_ENVELOPE, VCENTER_MOCK

HEADERS = {"content-type": "text/xml; charset=utf-8", "SOAPAction": "urn:vim25/8.0"}

pytestmark = pytest.mark.skipif(
    not (VCENTER_MOCK / "corpus" / "manifest.json").exists(),
    reason="examples/vcenter corpus is not present",
)


def _config(scenario_file: Path) -> MockConfig:
    return MockConfig(
        name="vcenter-projections",
        listen=ListenConfig(port=0, tls="off"),
        protocols=[
            ProtocolConfig(
                pack="soap",
                paths=["/sdk"],
                session={"/**": SessionRule(transport="cookie", name="vmware_soap_session")},
            )
        ],
        model=ModelConfig(pack="vcenter", scenario=scenario_file, seed=7),
        responders=[ResponderRule(match={}, responder="model")],  # type: ignore[arg-type]
        base_dir=scenario_file.parent,
    )


@pytest.fixture
def client(tmp_path, vcenter_scenario) -> TestClient:
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(yaml.safe_dump(vcenter_scenario))
    return TestClient(create_mock_app(_config(scenario_file)))


def call(client: TestClient, body: str, cookie: str | None = None):
    headers = dict(HEADERS)
    if cookie:
        headers["cookie"] = cookie
    return client.post("/sdk", content=SOAP_ENVELOPE.format(body=body), headers=headers)


@pytest.fixture
def session(client) -> str:
    """The real handshake: the cookie is issued first, then authenticated.

    Logging in without the cookie authenticates a different session, which is
    the mistake that made a live capture return "not authenticated" for
    everything after the first call.
    """
    issued = call(
        client,
        '<RetrieveServiceContent xmlns="urn:vim25">'
        '<_this type="ServiceInstance">ServiceInstance</_this></RetrieveServiceContent>',
    )
    cookie = issued.headers["set-cookie"].split(";")[0]
    call(
        client,
        '<Login xmlns="urn:vim25"><_this type="SessionManager">SessionManager</_this>'
        "<userName>u</userName><password>p</password></Login>",
        cookie,
    )
    return cookie


# -- authentication is required ----------------------------------------------


def test_an_unauthenticated_call_is_refused(client):
    response = call(
        client,
        '<QueryPerfProviderSummary xmlns="urn:vim25">'
        '<_this type="PerformanceManager">PerfMgr</_this>'
        '<entity type="VirtualMachine">vm-1</entity></QueryPerfProviderSummary>',
    )
    assert response.status_code == 500
    assert "NotAuthenticated" in response.text


# -- search ------------------------------------------------------------------


def test_finding_a_machine_by_uuid_returns_a_reference(client, session):
    """The uuid comes from the captured topology, so the lookup is a real one."""
    import json

    inventory = json.loads((VCENTER_MOCK / "topology" / "inventory.json").read_text())
    known = next(
        (
            obj["props"]["summary"]["config"]["instanceUuid"]
            for obj in inventory["objects"]
            if obj.get("type") == "VirtualMachine"
            and obj.get("props", {}).get("summary", {}).get("config", {}).get("instanceUuid")
        ),
        None,
    )
    if known is None:
        pytest.skip("the captured topology carries no instance uuid")

    response = call(
        client,
        '<FindByUuid xmlns="urn:vim25"><_this type="SearchIndex">SearchIndex</_this>'
        f"<uuid>{known}</uuid><vmSearch>true</vmSearch>"
        "<instanceUuid>true</instanceUuid></FindByUuid>",
        session,
    )
    assert response.status_code == 200


def test_an_unknown_uuid_finds_nothing(client, session):
    response = call(
        client,
        '<FindByUuid xmlns="urn:vim25"><_this type="SearchIndex">SearchIndex</_this>'
        "<uuid>00000000-0000-0000-0000-000000000000</uuid>"
        "<vmSearch>true</vmSearch></FindByUuid>",
        session,
    )
    assert response.status_code == 200
    assert "<obj" not in response.text


# -- authorisation -----------------------------------------------------------


def test_the_role_list_is_served_from_the_catalogue(client, session):
    response = call(
        client,
        '<RetrieveProperties xmlns="urn:vim25">'
        '<_this type="PropertyCollector">propertyCollector</_this><specSet>'
        "<propSet><type>AuthorizationManager</type><pathSet>roleList</pathSet></propSet>"
        '<objectSet><obj type="AuthorizationManager">AuthorizationManager</obj></objectSet>'
        "</specSet></RetrieveProperties>",
        session,
    )
    assert response.status_code == 200
    assert "AuthorizationRole" in response.text


def test_privilege_checks_answer_for_every_privilege_asked(client, session):
    response = call(
        client,
        '<HasPrivilegeOnEntities xmlns="urn:vim25">'
        '<_this type="AuthorizationManager">AuthorizationManager</_this>'
        '<entity type="VirtualMachine">vm-1</entity><sessionId>s</sessionId>'
        "<privId>System.View</privId><privId>System.Read</privId>"
        "</HasPrivilegeOnEntities>",
        session,
    )
    assert response.status_code == 200
    # A dict comprehension over the call's children kept only the last `privId`,
    # and the loss was silent.
    assert "System.View" in response.text
    assert "System.Read" in response.text


def test_an_echoed_parameter_keeps_its_attribute_form(client, session):
    """`<object type="X">v</object>`, not `<type>X</type><#text>v</#text>`.

    `#text` is how the decoder stores text alongside attributes; rebuilding it
    as child elements emits a name XML does not allow.
    """
    response = call(
        client,
        '<HasPrivilegeOnEntities xmlns="urn:vim25">'
        '<_this type="AuthorizationManager">AuthorizationManager</_this>'
        '<entity type="VirtualMachine">vm-1</entity><sessionId>s</sessionId>'
        "<privId>System.View</privId></HasPrivilegeOnEntities>",
        session,
    )

    assert "#text" not in response.text
    assert '<object type="VirtualMachine">vm-1</object>' in response.text


# -- events ------------------------------------------------------------------


def test_an_event_collector_can_be_created_read_reset_and_destroyed(client, session):
    created = call(
        client,
        '<CreateCollectorForEvents xmlns="urn:vim25">'
        '<_this type="EventManager">EventManager</_this><filter/>'
        "</CreateCollectorForEvents>",
        session,
    )
    assert created.status_code == 200

    import re

    match = re.search(r"<returnval[^>]*>([^<]+)</returnval>", created.text)
    assert match, created.text
    collector = match.group(1)

    read = call(
        client,
        '<ReadNextEvents xmlns="urn:vim25">'
        f'<_this type="EventHistoryCollector">{collector}</_this>'
        "<maxCount>10</maxCount></ReadNextEvents>",
        session,
    )
    assert read.status_code == 200

    reset = call(
        client,
        '<ResetCollector xmlns="urn:vim25">'
        f'<_this type="EventHistoryCollector">{collector}</_this></ResetCollector>',
        session,
    )
    assert reset.status_code == 200

    destroyed = call(
        client,
        '<DestroyCollector xmlns="urn:vim25">'
        f'<_this type="EventHistoryCollector">{collector}</_this></DestroyCollector>',
        session,
    )
    assert destroyed.status_code == 200


def test_reading_from_a_collector_that_never_existed_is_a_fault(client, session):
    response = call(
        client,
        '<ReadNextEvents xmlns="urn:vim25">'
        '<_this type="EventHistoryCollector">session[nope]</_this>'
        "<maxCount>10</maxCount></ReadNextEvents>",
        session,
    )
    assert response.status_code == 500
    assert "ManagedObjectNotFound" in response.text


def test_a_nonsense_event_count_falls_back_to_a_default(client, session):
    created = call(
        client,
        '<CreateCollectorForEvents xmlns="urn:vim25">'
        '<_this type="EventManager">EventManager</_this><filter/>'
        "</CreateCollectorForEvents>",
        session,
    )
    import re

    collector = re.search(r"<returnval[^>]*>([^<]+)</returnval>", created.text).group(1)

    response = call(
        client,
        '<ReadNextEvents xmlns="urn:vim25">'
        f'<_this type="EventHistoryCollector">{collector}</_this>'
        "<maxCount>not-a-number</maxCount></ReadNextEvents>",
        session,
    )
    assert response.status_code == 200


def test_events_can_be_queried_without_a_collector(client, session):
    response = call(
        client,
        '<QueryEvents xmlns="urn:vim25"><_this type="EventManager">EventManager</_this>'
        "<filter/></QueryEvents>",
        session,
    )
    assert response.status_code == 200


# -- performance -------------------------------------------------------------


def test_performance_counters_are_served_from_the_catalogue(client, session):
    response = call(
        client,
        '<RetrieveProperties xmlns="urn:vim25">'
        '<_this type="PropertyCollector">propertyCollector</_this><specSet>'
        "<propSet><type>PerformanceManager</type><pathSet>perfCounter</pathSet></propSet>"
        '<objectSet><obj type="PerformanceManager">PerfMgr</obj></objectSet>'
        "</specSet></RetrieveProperties>",
        session,
    )
    assert response.status_code == 200
    assert "perfCounter" in response.text


def test_a_performance_query_returns_samples_for_the_entity(client, session):
    response = call(
        client,
        '<QueryPerf xmlns="urn:vim25"><_this type="PerformanceManager">PerfMgr</_this>'
        '<querySpec><entity type="VirtualMachine">vm-1</entity>'
        "<maxSample>2</maxSample></querySpec></QueryPerf>",
        session,
    )
    assert response.status_code == 200
    assert "PerfEntityMetric" in response.text
    assert response.text.count("<timestamp>") == 2


def test_an_absurd_sample_count_is_clamped(client, session):
    response = call(
        client,
        '<QueryPerf xmlns="urn:vim25"><_this type="PerformanceManager">PerfMgr</_this>'
        '<querySpec><entity type="VirtualMachine">vm-1</entity>'
        "<maxSample>99999</maxSample></querySpec></QueryPerf>",
        session,
    )
    assert response.status_code == 200
    assert response.text.count("<timestamp>") <= 10


def test_a_non_numeric_sample_count_falls_back(client, session):
    response = call(
        client,
        '<QueryPerf xmlns="urn:vim25"><_this type="PerformanceManager">PerfMgr</_this>'
        '<querySpec><entity type="VirtualMachine">vm-1</entity>'
        "<maxSample>lots</maxSample></querySpec></QueryPerf>",
        session,
    )
    assert response.status_code == 200


def test_a_provider_summary_describes_the_available_intervals(client, session):
    response = call(
        client,
        '<QueryPerfProviderSummary xmlns="urn:vim25">'
        '<_this type="PerformanceManager">PerfMgr</_this>'
        '<entity type="VirtualMachine">vm-1</entity></QueryPerfProviderSummary>',
        session,
    )
    assert response.status_code == 200
    assert "currentSupported" in response.text


# -- container views ---------------------------------------------------------
#
# The entry point most inventory collectors use: make a view over a container,
# then collect properties by traversing the view's `view` edge.


def _make_view(client, session, kind="VirtualMachine", recursive="true"):
    response = call(
        client,
        '<CreateContainerView xmlns="urn:vim25">'
        '<_this type="ViewManager">ViewManager</_this>'
        '<container type="Folder">group-d1</container>'
        f"<type>{kind}</type><recursive>{recursive}</recursive></CreateContainerView>",
        session,
    )
    assert response.status_code == 200, response.text
    match = re.search(r'<returnval type="ContainerView">([^<]+)</returnval>', response.text)
    assert match, response.text
    return match.group(1)


def _collect_via_view(client, session, view, kind="VirtualMachine"):
    return call(
        client,
        '<RetrievePropertiesEx xmlns="urn:vim25">'
        '<_this type="PropertyCollector">propertyCollector</_this>'
        f"<specSet><propSet><type>{kind}</type><pathSet>name</pathSet></propSet>"
        f'<objectSet><obj type="ContainerView">{view}</obj><skip>true</skip>'
        "<selectSet><name>view</name><type>ContainerView</type>"
        "<path>view</path><skip>false</skip></selectSet>"
        "</objectSet></specSet><options/></RetrievePropertiesEx>",
        session,
    )


def _expected(scenario):
    """Host and VM counts the scenario asks for, so the numbers cannot drift."""
    clusters = [c for dc in scenario["topology"]["datacenters"] for c in dc["clusters"]]
    hosts = sum(c["hosts"] for c in clusters)
    vms = sum(c["hosts"] * c["vms_per_host"] for c in clusters)
    return hosts, vms


def test_a_container_view_can_be_created(client, session):
    assert _make_view(client, session).startswith("session[")


def test_collecting_through_a_view_returns_the_contained_machines(
    client, session, vcenter_scenario
):
    _, vms = _expected(vcenter_scenario)
    view = _make_view(client, session)
    response = _collect_via_view(client, session, view)
    assert response.status_code == 200, response.text
    assert "soapenv:Fault" not in response.text
    assert response.text.count("<propSet>") == vms


def test_a_shallow_view_does_not_descend(client, session, vcenter_scenario):
    """recursive=false sees only what sits directly in the root folder."""
    _, vms = _expected(vcenter_scenario)
    deep = _make_view(client, session, recursive="true")
    shallow = _make_view(client, session, recursive="false")
    assert _collect_via_view(client, session, deep).text.count("<propSet>") == vms
    assert _collect_via_view(client, session, shallow).text.count("<propSet>") == 0


def test_a_view_of_hosts_holds_only_hosts(client, session, vcenter_scenario):
    hosts, _ = _expected(vcenter_scenario)
    view = _make_view(client, session, kind="HostSystem")
    response = _collect_via_view(client, session, view, kind="HostSystem")
    assert response.text.count("<propSet>") == hosts


def test_a_destroyed_view_is_gone(client, session):
    view = _make_view(client, session)
    destroyed = call(
        client,
        f'<DestroyView xmlns="urn:vim25"><_this type="ContainerView">{view}</_this></DestroyView>',
        session,
    )
    assert destroyed.status_code == 200
    assert _collect_via_view(client, session, view).text.count("<propSet>") == 0


def test_a_view_over_a_missing_container_is_refused(client, session):
    response = call(
        client,
        '<CreateContainerView xmlns="urn:vim25">'
        '<_this type="ViewManager">ViewManager</_this>'
        '<container type="Folder">group-does-not-exist</container>'
        "<type>VirtualMachine</type><recursive>true</recursive></CreateContainerView>",
        session,
    )
    assert response.status_code == 500
    assert "ManagedObjectNotFound" in response.text
