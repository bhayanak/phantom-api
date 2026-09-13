"""The vSphere domain pack: scenario to inventory, and operations to projections.

This is the whole product-specific half of the vCenter mock. Everything else it
needs -- SOAP framing, sessions, cursors, traversal, chaos, the control plane --
comes from the framework. The ratio is the design target for any future pack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phantom_api.mock.model.entity import Entity, Inventory
from phantom_api.mock.model.generator import Builder
from phantom_api.mock.packs.base import register
from phantom_api.mock.packs.vcenter.events import MUTATIONS
from phantom_api.mock.packs.vcenter.projections import PROJECTIONS, ROOT_FOLDER

GB = 1024 * 1024 * 1024


class VCenterPack:
    name = "vcenter"

    def build(self, scenario: dict[str, Any], seed: int | None) -> Inventory:
        builder = Builder(seed=seed)
        for kind, prefix in (
            ("Datacenter", "datacenter"),
            ("Folder", "group"),
            ("ClusterComputeResource", "domain-c"),
            ("HostSystem", "host"),
            ("VirtualMachine", "vm"),
            ("Datastore", "datastore"),
            ("Network", "network"),
            ("ResourcePool", "resgroup"),
        ):
            builder.ids.prefix_for(kind, prefix)

        root = Entity(id=ROOT_FOLDER, kind="Folder", props={"name": "Datacenters"})
        builder.inventory.add(root)

        topology = scenario.get("topology") or {}
        datastores = self._datastores(builder, topology, root)
        networks = self._networks(builder, topology, root)

        for dc_spec in topology.get("datacenters") or []:
            self._datacenter(builder, root, dc_spec, datastores, networks, scenario)

        return builder.inventory

    # -- construction ----------------------------------------------------

    def _datastores(self, builder: Builder, topology: dict, root: Entity) -> list[Entity]:
        out = []
        for spec in topology.get("datastores") or []:
            capacity = int(spec.get("capacity_gb", 2048)) * GB
            free = int(spec.get("free_gb", capacity // GB // 2)) * GB
            entity = builder.entity(
                "Datastore",
                name=spec.get("name", "DS"),
                props={
                    "summary": {
                        "name": spec.get("name", "DS"),
                        "type": spec.get("type", "VMFS"),
                        "accessible": True,
                        "capacity": capacity,
                        "freeSpace": free,
                        "uncommitted": 0,
                        "multipleHostAccess": True,
                        "maintenanceMode": "normal",
                    },
                    "overallStatus": "green",
                },
                parent=root,
            )
            out.append(entity)
        return out

    def _networks(self, builder: Builder, topology: dict, root: Entity) -> list[Entity]:
        out = []
        for spec in topology.get("networks") or []:
            entity = builder.entity(
                "Network",
                name=spec.get("name", "VM Network"),
                props={"summary": {"name": spec.get("name", "VM Network"), "accessible": True}},
                parent=root,
            )
            out.append(entity)
        return out

    def _datacenter(
        self,
        builder: Builder,
        root: Entity,
        spec: dict,
        datastores: list[Entity],
        networks: list[Entity],
        scenario: dict,
    ) -> Entity:
        name = spec.get("name", "DC")
        datacenter = builder.entity("Datacenter", name=name, parent=root, parent_edge="childEntity")
        host_folder = builder.entity("Folder", name="host", parent=datacenter)
        vm_folder = builder.entity("Folder", name="vm", parent=datacenter)
        ds_folder = builder.entity("Folder", name="datastore", parent=datacenter)
        net_folder = builder.entity("Folder", name="network", parent=datacenter)
        datacenter.edges["hostFolder"] = [host_folder.id]
        datacenter.edges["vmFolder"] = [vm_folder.id]
        datacenter.edges["datastoreFolder"] = [ds_folder.id]
        datacenter.edges["networkFolder"] = [net_folder.id]
        for entity in datastores:
            ds_folder.edges.setdefault("childEntity", []).append(entity.id)
            datacenter.edges.setdefault("datastore", []).append(entity.id)
        for entity in networks:
            net_folder.edges.setdefault("childEntity", []).append(entity.id)
            datacenter.edges.setdefault("network", []).append(entity.id)

        for cluster_spec in spec.get("clusters") or []:
            self._cluster(builder, host_folder, vm_folder, cluster_spec, datastores, networks)
        return datacenter

    def _cluster(
        self,
        builder: Builder,
        host_folder: Entity,
        vm_folder: Entity,
        spec: dict,
        datastores: list[Entity],
        networks: list[Entity],
    ) -> Entity:
        name = spec.get("name", "Cluster")
        cluster = builder.entity(
            "ClusterComputeResource",
            name=name,
            parent=host_folder,
            parent_edge="childEntity",
            props={
                "summary": {
                    "numHosts": int(spec.get("hosts", 0)),
                    "numEffectiveHosts": int(spec.get("hosts", 0)),
                    "overallStatus": "green",
                    "totalCpu": 0,
                    "totalMemory": 0,
                },
                "configuration": {
                    "dasConfig": {
                        "enabled": bool(spec.get("ha_enabled", True)),
                        "hostMonitoring": "enabled",
                    },
                    "drsConfig": {
                        "enabled": bool(spec.get("drs_enabled", True)),
                        "defaultVmBehavior": spec.get("drs_behavior", "fullyAutomated"),
                        "vmotionRate": 3,
                    },
                },
            },
        )
        pool = builder.entity(
            "ResourcePool",
            name="Resources",
            parent=cluster,
            parent_edge="resourcePool",
            props={"overallStatus": "green"},
        )
        pool.edges["owner"] = [cluster.id]

        total_cpu = total_memory = 0
        for index in range(int(spec.get("hosts", 0))):
            host = self._host(builder, cluster, index + 1, datastores, networks)
            total_cpu += host.props["summary"]["hardware"]["cpuMhz"]
            total_memory += host.props["summary"]["hardware"]["memorySize"]
            for vm_index in range(int(spec.get("vms_per_host", 0))):
                self._vm(builder, host, pool, vm_folder, datastores, networks, vm_index + 1)
        cluster.props["summary"]["totalCpu"] = total_cpu
        cluster.props["summary"]["totalMemory"] = total_memory
        return cluster

    def _host(
        self,
        builder: Builder,
        cluster: Entity,
        index: int,
        datastores: list[Entity],
        networks: list[Entity],
    ) -> Entity:
        name = f"esx-{index:02d}.{_domain(builder)}"
        host = builder.entity(
            "HostSystem",
            name=name,
            parent=cluster,
            parent_edge="host",
            props={
                "runtime": {
                    "connectionState": "connected",
                    "powerState": "poweredOn",
                    "inMaintenanceMode": False,
                },
                "capability": {"iscsiSupported": True, "supportedVmfsMajorVersion": [5, 6]},
                "config": {
                    "product": {
                        "version": "8.0.3",
                        "build": "24022510",
                        "fullName": "Mock ESX 8.0.3 build-24022510",
                    },
                    "hyperThread": {"active": True},
                },
                "summary": {
                    "hardware": {
                        "uuid": f"4c4c4544-0000-0000-0000-{index:012d}",
                        "model": "Example Compute 2U",
                        "vendor": "Example, Inc.",
                        "cpuMhz": 2400,
                        "numCpuCores": 32,
                        "numCpuPkgs": 2,
                        "memorySize": 512 * GB,
                    },
                    "quickStats": {"overallCpuUsage": 4200, "overallMemoryUsage": 90000},
                    "managementServerIp": "127.0.0.1",
                },
            },
        )
        for entity in datastores:
            host.edges.setdefault("datastore", []).append(entity.id)
            entity.edges.setdefault("host", []).append(host.id)
        for entity in networks:
            host.edges.setdefault("network", []).append(entity.id)
        return host

    def _vm(
        self,
        builder: Builder,
        host: Entity,
        pool: Entity,
        vm_folder: Entity,
        datastores: list[Entity],
        networks: list[Entity],
        index: int,
    ) -> Entity:
        number = len(builder.inventory.of_kind("VirtualMachine")) + 1
        name = f"VM-{number:04d}"
        datastore = datastores[number % len(datastores)] if datastores else None
        vm = builder.entity(
            "VirtualMachine",
            name=name,
            props={
                "summary": {
                    "config": {
                        "name": name,
                        "instanceUuid": f"5000{number:04d}-0000-4000-8000-000000000000",
                        "uuid": f"4200{number:04d}-0000-4000-8000-000000000000",
                        "guestFullName": "Other Linux (64-bit)",
                        "numCpu": 2 + (number % 3) * 2,
                        "memorySizeMB": 4096 * (1 + number % 4),
                        "vmPathName": (
                            f"[{datastore.props['name']}] {name}/{name}.vmx"
                            if datastore
                            else f"[local] {name}/{name}.vmx"
                        ),
                        "template": False,
                    },
                    "runtime": {
                        "powerState": "poweredOn",
                        "connectionState": "connected",
                        "host": host.id,
                    },
                    "storage": {"committed": 20 * GB, "uncommitted": 10 * GB, "unshared": 20 * GB},
                    "guest": {
                        "ipAddress": f"192.0.2.{(number % 250) + 1}",
                        "hostName": name.lower(),
                    },
                },
                "config": {"version": "vmx-20", "uuid": f"4200{number:04d}-0000-4000-8000-0"},
                "layout": {"swapFile": f"[local] {name}/{name}.vswp"},
            },
        )
        vm.edges["parent"] = [vm_folder.id]
        vm.edges["host"] = [host.id]
        vm.edges["resourcePool"] = [pool.id]
        vm_folder.edges.setdefault("childEntity", []).append(vm.id)
        host.edges.setdefault("vm", []).append(vm.id)
        pool.edges.setdefault("vm", []).append(vm.id)
        if datastore:
            vm.edges["datastore"] = [datastore.id]
            datastore.edges.setdefault("vm", []).append(vm.id)
        for entity in networks[:1]:
            vm.edges.setdefault("network", []).append(entity.id)
        return vm

    # -- pack interface --------------------------------------------------

    def projections(self) -> dict[str, Any]:
        return dict(PROJECTIONS)

    def mutations(self) -> dict[str, Any]:
        return dict(MUTATIONS)

    def catalogs(self, data_dir: Path | None) -> dict[str, Any]:
        """Load reference data, preferring a corpus-derived catalog if present."""
        loaded: dict[str, Any] = {"perf_counters": [], "roles": [], "event_types": {}}
        candidates = [p for p in (data_dir, Path.cwd() / "examples/vcenter") if p]
        for base in candidates:
            catalog_dir = base / "catalog"
            if not catalog_dir.is_dir():
                continue
            for key, filename in (
                ("perf_counters", "perf-counters.json"),
                ("roles", "roles.json"),
                ("event_types", "event-types.json"),
                ("event_subscription", "event-subscription.json"),
            ):
                path = catalog_dir / filename
                if path.exists():
                    loaded[key] = json.loads(path.read_text(encoding="utf-8"))
            break
        if not loaded["perf_counters"]:
            loaded["perf_counters"] = _DEFAULT_COUNTERS
        if not loaded["roles"]:
            loaded["roles"] = _DEFAULT_ROLES
        return loaded


def _domain(builder: Builder) -> str:
    return "lab.example.com"


_DEFAULT_COUNTERS = [
    {
        "key": 1,
        "group": "cpu",
        "name": "usage",
        "unit": "percent",
        "rollup": "average",
        "level": 1,
        "statsType": "rate",
    },
    {
        "key": 2,
        "group": "cpu",
        "name": "usagemhz",
        "unit": "megaHertz",
        "rollup": "average",
        "level": 1,
        "statsType": "rate",
    },
    {
        "key": 24,
        "group": "mem",
        "name": "usage",
        "unit": "percent",
        "rollup": "average",
        "level": 1,
        "statsType": "absolute",
    },
    {
        "key": 125,
        "group": "disk",
        "name": "numberReadAveraged",
        "unit": "number",
        "rollup": "average",
        "level": 1,
        "statsType": "rate",
    },
    {
        "key": 126,
        "group": "disk",
        "name": "numberWriteAveraged",
        "unit": "number",
        "rollup": "average",
        "level": 1,
        "statsType": "rate",
    },
    {
        "key": 133,
        "group": "disk",
        "name": "totalLatency",
        "unit": "millisecond",
        "rollup": "average",
        "level": 2,
        "statsType": "absolute",
    },
]

_DEFAULT_ROLES = [
    {"roleId": -1, "name": "NoAccess", "system": True, "privilege_count": 0},
    {"roleId": -2, "name": "Anonymous", "system": True, "privilege_count": 0},
    {"roleId": -3, "name": "View", "system": True, "privilege_count": 1},
    {"roleId": -4, "name": "ReadOnly", "system": True, "privilege_count": 3},
    {"roleId": -5, "name": "Admin", "system": True, "privilege_count": 400},
]


register(VCenterPack())
