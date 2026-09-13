"""Shared fixtures for the mock engine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom_api.mock.model.entity import Entity, Inventory
from phantom_api.mock.types import RawRequest

SOAP_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VCENTER_MOCK = REPO_ROOT / "examples/vcenter"


def soap_request(body: str, *, path: str = "/sdk", cookie: str | None = None) -> RawRequest:
    headers = {"content-type": "text/xml; charset=utf-8", "soapaction": "urn:vim25/8.0"}
    if cookie:
        headers["cookie"] = cookie
    return RawRequest(
        method="POST",
        path=path,
        query="",
        headers=headers,
        body=SOAP_ENVELOPE.format(body=body).encode(),
    )


@pytest.fixture
def simple_inventory() -> Inventory:
    """A four-level graph with a deliberate cycle between host and vm."""
    root = Entity(id="root", kind="Folder", props={"name": "Datacenters"})
    dc = Entity(id="dc-1", kind="Datacenter", props={"name": "DC"})
    cluster = Entity(id="cl-1", kind="ClusterComputeResource", props={"name": "Cluster"})
    host = Entity(
        id="h-1",
        kind="HostSystem",
        props={"name": "esx-01", "runtime": {"powerState": "poweredOn"}},
    )
    vm = Entity(
        id="vm-1",
        kind="VirtualMachine",
        props={
            "name": "VM-1",
            "summary": {"runtime": {"powerState": "poweredOn"}, "config": {"numCpu": 4}},
        },
    )
    root.edges["childEntity"] = [dc.id]
    dc.edges["hostFolder"] = [cluster.id]
    dc.edges["parent"] = [root.id]
    cluster.edges["host"] = [host.id]
    host.edges["vm"] = [vm.id]
    vm.edges["host"] = [host.id]  # the cycle
    return Inventory([root, dc, cluster, host, vm])


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    """A two-exchange corpus written in the layout CorpusResponder expects."""
    corpus = tmp_path / "corpus"
    (corpus / "soap").mkdir(parents=True)
    (corpus / "rest").mkdir(parents=True)

    request_xml = SOAP_ENVELOPE.format(
        body='<Ping xmlns="urn:demo"><_this type="Service">svc</_this></Ping>'
    )
    response_xml = SOAP_ENVELOPE.format(
        body='<PingResponse xmlns="urn:demo"><returnval>pong</returnval></PingResponse>'
    )
    (corpus / "soap" / "001-Ping.req.xml").write_text(request_xml)
    (corpus / "soap" / "001-Ping.resp.xml").write_text(response_xml)
    (corpus / "rest" / "002-list.req.json").write_text('{"method": "GET", "payload": null}')
    (corpus / "rest" / "002-list.resp.json").write_text('{"value": [1, 2, 3]}')

    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "sanitised": True,
                "exchanges": [
                    {
                        "stem": "001-Ping",
                        "label": "Ping",
                        "transport": "soap",
                        "status": 200,
                        "endpoint_path": "/sdk",
                        "request": "soap/001-Ping.req.xml",
                        "response": "soap/001-Ping.resp.xml",
                    },
                    {
                        "stem": "002-list",
                        "label": "GET /items",
                        "transport": "rest",
                        "status": 200,
                        "endpoint_path": "/items",
                        "request": "rest/002-list.req.json",
                        "response": "rest/002-list.resp.json",
                    },
                ],
            }
        )
    )
    return corpus


@pytest.fixture
def vcenter_scenario() -> dict:
    return {
        "service": {
            "name": "Mock vCenter Server",
            "version": "8.0.3",
            "build": "24022515",
            "api_version": "8.0.3.0",
            "instance_uuid": "11111111-2222-4333-8444-555555555555",
            "https_port": 443,
        },
        "auth": {"mode": "accept-any"},
        "topology": {
            "datacenters": [
                {
                    "name": "DC-01",
                    "clusters": [{"name": "Cluster-01", "hosts": 2, "vms_per_host": 2}],
                }
            ],
            "datastores": [{"name": "DS-01", "type": "VMFS", "capacity_gb": 100, "free_gb": 50}],
            "networks": [{"name": "PG-01", "type": "Network"}],
        },
        "change_engine": {"mode": "off"},
    }
