"""Operation projections for the vSphere pack.

Each function answers one SOAP operation against the model. They are the only
code in the package that knows vim25 vocabulary; the traversal, cursors,
sessions and faults they use are all framework.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count
from typing import Any

from phantom_api.mock.model.entity import Entity
from phantom_api.mock.model.graph import ObjectSpec, TraversalSpec, traverse
from phantom_api.mock.packs.base import PackContext
from phantom_api.mock.packs.vcenter import events as event_module
from phantom_api.mock.protocols.xml_codec import Raw, Typed, to_xml
from phantom_api.mock.session import CursorLimitReached
from phantom_api.mock.types import MockError, Operation

ROOT_FOLDER = "group-d1"

#: View handles only need to be unique for the life of the process.
_VIEW_IDS = count(1)

#: Fixed identifiers for the service singletons, matching the real product so
#: clients that hard-code them keep working.
SINGLETONS = {
    "rootFolder": ("Folder", ROOT_FOLDER),
    "propertyCollector": ("PropertyCollector", "propertyCollector"),
    "viewManager": ("ViewManager", "ViewManager"),
    "setting": ("OptionManager", "VpxSettings"),
    "userDirectory": ("UserDirectory", "UserDirectory"),
    "sessionManager": ("SessionManager", "SessionManager"),
    "authorizationManager": ("AuthorizationManager", "AuthorizationManager"),
    "serviceManager": ("ServiceManager", "ServiceMgr"),
    "perfManager": ("PerformanceManager", "PerfMgr"),
    "scheduledTaskManager": ("ScheduledTaskManager", "ScheduledTaskManager"),
    "alarmManager": ("AlarmManager", "AlarmManager"),
    "eventManager": ("EventManager", "EventManager"),
    "taskManager": ("TaskManager", "TaskManager"),
    "extensionManager": ("ExtensionManager", "ExtensionManager"),
    "customFieldsManager": ("CustomFieldsManager", "CustomFieldsManager"),
    "diagnosticManager": ("DiagnosticManager", "DiagMgr"),
    "licenseManager": ("LicenseManager", "LicenseManager"),
    "searchIndex": ("SearchIndex", "SearchIndex"),
    "fileManager": ("FileManager", "FileManager"),
    "virtualDiskManager": ("VirtualDiskManager", "virtualDiskManager"),
    "dvSwitchManager": ("DistributedVirtualSwitchManager", "DVSManager"),
    "storageResourceManager": ("StorageResourceManager", "StorageResourceManager"),
    "guestOperationsManager": ("GuestOperationsManager", "guestOperationsManager"),
    "ovfManager": ("OvfManager", "OvfManager"),
    "ipPoolManager": ("IpPoolManager", "IpPoolManager"),
    "hostProfileManager": ("HostProfileManager", "HostProfileManager"),
    "clusterProfileManager": ("ClusterProfileManager", "ClusterProfileManager"),
    "localizationManager": ("LocalizationManager", "LocalizationManager"),
}


#: Clients write traversals and property specs against base types, so the
#: concrete kinds that satisfy each one have to be declared.
SUBTYPES = {
    "ComputeResource": {"ClusterComputeResource"},
    "ManagedEntity": {
        "Folder",
        "Datacenter",
        "ComputeResource",
        "ClusterComputeResource",
        "HostSystem",
        "VirtualMachine",
        "Datastore",
        "Network",
        "ResourcePool",
    },
    "Network": {"DistributedVirtualPortgroup"},
    "ResourcePool": {"VirtualApp"},
}


def _kinds_for(declared: str) -> set[str]:
    return {declared} | SUBTYPES.get(declared, set())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _service(ctx: PackContext) -> dict[str, Any]:
    return ctx.scenario.get("service") or {}


def _require_session(ctx: PackContext, op: Operation) -> Any:
    session = ctx.sessions.get(op.session_id)
    if session is None or not session.authenticated:
        raise MockError(
            "The session is not authenticated.",
            kind="not-authenticated",
            detail_type="NotAuthenticated",
            detail={
                "object": Typed("ServiceInstance", ref_type="ServiceInstance"),
                "privilegeId": "",
            },
        )
    return session


# -- session -----------------------------------------------------------------


def retrieve_service_content(ctx: PackContext, op: Operation) -> dict[str, Any]:
    service = _service(ctx)
    content: dict[str, Any] = {}
    for field, (kind, value) in SINGLETONS.items():
        content[field] = Typed(value, ref_type=kind)
        if field == "propertyCollector":
            content["about"] = None  # placeholder, replaced below to keep ordering
    version = str(service.get("version", "8.0.3"))
    content["about"] = {
        "name": service.get("name", "Mock vCenter Server"),
        "fullName": service.get(
            "full_name", f"Mock vCenter Server {version} build-{service.get('build', '00000')}"
        ),
        "vendor": service.get("vendor", "Example, Inc."),
        "version": version,
        "patchLevel": "00000",
        "build": str(service.get("build", "00000")),
        "localeVersion": service.get("locale_version", "INTL"),
        "localeBuild": "000",
        "osType": service.get("os_type", "linux-x64"),
        "productLineId": service.get("product_line_id", "vpx"),
        "apiType": service.get("api_type", "VirtualCenter"),
        "apiVersion": str(service.get("api_version", f"{version}.0")),
        "instanceUuid": service.get("instance_uuid", "11111111-2222-4333-8444-555555555555"),
        "licenseProductName": "Mock VirtualCenter Server",
        "licenseProductVersion": version.rsplit(".", 1)[0],
    }
    return content


def login(ctx: PackContext, op: Operation) -> dict[str, Any]:
    auth = ctx.scenario.get("auth") or {}
    mode = auth.get("mode", "accept-any")
    username = str(op.params.get("userName", ""))
    password = str(op.params.get("password", ""))

    if mode == "reject" or (
        mode == "fixed"
        and (username != auth.get("username") or password != str(auth.get("password", "")))
    ):
        raise MockError(
            "Cannot complete login due to an incorrect user name or password.",
            kind="invalid-login",
            detail_type="InvalidLogin",
        )

    session = ctx.sessions.get(op.session_id) or ctx.sessions.create()
    session.authenticated = True
    session.username = username
    session.locale = str(op.params.get("locale", "en_US"))
    return {
        "key": session.id,
        "userName": username,
        "fullName": username,
        "loginTime": _now(),
        "lastActiveTime": _now(),
        "locale": session.locale,
        "messageLocale": session.locale,
        "extensionSession": False,
        "ipAddress": "127.0.0.1",
        "userAgent": op.headers.get("user-agent", "mock-client"),
        "callCount": 0,
    }


def logout(ctx: PackContext, op: Operation) -> None:
    ctx.sessions.destroy(op.session_id)
    return None


def current_time(ctx: PackContext, op: Operation) -> str:
    _require_session(ctx, op)
    return _now()


def session_is_active(ctx: PackContext, op: Operation) -> bool:
    return ctx.sessions.get(op.session_id) is not None


# -- property collector ------------------------------------------------------


def _traversal_specs(spec_set: Any) -> dict[str, TraversalSpec]:
    """Read the client's traversal program out of the decoded parameters."""
    specs: dict[str, TraversalSpec] = {}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        name, kind, path = node.get("name"), node.get("type"), node.get("path")
        if name and kind and path:
            select = node.get("selectSet")
            children = select if isinstance(select, list) else ([select] if select else [])
            specs[str(name)] = TraversalSpec(
                name=str(name),
                kind=str(kind),
                edge=str(path),
                select=[
                    str(c.get("name")) for c in children if isinstance(c, dict) and c.get("name")
                ],
                skip=str(node.get("skip", "false")).lower() == "true",
            )
        for value in node.values():
            walk(value)

    walk(spec_set)
    return specs


