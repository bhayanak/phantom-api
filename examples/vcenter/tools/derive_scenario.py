#!/usr/bin/env python3
"""Derive a synthetic scenario and type catalogs from a sanitised corpus.

A replay corpus answers only the requests that were recorded. A scenario is the
generative counterpart: a compact description of a world, from which a
simulator can synthesise an inventory of any size and answer requests that were
never captured.

This reads the corpus and emits:

    topology/inventory.json    the object graph: morefs, types, edges, properties
    catalog/vim-types.json     xsi:type registry -- which concrete type each
                               polymorphic element carried on the wire
    catalog/perf-counters.json the performance counter catalog
    catalog/roles.json         authorisation roles
    catalog/event-types.json   event type ids observed, grouped by base class
    scenario/vcenter-scenario.yaml  the generative scenario

Usage:
    python3 derive_scenario.py --corpus corpus --out .
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

VIM = "{urn:vim25}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# Reference-valued properties that form the inventory graph.
EDGE_PROPS = ("parent", "hostFolder", "vmFolder", "datastoreFolder", "networkFolder",
              "childEntity", "host", "vm", "datastore", "network", "resourcePool",
              "owner", "portgroup")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element) -> str:
    return (el.text or "").strip()


def parse_returnvals(path: Path):
    """Yield (moref_type, moref, {property_path: value_element}) per object."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return
    for rv in root.iter():
        if _local(rv.tag) != "returnval":
            continue
        obj = next((c for c in rv if _local(c.tag) == "obj"), None)
        if obj is None:
            continue
        props: dict[str, ET.Element] = {}
        for ps in rv:
            if _local(ps.tag) != "propSet":
                continue
            name = next((_text(c) for c in ps if _local(c.tag) == "name"), None)
            val = next((c for c in ps if _local(c.tag) == "val"), None)
            if name and val is not None:
                props[name] = val
        yield obj.get("type"), _text(obj), props


def morefs_in(val: ET.Element) -> list[dict]:
    out = []
    if val.get("type") and _text(val):
        out.append({"type": val.get("type"), "value": _text(val)})
    for child in val.iter():
        if child is val:
            continue
        if child.get("type") and _text(child):
            out.append({"type": child.get("type"), "value": _text(child)})
    return out


def scalar(val: ET.Element):
    kind = (val.get(XSI_TYPE) or "").replace("xsd:", "")
    text = _text(val)
    if not text or len(val):
        return None
    if kind in {"int", "long", "short"}:
        try:
            return int(text)
        except ValueError:
            return text
    if kind in {"float", "double"}:
        try:
            return float(text)
        except ValueError:
            return text
    if kind == "boolean":
        return text == "true"
    return text


def build_inventory(corpus: Path) -> dict:
    objects: dict[str, dict] = {}
    for path in sorted((corpus / "soap").glob("*RetrieveProperties*.resp.xml")):
        for kind, moref, props in parse_returnvals(path):
            if not moref:
                continue
            entry = objects.setdefault(moref, {"type": kind, "moref": moref,
                                               "props": {}, "edges": {}})
            for name, val in props.items():
                if name in EDGE_PROPS:
                    refs = morefs_in(val)
                    if refs:
                        entry["edges"].setdefault(name, [])
                        known = {(r["type"], r["value"]) for r in entry["edges"][name]}
                        entry["edges"][name].extend(
                            r for r in refs if (r["type"], r["value"]) not in known)
                        continue
                value = scalar(val)
                if value is not None and name not in entry["props"]:
                    entry["props"][name] = value

    counts = collections.Counter(o["type"] for o in objects.values())
    return {"object_count": len(objects), "by_type": dict(counts),
            "objects": list(objects.values())}


