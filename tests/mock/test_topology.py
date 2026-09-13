"""Tests for recovering an object graph from a recording."""

from __future__ import annotations

import json

import pytest

from phantom_api.mock.record.topology import (
    DerivedEntity,
    Topology,
    derive_topology,
    render_scenario,
    singular,
    slug,
)

REST_COLLECTION = json.dumps(
    {
        "servers": [
            {
                "id": 1,
                "name": "web-01",
                "powerState": "on",
                "account": {"id": 7, "name": "acme"},
                "zone": {"id": 3, "name": "dc-1"},
                "volumes": [{"id": 11, "name": "root"}, {"id": 12, "name": "data"}],
            },
            {"id": 2, "name": "web-02", "account": {"id": 7, "name": "acme"}},
        ],
        "meta": {"total": 2},
    }
)

REST_ACCOUNTS = json.dumps({"accounts": [{"id": 7, "name": "acme", "active": True}]})
REST_ZONES = json.dumps({"zones": [{"id": 3, "name": "dc-1"}]})
REST_VOLUMES = json.dumps(
    {"volumes": [{"id": 11, "name": "root", "sizeGB": 40}, {"id": 12, "name": "data"}]}
)

REST_DETAIL = json.dumps(
    {"server": {"id": 1, "name": "web-01", "description": "the full record", "cores": 8}}
)

SOAP_BODY = """<?xml version="1.0"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"><Body>
<RetrievePropertiesResponse xmlns="urn:vim25">
<returnval>
  <obj type="Datacenter">datacenter-2</obj>
  <propSet><name>name</name><val>DC-01</val></propSet>
  <propSet><name>hostFolder</name><val type="Folder">group-h4</val></propSet>
</returnval>
<returnval>
  <obj type="Folder">group-h4</obj>
  <propSet><name>name</name><val>host</val></propSet>
  <propSet><name>childEntity</name><val>
    <ManagedObjectReference type="HostSystem">host-9</ManagedObjectReference>
    <ManagedObjectReference type="HostSystem">host-10</ManagedObjectReference>
  </val></propSet>
</returnval>
<returnval>
  <obj type="HostSystem">host-9</obj>
  <propSet><name>name</name><val>esx-01</val></propSet>
</returnval>
<returnval>
  <obj type="HostSystem">host-10</obj>
  <propSet><name>name</name><val>esx-02</val></propSet>
</returnval>
</RetrievePropertiesResponse></Body></Envelope>
"""


def write_corpus(root, exchanges):
    """Build a corpus from (transport, path, body, status) tuples."""
    corpus = root / "corpus"
    (corpus / "rest").mkdir(parents=True, exist_ok=True)
    (corpus / "soap").mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (transport, path, body, status) in enumerate(exchanges, start=1):
        suffix = "json" if transport == "rest" else "xml"
        stem = f"{index:03d}-op"
        (corpus / transport / f"{stem}.req.{suffix}").write_text("")
        (corpus / transport / f"{stem}.resp.{suffix}").write_text(body)
        entries.append(
            {
                "stem": stem,
                "label": path,
                "transport": transport,
                "status": status,
                "method": "GET" if transport == "rest" else "POST",
                "endpoint_path": path,
                "request": f"{transport}/{stem}.req.{suffix}",
                "response": f"{transport}/{stem}.resp.{suffix}",
            }
        )
    (corpus / "manifest.json").write_text(json.dumps({"exchanges": entries}))
    return corpus


@pytest.fixture
def rest_corpus(tmp_path):
    return write_corpus(
        tmp_path,
        [
            ("rest", "/api/servers", REST_COLLECTION, 200),
            ("rest", "/api/accounts", REST_ACCOUNTS, 200),
            ("rest", "/api/zones", REST_ZONES, 200),
            ("rest", "/api/volumes", REST_VOLUMES, 200),
            ("rest", "/api/servers/1", REST_DETAIL, 200),
        ],
    )


@pytest.fixture
def soap_corpus(tmp_path):
    return write_corpus(tmp_path, [("soap", "/sdk", SOAP_BODY, 200)])


# -- REST --------------------------------------------------------------------


def test_entities_are_named_from_the_collection_that_held_them(rest_corpus):
    topology = derive_topology(rest_corpus)

    assert topology.by_kind["server"] == 2
    assert topology.by_kind["account"] == 1
    assert topology.by_kind["zone"] == 1


def test_references_become_edges(rest_corpus):
    topology = derive_topology(rest_corpus)

    server = topology.entities["server:1"]
    assert server.edges["account"] == ["account:7"]
    assert server.edges["zone"] == ["zone:3"]
    assert sorted(server.edges["volumes"]) == ["volume:11", "volume:12"]


def test_an_object_referenced_twice_is_recorded_once(rest_corpus):
    """Both servers point at the same account; that is one object, not two."""
    topology = derive_topology(rest_corpus)
    assert topology.by_kind["account"] == 1