def _object_specs(spec_set: Any) -> list[ObjectSpec]:
    out: list[ObjectSpec] = []
    for filter_spec in _as_list(spec_set):
        for object_spec in _as_list(filter_spec.get("objectSet")):
            obj = object_spec.get("obj")
            start = obj.get("#text") if isinstance(obj, dict) else obj
            if not start:
                continue
            select = object_spec.get("selectSet")
            children = select if isinstance(select, list) else ([select] if select else [])
            out.append(
                ObjectSpec(
                    start=str(start),
                    select=[
                        str(c.get("name"))
                        for c in children
                        if isinstance(c, dict) and c.get("name")
                    ],
                    skip=str(object_spec.get("skip", "false")).lower() == "true",
                )
            )
    return out


def _property_specs(spec_set: Any) -> list[tuple[str, bool, list[str]]]:
    out: list[tuple[str, bool, list[str]]] = []
    for filter_spec in _as_list(spec_set):
        for prop_spec in _as_list(filter_spec.get("propSet")):
            kind = str(prop_spec.get("type", ""))
            all_props = str(prop_spec.get("all", "false")).lower() == "true"
            paths = prop_spec.get("pathSet") or []
            if isinstance(paths, str):
                paths = [paths]
            out.append((kind, all_props, [str(p) for p in paths]))
    return out


