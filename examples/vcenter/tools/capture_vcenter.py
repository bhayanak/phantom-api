#!/usr/bin/env python3
"""Capture a real vSphere endpoint's wire traffic into a replayable corpus.

Stdlib only -- designed to be uploaded to a jump host that can reach the target
and run without installing anything.

Writes, per exchange:
    raw/NNN-<label>.req.xml|json   the request body we sent
    raw/NNN-<label>.resp.xml|json  the verbatim response body
    raw/NNN-<label>.meta.json      status, headers, timing, endpoint

Credentials are read from the environment only and are never written to disk.

Request shapes mirror what the vijava client emits, including the
`xsi:type="ManagedObjectReference"` on `_this`, `SelectionSpec` typing on nested
selectSets, the double RetrieveServiceContent handshake, and the
`vcSessionCookie` SOAP header used by the PBM and SMS endpoints.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOST = os.environ["VC_HOST"]
USER = os.environ["VC_USER"]
PASSWORD = os.environ["VC_PASSWORD"]
OUT = Path(os.environ.get("VC_OUT", "./raw"))
OUT.mkdir(parents=True, exist_ok=True)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

SDK = f"https://{HOST}/sdk"
PBM = f"https://{HOST}/pbm/sdk"
SMS = f"https://{HOST}/sms/sdk"

_seq = 0
_cookie: str | None = None
_index: list[dict] = []


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _redact(blob: str) -> str:
    """Strip anything secret before it ever touches disk."""
    blob = re.sub(r"(<password[^>]*>).*?(</password>)", r"\1***REDACTED***\2", blob, flags=re.S)
    blob = blob.replace(PASSWORD, "***REDACTED***")
    blob = re.sub(r"(vmware_soap_session=)[^;\"]+", r"\1***SESSION***", blob)
    blob = re.sub(r"(<vcSessionCookie>).*?(</vcSessionCookie>)", r"\1***SESSION***\2", blob)
    blob = re.sub(r'("value"\s*:\s*")[A-Za-z0-9+/=]{24,}(")', r"\1***SESSION***\2", blob)
    return blob


def _record(label: str, endpoint: str, req: str, resp: str, status: int, headers, elapsed: float,
            ext: str = "xml") -> str:
    global _seq
    _seq += 1
    stem = f"{_seq:03d}-{label}"
    (OUT / f"{stem}.req.{ext}").write_text(_redact(req), encoding="utf-8")
    (OUT / f"{stem}.resp.{ext}").write_text(_redact(resp), encoding="utf-8")
    meta = {
        "seq": _seq,
        "label": label,
        "endpoint": endpoint,
        "status": status,
        "elapsed_ms": round(elapsed * 1000, 1),
        "response_bytes": len(resp),
        "headers": {k: ("***SESSION***" if k.lower() == "set-cookie" else v)
                    for k, v in dict(headers or {}).items()},
    }
    (OUT / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _index.append(meta)
    print(f"  [{_seq:03d}] {label:<46} {status}  {len(resp):>9,}B  {meta['elapsed_ms']:>8}ms")
    return stem


def _post(url: str, payload: bytes, headers: dict) -> tuple[str, int, dict]:
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=300) as r:
            return r.read().decode("utf-8", "replace"), r.status, r.headers
    except urllib.error.HTTPError as e:  # the server returns 500 for SOAP faults
        return e.read().decode("utf-8", "replace"), e.code, e.headers


def soap(label: str, body: str, endpoint: str = SDK, action: str = "urn:vim25/6.5",
         use_cookie: bool = True) -> str:
    """POST a SOAP body to the vim25 endpoint and record the exchange."""
    global _cookie
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/" '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<soapenv:Body>" + body + "</soapenv:Body></soapenv:Envelope>"
    )
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action}
    if use_cookie and _cookie:
        headers["Cookie"] = _cookie
    started = time.time()
    text, status, rhead = _post(endpoint, envelope.encode(), headers)
    if rhead and rhead.get("Set-Cookie"):
        _cookie = rhead.get("Set-Cookie").split(";")[0]
    _record(label, endpoint, envelope, text, status, rhead, time.time() - started)
    return text


def soap_cookie_header(label: str, body: str, endpoint: str, action: str) -> str:
    """PBM/SMS variant: the session travels in a <vcSessionCookie> SOAP header."""
    session_value = (_cookie or "").split("=", 1)[-1]
    envelope = (
        '<soapenv:Envelope xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/" '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<soapenv:Header><vcSessionCookie>{_esc(session_value)}</vcSessionCookie>"
        "</soapenv:Header><soapenv:Body>" + body + "</soapenv:Body></soapenv:Envelope>"
    )
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action}
    started = time.time()
    text, status, rhead = _post(endpoint, envelope.encode(), headers)
    _record(label, endpoint, envelope, text, status, rhead, time.time() - started)
    return text


def rest(label: str, path: str, method: str = "GET", payload: dict | None = None,
         basic: bool = False, session: str | None = None) -> tuple[str, str]:
    url = f"https://{HOST}/{path.lstrip('/')}"
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{USER}:{PASSWORD}".encode()).decode()
    if session:
        headers["vmware-api-session-id"] = session
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=120) as r:
            text, status, rhead = r.read().decode("utf-8", "replace"), r.status, r.headers
    except urllib.error.HTTPError as e:
        text, status, rhead = e.read().decode("utf-8", "replace"), e.code, e.headers
    except Exception as e:
        text, status, rhead = json.dumps({"_capture_error": str(e)}), 0, {}
    stem = _record(label, url, json.dumps({"method": method, "payload": payload}, indent=2),
                   text, status, rhead, time.time() - started, ext="json")
    return stem, text


# --------------------------------------------------------------------------
# vim25 helpers
# --------------------------------------------------------------------------

def mor(kind: str, value: str) -> str:
    return f'<_this type="{kind}" xsi:type="ManagedObjectReference">{_esc(value)}</_this>'


def find(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def find_mor(text: str, tag: str) -> tuple[str, str] | None:
    m = re.search(rf'<{tag}\s+type="([^"]+)"[^>]*>(.*?)</{tag}>', text, re.S)
    return (m.group(1), m.group(2).strip()) if m else None


def _sel(name: str) -> str:
    return f'<selectSet xsi:type="SelectionSpec"><name>{name}</name></selectSet>'


def _trav(name: str, kind: str, path: str, links: list[str]) -> str:
    return (
        f'<selectSet xsi:type="TraversalSpec"><name>{name}</name><type>{kind}</type>'
        f"<path>{path}</path><skip>false</skip>"
        + "".join(_sel(n) for n in links)
        + "</selectSet>"
    )


# The exact traversal set the client's buildFullTraversal() emits.
FULL_TRAVERSAL = (
    _trav("visitFolders", "Folder", "childEntity",
          ["visitFolders", "dcToHf", "dcToVmf", "dcToDs", "dcToNetf", "crToH", "crToRp",
           "HToVm", "rpToVm"])
    + _trav("dcToDs", "Datacenter", "datastoreFolder", ["visitFolders"])
    + _trav("dcToNetf", "Datacenter", "networkFolder", ["visitFolders"])
    + _trav("vAppToRp", "VirtualApp", "resourcePool", ["rpToRp", "vAppToRp"])
    + _trav("dcToVmf", "Datacenter", "vmFolder", ["visitFolders"])
    + _trav("dcToHf", "Datacenter", "hostFolder", ["visitFolders"])
    + _trav("crToH", "ComputeResource", "host", [])
    + _trav("crToRp", "ComputeResource", "resourcePool", ["rpToRp", "rpToVm"])
    + _trav("rpToRp", "ResourcePool", "resourcePool", ["rpToRp", "rpToVm"])
    + _trav("HToVm", "HostSystem", "vm", ["visitFolders"])
    + _trav("rpToVm", "ResourcePool", "vm", [])
)

BASE_PROPS = ["name", "parent", "effectiveRole"]

# Property sets copied verbatim from the consumer's entity wrappers.
PROPERTY_SETS: dict[str, list[str]] = {
    "Datacenter": ["datastoreFolder", "networkFolder", "hostFolder", "vmFolder", *BASE_PROPS],
    "ClusterComputeResource": ["configuration.dasConfig", "configuration.drsConfig", "summary",
                               "host", "resourcePool", "datastore", "network", *BASE_PROPS],
    "ComputeResource": ["summary", "host", "resourcePool", *BASE_PROPS],
    "HostSystem": ["runtime.connectionState", "runtime.inMaintenanceMode", "runtime.powerState",
                   "capability.iscsiSupported", "capability.supportedVmfsMajorVersion",
                   "config.product.version", "config.product.build", "summary.quickStats",
                   "summary.hardware.uuid", "summary.managementServerIp",
                   "config.storageDevice.hostBusAdapter", "vm", "datastore", "network",
                   *BASE_PROPS],
    "HostSystem_network": ["config.network", "configManager.iscsiManager",
                           "config.product.fullName", "hardware.cpuInfo", "hardware.cpuPkg",
                           "config.hyperThread.active", "hardware.memorySize",
                           "hardware.systemInfo", "config.virtualNicManagerInfo.netConfig",
                           "config.service", "configManager.networkSystem", *BASE_PROPS],
    "VirtualMachine": ["summary", "recentTask", "config.version", "datastore", "resourcePool",
                       "layout.swapFile", "config.managedBy", "config.extraConfig", "config.uuid",
                       "guest", "runtime", "network", *BASE_PROPS],
    "Datastore": ["info", "summary", "host", "vm", "iormConfiguration", "overallStatus",
                  *BASE_PROPS],
    "ResourcePool": ["owner", "overallStatus", "resourcePool", "vm", "config", *BASE_PROPS],
    "Folder": ["childEntity", "childType", *BASE_PROPS],
    "Network": ["summary", "host", "vm", *BASE_PROPS],
    "DistributedVirtualSwitch": ["uuid", "summary", "config", "portgroup", *BASE_PROPS],
    "DistributedVirtualPortgroup": ["key", "config", "host", "vm", *BASE_PROPS],
    "VirtualApp": ["summary", "vm", *BASE_PROPS],
}


def prop_spec(kind: str, paths: list[str]) -> str:
    inner = "".join(f"<pathSet>{p}</pathSet>" for p in paths)
    return f'<propSet xsi:type="PropertySpec"><type>{kind}</type><all>false</all>{inner}</propSet>'


def _obj_set(root_type: str, root: str) -> str:
    return (f'<objectSet xsi:type="ObjectSpec"><obj type="{root_type}">{root}</obj>'
            f"<skip>false</skip>{FULL_TRAVERSAL}</objectSet>")


def retrieve(label: str, root_type: str, root: str, kind: str, paths: list[str]) -> str:
    body = (
        '<RetrieveProperties xmlns="urn:vim25">'
        + mor("PropertyCollector", "propertyCollector")
        + '<specSet xsi:type="PropertyFilterSpec">'
        + prop_spec(kind, paths)
        + _obj_set(root_type, root)
        + "</specSet></RetrieveProperties>"
    )
    return soap(label, body)


def retrieve_ex(label: str, root_type: str, root: str, kind: str, paths: list[str],
                max_objects: int) -> str:
    body = (
        '<RetrievePropertiesEx xmlns="urn:vim25">'
        + mor("PropertyCollector", "propertyCollector")
        + '<specSet xsi:type="PropertyFilterSpec">'
        + prop_spec(kind, paths)
        + _obj_set(root_type, root)
        + '</specSet><options xsi:type="RetrieveOptions">'
        + f"<maxObjects>{max_objects}</maxObjects></options></RetrievePropertiesEx>"
    )
    return soap(label, body)


def retrieve_on(label: str, kind: str, value: str, paths: list[str]) -> str:
    """Read properties directly off one managed object (no traversal)."""
    body = (
        '<RetrieveProperties xmlns="urn:vim25">'
        + mor("PropertyCollector", "propertyCollector")
        + '<specSet xsi:type="PropertyFilterSpec">'
        + prop_spec(kind, paths)
        + f'<objectSet xsi:type="ObjectSpec"><obj type="{kind}">{value}</obj>'
        + "<skip>false</skip></objectSet></specSet></RetrieveProperties>"
    )
    return soap(label, body)


# --------------------------------------------------------------------------

def main() -> int:
    global _cookie
    print(f"==> capturing {HOST} into {OUT}")

    # 1. Anonymous bootstrap. The client does this twice: once with the default
    #    SOAPAction, then again after re-pinning it from about.apiVersion.
    content = soap(
        "RetrieveServiceContent",
        '<RetrieveServiceContent xmlns="urn:vim25">'
        + mor("ServiceInstance", "ServiceInstance")
        + "</RetrieveServiceContent>",
    )
    api_version = find(content, "apiVersion") or "6.5"
    pinned = "urn:vim25/" + ".".join(api_version.split(".")[:2])
    content = soap(
        "RetrieveServiceContent_versionPinned",
        '<RetrieveServiceContent xmlns="urn:vim25">'
        + mor("ServiceInstance", "ServiceInstance")
        + "</RetrieveServiceContent>",
        action=pinned,
    )

    session_mgr = find_mor(content, "sessionManager")
    root_folder = find_mor(content, "rootFolder")
    event_mgr = find_mor(content, "eventManager")
    perf_mgr = find_mor(content, "perfManager")
    setting = find_mor(content, "setting")
    auth_mgr = find_mor(content, "authorizationManager")
    search_idx = find_mor(content, "searchIndex")
    instance_uuid = find(content, "instanceUuid")
    print(f"    apiVersion={api_version} soapAction={pinned} instanceUuid={instance_uuid}")
    if not (session_mgr and root_folder):
        print("!! could not parse ServiceContent", file=sys.stderr)
        return 1

    # 2. Login -------------------------------------------------------------
    soap(
        "Login",
        f'<Login xmlns="urn:vim25">{mor(*session_mgr)}'
        f"<userName>{_esc(USER)}</userName><password>{_esc(PASSWORD)}</password>"
        f"<locale>en_US</locale></Login>",
        action=pinned,
    )
    authed_cookie = _cookie

    soap("CurrentTime",
         f'<CurrentTime xmlns="urn:vim25">{mor("ServiceInstance", "ServiceInstance")}'
         "</CurrentTime>", action=pinned)
    retrieve_on("SessionManager_currentSession", session_mgr[0], session_mgr[1],
                ["currentSession"])
    retrieve_on("ServiceInstance_capability", "ServiceInstance", "ServiceInstance",
                ["capability", "serverClock"])

    # 3. Service-wide singletons the consumer reads ------------------------
    if setting:
        soap(
            "QueryOptions_httpsport",
            f'<QueryOptions xmlns="urn:vim25">{mor(*setting)}'
            "<name>config.vpxd.rhttpproxy.httpsport</name></QueryOptions>",
            action=pinned,
        )
    if auth_mgr:
        retrieve_on("AuthorizationManager_roleList", auth_mgr[0], auth_mgr[1], ["roleList"])
        soap(
            "HasPrivilegeOnEntities",
            f'<HasPrivilegeOnEntities xmlns="urn:vim25">{mor(*auth_mgr)}'
            f'<entity type="{root_folder[0]}">{root_folder[1]}</entity>'
            "<sessionId>_self_</sessionId><privId>System.View</privId>"
            "</HasPrivilegeOnEntities>",
            action=pinned,
        )
    if perf_mgr:
        retrieve_on("PerformanceManager_perfCounter", perf_mgr[0], perf_mgr[1],
                    ["perfCounter", "description"])

    # 4. The root-folder name probe used as the session keep-alive ---------
    retrieve_on("KeepAlive_rootFolder_name", root_folder[0], root_folder[1], ["name"])

    # 5. Full inventory sweeps, one per entity type ------------------------
    first_vm = first_host = None
    for kind, paths in PROPERTY_SETS.items():
        real_kind = kind.split("_")[0]
        text = retrieve(f"RetrieveProperties_{kind}", root_folder[0], root_folder[1],
                        real_kind, paths)
        if kind == "VirtualMachine":
            first_vm = find_mor(text, "obj")
        if kind == "HostSystem":
            first_host = find_mor(text, "obj")
    print(f"    first_vm={first_vm} first_host={first_host}")

    # 6. Paged variant so we capture the token / continuation shape --------
    paged = retrieve_ex("RetrievePropertiesEx_VirtualMachine_page", root_folder[0], root_folder[1],
                        "VirtualMachine", PROPERTY_SETS["VirtualMachine"], max_objects=2)
    token = find(paged, "token")
    if token:
        soap(
            "ContinueRetrievePropertiesEx",
            '<ContinueRetrievePropertiesEx xmlns="urn:vim25">'
            + mor("PropertyCollector", "propertyCollector")
            + f"<token>{_esc(token)}</token></ContinueRetrievePropertiesEx>",
            action=pinned,
        )

    # 7. Performance -------------------------------------------------------
    if perf_mgr and first_vm:
        soap(
            "QueryPerfProviderSummary",
            f'<QueryPerfProviderSummary xmlns="urn:vim25">{mor(*perf_mgr)}'
            f'<entity type="{first_vm[0]}">{first_vm[1]}</entity></QueryPerfProviderSummary>',
            action=pinned,
        )
        soap(
            "QueryAvailablePerfMetric",
            f'<QueryAvailablePerfMetric xmlns="urn:vim25">{mor(*perf_mgr)}'
            f'<entity type="{first_vm[0]}">{first_vm[1]}</entity>'
            "<intervalId>20</intervalId></QueryAvailablePerfMetric>",
            action=pinned,
        )
        soap(
            "QueryPerf_vm",
            f'<QueryPerf xmlns="urn:vim25">{mor(*perf_mgr)}'
            f'<querySpec xsi:type="PerfQuerySpec">'
            f'<entity type="{first_vm[0]}">{first_vm[1]}</entity>'
            "<maxSample>3</maxSample><intervalId>20</intervalId></querySpec></QueryPerf>",
            action=pinned,
        )

    # 8. Events ------------------------------------------------------------
    if event_mgr:
        retrieve_on("EventManager_props", event_mgr[0], event_mgr[1],
                    ["latestEvent", "maxCollector"])
        begin = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        time_filter = ('<time xsi:type="EventFilterSpecByTime">'
                       f"<beginTime>{begin}</beginTime><endTime>{end}</endTime></time>")
        collector = soap(
            "CreateCollectorForEvents",
            f'<CreateCollectorForEvents xmlns="urn:vim25">{mor(*event_mgr)}'
            f'<filter xsi:type="EventFilterSpec">{time_filter}</filter>'
            "</CreateCollectorForEvents>",
            action=pinned,
        )
        handle = find_mor(collector, "returnval")
        if handle:
            for page in range(1, 4):
                resp = soap(
                    f"ReadNextEvents_page{page}",
                    f'<ReadNextEvents xmlns="urn:vim25">{mor(*handle)}'
                    "<maxCount>1000</maxCount></ReadNextEvents>",
                    action=pinned,
                )
                if "<returnval" not in resp:
                    break
            retrieve_on("EventHistoryCollector_latestPage", handle[0], handle[1], ["latestPage"])
            soap("ResetCollector",
                 f'<ResetCollector xmlns="urn:vim25">{mor(*handle)}</ResetCollector>',
                 action=pinned)
            soap("DestroyCollector",
                 f'<DestroyCollector xmlns="urn:vim25">{mor(*handle)}</DestroyCollector>',
                 action=pinned)
        soap(
            "QueryEvents",
            f'<QueryEvents xmlns="urn:vim25">{mor(*event_mgr)}'
            f'<filter xsi:type="EventFilterSpec">{time_filter}</filter></QueryEvents>',
            action=pinned,
        )

    # 9. Search index ------------------------------------------------------
    if search_idx and first_vm:
        vm_props = retrieve_on("SearchIndex_seed_vm_uuid", first_vm[0], first_vm[1],
                               ["config.instanceUuid"])
        vm_uuid = find(vm_props, "val")
        if vm_uuid:
            soap(
                "FindByUuid_vm",
                f'<FindByUuid xmlns="urn:vim25">{mor(*search_idx)}'
                f"<uuid>{_esc(vm_uuid)}</uuid><vmSearch>true</vmSearch>"
                "<instanceUuid>true</instanceUuid></FindByUuid>",
                action=pinned,
            )

    # 10. Storage policy (PBM) and storage monitoring (SMS) ----------------
    pbm = soap_cookie_header(
        "PbmRetrieveServiceContent",
        '<PbmRetrieveServiceContent xmlns="urn:pbm">'
        '<_this type="PbmServiceInstance" xsi:type="ManagedObjectReference">'
        "ServiceInstance</_this></PbmRetrieveServiceContent>",
        PBM, "urn:pbm",
    )
    pbm_profile_mgr = find_mor(pbm, "profileManager")
    if pbm_profile_mgr:
        profiles = soap_cookie_header(
            "PbmQueryProfile",
            '<PbmQueryProfile xmlns="urn:pbm">'
            f'<_this type="{pbm_profile_mgr[0]}" xsi:type="ManagedObjectReference">'
            f"{pbm_profile_mgr[1]}</_this>"
            '<resourceType xsi:type="PbmProfileResourceType">'
            "<resourceType>STORAGE</resourceType></resourceType></PbmQueryProfile>",
            PBM, "urn:pbm",
        )
        ids = re.findall(r"<uniqueId>(.*?)</uniqueId>", profiles, re.S)[:8]
        if ids:
            body = "".join(
                f'<profileIds xsi:type="PbmProfileId"><uniqueId>{_esc(i)}</uniqueId></profileIds>'
                for i in ids)
            soap_cookie_header(
                "PbmRetrieveContent",
                '<PbmRetrieveContent xmlns="urn:pbm">'
                f'<_this type="{pbm_profile_mgr[0]}" xsi:type="ManagedObjectReference">'
                f"{pbm_profile_mgr[1]}</_this>{body}</PbmRetrieveContent>",
                PBM, "urn:pbm",
            )
    soap_cookie_header(
        "SmsQueryStorageManager",
        '<QueryStorageManager xmlns="urn:sms">'
        '<_this type="SmsServiceInstance" xsi:type="ManagedObjectReference">'
        "ServiceInstance</_this></QueryStorageManager>",
        SMS, "urn:sms/6.5",
    )

    # 11. Automation REST + vAPI -------------------------------------------
    _, sess_raw = rest("cis_session", "rest/com/vmware/cis/session", method="POST", basic=True)
    try:
        api_session = json.loads(sess_raw).get("value")
    except Exception:
        api_session = None
    rest("tagging_category_list", "rest/com/vmware/cis/tagging/category", session=api_session)
    rest("tagging_tag_list", "rest/com/vmware/cis/tagging/tag", session=api_session)
    rest("tagging_tag_association_list_attached_tags_on_objects",
         "rest/com/vmware/cis/tagging/tag-association?~action=list-attached-tags-on-objects",
         method="POST", payload={"object_ids": []}, session=api_session)
    _, vms_raw = rest("vcenter_vm_list", "rest/vcenter/vm", session=api_session)
    rest("vcenter_host_list", "rest/vcenter/host", session=api_session)
    rest("vcenter_cluster_list", "rest/vcenter/cluster", session=api_session)
    rest("vcenter_datacenter_list", "rest/vcenter/datacenter", session=api_session)
    rest("vcenter_datastore_list", "rest/vcenter/datastore", session=api_session)
    rest("vcenter_network_list", "rest/vcenter/network", session=api_session)
    rest("content_library_list", "rest/com/vmware/content/library", session=api_session)
    rest("content_local_library_list", "rest/com/vmware/content/local-library", session=api_session)

    vm_ids: list[str] = []
    with contextlib.suppress(Exception):
        vm_ids = [v["vm"] for v in json.loads(vms_raw).get("value", [])][:2]
    for vm_id in vm_ids:
        rest(f"guest_networking_interfaces_{vm_id}",
             f"rest/vcenter/vm/{vm_id}/guest/networking/interfaces", session=api_session)
        rest(f"guest_networking_{vm_id}", f"api/vcenter/vm/{vm_id}/guest/networking",
             session=api_session)
        rest(f"guest_networking_routes_{vm_id}",
             f"api/vcenter/vm/{vm_id}/guest/networking/routes", session=api_session)

    # 12. Faults, captured deliberately and last so they cannot poison the
    #     authenticated session used by everything above.
    _cookie = 'vmware_soap_session="52000000-0000-0000-0000-000000000000"'
    retrieve_on("FAULT_NotAuthenticated", root_folder[0], root_folder[1], ["name"])
    soap(
        "FAULT_InvalidLogin",
        f'<Login xmlns="urn:vim25">{mor(*session_mgr)}'
        f"<userName>{_esc(USER)}</userName><password>not-the-password</password>"
        f"<locale>en_US</locale></Login>",
        action=pinned, use_cookie=False,
    )
    _cookie = authed_cookie
    soap(
        "FAULT_ManagedObjectNotFound",
        '<RetrieveProperties xmlns="urn:vim25">'
        + mor("PropertyCollector", "propertyCollector")
        + '<specSet xsi:type="PropertyFilterSpec">'
        + prop_spec("VirtualMachine", ["name"])
        + '<objectSet xsi:type="ObjectSpec"><obj type="VirtualMachine">vm-999999</obj>'
        + "<skip>false</skip></objectSet></specSet></RetrieveProperties>",
        action=pinned,
    )
    soap("Logout", f'<Logout xmlns="urn:vim25">{mor(*session_mgr)}</Logout>', action=pinned)

    (OUT / "index.json").write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "api_version": api_version,
                "soap_action": pinned,
                "exchanges": _index,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"==> {_seq} exchanges captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
