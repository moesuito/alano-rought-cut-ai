"""Tests for selective, fail-closed editorial knowledge loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import knowledge_loader
from helpers.knowledge_loader import (
    KnowledgeManifestError,
    compose_agent_knowledge,
    load_manifest,
    resolve_knowledge_root,
)


def _write_library(root: Path) -> Path:
    schema_document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.test/schemas/artifact.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
    }
    repository_root = Path(__file__).resolve().parents[1]
    manifest_schema = (
        repository_root / "agent_knowledge" / "schemas" / "knowledge-manifest.schema.json"
    ).read_text(encoding="utf-8")
    files = {
        "core/system.md": "SYSTEM UNIQUE",
        "core/always.md": "ALWAYS UNIQUE",
        "tasks/diagnose.md": "DIAGNOSE UNIQUE",
        "tasks/plan.md": "PLAN UNIQUE",
        "tasks/assemble.md": "ASSEMBLE UNIQUE",
        "tasks/review.md": "REVIEW UNIQUE",
        "schemas/knowledge-manifest.schema.json": manifest_schema,
        "schemas/diagnose.schema.json": json.dumps(schema_document),
        "schemas/plan.schema.json": json.dumps(
            {**schema_document, "$id": "https://example.test/schemas/plan.schema.json"}
        ),
        "schemas/assemble.schema.json": json.dumps(
            {**schema_document, "$id": "https://example.test/schemas/assemble.schema.json"}
        ),
        "schemas/review.schema.json": json.dumps(
            {**schema_document, "$id": "https://example.test/schemas/review.schema.json"}
        ),
        "archetypes/alpha.md": "ARCHETYPE ALPHA UNIQUE",
        "archetypes/beta.md": "ARCHETYPE BETA UNIQUE",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    manifest = {
        "$schema": "schemas/knowledge-manifest.schema.json",
        "schema_version": 1,
        "knowledge_version": "1.2.3",
        "entrypoint": "core/system.md",
        "always": ["core/always.md"],
        "phases": {
            "diagnose": {
                "task": "tasks/diagnose.md",
                "schema": "schemas/diagnose.schema.json",
                "reads": [],
                "writes": [],
            },
            "plan": {
                "task": "tasks/plan.md",
                "schema": "schemas/plan.schema.json",
                "reads": [],
                "writes": [],
                "dynamic": ["one_selected_archetype"],
            },
            "assemble": {
                "task": "tasks/assemble.md",
                "schema": "schemas/assemble.schema.json",
                "reads": [],
                "writes": [],
            },
            "review": {
                "task": "tasks/review.md",
                "schema": "schemas/review.schema.json",
                "reads": [],
                "writes": [],
            },
        },
        "archetypes": {
            "alpha": "archetypes/alpha.md",
            "beta": "archetypes/beta.md",
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_resolves_checkout_library_before_appdata(tmp_path, monkeypatch):
    checkout = _write_library(tmp_path / "checkout" / "agent_knowledge")
    appdata = _write_library(
        tmp_path / "appdata" / "alano-rought-cut-ai" / "agent_knowledge"
    )
    monkeypatch.delenv(knowledge_loader.KNOWLEDGE_DIR_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(knowledge_loader, "DEFAULT_CHECKOUT_KNOWLEDGE_DIR", checkout)

    assert resolve_knowledge_root() == checkout.resolve()
    assert appdata.exists()


def test_falls_back_to_global_appdata_install(tmp_path, monkeypatch):
    appdata = _write_library(
        tmp_path / "appdata" / "alano-rought-cut-ai" / "agent_knowledge"
    )
    monkeypatch.delenv(knowledge_loader.KNOWLEDGE_DIR_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        knowledge_loader,
        "DEFAULT_CHECKOUT_KNOWLEDGE_DIR",
        tmp_path / "missing-checkout" / "agent_knowledge",
    )

    assert resolve_knowledge_root() == appdata.resolve()


def test_explicit_override_has_priority(tmp_path, monkeypatch):
    explicit = _write_library(tmp_path / "explicit")
    env_library = _write_library(tmp_path / "from-env")
    monkeypatch.setenv(knowledge_loader.KNOWLEDGE_DIR_ENV, str(env_library))

    root, manifest = load_manifest(explicit)

    assert root == explicit.resolve()
    assert manifest["knowledge_version"] == "1.2.3"


def test_environment_override_is_supported(tmp_path, monkeypatch):
    environment_library = _write_library(tmp_path / "environment-library")
    monkeypatch.setenv(
        knowledge_loader.KNOWLEDGE_DIR_ENV, str(environment_library)
    )
    monkeypatch.setattr(
        knowledge_loader,
        "DEFAULT_CHECKOUT_KNOWLEDGE_DIR",
        tmp_path / "different-checkout",
    )

    assert resolve_knowledge_root() == environment_library.resolve()


@pytest.mark.parametrize("contents", [None, "{not-json", "[]"])
def test_missing_or_invalid_manifest_fails_closed(tmp_path, contents):
    root = tmp_path / "knowledge"
    root.mkdir()
    if contents is not None:
        (root / "manifest.json").write_text(contents, encoding="utf-8")

    with pytest.raises(KnowledgeManifestError):
        load_manifest(root)


def test_missing_declared_file_fails_closed(tmp_path):
    root = _write_library(tmp_path / "knowledge")
    (root / "tasks/plan.md").unlink()

    with pytest.raises(KnowledgeManifestError, match="missing file"):
        load_manifest(root)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda manifest: manifest.update(knowledge_version="test"), "pattern"),
        (lambda manifest: manifest.update(unexpected=True), "unsupported key"),
        (lambda manifest: manifest["phases"].pop("review"), "missing required"),
        (
            lambda manifest: manifest["phases"].update(extra=manifest["phases"]["review"]),
            "unsupported key",
        ),
        (
            lambda manifest: manifest["phases"]["plan"].update(
                reads=["diagnosis.json", "diagnosis.json"]
            ),
            "duplicates",
        ),
    ],
)
def test_manifest_schema_contract_fails_closed(tmp_path, mutation, match):
    root = _write_library(tmp_path / "knowledge")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutation(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(KnowledgeManifestError, match=match):
        load_manifest(root)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda schema: schema.pop("$id"), r"\$id"),
        (lambda schema: schema.update({"$schema": "https://json-schema.org/draft-07/schema"}), "2020-12"),
        (lambda schema: schema.update(required=["missing"]), "undeclared"),
        (
            lambda schema: schema["properties"].update(
                linked={"$ref": "not-declared.schema.json"}
            ),
            "undeclared schema",
        ),
        (
            lambda schema: schema["properties"].update(
                linked={"$ref": "#/$defs/missing"}
            ),
            "missing JSON pointer",
        ),
    ],
)
def test_declared_artifact_schema_metadata_and_refs_fail_closed(
    tmp_path, mutation, match
):
    root = _write_library(tmp_path / "knowledge")
    schema_path = root / "schemas" / "plan.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    mutation(schema)
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(KnowledgeManifestError, match=match):
        load_manifest(root)


def test_production_manifest_enforces_artifact_ownership_and_template_input():
    root, manifest = load_manifest()

    assert "edl_template.json" in manifest["phases"]["assemble"]["reads"]
    assert manifest["phases"]["assemble"]["input_schemas"] == {
        "edl_template.json": "schemas/edl-template.schema.json"
    }
    # A refined review carries the complete EDL, but only the host persists the
    # review and derived draft in one transaction.
    assert manifest["phases"]["review"]["writes"] == ["review.NNN.json"]
    diagnosis_schema = json.loads(
        (root / "schemas" / "diagnosis.schema.json").read_text(encoding="utf-8")
    )
    selected_archetypes = diagnosis_schema["properties"]["selected_archetype"]["enum"]
    assert None in selected_archetypes
    assert "custom" not in selected_archetypes

    custom_example = json.loads(
        (root / "examples" / "diagnosis-custom.json").read_text(encoding="utf-8")
    )
    assert custom_example["content_type"] == "custom"
    assert custom_example["selected_archetype"] is None


def test_composes_current_phase_and_only_one_selected_archetype(tmp_path):
    root = _write_library(tmp_path / "knowledge")

    bundle = compose_agent_knowledge(
        phase="plan", archetype="alpha", knowledge_dir=root
    )

    assert bundle.sources == (
        "core/system.md",
        "core/always.md",
        "tasks/plan.md",
        "schemas/plan.schema.json",
        "archetypes/alpha.md",
    )
    assert "PLAN UNIQUE" in bundle.content
    assert "DIAGNOSE UNIQUE" not in bundle.content
    assert "ARCHETYPE ALPHA UNIQUE" in bundle.content
    assert "ARCHETYPE BETA UNIQUE" not in bundle.content

    with pytest.raises(KnowledgeManifestError, match="at most one archetype"):
        compose_agent_knowledge(
            phase="plan",
            archetype=["alpha", "beta"],  # type: ignore[arg-type]
            knowledge_dir=root,
        )