def _as_list(node: Any) -> list[dict[str, Any]]:
    if node is None:
        return []
    if isinstance(node, list):
        return [n for n in node if isinstance(n, dict)]
    return [node] if isinstance(node, dict) else []


def _collect(ctx: PackContext, op: Operation) -> list[Raw]:
    """Evaluate the client's filter and render one <returnval> per object."""
    spec_set = op.params.get("specSet")
    specs = _traversal_specs(spec_set)
    prop_specs = _property_specs(spec_set)
    wanted: dict[str, tuple[bool, list[str]]] = {}
    for kind, all_props, paths in prop_specs:
        for concrete in _kinds_for(kind):
            wanted[concrete] = (all_props, paths)

    visited: list[Entity] = []
    seen: set[str] = set()
    for object_spec in _object_specs(spec_set):
        for entity in traverse(ctx.inventory, object_spec, specs, SUBTYPES):
            if entity.id not in seen:
                seen.add(entity.id)
                visited.append(entity)

    results: list[Raw] = []
    for entity in visited:
        if entity.kind not in wanted:
            continue
        all_props, paths = wanted[entity.kind]
        if all_props:
            paths = sorted(entity.props) + sorted(entity.edges)
        result = ctx.inventory.read(entity, paths)
        results.append(Raw(_render_object(entity, result)))
    return results


def _render_object(entity: Entity, result: Any) -> str:
    parts = [to_xml("obj", Typed(entity.id, ref_type=entity.kind))]
    for path in sorted(result.values):
        parts.append(
            "<propSet>"
            + to_xml("name", path)
            + to_xml("val", _as_value(path, result.values[path], entity))
            + "</propSet>"
        )
    for path in sorted(result.missing):
        parts.append(
            "<missingSet>"
            + to_xml("path", path)
            + '<fault><fault xsi:type="NotAuthenticated">'
            + to_xml("object", Typed(entity.id, ref_type=entity.kind))
            + "<privilegeId>System.Read</privilegeId></fault>"
            + "<localizedMessage></localizedMessage></fault></missingSet>"
        )
    return f"<returnval>{''.join(parts)}</returnval>"


#: Edge names whose values are object references rather than scalars.
_REFERENCE_EDGES = {
    "parent",
    "host",
    "vm",
    "datastore",
    "network",
    "resourcePool",
    "hostFolder",
    "vmFolder",
    "datastoreFolder",
    "networkFolder",
    "childEntity",
    "owner",
    "portgroup",
}

#: Which reference kind each edge points at, for the ``type=`` attribute.
_EDGE_KINDS = {
    "parent": "Folder",
    "host": "HostSystem",
    "vm": "VirtualMachine",
    "datastore": "Datastore",
    "network": "Network",
    "resourcePool": "ResourcePool",
    "hostFolder": "Folder",
    "vmFolder": "Folder",
    "datastoreFolder": "Folder",
    "networkFolder": "Folder",
    "childEntity": "ManagedEntity",
    "owner": "Datacenter",
    "portgroup": "DistributedVirtualPortgroup",
}


def _as_value(path: str, value: Any, entity: Entity) -> Any:
    if path in _REFERENCE_EDGES and isinstance(value, list):
        kind = _EDGE_KINDS.get(path, "ManagedEntity")
        refs = [Typed(ref, ref_type=kind) for ref in value]
        if path in {
            "parent",
            "hostFolder",
            "vmFolder",
            "datastoreFolder",
            "networkFolder",
            "owner",
        }:
            return Typed(refs[0].value, ref_type=kind) if refs else None
        return refs
    if isinstance(value, bool):
        return Typed(value, type_name="xsd:boolean")
    if isinstance(value, int):
        return Typed(value, type_name="xsd:long" if abs(value) > 2**31 else "xsd:int")
    if isinstance(value, str):
        return Typed(value, type_name="xsd:string")
    return value


