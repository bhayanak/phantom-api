"""The evolution engine and the vCenter event projection."""

from __future__ import annotations

import pytest

from phantom_api.mock.model.entity import Entity, Inventory
from phantom_api.mock.model.evolution import EvolutionEngine, parse_duration
from phantom_api.mock.model.generator import Builder, IdAllocator, expand_counts
from phantom_api.mock.packs.vcenter.events import MUTATIONS, base_class_for, to_wire
from phantom_api.mock.protocols.xml_codec import to_xml


@pytest.fixture
def inventory() -> Inventory:
    host_a = Entity(id="host-1", kind="HostSystem", props={"name": "esx-01"})
    host_b = Entity(id="host-2", kind="HostSystem", props={"name": "esx-02"})
    vm = Entity(
        id="vm-1",
        kind="VirtualMachine",
        props={"name": "VM-1", "summary": {"runtime": {"powerState": "poweredOn"}}},
        edges={"host": ["host-1"]},
    )
    host_a.edges["vm"] = ["vm-1"]
    datastore = Entity(
        id="ds-1",
        kind="Datastore",
        props={"name": "DS-1", "summary": {"capacity": 1024}},
    )
    cluster = Entity(
        id="cl-1",
        kind="ClusterComputeResource",
        props={"name": "C1"},
        edges={"host": ["host-1", "host-2"]},
    )
    return Inventory([host_a, host_b, vm, datastore, cluster])


@pytest.fixture
def engine(inventory) -> EvolutionEngine:
    return EvolutionEngine(inventory, mutations=MUTATIONS, seed=1)


class TestDurations:
    @pytest.mark.parametrize(
        ("text", "seconds"), [("250ms", 0.25), ("30s", 30), ("5m", 300), ("2h", 7200), (7, 7)]
    )
    def test_parsing(self, text, seconds):
        assert parse_duration(text) == seconds

    def test_invalid_duration(self):
        with pytest.raises(ValueError, match="invalid duration"):
            parse_duration("soon")


class TestMutationsRunBeforeEvents:
    def test_power_off_changes_state(self, engine, inventory):
        event = engine.apply("VmPoweredOffEvent", "vm-1")
        assert inventory.get("vm-1").resolve("summary.runtime.powerState") == "poweredOff"
        assert event.type == "VmPoweredOffEvent"
        assert event.target_id == "vm-1"

    def test_target_can_be_a_name(self, engine, inventory):
        engine.apply("VmPoweredOffEvent", "vm:VM-1")
        assert inventory.get("vm-1").resolve("summary.runtime.powerState") == "poweredOff"

    def test_migration_moves_the_reference_on_both_sides(self, engine, inventory):
        engine.apply("VmMigratedEvent", "vm-1", {"to": "host-2"})
        assert inventory.get("vm-1").refs("host") == ["host-2"]
        assert "vm-1" not in inventory.get("host-1").refs("vm")
        assert "vm-1" in inventory.get("host-2").refs("vm")

    def test_reconfigure_applies_the_supplied_values(self, engine, inventory):
        engine.apply("VmReconfiguredEvent", "vm-1", {"memory_mb": 8192, "cpu_count": 8})
        assert inventory.get("vm-1").resolve("summary.config.memorySizeMB") == 8192
        assert inventory.get("vm-1").resolve("summary.config.numCpu") == 8

    def test_create_adds_a_vm_attached_to_a_host(self, engine, inventory):
        before = len(inventory.of_kind("VirtualMachine"))
        event = engine.apply("VmCreatedEvent", None)
        assert len(inventory.of_kind("VirtualMachine")) == before + 1
        created = inventory.get(event.detail["created"])
        assert created.refs("host")

    def test_remove_detaches_everywhere(self, engine, inventory):
        engine.apply("VmRemovedEvent", "vm-1")
        assert inventory.get("vm-1") is None
        assert inventory.get("host-1").refs("vm") == []

    def test_host_disconnect_and_reconnect(self, engine, inventory):
        engine.apply("HostDisconnectedEvent", "host-1")
        assert inventory.get("host-1").resolve("runtime.connectionState") == "disconnected"
        engine.apply("HostConnectedEvent", "host-1")
        assert inventory.get("host-1").resolve("runtime.connectionState") == "connected"

    def test_datastore_growth(self, engine, inventory):
        engine.apply("DatastoreCapacityIncreasedEvent", "ds-1", {"capacity_gb": 2})
        assert inventory.get("ds-1").resolve("summary.capacity") == 2 * 1024**3

    def test_unknown_event_still_records(self, engine):
        event = engine.apply("SomethingNobodyModelled", "vm-1", {"a": 1})
        assert event.type == "SomethingNobodyModelled"


