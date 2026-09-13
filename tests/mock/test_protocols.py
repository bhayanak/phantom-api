"""Protocol packs: decoding, encoding and the shapes clients depend on."""

from __future__ import annotations

import json

import pytest

from phantom_api.mock.config import SessionRule
from phantom_api.mock.protocols import detect, get_pack
from phantom_api.mock.protocols.jsonrpc import JsonRpcPack
from phantom_api.mock.protocols.openapi import OpenApiPack
from phantom_api.mock.protocols.soap import SoapPack
from phantom_api.mock.protocols.xml_codec import (
    Raw,
    Typed,
    XmlError,
    element_to_dict,
    parse_xml,
    to_xml,
)
from phantom_api.mock.types import MockError, Operation, RawRequest

from .conftest import soap_request


class TestXmlHardening:
    def test_doctype_is_refused(self):
        with pytest.raises(XmlError, match="DOCTYPE"):
            parse_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')

    def test_entity_declaration_is_refused(self):
        payload = b'<?xml version="1.0"?><!ENTITY lol "lol"><x/>'
        with pytest.raises(XmlError):
            parse_xml(payload)

    def test_billion_laughs_never_reaches_the_parser(self):
        bomb = (
            b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
            b"<lolz>&lol2;</lolz>"
        )
        with pytest.raises(XmlError):
            parse_xml(bomb)

    def test_oversized_document_is_refused(self):
        with pytest.raises(XmlError, match="exceeds"):
            parse_xml(b"<x>" + b"a" * 200 + b"</x>", max_bytes=100)

    def test_deep_nesting_is_refused(self):
        deep = "<a>" * 300 + "</a>" * 300
        with pytest.raises(XmlError, match="nesting"):
            parse_xml(deep)

    def test_malformed_xml_is_an_xml_error(self):
        with pytest.raises(XmlError, match="malformed"):
            parse_xml("<a><b></a>")


class TestTypedSerialisation:
    def test_xsi_type_is_emitted(self):
        assert to_xml("val", Typed("x", type_name="xsd:string")) == (
            '<val xsi:type="xsd:string">x</val>'
        )

    def test_reference_uses_a_type_attribute(self):
        assert to_xml("obj", Typed("vm-1", ref_type="VirtualMachine")) == (
            '<obj type="VirtualMachine">vm-1</obj>'
        )

    def test_none_produces_no_element_at_all(self):
        # An absent element and an empty one mean different things to clients.
        assert to_xml("val", None) == ""

    def test_lists_repeat_the_element(self):
        assert to_xml("x", [1, 2]) == "<x>1</x><x>2</x>"

    def test_booleans_are_lowercase(self):
        assert to_xml("b", True) == "<b>true</b>"

    def test_nested_mappings_become_child_elements(self):
        assert to_xml("a", {"b": {"c": 1}}) == "<a><b><c>1</c></b></a>"

    def test_raw_is_inserted_verbatim(self):
        assert to_xml("ignored", Raw("<already/>")) == "<already/>"

    def test_text_is_escaped(self):
        assert to_xml("x", "a<b&c") == "<x>a&lt;b&amp;c</x>"

    def test_element_to_dict_collapses_repeated_siblings(self):
        root = parse_xml("<r><i>1</i><i>2</i><j>3</j></r>")
        assert element_to_dict(root) == {"i": ["1", "2"], "j": "3"}