def retrieve_properties(ctx: PackContext, op: Operation) -> Raw:
    _require_session(ctx, op)
    singleton = _singleton_read(ctx, op)
    if singleton is not None:
        return singleton
    return Raw("".join(item.xml for item in _collect(ctx, op)))


#: Properties that live on a service singleton rather than on an inventory
#: object. The client reads them through the same PropertyCollector call, so
#: they have to be answered there.
_SINGLETON_PROPERTIES: dict[tuple[str, str], str] = {
    ("PerformanceManager", "perfCounter"): "perf_counters",
    ("AuthorizationManager", "roleList"): "roles",
}


def _singleton_read(ctx: PackContext, op: Operation) -> Raw | None:
    for kind, _all_props, paths in _property_specs(op.params.get("specSet")):
        for path in paths:
            handler = _SINGLETON_PROPERTIES.get((kind, path))
            if handler is None:
                continue
            values = perf_counters(ctx, op) if handler == "perf_counters" else role_list(ctx, op)
            target = SINGLETONS.get(
                "perfManager" if handler == "perf_counters" else "authorizationManager"
            )
            body = (
                to_xml("obj", Typed(target[1], ref_type=target[0]))
                + "<propSet>"
                + to_xml("name", path)
                + to_xml("val", values)
                + "</propSet>"
            )
            return Raw(f"<returnval>{body}</returnval>")
    return None


def retrieve_properties_ex(ctx: PackContext, op: Operation) -> Raw:
    _require_session(ctx, op)
    items = _collect(ctx, op)
    options = op.params.get("options") or {}
    try:
        max_objects = int(options.get("maxObjects", 0))
    except (TypeError, ValueError):
        max_objects = 0
    if max_objects <= 0 or len(items) <= max_objects:
        payload = "".join(i.xml for i in items)
        return Raw(f"<returnval>{payload}</returnval>" if items else "")
    cursor = ctx.cursors.create("retrieve", items, session_id=op.session_id)
    chunk = cursor.take(max_objects)
    body = "".join(i.xml for i in chunk) + to_xml("token", cursor.token)
    return Raw(f"<returnval>{body}</returnval>")


def continue_retrieve_properties_ex(ctx: PackContext, op: Operation) -> Raw:
    _require_session(ctx, op)
    cursor = ctx.cursors.get(str(op.params.get("token", "")))
    if cursor is None:
        raise MockError(
            "Invalid continuation token.", kind="invalid-request", detail_type="InvalidArgument"
        )
    # A page size is not repeated on continuation; the real server keeps its own.
    chunk = cursor.take(len(cursor.items))
    body = "".join(i.xml for i in chunk)
    if cursor.exhausted:
        ctx.cursors.destroy(cursor.token)
    else:
        body += to_xml("token", cursor.token)
    return Raw(f"<returnval>{body}</returnval>")


def cancel_retrieve_properties_ex(ctx: PackContext, op: Operation) -> None:
    ctx.cursors.destroy(str(op.params.get("token", "")))
    return None


# -- options, roles, search --------------------------------------------------


def query_options(ctx: PackContext, op: Operation) -> list[Any]:
    _require_session(ctx, op)
    service = _service(ctx)
    name = str(op.params.get("name", ""))
    known = {
        "config.vpxd.rhttpproxy.httpsport": Typed(
            int(service.get("https_port", 443)), type_name="xsd:int"
        ),
        "VirtualCenter.InstanceName": Typed(
            str(service.get("name", "mock-vcenter")), type_name="xsd:string"
        ),
    }
    if name and name in known:
        return [{"key": name, "value": known[name]}]
    if name:
        raise MockError(
            f"Option {name} is not supported.", kind="invalid-request", detail_type="InvalidName"
        )
    return [{"key": key, "value": value} for key, value in known.items()]


def role_list(ctx: PackContext, op: Operation) -> list[Any]:
    roles = ctx.catalog("roles") or []
    return [
        Typed(
            {
                "roleId": role.get("roleId", index),
                "system": role.get("system", True),
                "name": role.get("name", f"role-{index}"),
                "info": {"label": role.get("name", ""), "summary": role.get("name", "")},
            },
            type_name="AuthorizationRole",
        )
        for index, role in enumerate(roles)
    ]


