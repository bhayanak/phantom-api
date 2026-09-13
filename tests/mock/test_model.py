"""The traversal engine: the component every graph-shaped read depends on."""

from __future__ import annotations

import pytest

from phantom_api.mock.model.entity import MISSING, Entity, Inventory
from phantom_api.mock.model.graph import (
    ObjectSpec,
    TraversalError,
    TraversalSpec,
    traverse,
)

FULL = {
    "visitFolders": TraversalSpec(
        "visitFolders", "Folder", "childEntity", ["visitFolders", "dcToHf"]
    ),
    "dcToHf": TraversalSpec("dcToHf", "Datacenter", "hostFolder", ["crToH"]),
    "crToH": TraversalSpec("crToH", "ComputeResource", "host", ["hToVm"]),
    "hToVm": TraversalSpec("hToVm", "HostSystem", "vm", []),
}


def test_walks_the_whole_graph(simple_inventory):
    visited = traverse(
        simple_inventory,
        ObjectSpec("root", list(FULL)),
        FULL,
        {"ComputeResource": {"ClusterComputeResource"}},
    )
    assert [e.id for e in visited] == ["root", "dc-1", "cl-1", "h-1", "vm-1"]


def test_cycle_does_not_recurse_forever(simple_inventory):
    specs = dict(FULL)
    specs["vmToHost"] = TraversalSpec("vmToHost", "VirtualMachine", "host", ["hToVm"])
    specs["hToVm"] = TraversalSpec("hToVm", "HostSystem", "vm", ["vmToHost"])
    visited = traverse(
        simple_inventory,
        ObjectSpec("root", list(specs)),
        specs,
        {"ComputeResource": {"ClusterComputeResource"}},
    )
    assert len({e.id for e in visited}) == len(visited)


def test_subtype_rule_is_required_for_base_type_traversals(simple_inventory):
    """A rule declared on ComputeResource must not fire for a subtype by luck."""
    without = traverse(simple_inventory, ObjectSpec("root", list(FULL)), FULL)
    assert [e.id for e in without] == ["root", "dc-1", "cl-1"]


def test_skip_excludes_the_entity_but_keeps_walking(simple_inventory):
    specs = dict(FULL)
    specs["dcToHf"] = TraversalSpec("dcToHf", "Datacenter", "hostFolder", ["crToH"], skip=True)
    visited = traverse(
        simple_inventory,
        ObjectSpec("root", list(specs)),
        specs,
        {"ComputeResource": {"ClusterComputeResource"}},
    )
    ids = [e.id for e in visited]
    assert "cl-1" not in ids
    assert "h-1" in ids


def test_unknown_start_returns_nothing(simple_inventory):
    assert traverse(simple_inventory, ObjectSpec("nope", ["visitFolders"]), FULL) == []


def test_undefined_rule_is_an_error(simple_inventory):
    with pytest.raises(TraversalError, match="ghost"):
        traverse(simple_inventory, ObjectSpec("root", ["ghost"]), FULL)


def test_dangling_reference_is_skipped():
    lonely = Entity(id="a", kind="Folder", edges={"childEntity": ["missing"]})
    inventory = Inventory([lonely])
    specs = {"f": TraversalSpec("f", "Folder", "childEntity", ["f"])}
    assert [e.id for e in traverse(inventory, ObjectSpec("a", ["f"]), specs)] == ["a"]


class TestPropertyResolution:
    def test_dotted_path(self, simple_inventory):
        vm = simple_inventory.get("vm-1")
        assert vm.resolve("summary.runtime.powerState") == "poweredOn"
        assert vm.resolve("summary.config.numCpu") == 4

    def test_absent_path_is_missing_not_none(self, simple_inventory):
        assert simple_inventory.get("vm-1").resolve("summary.nope.here") is MISSING

    def test_read_reports_partial_failure(self, simple_inventory):
        vm = simple_inventory.get("vm-1")
        result = simple_inventory.read(vm, ["name", "summary.config.numCpu", "not.there"])
        assert result.values["name"] == "VM-1"
        assert result.missing == ["not.there"]

    def test_edges_read_as_properties(self, simple_inventory):
        host = simple_inventory.get("h-1")
        assert simple_inventory.read(host, ["vm"]).values["vm"] == ["vm-1"]

    def test_set_creates_intermediate_levels(self):
        entity = Entity(id="x", kind="K")
        entity.set("a.b.c", 7)
        assert entity.props == {"a": {"b": {"c": 7}}}


class TestInventory:
    def test_remove_clears_inbound_references(self, simple_inventory):
        simple_inventory.remove("vm-1")
        assert simple_inventory.get("vm-1") is None
        assert simple_inventory.get("h-1").refs("vm") == []

    def test_find_by_prop(self, simple_inventory):
        found = simple_inventory.find_by_prop("VirtualMachine", "summary.config.numCpu", 4)
        assert found is not None and found.id == "vm-1"

    def test_round_trips_through_json(self, simple_inventory):
        restored = Inventory.from_json(simple_inventory.to_json())
        assert restored.kinds() == simple_inventory.kinds()
        assert restored.get("h-1").refs("vm") == ["vm-1"]