class TestSoapPack:
    pack = SoapPack()

    def test_operation_comes_from_the_body_element(self):
        body = '<Login xmlns="urn:vim25"><userName>a</userName></Login>'
        op = self.pack.decode(soap_request(body))
        assert op.name == "Login"
        assert op.params["userName"] == "a"
        assert op.context["namespace"] == "urn:vim25"

    def test_target_object_is_extracted(self):
        op = self.pack.decode(
            soap_request('<Read xmlns="urn:x"><_this type="Collector">c-1</_this></Read>')
        )
        assert op.context["target_type"] == "Collector"
        assert op.context["target_value"] == "c-1"

    def test_session_from_cookie(self):
        request = soap_request("<Ping/>", cookie='vmware_soap_session="ABC"')
        op = self.pack.decode(request, SessionRule(transport="cookie", name="vmware_soap_session"))
        assert op.session_id == "ABC"

    def test_session_from_a_soap_header(self):
        envelope = (
            '<?xml version="1.0"?><soapenv:Envelope '
            'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Header><vcSessionCookie>XYZ</vcSessionCookie></soapenv:Header>"
            '<soapenv:Body><PbmQueryProfile xmlns="urn:pbm"/></soapenv:Body></soapenv:Envelope>'
        )
        request = RawRequest(
            "POST", "/pbm/sdk", "", {"content-type": "text/xml"}, envelope.encode()
        )
        op = self.pack.decode(request, SessionRule(transport="soap-header", name="vcSessionCookie"))
        assert op.session_id == "XYZ"

    def test_faults_are_http_500(self):
        """Returning 200 for a fault disables client-side error handling."""
        op = self.pack.decode(soap_request('<X xmlns="urn:vim25"/>'))
        response = self.pack.encode_error(
            MockError("nope", kind="not-authenticated", detail_type="NotAuthenticated"), op
        )
        assert response.status == 500
        assert b"<faultcode>ServerFaultCode</faultcode>" in response.body
        assert b'xsi:type="NotAuthenticated"' in response.body

    def test_empty_result_is_an_empty_response_element(self):
        op = self.pack.decode(soap_request('<Logout xmlns="urn:vim25"/>'))
        body = self.pack.encode(None, op).body.decode()
        assert '<LogoutResponse xmlns="urn:vim25"></LogoutResponse>' in body

    def test_namespace_is_echoed_back(self):
        op = self.pack.decode(soap_request('<Q xmlns="urn:pbm"/>'))
        assert b'xmlns="urn:pbm"' in self.pack.encode("v", op).body

    def test_missing_body_is_rejected(self):
        request = RawRequest(
            "POST",
            "/sdk",
            "",
            {},
            b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"/>',
        )
        with pytest.raises(MockError, match="Body"):
            self.pack.decode(request)


class TestJsonRpcPack:
    pack = JsonRpcPack()

    def test_method_is_the_operation(self):
        request = RawRequest(
            "POST",
            "/api",
            "",
            {"content-type": "application/json"},
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": "list", "params": {"a": 1}}).encode(),
        )
        op = self.pack.decode(request)
        assert op.name == "list"
        assert op.params == {"a": 1}
        assert op.context["id"] == 7

    def test_result_is_wrapped_in_an_envelope(self):
        op = Operation(name="list", protocol="jsonrpc", context={"id": 7})
        assert json.loads(self.pack.encode([1], op).body) == {
            "jsonrpc": "2.0",
            "id": 7,
            "result": [1],
        }

    def test_errors_stay_on_a_200_transport(self):
        op = Operation(name="list", protocol="jsonrpc", context={"id": 1})
        response = self.pack.encode_error(MockError("gone", kind="not-found"), op)
        assert response.status == 200
        assert json.loads(response.body)["error"]["code"] == -32601


class TestOpenApiPack:
    def test_operation_is_method_and_path(self):
        op = OpenApiPack().decode(RawRequest("GET", "/pets", "limit=2", {}, b""))
        assert op.name == "GET /pets"
        assert op.params["query"] == {"limit": "2"}

    def test_error_status_follows_the_kind(self):
        pack = OpenApiPack()
        op = pack.decode(RawRequest("GET", "/x", "", {}, b""))
        assert pack.encode_error(MockError("no", kind="not-found"), op).status == 404
        assert pack.encode_error(MockError("no", kind="not-authenticated"), op).status == 401


class TestDetection:
    def test_soap_is_detected_from_the_envelope(self):
        assert detect(soap_request("<Ping/>")).name == "soap"

    def test_jsonrpc_is_detected_from_the_method_member(self):
        request = RawRequest(
            "POST",
            "/api",
            "",
            {"content-type": "application/json"},
            b'{"jsonrpc":"2.0","method":"x","id":1}',
        )
        assert detect(request).name == "jsonrpc"

    def test_plain_rest_falls_through_to_openapi(self):
        assert detect(RawRequest("GET", "/pets", "", {}, b"")).name == "openapi"

    def test_unknown_pack_name_is_an_error(self):
        with pytest.raises(KeyError, match="unknown protocol pack"):
            get_pack("smoke-signals")
