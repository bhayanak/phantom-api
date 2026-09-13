"""Hardened XML parsing and typed XML serialisation.

Parsing untrusted XML is the highest-risk thing this package does, so the entry
point is deliberately narrow: one function, with the dangerous constructs
refused outright rather than configured away.

The serialiser exists because generic ``dict -> XML`` loses the one thing SOAP
clients depend on: the concrete type of a polymorphic element. Values carry
their type alongside them via :class:`Typed`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = f"{{{XSI}}}type"

#: Refused outright. Entity expansion and external references have no legitimate
#: use in the payloads this package handles.
_FORBIDDEN = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

MAX_XML_BYTES = 32 * 1024 * 1024
MAX_XML_DEPTH = 256


class XmlError(ValueError):
    """Raised when a document is malformed or refused by the safety checks."""


def parse_xml(data: bytes | str, *, max_bytes: int = MAX_XML_BYTES) -> ET.Element:
    """Parse XML, refusing anything that could be used to attack the parser."""
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > max_bytes:
        raise XmlError(f"XML document exceeds {max_bytes} bytes")
    if _FORBIDDEN.search(raw):
        raise XmlError("DOCTYPE and ENTITY declarations are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise XmlError(f"malformed XML: {exc}") from exc
    _check_depth(root)
    return root


def _check_depth(element: ET.Element, depth: int = 1) -> None:
    if depth > MAX_XML_DEPTH:
        raise XmlError(f"XML nesting exceeds {MAX_XML_DEPTH} levels")
    for child in element:
        _check_depth(child, depth + 1)


def local_name(tag: str) -> str:
    """Strip any namespace, the way reflective SOAP clients do."""
    return tag.rsplit("}", 1)[-1]


def text_of(element: ET.Element) -> str:
    return (element.text or "").strip()


def find_local(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if local_name(child.tag) == name:
            return child
    return None


def children_named(element: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in element if local_name(c.tag) == name]


@dataclass(slots=True)
class Typed:
    """A value plus the concrete type name to advertise as ``xsi:type``.

    Clients that resolve polymorphic fields from ``xsi:type`` silently drop
    values when it is missing, so the type travels with the value rather than
    being inferred at serialisation time.
    """

    value: Any
    type_name: str | None = None
    #: Rendered as a ``type=`` attribute. Used for object references, which are
    #: identified by an attribute rather than by the element name.
    ref_type: str | None = None


@dataclass(slots=True)
class Raw:
    """Pre-rendered XML, inserted verbatim. Used for replayed fragments."""

    xml: str


def escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


#: How element_to_dict stores the text of an element that also has attributes.
TEXT_KEY = "#text"


def to_xml(tag: str, value: Any) -> str:
    """Serialise a value as an XML element, preserving declared types.

    Mapping keys become child elements, sequences repeat the element, and
    ``None`` produces nothing at all -- an absent element and an empty one mean
    different things to most clients.
    """
    if value is None:
        return ""
    if isinstance(value, Raw):
        return value.xml
    if isinstance(value, Typed):
        attrs = ""
        if value.ref_type:
            attrs += f' type="{escape(value.ref_type)}"'
        if value.type_name:
            attrs += f' xsi:type="{escape(value.type_name)}"'
        inner = _inner_xml(value.value)
        return f"<{tag}{attrs}>{inner}</{tag}>"
    if isinstance(value, (list, tuple)):
        return "".join(to_xml(tag, item) for item in value)
    # A parameter echoed back arrives as attributes plus `#text`, which is how
    # element_to_dict represents `<object type="X">v</object>`. Rebuilding it as
    # child elements would emit `<#text>`, which is not a legal element name.
    if isinstance(value, dict) and TEXT_KEY in value:
        attrs = "".join(
            f' {name}="{escape(str(item))}"' for name, item in value.items() if name != TEXT_KEY
        )
        return f"<{tag}{attrs}>{escape(value[TEXT_KEY])}</{tag}>"
    return f"<{tag}>{_inner_xml(value)}</{tag}>"


def _inner_xml(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Raw):
        return value.xml
    if isinstance(value, Typed):
        return _inner_xml(value.value)
    if isinstance(value, dict):
        return "".join(to_xml(key, item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "".join(_inner_xml(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return escape(value)


def element_to_dict(element: ET.Element) -> Any:
    """Best-effort XML to plain data, used for request parameters only.

    Repeated sibling elements collapse into a list; an element carrying both
    text and attributes keeps the text under ``#text``.
    """
    children = list(element)
    if not children:
        value: Any = text_of(element)
        attrs = {k: v for k, v in element.attrib.items() if not k.startswith(f"{{{XSI}}}")}
        if attrs:
            return {**attrs, TEXT_KEY: value} if value else attrs
        return value
    result: dict[str, Any] = {}
    for child in children:
        key = local_name(child.tag)
        parsed = element_to_dict(child)
        if key in result:
            existing = result[key]
            if isinstance(existing, list):
                existing.append(parsed)
            else:
                result[key] = [existing, parsed]
        else:
            result[key] = parsed
    for key, val in element.attrib.items():
        if not key.startswith(f"{{{XSI}}}"):
            result.setdefault(local_name(key), val)
    return result
