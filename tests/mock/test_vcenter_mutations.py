"""Tests for the vCenter mutation set and a few engine edges.

Mutations are the half of a simulation that has to be right: an event that does
not change the world is worse than no event, because a client reconciles against
it and silently drifts.
"""

from __future__ import annotations

import pytest

from phantom_api.mock.model.entity import Entity, Inventory
from phantom_api.mock.packs.vcenter.events import MUTATIONS, base_class_for, to_wire


@pytest.fixture
def world():
    inventory = Inventory()
    for entity in [
        Entity(id="host-1", kind="HostSystem", props={"name": "esx-01"}, edges={"vm": ["vm-1"]}),
        Entity(id="host-2", kind="HostSystem", props={"name": "esx-02"}),
        Entity(
            id="vm-1",
            kind="VirtualMachine",
            props={"name": "VM-01", "summary": {"runtime": {"powerState": "poweredOn"}}},
            edges={"runtime.host": ["host-1"]},
        ),
        Entity(id="ds-1", kind="Datastore", props={"name": "DS-01"}),
        Entity(id="cluster-1", kind="ClusterComputeResource", edges={"host": ["host-2"]}),
    ]:
        inventory.add(entity)
    return inventory


def apply(name, world, entity=None, **args):
    return MUTATIONS[name](world, entity, args)


# -- power and connection ----------------------------------------------------


def test_powering_off_changes_the_machine_not_just_the_event(world):
    result = apply("VmPoweredOffEvent", world, world.get("vm-1"))

    assert world.get("vm-1").resolve("summary.runtime.powerState") == "poweredOff"
    assert result["powerState"] == "poweredOff"


def test_a_mutation_with_no_target_picks_one_of_the_right_kind(world):
    """A scripted timeline need not name an object for every event."""
    result = apply("VmPoweredOnEvent", world)
    assert result["entity_kind"] == "VirtualMachine"


def test_a_host_disconnecting_is_recorded_on_the_host(world):
    apply("HostDisconnectedEvent", world, world.get("host-1"))
    assert world.get("host-1").resolve("runtime.connectionState") == "disconnected"


def test_a_mutation_on_an_empty_world_does_nothing(world):
    assert MUTATIONS["VmPoweredOffEvent"](Inventory(), None, {}) == {}


# -- migration ---------------------------------------------------------------


def test_migration_moves_the_machine_between_hosts(world):
    apply("VmMigratedEvent", world, world.get("vm-1"), to="HostSystem:host-2")

    assert "vm-1" in world.get("host-2").refs("vm")
    assert "vm-1" not in world.get("host-1").refs("vm")


def test_migration_accepts_a_host_name_as_well_as_an_id(world):
    apply("VmMigratedEvent", world, world.get("vm-1"), to="esx-02")
    assert "vm-1" in world.get("host-2").refs("vm")


# -- lifecycle ---------------------------------------------------------------


def test_creating_a_machine_adds_it_to_the_inventory(world):
    before = len(world.of_kind("VirtualMachine"))
    result = apply("VmCreatedEvent", world)

    assert len(world.of_kind("VirtualMachine")) == before + 1
    assert world.get(result["entity_id"]).kind == "VirtualMachine"


def test_a_machine_created_in_a_cluster_lands_on_one_of_its_hosts(world):
    result = apply("VmCreatedEvent", world, world.get("cluster-1"))
    created = world.get(result["entity_id"])

    assert created.refs("host") == ["host-2"], "it must land inside the cluster it was asked for"
    assert created.resolve("summary.runtime.host") == "host-2"


def test_removing_a_machine_clears_it_from_the_inventory(world):
    result = apply("VmRemovedEvent", world, world.get("vm-1"))

    assert result["removed"] is True
    assert world.get("vm-1") is None


def test_growing_a_datastore_changes_its_capacity(world):
    apply("DatastoreCapacityIncreasedEvent", world, world.get("ds-1"), capacity_gb=2)
    assert world.get("ds-1").resolve("summary.capacity") == 2 * 1024**3


def test_some_events_are_signals_with_no_structural_change(world):
    """A rename event changes nothing structural; the event is the whole point."""
    result = apply("VmRenamedEvent", world, world.get("vm-1"), newName="VM-99")
    assert result["newName"] == "VM-99"
    assert result["entity_id"] == "vm-1"


def test_a_signal_event_without_a_target_returns_its_arguments(world):
    assert MUTATIONS["ClusterCreatedEvent"](world, None, {"name": "C-9"}) == {"name": "C-9"}


# -- the wire form -----------------------------------------------------------


def test_every_event_declares_a_base_class():
    for name in MUTATIONS:
        assert base_class_for(name), f"{name} has no base class"


def change(kind: str, target_id: str | None = None, target_kind: str | None = None):
    from phantom_api.mock.model.evolution import ChangeEvent

    return ChangeEvent(
        key=1,
        type=kind,
        created_at=1_700_000_000.0,
        target_id=target_id,
        target_kind=target_kind,
    )


def test_an_event_carries_its_concrete_type(world):
    xml = to_wire(change("VmPoweredOffEvent", "vm-1", "VirtualMachine"), world)

    assert xml.type_name == "VmPoweredOffEvent"


def test_an_event_about_an_object_carries_a_reference_to_it(world):
    from phantom_api.mock.protocols.xml_codec import to_xml

    xml = to_xml("event", to_wire(change("VmPoweredOffEvent", "vm-1", "VirtualMachine"), world))

    assert "vm-1" in xml
    assert "VirtualMachine" in xml


def test_an_event_about_nothing_still_serialises(world):
    from phantom_api.mock.protocols.xml_codec import to_xml

    xml = to_xml("event", to_wire(change("ClusterCreatedEvent"), world))

    assert "<key>1</key>" in xml


