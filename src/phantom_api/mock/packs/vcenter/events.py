"""Event projection for the vSphere pack.

Each entry says what a change event *does* to the model and what the resulting
event carries on the wire. The mutation runs first; the event is a report of it.
"""

from __future__ import annotations

from typing import Any

from phantom_api.mock.model.entity import Entity, Inventory
from phantom_api.mock.model.evolution import ChangeEvent
from phantom_api.mock.protocols.xml_codec import Typed

#: Which abstract class a client casting on `instanceof` will see. The xsi:type
#: emitted on the wire is what selects that branch, so this table decides which
#: handler each event reaches.
BASE_CLASS = {
    "Vm": "VmEvent",
    "Host": "HostEvent",
    "Datastore": "DatastoreEvent",
    "Cluster": "ClusterEvent",
    "Datacenter": "DatacenterEvent",
    "ResourcePool": "ResourcePoolEvent",
    "DVPortgroup": "DVPortgroupEvent",
    "Task": "TaskEvent",
}


def base_class_for(event_type: str) -> str:
    for prefix, base in BASE_CLASS.items():
        if event_type.startswith(prefix):
            return base
    if event_type.startswith("Drs") and "Vm" in event_type:
        return "VmEvent"
    if event_type == "EventEx":
        return "EventEx"
    return "Event"