class TestTimeline:
    def test_entries_fire_when_due(self, engine, inventory):
        engine.load(
            {
                "mode": "timeline",
                "timeline": [
                    {"at": "0s", "event": "VmPoweredOffEvent", "target": "vm-1"},
                    {"at": "60s", "event": "VmPoweredOnEvent", "target": "vm-1"},
                ],
            }
        )
        engine.tick(engine._started)
        assert inventory.get("vm-1").resolve("summary.runtime.powerState") == "poweredOff"
        engine.tick(engine._started + 61)
        assert inventory.get("vm-1").resolve("summary.runtime.powerState") == "poweredOn"

    def test_nothing_fires_before_its_time(self, engine, inventory):
        engine.load(
            {
                "mode": "timeline",
                "timeline": [
                    {"at": "60s", "event": "VmPoweredOffEvent", "target": "vm-1"},
                ],
            }
        )
        assert engine.tick(engine._started) == []

    def test_off_mode_does_nothing(self, engine):
        engine.load({"mode": "off", "timeline": [{"at": 0, "event": "VmPoweredOffEvent"}]})
        assert engine.tick(engine._started + 999) == []


class TestChurn:
    def test_weighted_events_fire_over_time(self, engine):
        engine.load(
            {
                "mode": "churn",
                "churn": {"events_per_minute": 60, "weights": {"VmPoweredOffEvent": 1}},
            }
        )
        fired = engine.tick(engine._started + 3)
        assert len(fired) >= 2
        assert all(e.type == "VmPoweredOffEvent" for e in fired)


class TestEventLog:
    def test_since_filters_by_key(self, engine):
        for _ in range(3):
            engine.apply("VmPoweredOffEvent", "vm-1")
        assert [e.key for e in engine.since(1)] == [2, 3]

    def test_the_log_is_bounded(self, inventory):
        engine = EvolutionEngine(inventory, mutations=MUTATIONS, max_events=5)
        for _ in range(20):
            engine.apply("VmPoweredOffEvent", "vm-1")
        assert len(engine.events) == 5


class TestWireProjection:
    def test_concrete_type_is_on_the_element(self, engine, inventory):
        event = engine.apply("VmPoweredOffEvent", "vm-1")
        xml = to_xml("returnval", to_wire(event, inventory))
        assert xml.startswith('<returnval xsi:type="VmPoweredOffEvent">')

    def test_vm_reference_block_is_present(self, engine, inventory):
        event = engine.apply("VmPoweredOffEvent", "vm-1")
        xml = to_xml("returnval", to_wire(event, inventory))
        assert '<vm type="VirtualMachine">vm-1</vm>' in xml

    def test_host_reference_block_is_present(self, engine, inventory):
        event = engine.apply("HostDisconnectedEvent", "host-1")
        xml = to_xml("returnval", to_wire(event, inventory))
        assert '<host type="HostSystem">host-1</host>' in xml

    def test_cluster_events_use_computeresource(self, engine, inventory):
        event = engine.apply("ClusterReconfiguredEvent", "cl-1")
        xml = to_xml("returnval", to_wire(event, inventory))
        assert "<computeResource>" in xml

    @pytest.mark.parametrize(
        ("event_type", "base"),
        [
            ("VmPoweredOnEvent", "VmEvent"),
            ("HostConnectedEvent", "HostEvent"),
            ("DatastoreRenamedEvent", "DatastoreEvent"),
            ("ClusterCreatedEvent", "ClusterEvent"),
            ("DrsVmMigratedEvent", "VmEvent"),
            ("EventEx", "EventEx"),
            ("SomethingElse", "Event"),
        ],
    )
    def test_base_class_selects_the_client_handler(self, event_type, base):
        assert base_class_for(event_type) == base


class TestGeneratorHelpers:
    def test_identifiers_use_the_registered_prefix(self):
        ids = IdAllocator()
        ids.prefix_for("VirtualMachine", "vm")
        assert ids.allocate("VirtualMachine").startswith("vm-")
        assert ids.allocate("HostSystem").startswith("hostsystem-")

    def test_builder_links_parent_and_child(self):
        builder = Builder(seed=1)
        parent = builder.entity("Folder", name="root")
        child = builder.entity("Datacenter", name="dc", parent=parent, parent_edge="childEntity")
        assert child.refs("parent") == [parent.id]
        assert parent.refs("childEntity") == [child.id]

    def test_link_can_be_bidirectional(self):
        builder = Builder(seed=1)
        a = builder.entity("A")
        b = builder.entity("B")
        builder.link(a, "to", b, back="from")
        assert a.refs("to") == [b.id]
        assert b.refs("from") == [a.id]

    def test_expand_counts_tolerates_missing_sections(self):
        assert expand_counts(None, "hosts", 3) == 3
        assert expand_counts({"hosts": "4"}, "hosts") == 4
        assert expand_counts({"hosts": "many"}, "hosts", 1) == 1
