"""Derive an object graph from a recording.

A corpus is a pile of responses. Underneath it there is almost always a graph:
objects with identity, properties, and references to each other. Recovering that
graph is what lets a mock answer requests nobody recorded -- and it can be done
without knowing anything about the product, because the evidence is in the
responses.

Two extractors, one per wire shape, both producing the same neutral result:
entities in the form the model layer already consumes (``id``, ``kind``,
``props``, ``edges``).

Nothing is invented. An edge is only recorded when the reference **resolves** to
an object seen elsewhere in the recording, so a derived graph never claims a
relationship the traffic did not show.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from phantom_api.mock.protocols.xml_codec import XmlError, local_name, parse_xml

#: Fields that name an object rather than describe it.
_NAME_FIELDS = ("name", "displayName", "hostname", "label", "title")

#: Keys that are an object's own identity, not a reference to another object.
_SELF_KEYS = frozenset({"id", "uuid", "externalId", "internalId", "_id"})


@dataclass
class DerivedEntity:
    id: str
    kind: str
    props: dict[str, Any] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "props": self.props, "edges": self.edges}


@dataclass
class Topology:
    entities: dict[str, DerivedEntity] = field(default_factory=dict)
    #: Edges seen pointing at something never observed. Kept for reporting:
    #: a large number means the recording is missing a collection.
    dangling: Counter = field(default_factory=Counter)

    @property
    def by_kind(self) -> dict[str, int]:
        return dict(Counter(e.kind for e in self.entities.values()).most_common())

    def edge_summary(self) -> dict[str, int]:
        counts: Counter = Counter()
        for entity in self.entities.values():
            for name, targets in entity.edges.items():
                counts[f"{entity.kind}.{name}"] += len(targets)
        return dict(counts.most_common())

    def as_dict(self) -> dict[str, Any]:
        return {
            "object_count": len(self.entities),
            "by_kind": self.by_kind,
            "objects": [e.as_dict() for e in self.entities.values()],
        }


def derive_topology(corpus: Path) -> Topology:
    """Read every recorded response and recover the object graph."""
    manifest_path = corpus / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest.json in {corpus}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    topology = Topology()
    references: list[tuple[str, str, str, str]] = []  # owner, edge, kind hint, target id

    for entry in manifest.get("exchanges", []):
        if int(entry.get("status") or 200) >= 400:
            continue
        body = corpus / entry.get("response", "")
        if not body.exists():
            continue
        text = body.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        if entry.get("transport") == "rest":
            _from_json(text, topology, references)
        else:
            _from_xml(text, topology, references)

    _attach(topology, references)
    return topology


# -- JSON --------------------------------------------------------------------


def _from_json(text: str, topology: Topology, references: list[tuple[str, str, str, str]]) -> None:
    try:
        payload = json.loads(text)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _json_entity(item, singular(key), topology, references)
        elif isinstance(value, dict):
            _json_entity(value, singular(key), topology, references)


def _json_entity(
    node: dict,
    kind: str,
    topology: Topology,
    references: list[tuple[str, str, str, str]],
) -> str | None:
    """Record one object, and remember every reference it makes."""
    identity = node.get("id")
    if not isinstance(identity, (int, str)):
        return None

    key = f"{kind}:{identity}"
    entity = topology.entities.get(key)
    if entity is None:
        entity = DerivedEntity(id=str(identity), kind=kind)
        topology.entities[key] = entity

    for name, value in node.items():
        if name in _SELF_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            # Detail responses carry more than list responses; keep the richest.
            if name not in entity.props or entity.props[name] in (None, ""):
                entity.props[name] = value
        elif isinstance(value, dict):
            target = _reference(value)
            if target is not None:
                references.append((key, name, singular(name), target))
            else:
                _json_entity(value, singular(name), topology, references)
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                target = _reference(item)
                if target is not None:
                    references.append((key, name, singular(name), target))
                else:
                    _json_entity(item, singular(name), topology, references)
    return key


def _reference(node: dict) -> str | None:
    """A reference is a small object carrying an id and little else.

    A full nested object is an entity in its own right and is recorded as one;
    the distinction is what stops every embedded summary becoming a duplicate.
    """
    if "id" not in node or not isinstance(node["id"], (int, str)):
        return None
    if len(node) > 4:
        return None
    extra = set(node) - {"id", "uuid", "code", *(_NAME_FIELDS)}
    return str(node["id"]) if not extra else None


def singular(word: str) -> str:
    """`securityGroups` -> `securityGroup`. Crude on purpose: it only has to be
    stable, because it names a kind rather than addressing anything."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith("ches") or word.endswith("shes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


