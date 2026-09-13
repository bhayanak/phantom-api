"""Responders: the four ways to answer an operation."""

from __future__ import annotations

from phantom_api.mock.responders.base import Responder
from phantom_api.mock.responders.corpus import CorpusResponder
from phantom_api.mock.responders.forward import ForwardResponder
from phantom_api.mock.responders.model import ModelResponder
from phantom_api.mock.responders.template import TemplateResponder

__all__ = [
    "CorpusResponder",
    "ForwardResponder",
    "ModelResponder",
    "Responder",
    "TemplateResponder",
]