def test_a_detail_response_enriches_the_object_from_the_list(rest_corpus):
    """List and detail responses describe the same object at different depth."""
    topology = derive_topology(rest_corpus)
    server = topology.entities["server:1"]

    assert server.props["name"] == "web-01"
    assert server.props["cores"] == 8, "the detail response must contribute"
    assert server.props["powerState"] == "on", "the list response must survive"


def test_failed_responses_are_ignored(tmp_path):
    corpus = write_corpus(
        tmp_path,
        [
            ("rest", "/api/servers", REST_COLLECTION, 200),
            ("rest", "/api/missing", json.dumps({"servers": [{"id": 99}]}), 404),
        ],
    )
    topology = derive_topology(corpus)
    assert "server:99" not in topology.entities


def test_an_unresolvable_reference_is_counted_not_invented(tmp_path):
    """A graph must never claim a relationship the traffic did not show."""
    body = json.dumps({"servers": [{"id": 1, "cluster": {"id": 404, "name": "gone"}}]})
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/servers", body, 200)]))

    assert topology.entities["server:1"].edges == {}
    assert sum(topology.dangling.values()) == 1


def test_an_ambiguous_reference_is_not_guessed(tmp_path):
    """Two kinds share id 5, and the field name matches neither."""
    body = json.dumps(
        {
            "alphas": [{"id": 5, "name": "a"}],
            "betas": [{"id": 5, "name": "b"}],
            "things": [{"id": 1, "owner": {"id": 5}}],
        }
    )
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/x", body, 200)]))

    assert topology.entities["thing:1"].edges == {}
    assert sum(topology.dangling.values()) == 1


def test_a_reference_resolves_by_id_when_the_field_name_does_not_match(tmp_path):
    body = json.dumps(
        {"clouds": [{"id": 9, "name": "c"}], "things": [{"id": 1, "parent": {"id": 9}}]}
    )
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/x", body, 200)]))

    assert topology.entities["thing:1"].edges["parent"] == ["cloud:9"]


def test_a_rich_nested_object_is_an_entity_not_a_reference(tmp_path):
    """A summary carries an id and little else; a full object is its own thing."""
    body = json.dumps(
        {
            "apps": [
                {
                    "id": 1,
                    "tier": {
                        "id": 2,
                        "name": "web",
                        "port": 80,
                        "protocol": "http",
                        "enabled": True,
                        "weight": 5,
                    },
                }
            ]
        }
    )
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/apps", body, 200)]))

    assert "tier:2" in topology.entities
    assert topology.entities["tier:2"].props["port"] == 80


def test_objects_without_identity_are_skipped(tmp_path):
    body = json.dumps({"stats": [{"cpu": 10, "memory": 20}], "meta": {"total": 1}})
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/x", body, 200)]))

    assert topology.entities == {}


def test_malformed_and_empty_bodies_do_not_stop_the_scan(tmp_path):
    corpus = write_corpus(
        tmp_path,
        [
            ("rest", "/api/broken", "{not json", 200),
            ("rest", "/api/empty", "", 200),
            ("rest", "/api/list", "[1, 2, 3]", 200),
            ("rest", "/api/servers", REST_COLLECTION, 200),
        ],
    )
    assert derive_topology(corpus).by_kind["server"] == 2


# -- SOAP --------------------------------------------------------------------


def test_the_same_extractor_recovers_a_soap_graph(soap_corpus):
    topology = derive_topology(soap_corpus)

    assert topology.by_kind == {"HostSystem": 2, "Datacenter": 1, "Folder": 1}
    assert topology.entities["Datacenter:datacenter-2"].props["name"] == "DC-01"


def test_soap_typed_values_and_reference_lists_both_become_edges(soap_corpus):
    topology = derive_topology(soap_corpus)

    assert topology.entities["Datacenter:datacenter-2"].edges["hostFolder"] == ["Folder:group-h4"]
    assert sorted(topology.entities["Folder:group-h4"].edges["childEntity"]) == [
        "HostSystem:host-10",
        "HostSystem:host-9",
    ]


def test_malformed_xml_is_skipped(tmp_path):
    corpus = write_corpus(
        tmp_path, [("soap", "/sdk", "<broken", 200), ("soap", "/sdk", SOAP_BODY, 200)]
    )
    assert derive_topology(corpus).by_kind["HostSystem"] == 2


def test_a_returnval_without_an_object_is_skipped(tmp_path):
    body = "<Envelope><Body><R><returnval><propSet/></returnval></R></Body></Envelope>"
    assert derive_topology(write_corpus(tmp_path, [("soap", "/sdk", body, 200)])).entities == {}