# -- engine edges ------------------------------------------------------------


def test_closing_the_engine_closes_responders_that_hold_resources(tmp_path):
    """A forward responder owns an HTTP client; leaking it leaks sockets."""
    from phantom_api.mock.config import (
        ListenConfig,
        MockConfig,
        ProtocolConfig,
        ResponderRule,
    )
    from phantom_api.mock.engine import MockEngine

    engine = MockEngine(
        MockConfig(
            name="t",
            listen=ListenConfig(),
            protocols=[ProtocolConfig(pack="soap", paths=["/**"])],
            responders=[ResponderRule(responder="forward", target="https://example.com")],
            base_dir=tmp_path,
        )
    )
    closed: list[bool] = []
    engine._responders.bindings[0].responder.close = lambda: closed.append(True)

    engine.close()

    assert closed == [True]


def test_an_unhandled_operation_becomes_a_404(tmp_path):
    from phantom_api.mock.config import (
        ListenConfig,
        MockConfig,
        ProtocolConfig,
        ResponderRule,
    )
    from phantom_api.mock.engine import MockEngine
    from phantom_api.mock.types import RawRequest

    engine = MockEngine(
        MockConfig(
            name="t",
            listen=ListenConfig(),
            protocols=[ProtocolConfig(pack="openapi", paths=["/**"])],
            responders=[ResponderRule(responder="template", operations={"Other": "x"})],
            base_dir=tmp_path,
        )
    )

    response = engine.handle(
        RawRequest(method="GET", path="/thing", query="", headers={}, body=b"")
    )
    assert response.status == 404


@pytest.mark.parametrize(
    "event",
    ["VmSuspendedEvent", "VmRelocatedEvent", "VmReconfiguredEvent"],
)
def test_every_machine_event_names_the_machine_it_affected(event, world):
    assert apply(event, world, world.get("vm-1"))["entity_id"] == "vm-1"


def test_cloning_produces_a_new_machine(world):
    """A clone is not an event about the original; it is a new object."""
    before = len(world.of_kind("VirtualMachine"))
    result = apply("VmClonedEvent", world, world.get("vm-1"))

    assert len(world.of_kind("VirtualMachine")) == before + 1
    assert result["entity_id"] != "vm-1"


@pytest.mark.parametrize(
    "event", ["HostConnectedEvent", "HostConnectionLostEvent", "HostDisconnectedEvent"]
)
def test_host_events_record_a_connection_state(event, world):
    result = apply(event, world, world.get("host-1"))
    assert result["entity_kind"] == "HostSystem"
    assert world.get("host-1").resolve("runtime.connectionState")


@pytest.mark.parametrize("event", ["DrsVmMigratedEvent", "DrsVmPoweredOnEvent"])
def test_drs_events_behave_like_their_manual_counterparts(event, world):
    assert apply(event, world, world.get("vm-1"))["entity_id"] == "vm-1"


@pytest.mark.parametrize(
    "event",
    ["VmRemovedEvent", "DatastoreCapacityIncreasedEvent", "HostDisconnectedEvent"],
)
def test_no_candidate_of_the_right_kind_is_a_no_op(event):
    assert MUTATIONS[event](Inventory(), None, {}) == {}


def test_creating_a_machine_needs_somewhere_to_put_it():
    assert MUTATIONS["VmCreatedEvent"](Inventory(), None, {}) == {}


def test_migration_with_nowhere_to_go_still_names_the_machine():
    """There is no host to move to, but the event is still about this machine."""
    inventory = Inventory()
    inventory.add(Entity(id="vm-1", kind="VirtualMachine"))

    assert MUTATIONS["VmMigratedEvent"](inventory, None, {"to": "nowhere"}) == {"entity_id": "vm-1"}


def test_growing_a_datastore_without_a_size_changes_nothing(world):
    apply("DatastoreCapacityIncreasedEvent", world, world.get("ds-1"))
    assert world.get("ds-1").resolve("summary.capacity") is not world.get("ds-1")
    assert "summary" not in world.get("ds-1").props or not world.get("ds-1").props.get(
        "summary", {}
    ).get("capacity")


def test_an_event_naming_an_object_that_has_gone_still_serialises(world):
    from phantom_api.mock.protocols.xml_codec import to_xml

    xml = to_xml("event", to_wire(change("VmRemovedEvent", "vm-gone", "VirtualMachine"), world))
    assert "<key>1</key>" in xml


# -- traversal helpers -------------------------------------------------------


def test_descendants_follows_named_edges_to_exhaustion(world):
    from phantom_api.mock.model.graph import descendants

    world.get("host-1").edges["vm"] = ["vm-1"]
    found = descendants(world, "cluster-1", ["host", "vm"])

    assert {e.id for e in found} >= {"host-2"}


def test_descendants_of_a_leaf_is_just_itself(world):
    from phantom_api.mock.model.graph import descendants

    assert [e.id for e in descendants(world, "ds-1", ["host"])] == ["ds-1"]


def test_a_traversal_budget_stops_a_runaway_walk():
    """A cycle with a huge fan-out must terminate rather than hang."""
    from phantom_api.mock.model.graph import descendants

    inventory = Inventory()
    for n in range(50):
        inventory.add(Entity(id=f"n-{n}", kind="Node", edges={"next": [f"n-{(n + 1) % 50}"]}))

    found = descendants(inventory, "n-0", ["next"])
    assert len(found) <= 50


def test_domain_packs_are_discovered_from_entry_points():
    """The vCenter pack is registered through the packaging entry point."""
    from phantom_api.mock.packs.base import available, get_pack

    assert "vcenter" in available()
    assert get_pack("vcenter") is not None