def build_type_registry(corpus: Path) -> dict:
    """Record which concrete xsi:type each element carried.

    This is the single most important artefact for a serialiser: the consumer
    resolves polymorphic fields from xsi:type, so emitting the wrong one -- or
    none at all -- silently drops data.
    """
    registry: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for path in sorted((corpus / "soap").glob("*.resp.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for el in root.iter():
            declared = el.get(XSI_TYPE)
            if declared:
                registry[_local(el.tag)][declared] += 1
    return {element: dict(types.most_common()) for element, types in sorted(registry.items())}


def build_perf_counters(corpus: Path) -> list[dict]:
    path = corpus / "soap" / "010-PerformanceManager_perfCounter.resp.xml"
    if not path.exists():
        return []
    counters = []
    for _, _, props in parse_returnvals(path):
        val = props.get("perfCounter")
        if val is None:
            continue
        for info in val:
            entry = {"key": None, "group": None, "name": None, "rollup": None,
                     "unit": None, "level": None, "statsType": None}
            for child in info:
                tag = _local(child.tag)
                if tag == "key":
                    entry["key"] = int(_text(child) or 0)
                elif tag == "rollupType":
                    entry["rollup"] = _text(child)
                elif tag == "statsType":
                    entry["statsType"] = _text(child)
                elif tag == "level":
                    entry["level"] = int(_text(child) or 0)
                elif tag in {"groupInfo", "nameInfo", "unitInfo"}:
                    key = next((_text(c) for c in child if _local(c.tag) == "key"), None)
                    entry[{"groupInfo": "group", "nameInfo": "name",
                           "unitInfo": "unit"}[tag]] = key
            if entry["key"] is not None:
                counters.append(entry)
    return counters


def build_roles(corpus: Path) -> list[dict]:
    path = corpus / "soap" / "008-AuthorizationManager_roleList.resp.xml"
    if not path.exists():
        return []
    roles = []
    for _, _, props in parse_returnvals(path):
        val = props.get("roleList")
        if val is None:
            continue
        for role in val:
            entry = {"roleId": None, "name": None, "system": None, "privilege_count": 0}
            for child in role:
                tag = _local(child.tag)
                if tag == "roleId":
                    entry["roleId"] = int(_text(child) or 0)
                elif tag == "name":
                    entry["name"] = _text(child)
                elif tag == "system":
                    entry["system"] = _text(child) == "true"
                elif tag == "privilege":
                    entry["privilege_count"] += 1
            roles.append(entry)
    return roles


def build_event_catalog(corpus: Path) -> dict:
    """Group observed event instances by their concrete xsi:type."""
    observed: collections.Counter = collections.Counter()
    shapes: dict[str, list[str]] = {}
    for path in sorted((corpus / "soap").glob("*ReadNextEvents*.resp.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for el in root.iter():
            if _local(el.tag) != "returnval":
                continue
            kind = el.get(XSI_TYPE)
            if not kind:
                continue
            observed[kind] += 1
            shapes.setdefault(kind, sorted({_local(c.tag) for c in el}))
    return {"observed_count": sum(observed.values()),
            "distinct_types": len(observed),
            "types": [{"type": k, "count": v, "fields": shapes.get(k, [])}
                      for k, v in observed.most_common()]}


def summarise_topology(inventory: dict) -> dict:
    by_type = inventory["by_type"]
    objects = {o["moref"]: o for o in inventory["objects"]}

    datacenters = []
    for obj in inventory["objects"]:
        if obj["type"] != "Datacenter":
            continue
        datacenters.append({"name": obj["props"].get("name", obj["moref"]),
                            "moref": obj["moref"]})

    clusters = []
    for obj in inventory["objects"]:
        if obj["type"] not in {"ClusterComputeResource", "ComputeResource"}:
            continue
        hosts = obj["edges"].get("host", [])
        vm_total = 0
        for h in hosts:
            host = objects.get(h["value"])
            if host:
                vm_total += len(host["edges"].get("vm", []))
        clusters.append({"name": obj["props"].get("name", obj["moref"]),
                         "hosts": len(hosts), "vms": vm_total})

    datastores = []
    for obj in inventory["objects"]:
        if obj["type"] != "Datastore":
            continue
        datastores.append({"name": obj["props"].get("name", obj["moref"]),
                           "type": obj["props"].get("type", "VMFS")})

    return {"by_type": by_type, "datacenters": datacenters, "clusters": clusters,
            "datastores": datastores}


SCENARIO_TEMPLATE = """# Generative scenario for the vcenter simulator.
#
# Derived from a sanitised capture of a live endpoint by tools/derive_scenario.py.
# Shapes and proportions are real; every name, address and identifier is a
# pseudonym. Edit freely -- this file is the contract, not the corpus.

service:
  # Reported by RetrieveServiceContent. The consumer pins its SOAPAction from
  # api_version, so changing it changes the wire dialect.
  name: "Mock vCenter Server"
  full_name: "Mock vCenter Server {version} build-{build}"
  vendor: "Example, Inc."
  version: "{version}"
  build: "{build}"
  api_type: VirtualCenter
  api_version: "{api_version}"
  os_type: linux-x64
  product_line_id: vpx
  locale_version: INTL
  # Stable across restarts: consumers key all their state on this value.
  instance_uuid: 11111111-2222-4333-8444-555555555555
  # Returned by OptionManager for config.vpxd.rhttpproxy.httpsport.
  https_port: 443

auth:
  # accept-any is a development default and prints a warning on start.
  # Use `fixed` with credentials from the environment for anything shared.
  mode: accept-any
  username: administrator@vsphere.local
  password: ${{MOCK_VC_PASSWORD}}
  # Session cookie name the consumer echoes back on every request.
  session_cookie: vmware_soap_session
  session_idle_timeout: 30m

topology:
{topology}

catalogs:
  # Loaded from catalog/. Keep these as data, not code: they are large,
  # mechanical, and change with the emulated version.
  perf_counters: catalog/perf-counters.json
  roles: catalog/roles.json
  event_types: catalog/event-types.json
  type_registry: catalog/vim-types.json

seed: 20260913

change_engine:
  # off | timeline | churn | replay
  mode: timeline
  # Every entry mutates the inventory first, then appends the matching event to
  # the event log. A mock that reports a power-off while the model still says
  # poweredOn teaches consumers to distrust their own reconciliation.
  timeline:
    - {{ at: 60s,  event: VmPoweredOffEvent,    target: "vm:VM-0001" }}
    - {{ at: 65s,  event: VmPoweredOnEvent,     target: "vm:VM-0001" }}
    - {{ at: 120s, event: VmMigratedEvent,      target: "vm:VM-0002", to: "host:esx-02" }}
    - {{ at: 180s, event: VmReconfiguredEvent,  target: "vm:VM-0003", memory_mb: 8192 }}
    - {{ at: 240s, event: HostDisconnectedEvent, target: "host:esx-03" }}
    - {{ at: 300s, event: HostConnectedEvent,   target: "host:esx-03" }}
    - {{ at: 360s, event: DatastoreCapacityIncreasedEvent, target: "ds:DS-01", capacity_gb: 4096 }}
    - {{ at: 420s, event: VmCreatedEvent,       target: "cluster:Cluster-01" }}
    - {{ at: 480s, event: VmRemovedEvent,       target: "vm:VM-0004" }}
    - {{ at: 540s, event: ClusterReconfiguredEvent, target: "cluster:Cluster-02" }}
  churn:
    events_per_minute: 20
    weights:
      VmPoweredOnEvent: 3
      VmPoweredOffEvent: 3
      VmReconfiguredEvent: 2
      VmMigratedEvent: 1
      DrsVmPoweredOnEvent: 1

faults:
  # All off by default. Each one reproduces a failure the real endpoint has.
  login_failure_after: null
  session_expiry_after: null
  retrieve_properties_latency_ms: 0
  fault_rate: 0.0
  # The real server caps event collectors per session and faults past the limit.
  # Consumers that leak collectors only discover this in production.
  max_event_collectors: 32
  strict_collector_limit: false
"""


def render_topology(summary: dict) -> str:
    lines = ["  datacenters:"]
    clusters = summary["clusters"]
    per_dc = max(1, len(clusters) // max(1, len(summary["datacenters"])))
    idx = 0
    for dc in summary["datacenters"]:
        lines.append(f"    - name: {dc['name']}")
        owned = clusters[idx:idx + per_dc]
        idx += per_dc
        if owned:
            lines.append("      clusters:")
            for c in owned:
                lines.append(f"        - {{ name: {c['name']}, hosts: {c['hosts']}, "
                             f"vms_per_host: {max(1, c['vms'] // max(1, c['hosts']))} }}")
    lines.append("  datastores:")
    for ds in summary["datastores"][:12]:
        lines.append(f"    - {{ name: {ds['name']}, type: {ds['type']}, "
                     f"capacity_gb: 2048, free_gb: 1024 }}")
    lines.append("  networks:")
    lines.append("    - { name: PG-01, type: Network }")
    lines.append("    - { name: PG-02, type: Network }")
    lines.append("  tags:")
    lines.append("    - { category: environment, tags: [prod, dev, dr] }")
    lines.append("  storage_profiles:")
    lines.append('    - { name: "No Requirements Policy" }')
    lines.append("  content_libraries: []")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    corpus: Path = args.corpus
    out: Path = args.out
    for sub in ("topology", "catalog", "scenario"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    inventory = build_inventory(corpus)
    (out / "topology" / "inventory.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"inventory      {inventory['object_count']} objects {inventory['by_type']}")

    registry = build_type_registry(corpus)
    (out / "catalog" / "vim-types.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8")
    print(f"type registry  {len(registry)} elements")

    counters = build_perf_counters(corpus)
    (out / "catalog" / "perf-counters.json").write_text(
        json.dumps(counters, indent=2), encoding="utf-8")
    print(f"perf counters  {len(counters)}")

    roles = build_roles(corpus)
    (out / "catalog" / "roles.json").write_text(json.dumps(roles, indent=2), encoding="utf-8")
    print(f"roles          {len(roles)}")

    events = build_event_catalog(corpus)
    (out / "catalog" / "event-types.json").write_text(
        json.dumps(events, indent=2), encoding="utf-8")
    print(f"events         {events['observed_count']} instances, "
          f"{events['distinct_types']} distinct types")

    manifest = json.loads((corpus / "manifest.json").read_text())
    api_version = manifest.get("source_api_version") or "8.0.3.0"
    version = ".".join(api_version.split(".")[:3])
    about = (corpus / "soap" / "002-RetrieveServiceContent_versionPinned.resp.xml")
    build = "00000"
    if about.exists():
        m = re.search(r"<build>([^<]+)</build>", about.read_text())
        if m:
            build = m.group(1)

    scenario = SCENARIO_TEMPLATE.format(
        version=version, build=build, api_version=api_version,
        topology=render_topology(summarise_topology(inventory)),
    )
    (out / "scenario" / "vcenter-scenario.yaml").write_text(scenario, encoding="utf-8")
    print(f"scenario       {out / 'scenario' / 'vcenter-scenario.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
