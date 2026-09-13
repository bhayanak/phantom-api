"""Tests for the JSON-RPC pack and the XML codec's encoding rules."""

from __future__ import annotations

import json

import pytest

from phantom_api.mock.protocols.jsonrpc import JsonRpcPack
from phantom_api.mock.protocols.xml_codec import (
    Raw,
    Typed,
    XmlError,
    children_named,
    element_to_dict,
    find_local,
    parse_xml,
    text_of,
    to_xml,
)
from phantom_api.mock.types import MockError, RawRequest, RawResponse


def rpc_request(payload, *, method: str = "POST", content_type: str = "application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return RawRequest(
        method=method,
        path="/rpc",
        query="",
        headers={"content-type": content_type},
        body=body,
    )


@pytest.fixture
def pack():
    return JsonRpcPack()


# -- recognising JSON-RPC ----------------------------------------------------


def test_a_call_is_recognised(pack):
    assert pack.matches(rpc_request({"jsonrpc": "2.0", "method": "add", "id": 1}))


def test_a_batch_is_recognised_from_its_first_member(pack):
    assert pack.matches(rpc_request([{"method": "a", "id": 1}, {"method": "b", "id": 2}]))


@pytest.mark.parametrize(
    ("payload", "kwargs"),
    [
        ({"method": "a"}, {"method": "GET"}),
        ({"method": "a"}, {"content_type": "text/xml"}),
        (b"", {}),
        (b"not json", {}),
        ({"no": "method"}, {}),
        ([], {}),
    ],
)
def test_other_traffic_is_not_claimed(pack, payload, kwargs):
    """Auto-detection must not steal requests belonging to another pack."""
    assert not pack.matches(rpc_request(payload, **kwargs))


# -- decoding ----------------------------------------------------------------


def test_the_method_becomes_the_operation(pack):
    op = pack.decode(rpc_request({"jsonrpc": "2.0", "method": "getUser", "id": 9}))

    assert op.name == "getUser"
    assert op.context["id"] == 9


def test_named_parameters_are_passed_through(pack):
    op = pack.decode(rpc_request({"method": "add", "params": {"a": 1, "b": 2}, "id": 1}))
    assert op.params == {"a": 1, "b": 2}


def test_invalid_json_is_a_bad_request(pack):
    with pytest.raises(MockError) as caught:
        pack.decode(rpc_request(b"{oh no"))
    assert caught.value.status == 400


def test_a_payload_without_a_method_is_a_bad_request(pack):
    with pytest.raises(MockError) as caught:
        pack.decode(rpc_request({"jsonrpc": "2.0", "id": 1}))
    assert caught.value.status == 400


# -- encoding ----------------------------------------------------------------


def test_a_result_is_wrapped_with_the_request_id(pack):
    op = pack.decode(rpc_request({"method": "ping", "id": 77}))
    body = json.loads(pack.encode({"ok": True}, op).body)

    assert body == {"jsonrpc": "2.0", "id": 77, "result": {"ok": True}}


def test_a_prepared_response_is_returned_unchanged(pack):
    op = pack.decode(rpc_request({"method": "ping", "id": 1}))
    prepared = RawResponse(204, {}, b"")
    assert pack.encode(prepared, op) is prepared


def test_errors_stay_on_a_200_transport(pack):
    """JSON-RPC carries failure in the body; the HTTP status is not the signal."""
    op = pack.decode(rpc_request({"method": "boom", "id": 3}))
    response = pack.encode_error(MockError("nope", kind="not-found"), op)

    assert response.status == 200
    body = json.loads(response.body)
    assert body["error"]["message"] == "nope"
    assert body["id"] == 3


# -- the XML codec -----------------------------------------------------------


def test_scalars_and_nesting_round_trip():
    assert to_xml("a", "x") == "<a>x</a>"
    assert to_xml("a", {"b": "c"}) == "<a><b>c</b></a>"


def test_a_sequence_repeats_the_element():
    assert to_xml("a", ["x", "y"]) == "<a>x</a><a>y</a>"


def test_booleans_use_the_xml_spelling():
    assert to_xml("flag", True) == "<flag>true</flag>"
    assert to_xml("flag", False) == "<flag>false</flag>"


def test_none_produces_no_element_at_all():
    """An absent element and an empty one mean different things to a client."""
    assert to_xml("a", None) == ""
    assert to_xml("a", {"b": None}) == "<a></a>", "but an empty parent still renders"


def test_text_is_escaped():
    assert to_xml("a", "<script> & 'x'") == "<a>&lt;script&gt; &amp; 'x'</a>"


def test_raw_is_inserted_verbatim_without_a_wrapper():
    """Raw already carries its own element, so wrapping would double it."""
    assert to_xml("a", Raw("<b/>")) == "<b/>"
    assert to_xml("a", {"inner": Raw("<b/>")}) == "<a><b/></a>"


def test_typed_values_carry_their_type():
    out = to_xml("val", Typed("7", "int"))
    assert 'type="int"' in out or "xsi:type" in out
    assert ">7<" in out


def test_parsing_refuses_a_doctype():
    with pytest.raises(XmlError):
        parse_xml('<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>')


def test_parsing_refuses_malformed_xml():
    with pytest.raises(XmlError):
        parse_xml("<a><b></a>")


def test_helpers_find_elements_regardless_of_namespace():
    root = parse_xml('<a xmlns="urn:x"><b>text</b><c/><c/></a>')

    assert text_of(find_local(root, "b")) == "text"
    assert len(children_named(root, "c")) == 2
    assert find_local(root, "absent") is None


def test_an_element_becomes_a_dictionary():
    root = parse_xml("<obj><name>x</name><count>2</count></obj>")
    assert element_to_dict(root) == {"name": "x", "count": "2"}


def test_repeated_children_become_a_list():
    root = parse_xml("<obj><item>a</item><item>b</item></obj>")
    assert element_to_dict(root)["item"] == ["a", "b"]


def test_attributes_are_folded_in_but_never_shadow_a_child():
    root = parse_xml('<obj kind="disk"><name>x</name></obj>')
    result = element_to_dict(root)

    assert result["kind"] == "disk"
    assert result["name"] == "x"