# -- XML ---------------------------------------------------------------------


def _from_xml(text: str, topology: Topology, references: list[tuple[str, str, str, str]]) -> None:
    try:
        root = parse_xml(text)
    except XmlError:
        return
    for returnval in root.iter():
        if local_name(returnval.tag) != "returnval":
            continue
        _xml_entity(returnval, topology, references)


def _xml_entity(
    node: ET.Element, topology: Topology, references: list[tuple[str, str, str, str]]
) -> None:
    obj = next((c for c in node if local_name(c.tag) == "obj"), None)
    if obj is None or not (obj.text or "").strip():
        return
    kind = obj.get("type") or "Object"
    identity = obj.text.strip()
    key = f"{kind}:{identity}"

    entity = topology.entities.get(key)
    if entity is None:
        entity = DerivedEntity(id=identity, kind=kind)
        topology.entities[key] = entity

    for prop in node:
        if local_name(prop.tag) != "propSet":
            continue
        name_el = next((c for c in prop if local_name(c.tag) == "name"), None)
        val_el = next((c for c in prop if local_name(c.tag) == "val"), None)
        if name_el is None or val_el is None:
            continue
        name = (name_el.text or "").strip()
        if not name:
            continue

        targets = [c for c in val_el.iter() if local_name(c.tag) == "ManagedObjectReference"]
        if val_el.get("type") and (val_el.text or "").strip():
            targets = [val_el]
        if targets:
            for target in targets:
                target_kind = target.get("type") or _xsi_type(target) or "Object"
                value = (target.text or "").strip()
                if value:
                    references.append((key, name, target_kind, value))
        elif (val_el.text or "").strip():
            entity.props.setdefault(name, val_el.text.strip())


def _xsi_type(element: ET.Element) -> str | None:
    for key, value in element.attrib.items():
        if local_name(key) == "type":
            return value.split(":")[-1]
    return None


# -- resolution --------------------------------------------------------------


def _attach(topology: Topology, references: list[tuple[str, str, str, str]]) -> None:
    """Turn observed references into edges, keeping only the ones that resolve.

    An id alone is ambiguous -- REST ids are only unique within a kind -- so a
    reference is matched against the kind its field name suggests first, then
    against any kind that has that id and only if exactly one does.
    """
    by_id: dict[str, list[str]] = defaultdict(list)
    for key, entity in topology.entities.items():
        by_id[entity.id].append(key)

    for owner, edge, kind_hint, target_id in references:
        candidate = f"{kind_hint}:{target_id}"
        if candidate in topology.entities:
            resolved = candidate
        else:
            matches = by_id.get(target_id, [])
            if len(matches) != 1:
                topology.dangling[f"{kind_hint}:{edge}"] += 1
                continue
            resolved = matches[0]

        if resolved == owner:
            continue
        targets = topology.entities[owner].edges.setdefault(edge, [])
        if resolved not in targets:
            targets.append(resolved)


# -- scenario ----------------------------------------------------------------


def render_scenario(topology: Topology, *, name: str, seed: int = 1234) -> str:
    """Describe the shape of the derived graph, so it can be regenerated.

    The inventory is what was seen; the scenario is the recipe. Editing the
    counts here is how you get a world of a different size with the same shape.
    """
    lines = [
        f"# Generative scenario for {name}, derived by `phantom-api mock derive`.",
        "#",
        "# Proportions are real -- they were counted from a recording. Every name",
        "# and identifier is a pseudonym. Edit the counts to change the size of the",
        "# generated world without changing its shape.",
        "",
        f"name: {name}",
        f"seed: {seed}",
        "",
        "topology:",
    ]
    for kind, count in topology.by_kind.items():
        lines.append(f"  {kind}: {count}")

    edges = topology.edge_summary()
    if edges:
        lines += ["", "# Observed relationships, as total edges of each kind.", "relations:"]
        for edge, count in edges.items():
            lines.append(f"  {edge}: {count}")

    if topology.dangling:
        lines += [
            "",
            "# References that pointed outside the recording. A large count here",
            "# means a collection was never captured -- record it and re-derive.",
            "unresolved:",
        ]
        for edge, count in topology.dangling.most_common(10):
            lines.append(f"  {edge}: {count}")
    return "\n".join(lines) + "\n"


_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "mock"