def _power(state: str):
    def mutate(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
        if entity is None:
            entity = _any(inv, "VirtualMachine")
        if entity is None:
            return {}
        entity.set("summary.runtime.powerState", state)
        entity.set("runtime.powerState", state)
        return {"entity_id": entity.id, "entity_kind": entity.kind, "powerState": state}

    return mutate


def _connection(state: str):
    def mutate(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
        if entity is None:
            entity = _any(inv, "HostSystem")
        if entity is None:
            return {}
        entity.set("runtime.connectionState", state)
        return {"entity_id": entity.id, "entity_kind": entity.kind, "connectionState": state}

    return mutate


def _migrate(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    if entity is None:
        entity = _any(inv, "VirtualMachine")
    if entity is None:
        return {}
    target_name = str(args.get("to", "")).partition(":")[2] or args.get("to")
    target = None
    for host in inv.of_kind("HostSystem"):
        if host.id == target_name or host.props.get("name") == target_name:
            target = host
            break
    target = target or (inv.of_kind("HostSystem") or [None])[0]
    if target is None:
        return {"entity_id": entity.id}
    for host in inv.of_kind("HostSystem"):
        if entity.id in host.edges.get("vm", []):
            host.edges["vm"].remove(entity.id)
    target.edges.setdefault("vm", []).append(entity.id)
    entity.set("summary.runtime.host", target.id)
    entity.edges["host"] = [target.id]
    return {"entity_id": entity.id, "entity_kind": entity.kind, "host": target.id}


def _reconfigure(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    if entity is None:
        entity = _any(inv, "VirtualMachine")
    if entity is None:
        return {}
    if "memory_mb" in args:
        entity.set("summary.config.memorySizeMB", int(args["memory_mb"]))
    if "cpu_count" in args:
        entity.set("summary.config.numCpu", int(args["cpu_count"]))
    return {"entity_id": entity.id, "entity_kind": entity.kind, **args}


def _create_vm(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    hosts = inv.of_kind("HostSystem")
    if not hosts:
        return {}
    host = hosts[0]
    if entity is not None and entity.kind == "ClusterComputeResource":
        cluster_hosts = [inv.get(h) for h in entity.refs("host")]
        host = next((h for h in cluster_hosts if h), host)
    index = len(inv.of_kind("VirtualMachine")) + 1
    new = Entity(
        id=f"vm-{9000 + index}",
        kind="VirtualMachine",
        props={
            "name": args.get("name", f"VM-{index:04d}"),
            "summary": {
                "config": {
                    "name": args.get("name", f"VM-{index:04d}"),
                    "instanceUuid": f"5001{index:04d}-0000-0000-0000-000000000000",
                    "numCpu": int(args.get("cpu_count", 2)),
                    "memorySizeMB": int(args.get("memory_mb", 4096)),
                    "guestFullName": "Other Linux (64-bit)",
                },
                "runtime": {
                    "powerState": "poweredOn",
                    "connectionState": "connected",
                    "host": host.id,
                },
            },
        },
        edges={"parent": [host.id], "host": [host.id]},
    )
    inv.add(new)
    host.edges.setdefault("vm", []).append(new.id)
    return {"entity_id": new.id, "entity_kind": new.kind, "created": new.id}


def _remove_vm(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    if entity is None:
        entity = _any(inv, "VirtualMachine")
    if entity is None:
        return {}
    removed = inv.remove(entity.id)
    return {"entity_id": entity.id, "entity_kind": "VirtualMachine", "removed": bool(removed)}


def _grow_datastore(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    if entity is None:
        entity = _any(inv, "Datastore")
    if entity is None:
        return {}
    capacity = int(args.get("capacity_gb", 0)) * 1024 * 1024 * 1024
    if capacity:
        entity.set("summary.capacity", capacity)
    return {"entity_id": entity.id, "entity_kind": entity.kind, "capacity": capacity}


def _touch(inv: Inventory, entity: Entity | None, args: dict[str, Any]) -> dict[str, Any]:
    """No structural change; the event alone is the signal."""
    if entity is None:
        return dict(args)
    return {"entity_id": entity.id, "entity_kind": entity.kind, **args}


def _any(inv: Inventory, kind: str) -> Entity | None:
    items = inv.of_kind(kind)
    return items[0] if items else None


MUTATIONS = {
    "VmPoweredOnEvent": _power("poweredOn"),
    "DrsVmPoweredOnEvent": _power("poweredOn"),
    "VmPoweredOffEvent": _power("poweredOff"),
    "VmSuspendedEvent": _power("suspended"),
    "VmMigratedEvent": _migrate,
    "DrsVmMigratedEvent": _migrate,
    "VmRelocatedEvent": _migrate,
    "VmReconfiguredEvent": _reconfigure,
    "VmCreatedEvent": _create_vm,
    "VmClonedEvent": _create_vm,
    "VmRemovedEvent": _remove_vm,
    "HostConnectedEvent": _connection("connected"),
    "HostDisconnectedEvent": _connection("disconnected"),
    "HostConnectionLostEvent": _connection("notResponding"),
    "DatastoreCapacityIncreasedEvent": _grow_datastore,
    "ClusterReconfiguredEvent": _touch,
    "ClusterCreatedEvent": _touch,
    "ClusterDestroyedEvent": _touch,
    "DatacenterCreatedEvent": _touch,
    "VmRenamedEvent": _touch,
}


def to_wire(event: ChangeEvent, inventory: Inventory) -> Typed:
    """Render a change as a vim25 Event element with the right xsi:type."""
    entity = inventory.get(event.target_id) if event.target_id else None
    created = _iso(event.created_at)
    body: dict[str, Any] = {
        "key": event.key,
        "chainId": event.key,
        "createdTime": created,
        "userName": "mock",
        "fullFormattedMessage": f"{event.type} on {event.target_id or 'system'}",
    }

    if entity is not None:
        reference = Typed(entity.id, ref_type=entity.kind)
        name = entity.props.get("name", entity.id)
        if entity.kind == "VirtualMachine":
            body["vm"] = {"name": name, "vm": reference}
        elif entity.kind == "HostSystem":
            body["host"] = {"name": name, "host": reference}
        elif entity.kind == "Datastore":
            body["datastore"] = {"name": name, "datastore": reference}
        elif entity.kind in {"ClusterComputeResource", "ComputeResource"}:
            body["computeResource"] = {"name": name, "computeResource": reference}
        elif entity.kind == "Datacenter":
            body["datacenter"] = {"name": name, "datacenter": reference}
        elif entity.kind == "ResourcePool":
            body["resourcePool"] = {"name": name, "resourcePool": reference}

    if event.type == "EventEx":
        body["eventTypeId"] = event.detail.get("eventTypeId", "com.example.event")

    return Typed(body, type_name=event.type)


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