def has_privilege_on_entities(ctx: PackContext, op: Operation) -> list[Any]:
    _require_session(ctx, op)
    priv = op.params.get("privId")
    privileges = priv if isinstance(priv, list) else [priv or "System.View"]
    return [
        {
            "object": op.params.get("entity"),
            "privAvailability": [{"privId": p, "isGranted": True} for p in privileges],
        }
    ]


def find_by_uuid(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    uuid = str(op.params.get("uuid", ""))
    entity = ctx.inventory.find_by_prop(
        "VirtualMachine", "summary.config.instanceUuid", uuid
    ) or ctx.inventory.find_by_prop("VirtualMachine", "config.instanceUuid", uuid)
    if entity is None:
        return None
    return Typed(entity.id, ref_type=entity.kind)


# -- events ------------------------------------------------------------------


def create_collector_for_events(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    ctx.evolution.tick()
    wanted = op.params.get("filter") or {}
    type_ids = wanted.get("eventTypeId")
    allowed = set(type_ids if isinstance(type_ids, list) else ([type_ids] if type_ids else []))
    events = [
        e
        for e in ctx.evolution.events
        if not allowed or e.type in allowed or event_module.base_class_for(e.type) in allowed
    ]
    try:
        cursor = ctx.cursors.create("events", events, session_id=op.session_id)
    except CursorLimitReached as exc:
        raise MockError(str(exc), kind="invalid-request", detail_type="InvalidState") from exc
    return Typed(cursor.token, ref_type="EventHistoryCollector")


def read_next_events(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    token = op.context.get("target_value") or ""
    cursor = ctx.cursors.get(str(token))
    if cursor is None:
        raise MockError(
            "The event collector no longer exists.",
            kind="not-found",
            detail_type="ManagedObjectNotFound",
        )
    try:
        count = int(op.params.get("maxCount", 100))
    except (TypeError, ValueError):
        count = 100
    chunk = cursor.take(max(1, min(count, 1000)))
    if not chunk:
        return None
    return [event_module.to_wire(event, ctx.inventory) for event in chunk]


def reset_collector(ctx: PackContext, op: Operation) -> None:
    cursor = ctx.cursors.get(str(op.context.get("target_value") or ""))
    if cursor is not None:
        cursor.reset()
    return None


def destroy_collector(ctx: PackContext, op: Operation) -> None:
    ctx.cursors.destroy(str(op.context.get("target_value") or ""))
    return None


def query_events(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    ctx.evolution.tick()
    events = ctx.evolution.events[-1000:]
    if not events:
        return None
    return [event_module.to_wire(event, ctx.inventory) for event in events]


# -- performance -------------------------------------------------------------


def perf_counters(ctx: PackContext, op: Operation) -> list[Any]:
    counters = ctx.catalog("perf_counters") or []

    def info(value: str) -> dict[str, str]:
        return {"label": value, "summary": value, "key": value}

    return [
        Typed(
            {
                "key": counter["key"],
                "nameInfo": info(counter.get("name", "")),
                "groupInfo": info(counter.get("group", "")),
                "unitInfo": info(counter.get("unit", "")),
                "rollupType": counter.get("rollup", "average"),
                "statsType": counter.get("statsType", "absolute"),
                "level": counter.get("level", 1),
            },
            type_name="PerfCounterInfo",
        )
        for counter in counters
    ]


def query_perf(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    specs = _as_list(op.params.get("querySpec"))
    counters = (ctx.catalog("perf_counters") or [])[:12]
    samples: list[Any] = []
    for spec in specs:
        entity = spec.get("entity")
        entity_id = entity.get("#text") if isinstance(entity, dict) else entity
        entity_kind = entity.get("type") if isinstance(entity, dict) else "VirtualMachine"
        try:
            max_sample = max(1, min(int(spec.get("maxSample", 3)), 10))
        except (TypeError, ValueError):
            max_sample = 3
        stamps = [_now() for _ in range(max_sample)]
        samples.append(
            Typed(
                {
                    "entity": Typed(entity_id, ref_type=entity_kind),
                    "sampleInfo": [{"timestamp": stamp, "interval": 20} for stamp in stamps],
                    "value": [
                        Typed(
                            {
                                "id": {"counterId": counter["key"], "instance": ""},
                                "value": [
                                    (counter["key"] * 7 + i * 13) % 1000 for i in range(max_sample)
                                ],
                            },
                            type_name="PerfMetricIntSeries",
                        )
                        for counter in counters
                    ],
                },
                type_name="PerfEntityMetric",
            )
        )
    return samples or None


def query_perf_provider_summary(ctx: PackContext, op: Operation) -> dict[str, Any]:
    _require_session(ctx, op)
    entity = op.params.get("entity")
    return {
        "entity": entity,
        "currentSupported": True,
        "summarySupported": True,
        "refreshRate": 20,
    }


# -- views -------------------------------------------------------------------

#: Which edges actually nest one object inside another. Only these are walked
#: when a view is populated. ``datastore`` and ``network`` on a host or VM point
#: sideways at shared objects rather than down the hierarchy, so following them
#: would pull in entities the container does not contain.
CONTAINER_EDGES: dict[str, tuple[str, ...]] = {
    "Folder": ("childEntity",),
    "Datacenter": ("hostFolder", "vmFolder", "datastoreFolder", "networkFolder"),
    "ClusterComputeResource": ("host", "resourcePool"),
    "ComputeResource": ("host", "resourcePool"),
    "ResourcePool": ("vm", "resourcePool"),
    "HostSystem": ("vm",),
}


def _contained(ctx: PackContext, start: Entity, recursive: bool) -> list[Entity]:
    """Objects inside ``start``, descending only when the client asked to."""
    found: list[Entity] = []
    seen = {start.id}
    frontier = [start]
    while frontier:
        current = frontier.pop(0)
        for edge in CONTAINER_EDGES.get(current.kind, ()):
            for ref in current.refs(edge):
                child = ctx.inventory.get(ref)
                if child is None or child.id in seen:
                    continue
                seen.add(child.id)
                found.append(child)
                if recursive:
                    frontier.append(child)
    return found


def create_container_view(ctx: PackContext, op: Operation) -> Any:
    _require_session(ctx, op)
    container = op.params.get("container")
    container_id = container.get("#text") if isinstance(container, dict) else container
    start = ctx.inventory.get(str(container_id or ""))
    if start is None:
        raise MockError(
            f"The object {container_id} has already been deleted or has not been "
            "completely created.",
            kind="not-found",
            detail_type="ManagedObjectNotFound",
        )

    declared = op.params.get("type")
    declared_list = declared if isinstance(declared, list) else ([declared] if declared else [])
    wanted: set[str] = set()
    for name in declared_list:
        wanted |= _kinds_for(str(name))

    recursive = str(op.params.get("recursive", "false")).lower() == "true"
    members = [
        entity.id
        for entity in _contained(ctx, start, recursive)
        if not wanted or entity.kind in wanted
    ]

    view = ctx.inventory.add(
        Entity(
            id=f"session[{op.session_id or 'anon'}]{next(_VIEW_IDS)}",
            kind="ContainerView",
            # The view is a real entity so the ordinary property collector can
            # traverse its `view` edge; no special case in the traversal code.
            props={"recursive": recursive, "type": [str(n) for n in declared_list]},
            edges={"view": members, "container": [start.id]},
        )
    )
    return Typed(view.id, ref_type="ContainerView")


def destroy_view(ctx: PackContext, op: Operation) -> None:
    ctx.inventory.remove(str(op.context.get("target_value") or ""))
    return None


PROJECTIONS = {
    "RetrieveServiceContent": retrieve_service_content,
    "Login": login,
    "Logout": logout,
    "CurrentTime": current_time,
    "SessionIsActive": session_is_active,
    "RetrieveProperties": retrieve_properties,
    "RetrievePropertiesEx": retrieve_properties_ex,
    "ContinueRetrievePropertiesEx": continue_retrieve_properties_ex,
    "CancelRetrievePropertiesEx": cancel_retrieve_properties_ex,
    "QueryOptions": query_options,
    "HasPrivilegeOnEntities": has_privilege_on_entities,
    "FindByUuid": find_by_uuid,
    "CreateContainerView": create_container_view,
    "CreateListView": create_container_view,
    "DestroyView": destroy_view,
    "CreateCollectorForEvents": create_collector_for_events,
    "ReadNextEvents": read_next_events,
    "ResetCollector": reset_collector,
    "RewindCollector": reset_collector,
    "DestroyCollector": destroy_collector,
    "QueryEvents": query_events,
    "QueryPerf": query_perf,
    "QueryPerfProviderSummary": query_perf_provider_summary,
}
