"""Tests for the published-corpus credential gate.

The gate is a security control, and its main failure mode is not missing a
secret -- it is crying wolf until somebody switches it off. Both directions are
tested here.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_no_credentials.py"


def load_script():
    spec = importlib.util.spec_from_file_location("check_no_credentials", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return load_script()


def corpus_with(tmp_path: Path, name: str, body: str) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"sanitised": True, "exchanges": []}))
    return root


# -- what must be caught -----------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        '{"sshPassword": "NotARealPassword1!"}',
        '{"api_key": "AKIAEXAMPLE1234567890"}',
        '{"clientSecret": "s3cret-value-here"}',
        '{"accessToken": "eyJhbGciOiJIUzI1NiJ9"}',
        '{"sshPasswordHash": "deadbeefcafe0123456789abcdef0123456789abcdef0123"}',
        "<config><password>hunter2xyz</password></config>",
    ],
)
def test_a_real_credential_is_caught(checker, tmp_path, body):
    name = "leak.json" if body.startswith("{") else "leak.xml"
    assert checker.findings(corpus_with(tmp_path, name, body))


# -- what must not be ---------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        '{"sshPassword": "[scrubbed]"}',
        '{"sshPassword": null}',
        '{"sshPassword": ""}',
        "<Login><password>***REDACTED***</password></Login>",
        # A continuation token for paging, which is what vCenter returns.
        "<RetrievePropertiesExResponse><token>0</token></RetrievePropertiesExResponse>",
        '{"token": "1"}',
        '{"passwordPolicy": "strict"}',
        '{"tokenType": "bearer"}',
    ],
)
def test_values_that_are_not_credentials_are_left_alone(checker, tmp_path, body):
    name = "fine.json" if body.startswith("{") else "fine.xml"
    assert checker.findings(corpus_with(tmp_path, name, body)) == []


def test_a_short_value_is_not_treated_as_a_secret(checker):
    """No real secret is three characters, and this is where false positives start."""
    assert not checker.is_credential("abc")
    assert checker.is_credential("abcdefghij")


def test_a_number_is_not_a_secret(checker):
    assert not checker.is_credential("12345678901234")


@pytest.mark.parametrize("marker", ["[scrubbed]", "<scrubbed>", "***REDACTED***", "REDACTED"])
def test_redaction_markers_are_recognised(checker, marker):
    assert not checker.is_credential(marker)


# -- the real corpora ---------------------------------------------------------


@pytest.mark.skipif(
    not (REPO_ROOT / "examples" / "vcenter" / "corpus").exists(),
    reason="examples are not present",
)
def test_the_published_corpora_carry_no_credentials(checker):
    """The gate, run against what is actually committed."""
    for corpus in checker.CORPORA:
        root = REPO_ROOT / corpus
        if root.exists():
            assert checker.findings(root) == []


@pytest.mark.skipif(
    not (REPO_ROOT / "examples" / "vcenter" / "corpus").exists(),
    reason="examples are not present",
)
def test_the_published_corpora_are_marked_sanitised():
    for corpus in ("examples/vcenter/corpus", "examples/morpheus/corpus"):
        manifest = REPO_ROOT / corpus / "manifest.json"
        if manifest.exists():
            assert json.loads(manifest.read_text())["sanitised"] is True