# -- naming ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plural", "expected"),
    [
        ("servers", "server"),
        ("zones", "zone"),
        ("policies", "policy"),
        ("addresses", "address"),
        ("status", "statu"),
        ("access", "access"),
        ("data", "data"),
    ],
)
def test_collection_names_reduce_to_a_kind(plural, expected):
    assert singular(plural) == expected


def test_slug_is_filesystem_safe():
    assert slug("My Service 2.0") == "my-service-2-0"
    assert slug("!!!") == "mock"


# -- scenario ----------------------------------------------------------------


def test_scenario_reports_counts_and_relationships(rest_corpus):
    topology = derive_topology(rest_corpus)
    text = render_scenario(topology, name="demo")

    import yaml

    parsed = yaml.safe_load(text)
    assert parsed["name"] == "demo"
    assert parsed["topology"]["server"] == 2
    assert "server.account" in parsed["relations"]


def test_scenario_calls_out_references_that_left_the_recording(tmp_path):
    body = json.dumps({"servers": [{"id": 1, "cluster": {"id": 404}}]})
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/s", body, 200)]))

    assert "unresolved:" in render_scenario(topology, name="demo")


def test_an_empty_topology_still_renders(tmp_path):
    text = render_scenario(Topology(), name="empty")

    import yaml

    assert yaml.safe_load(text)["name"] == "empty"


def test_deriving_without_a_manifest_is_reported(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(FileNotFoundError):
        derive_topology(tmp_path / "nope")


def test_a_self_reference_is_not_an_edge(tmp_path):
    body = json.dumps({"things": [{"id": 1, "self": {"id": 1}}]})
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/t", body, 200)]))
    assert topology.entities["thing:1"].edges == {}


def test_entity_serialises_to_the_shape_the_model_consumes():
    entity = DerivedEntity(id="7", kind="server", props={"name": "x"}, edges={"a": ["b"]})
    assert entity.as_dict() == {
        "id": "7",
        "kind": "server",
        "props": {"name": "x"},
        "edges": {"a": ["b"]},
    }


def test_a_missing_response_file_is_skipped(tmp_path):
    corpus = write_corpus(tmp_path, [("rest", "/api/servers", REST_COLLECTION, 200)])
    (corpus / "rest" / "001-op.resp.json").unlink()
    assert derive_topology(corpus).entities == {}


def test_a_top_level_json_object_is_read_as_one_entity(tmp_path):
    body = json.dumps({"server": {"id": 3, "name": "solo"}})
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/api/servers/3", body, 200)]))
    assert topology.by_kind == {"server": 1}


def test_a_list_of_scalars_is_not_mistaken_for_objects(tmp_path):
    body = json.dumps({"tags": ["a", "b"], "servers": [{"id": 1}]})
    assert derive_topology(write_corpus(tmp_path, [("rest", "/x", body, 200)])).by_kind == {
        "server": 1
    }


def test_a_reference_inside_a_list_resolves(tmp_path):
    body = json.dumps(
        {
            "zones": [{"id": 4, "name": "z"}],
            "servers": [{"id": 1, "zones": [{"id": 4, "name": "z"}]}],
        }
    )
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/x", body, 200)]))
    assert topology.entities["server:1"].edges["zones"] == ["zone:4"]


def test_the_same_reference_twice_is_one_edge(tmp_path):
    body = json.dumps(
        {
            "zones": [{"id": 4}],
            "servers": [{"id": 1, "zones": [{"id": 4}, {"id": 4}]}],
        }
    )
    topology = derive_topology(write_corpus(tmp_path, [("rest", "/x", body, 200)]))
    assert topology.entities["server:1"].edges["zones"] == ["zone:4"]


def test_soap_properties_without_a_value_are_skipped(tmp_path):
    body = (
        "<Envelope><Body><R><returnval>"
        '<obj type="Thing">t-1</obj>'
        "<propSet><name></name><val>x</val></propSet>"
        "<propSet><val>orphan</val></propSet>"
        "</returnval></R></Body></Envelope>"
    )
    topology = derive_topology(write_corpus(tmp_path, [("soap", "/sdk", body, 200)]))
    assert topology.entities["Thing:t-1"].props == {}


def test_an_object_element_with_no_identifier_is_skipped(tmp_path):
    body = (
        '<Envelope><Body><R><returnval><obj type="Thing"></obj></returnval></R></Body></Envelope>'
    )
    assert derive_topology(write_corpus(tmp_path, [("soap", "/sdk", body, 200)])).entities == {}


def test_edge_summary_counts_by_kind_and_name(tmp_path):
    topology = derive_topology(
        write_corpus(
            tmp_path,
            [
                (
                    "rest",
                    "/x",
                    json.dumps(
                        {
                            "zones": [{"id": 4}],
                            "servers": [{"id": 1, "zone": {"id": 4}}, {"id": 2, "zone": {"id": 4}}],
                        }
                    ),
                    200,
                )
            ],
        )
    )
    assert topology.edge_summary()["server.zone"] == 2
