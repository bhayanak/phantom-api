"""SOAP 1.1 / 1.2 protocol pack.

Nothing here knows about any particular service. The pack extracts the operation
from the body element, pulls the session out of wherever that endpoint carries
it, and renders results and faults in the shapes SOAP clients expect.

Three details are load-bearing, and all three were confirmed against a live
endpoint rather than assumed:

* Faults are HTTP 500 with the fault document in the body. Returning 200
  disables client-side error handling entirely.
* ``xsi:type`` must be emitted on polymorphic elements, and tolerated on input
  both bare (``NotAuthenticated``) and namespace-prefixed (``vim25:NotAuthenticated``).
* An absent result is an empty response element, not an omitted one.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from phantom_api.mock.config import SessionRule
from phantom_api.mock.protocols.base import register
from phantom_api.mock.protocols.xml_codec import (
    Raw,
    XmlError,
    element_to_dict,
    escape,
    local_name,
    parse_xml,
    text_of,
    to_xml,
)
from phantom_api.mock.types import MockError, Operation, RawRequest, RawResponse

SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP12 = "http://www.w3.org/2003/05/soap-envelope"

_ENVELOPE_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/" '
    'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
)
_ENVELOPE_CLOSE = "</soapenv:Envelope>"

#: Neutral failure kinds mapped onto SOAP fault codes.
_FAULT_CODES = {
    "not-authenticated": "ServerFaultCode",
    "not-found": "ServerFaultCode",
    "invalid-request": "ServerFaultCode",
    "invalid-login": "ServerFaultCode",
    "internal": "ServerFaultCode",
    "client": "Client",
}


def _params_of(call):
    """Collect call parameters, keeping repeats.

    A SOAP operation may take the same parameter more than once -- several
    `<privId>` elements, for instance. A plain dict comprehension keeps only the
    last, and the loss is silent.
    """
    params: dict = {}
    for child in call:
        key = local_name(child.tag)
        value = element_to_dict(child)
        if key not in params:
            params[key] = value
        elif isinstance(params[key], list):
            params[key].append(value)
        else:
            params[key] = [params[key], value]
    return params


class SoapPack:
    name = "soap"

    def matches(self, request: RawRequest) -> bool:
        if request.method != "POST":
            return False
        content_type = request.header("content-type")
        if "xml" in content_type or "soap" in content_type:
            return True
        return request.body.lstrip()[:200].find(b"Envelope") != -1

    # -- decode ---------------------------------------------------------

    def decode(self, request: RawRequest, session: SessionRule | None = None) -> Operation:
        try:
            root = parse_xml(request.body)
        except XmlError as exc:
            raise MockError(str(exc), kind="invalid-request", status=500) from exc

        body = self._section(root, "Body")
        if body is None:
            raise MockError("SOAP envelope has no Body", kind="invalid-request")
        call = next(iter(body), None)
        if call is None:
            raise MockError("SOAP Body is empty", kind="invalid-request")

        namespace = call.tag[1:].split("}")[0] if call.tag.startswith("{") else ""
        header = self._section(root, "Header")
        params = _params_of(call)
        target = call.find("./{*}_this")
        if target is None:
            target = next((c for c in call if local_name(c.tag) == "_this"), None)

        return Operation(
            name=local_name(call.tag),
            protocol=self.name,
            params=params,
            headers=dict(request.headers),
            session_id=self._session_id(request, header, session),
            request=request,
            context={
                "namespace": namespace,
                "soap_action": request.header("soapaction").strip('"'),
                "target_type": target.get("type") if target is not None else None,
                "target_value": text_of(target) if target is not None else None,
                "header_elements": {
                    local_name(c.tag): text_of(c) for c in (header if header is not None else [])
                },
            },
        )

    @staticmethod
    def _section(root: ET.Element, name: str) -> ET.Element | None:
        for child in root:
            if local_name(child.tag) == name:
                return child
        return None

    @staticmethod
    def _session_id(
        request: RawRequest, header: ET.Element | None, session: SessionRule | None
    ) -> str | None:
        rule = session or SessionRule()
        if rule.transport == "cookie":
            cookies = request.header("cookie")
            for part in cookies.split(";"):
                key, _, value = part.strip().partition("=")
                if key == rule.name:
                    return value.strip('"')
            return None
        if rule.transport == "header":
            return request.header(rule.name.lower()) or None
        if rule.transport == "soap-header" and header is not None:
            for child in header:
                if local_name(child.tag) == rule.name:
                    return text_of(child).strip('"') or None
        return None

    # -- encode ---------------------------------------------------------

    def encode(self, result: Any, op: Operation) -> RawResponse:
        namespace = op.context.get("namespace") or "urn:vim25"
        if isinstance(result, RawResponse):
            return result
        payload = result.xml if isinstance(result, Raw) else to_xml("returnval", result)
        body = f'<{op.name}Response xmlns="{escape(namespace)}">{payload}</{op.name}Response>'
        return self._envelope(body, 200)

    def encode_error(self, error: MockError, op: Operation | None) -> RawResponse:
        namespace = (op.context.get("namespace") if op else None) or "urn:vim25"
        code = _FAULT_CODES.get(error.kind, "ServerFaultCode")
        detail = ""
        if error.detail_type:
            inner = "".join(to_xml(k, v) for k, v in error.detail.items())
            detail = (
                f'<detail><{error.detail_type}Fault xmlns="{escape(namespace)}" '
                f'xsi:type="{escape(error.detail_type)}">{inner}'
                f"</{error.detail_type}Fault></detail>"
            )
        body = (
            f"<soapenv:Fault><faultcode>{code}</faultcode>"
            f"<faultstring>{escape(error.message)}</faultstring>{detail}</soapenv:Fault>"
        )
        # A SOAP fault is always carried on HTTP 500, whatever the neutral kind
        # maps to elsewhere. Clients read the error stream to find it.
        return self._envelope(body, 500)

    @staticmethod
    def _envelope(body: str, status: int) -> RawResponse:
        text = f"{_ENVELOPE_OPEN}<soapenv:Body>{body}</soapenv:Body>{_ENVELOPE_CLOSE}"
        return RawResponse(
            status=status,
            headers={"content-type": "text/xml; charset=utf-8"},
            body=text.encode(),
        )


register(SoapPack())
