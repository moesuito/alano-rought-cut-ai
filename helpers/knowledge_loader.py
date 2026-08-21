"""Fail-closed loader for the embedded editorial agent knowledge library."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource


KNOWLEDGE_DIR_ENV = "ALANOCUT_KNOWLEDGE_DIR"
DEFAULT_CHECKOUT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "agent_knowledge"
SUPPORTED_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_PATH = "schemas/knowledge-manifest.schema.json"
JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


class KnowledgeManifestError(RuntimeError):
    """Raised when the editorial knowledge library is absent or inconsistent."""


class ArtifactSchemaError(RuntimeError):
    """Raised when an artifact does not satisfy its declared offline schema."""


@dataclass(frozen=True)
class KnowledgeBundle:
    """A deterministic, auditable prompt bundle selected from the manifest."""

    root: Path
    manifest: dict[str, Any]
    sources: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class ArtifactSchemaCatalog:
    """Compiled Draft 2020-12 validators backed only by local schemas."""

    root: Path
    schemas: Mapping[str, Mapping[str, Any]]
    validators: Mapping[str, Draft202012Validator]

    def validate(self, schema_path: str, payload: object) -> None:
        """Validate a payload without resolving network resources."""
        validator = self.validators.get(schema_path)
        if validator is None:
            raise ArtifactSchemaError(f"Unknown artifact schema: {schema_path}")
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors:
            return
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise ArtifactSchemaError(
            f"Artifact does not match {schema_path} at {location}: {first.message}"
        ) from first


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeManifestError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise KnowledgeManifestError(f"{label} must be a JSON object: {path}")
    return value


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _resolve_pointer(document: Any, pointer: str, label: str) -> Any:
    if pointer in {"", "#"}:
        return document
    if not pointer.startswith("#/"):
        raise KnowledgeManifestError(f"{label} uses an unsupported JSON pointer: {pointer}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise KnowledgeManifestError(f"{label} references a missing JSON pointer: {pointer}")
        current = current[part]
    return current


def _validate_instance(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    field: str,
) -> None:
    """Validate the JSON-Schema subset used by the knowledge manifest."""
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#"):
            raise KnowledgeManifestError(
                f"Manifest schema has an unsupported reference at {field}: {reference!r}"
            )
        target = _resolve_pointer(root_schema, reference, f"Manifest schema at {field}")
        if not isinstance(target, dict):
            raise KnowledgeManifestError(f"Manifest schema reference is not an object: {reference}")
        _validate_instance(value, target, root_schema, field)
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not types or not all(isinstance(item, str) for item in types):
            raise KnowledgeManifestError(f"Manifest schema has an invalid type at {field}")
        if not any(_matches_json_type(value, item) for item in types):
            raise KnowledgeManifestError(
                f"Manifest field '{field}' must have type {' or '.join(types)}"
            )

    if "const" in schema and value != schema["const"]:
        raise KnowledgeManifestError(
            f"Manifest field '{field}' must equal {schema['const']!r}"
        )
    if "enum" in schema and value not in schema["enum"]:
        raise KnowledgeManifestError(f"Manifest field '{field}' is not an allowed value")

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or re.search(pattern, value) is None:
                raise KnowledgeManifestError(
                    f"Manifest field '{field}' does not match the required pattern"
                )

    if isinstance(value, list):
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(serialized) != len(set(serialized)):
                raise KnowledgeManifestError(f"Manifest field '{field}' contains duplicates")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_instance(item, item_schema, root_schema, f"{field}[{index}]")

    if isinstance(value, dict):
        minimum = schema.get("minProperties")
        if isinstance(minimum, int) and len(value) < minimum:
            raise KnowledgeManifestError(
                f"Manifest field '{field}' must contain at least {minimum} properties"
            )
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise KnowledgeManifestError(f"Manifest schema has invalid required fields at {field}")
        missing = [name for name in required if name not in value]
        if missing:
            raise KnowledgeManifestError(
                f"Manifest field '{field}' is missing required keys: {', '.join(missing)}"
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise KnowledgeManifestError(f"Manifest schema has invalid properties at {field}")
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child_field = name if field == "$" else f"{field}.{name}"
            if name in properties:
                child_schema = properties[name]
                if not isinstance(child_schema, dict):
                    raise KnowledgeManifestError(
                        f"Manifest schema property is not an object at {child_field}"
                    )
                _validate_instance(item, child_schema, root_schema, child_field)
            elif additional is False:
                raise KnowledgeManifestError(
                    f"Manifest field '{field}' contains an unsupported key: {name}"
                )
            elif isinstance(additional, dict):
                _validate_instance(item, additional, root_schema, child_field)


def _iter_schema_nodes(value: Any, location: str = "$"):
    if isinstance(value, dict):
        yield location, value
        for key, item in value.items():
            yield from _iter_schema_nodes(item, f"{location}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_schema_nodes(item, f"{location}/{index}")


def _validate_schema_document(
    root: Path,
    relative: str,
    schema: dict[str, Any],
    declared_schemas: set[str],
    schema_cache: dict[str, dict[str, Any]],
) -> None:
    label = f"Schema '{relative}'"
    if schema.get("$schema") != JSON_SCHEMA_DRAFT_2020_12:
        raise KnowledgeManifestError(
            f"{label} must declare JSON Schema draft 2020-12"
        )
    if not isinstance(schema.get("$id"), str) or not schema["$id"].strip():
        raise KnowledgeManifestError(f"{label} must declare a non-empty $id")
    if schema.get("type") != "object":
        raise KnowledgeManifestError(f"{label} must describe a top-level object")
    if not isinstance(schema.get("properties"), dict):
        raise KnowledgeManifestError(f"{label} must declare top-level properties")
    required = schema.get("required")
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(item, str) and item for item in required)
        or len(required) != len(set(required))
    ):
        raise KnowledgeManifestError(f"{label} must declare unique required fields")
    missing_properties = [name for name in required if name not in schema["properties"]]
    if missing_properties:
        raise KnowledgeManifestError(
            f"{label} requires undeclared properties: {', '.join(missing_properties)}"
        )

    for location, node in _iter_schema_nodes(schema):
        nested_required = node.get("required")
        if isinstance(nested_required, list):
            nested_properties = node.get("properties")
            if (
                len(nested_required) != len(set(nested_required))
                or not all(isinstance(item, str) and item for item in nested_required)
                or not isinstance(nested_properties, dict)
            ):
                raise KnowledgeManifestError(f"{label} has incoherent required fields at {location}")
            missing = [name for name in nested_required if name not in nested_properties]
            if missing:
                raise KnowledgeManifestError(
                    f"{label} requires undeclared fields at {location}: {', '.join(missing)}"
                )
        elif nested_required is not None and (
            "type" in node or "properties" in node or "$schema" in node
        ):
            raise KnowledgeManifestError(f"{label} has incoherent required fields at {location}")

        reference = node.get("$ref")
        if reference is None:
            continue
        if not isinstance(reference, str) or not reference:
            raise KnowledgeManifestError(f"{label} has an invalid $ref at {location}")
        if reference.startswith("#"):
            _resolve_pointer(schema, reference, f"{label} at {location}")
            continue
        if "://" in reference or reference.startswith(("/", "\\")):
            raise KnowledgeManifestError(f"{label} uses a non-local $ref at {location}")
        reference_path, separator, fragment = reference.partition("#")
        candidate = (root / Path(relative).parent / reference_path).resolve()
        try:
            target_relative = candidate.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise KnowledgeManifestError(f"{label} has a $ref outside knowledge root") from exc
        if target_relative not in declared_schemas:
            raise KnowledgeManifestError(
                f"{label} references an undeclared schema: {target_relative}"
            )
        target_schema = schema_cache.get(target_relative)
        if target_schema is None:
            target_schema = _read_json_object(candidate, "Referenced schema")
            schema_cache[target_relative] = target_schema
        if separator:
            _resolve_pointer(target_schema, f"#{fragment}", f"{label} at {location}")


def _appdata_knowledge_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "alano-rought-cut-ai" / "agent_knowledge"


def resolve_knowledge_root(override: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an explicit override, checkout library, or global AppData install.

    An explicit argument has precedence over the environment variable. Once an
    existing candidate directory is selected, validation errors do not fall
    through to another copy: a broken library must fail closed.
    """
    explicit = override if override is not None else os.environ.get(KNOWLEDGE_DIR_ENV)
    if explicit:
        selected = Path(explicit).expanduser().absolute()
        if selected.exists() or selected.is_symlink():
            _reject_reparse(selected, "Editorial knowledge root")
        return selected.resolve()

    candidates = [DEFAULT_CHECKOUT_KNOWLEDGE_DIR]
    appdata_dir = _appdata_knowledge_dir()
    if appdata_dir is not None:
        candidates.append(appdata_dir)

    for candidate in candidates:
        if candidate.is_dir():
            _reject_reparse(candidate, "Editorial knowledge root")
            return candidate.resolve()

    rendered = ", ".join(str(path) for path in candidates)
    raise KnowledgeManifestError(
        f"Editorial knowledge directory not found. Checked: {rendered}"
    )


def _as_relative_file(root: Path, value: Any, field: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeManifestError(f"Manifest field '{field}' must be a non-empty path")

    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise KnowledgeManifestError(f"Manifest field '{field}' escapes the knowledge root")

    root_resolved = root.resolve()
    unresolved = root_resolved / relative
    current = root_resolved
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse(current, f"Manifest field '{field}'")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise KnowledgeManifestError(
            f"Manifest field '{field}' escapes the knowledge root"
        ) from exc

    if not candidate.is_file():
        raise KnowledgeManifestError(
            f"Manifest field '{field}' references a missing file: {value}"
        )
    return relative.as_posix(), candidate


def _reject_reparse(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise KnowledgeManifestError(f"{label} is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
        raise KnowledgeManifestError(f"{label} cannot be a filesystem link")


def _require_path_list(root: Path, value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise KnowledgeManifestError(f"Manifest field '{field}' must be a list")
    paths: list[str] = []
    for index, item in enumerate(value):
        relative, _ = _as_relative_file(root, item, f"{field}[{index}]")
        paths.append(relative)
    return paths


def load_manifest(
    knowledge_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Load and structurally validate ``manifest.json`` and every declared file."""
    root = resolve_knowledge_root(knowledge_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise KnowledgeManifestError(f"Editorial knowledge manifest not found: {manifest_path}")

    manifest = _read_json_object(manifest_path, "Editorial knowledge manifest")
    manifest_schema_file = root / MANIFEST_SCHEMA_PATH
    if not manifest_schema_file.is_file():
        raise KnowledgeManifestError(
            f"Editorial knowledge manifest schema not found: {manifest_schema_file}"
        )
    manifest_schema = _read_json_object(
        manifest_schema_file, "Editorial knowledge manifest schema"
    )
    _validate_instance(manifest, manifest_schema, manifest_schema, "$")

    declared_manifest_schema, _ = _as_relative_file(
        root, manifest.get("$schema"), "$schema"
    )
    if declared_manifest_schema != MANIFEST_SCHEMA_PATH:
        raise KnowledgeManifestError(
            f"Manifest $schema must reference {MANIFEST_SCHEMA_PATH}"
        )

    if manifest.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise KnowledgeManifestError(
            "Unsupported editorial knowledge schema_version: "
            f"{manifest.get('schema_version')!r}"
        )

    _as_relative_file(root, manifest.get("entrypoint"), "entrypoint")
    _require_path_list(root, manifest.get("always"), "always")

    phases = manifest.get("phases")
    if not isinstance(phases, dict) or not phases:
        raise KnowledgeManifestError("Manifest field 'phases' must be a non-empty object")
    for phase_name, phase in phases.items():
        if not isinstance(phase_name, str) or not phase_name or not isinstance(phase, dict):
            raise KnowledgeManifestError("Each manifest phase must be a named object")
        _as_relative_file(root, phase.get("task"), f"phases.{phase_name}.task")
        _as_relative_file(root, phase.get("schema"), f"phases.{phase_name}.schema")
        if "contracts" in phase:
            _require_path_list(
                root, phase["contracts"], f"phases.{phase_name}.contracts"
            )
        input_schemas = phase.get("input_schemas", {})
        if not isinstance(input_schemas, dict):
            raise KnowledgeManifestError(
                f"Manifest field 'phases.{phase_name}.input_schemas' must be an object"
            )
        for input_name, schema_path in input_schemas.items():
            if not isinstance(input_name, str) or not input_name:
                raise KnowledgeManifestError(
                    f"Manifest phase '{phase_name}' has an invalid input schema name"
                )
            _as_relative_file(
                root,
                schema_path,
                f"phases.{phase_name}.input_schemas.{input_name}",
            )
        for list_field in ("reads", "writes", "dynamic"):
            if list_field in phase and not isinstance(phase[list_field], list):
                raise KnowledgeManifestError(
                    f"Manifest field 'phases.{phase_name}.{list_field}' must be a list"
                )

    declared_schemas = {MANIFEST_SCHEMA_PATH}
    for phase in phases.values():
        schema_relative, _ = _as_relative_file(root, phase["schema"], "phase.schema")
        declared_schemas.add(schema_relative)
        for input_schema in phase.get("input_schemas", {}).values():
            input_schema_relative, _ = _as_relative_file(
                root, input_schema, "phase.input_schemas"
            )
            declared_schemas.add(input_schema_relative)

    schema_cache: dict[str, dict[str, Any]] = {
        MANIFEST_SCHEMA_PATH: manifest_schema
    }
    for relative in sorted(declared_schemas):
        schema = schema_cache.get(relative)
        if schema is None:
            schema = _read_json_object(root / relative, "Editorial artifact schema")
            schema_cache[relative] = schema
        _validate_schema_document(
            root, relative, schema, declared_schemas, schema_cache
        )

    archetypes = manifest.get("archetypes")
    if not isinstance(archetypes, dict):
        raise KnowledgeManifestError("Manifest field 'archetypes' must be an object")
    for name, path in archetypes.items():
        if not isinstance(name, str) or not name:
            raise KnowledgeManifestError("Each archetype must have a non-empty name")
        _as_relative_file(root, path, f"archetypes.{name}")

    return root, manifest


def load_artifact_schema_catalog(
    knowledge_dir: str | os.PathLike[str] | None = None,
) -> ArtifactSchemaCatalog:
    """Compile every manifest-declared artifact schema into an offline registry."""
    root, manifest = load_manifest(knowledge_dir)
    schema_paths: set[str] = set()
    for phase in manifest["phases"].values():
        schema_paths.add(str(phase["schema"]))
        schema_paths.update(str(path) for path in phase.get("input_schemas", {}).values())

    schemas: dict[str, Mapping[str, Any]] = {}
    resources: list[tuple[str, Resource[Any]]] = []
    for schema_path in sorted(schema_paths):
        _, absolute = _as_relative_file(root, schema_path, "artifact.schema")
        schema = _read_json_object(absolute, "Editorial artifact schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise KnowledgeManifestError(
                f"Schema '{schema_path}' is not valid Draft 2020-12"
            ) from exc
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise KnowledgeManifestError(f"Schema '{schema_path}' has no $id")
        frozen_schema = MappingProxyType(schema)
        schemas[schema_path] = frozen_schema
        resource = Resource.from_contents(schema)
        resources.append((schema_id, resource))
        resources.append((absolute.as_uri(), resource))

    registry: Registry[Any] = Registry().with_resources(resources)
    validators: dict[str, Draft202012Validator] = {}
    for schema_path, schema in schemas.items():
        try:
            validators[schema_path] = Draft202012Validator(schema, registry=registry)
        except (SchemaError, ValidationError) as exc:
            raise KnowledgeManifestError(
                f"Could not compile artifact schema: {schema_path}"
            ) from exc

    return ArtifactSchemaCatalog(
        root=root,
        schemas=MappingProxyType(schemas),
        validators=MappingProxyType(validators),
    )


def _selected_paths(
    root: Path,
    manifest: dict[str, Any],
    phase: str | None,
    archetype: str | None,
    include_durable: bool,
) -> list[tuple[str, Path]]:
    declared: list[tuple[str, Any]] = []
    if include_durable:
        declared.append(("entrypoint", manifest["entrypoint"]))
        declared.extend(
            (f"always[{index}]", path) for index, path in enumerate(manifest["always"])
        )

    if phase is not None:
        phases = manifest["phases"]
        if phase not in phases:
            raise KnowledgeManifestError(f"Unknown editorial knowledge phase: {phase}")
        phase_config = phases[phase]
        declared.append((f"phases.{phase}.task", phase_config["task"]))
        for index, path in enumerate(phase_config.get("contracts", [])):
            declared.append((f"phases.{phase}.contracts[{index}]", path))
        declared.append((f"phases.{phase}.schema", phase_config["schema"]))

    if archetype is not None:
        if not isinstance(archetype, str) or not archetype.strip():
            raise KnowledgeManifestError("Select at most one archetype by name")
        archetypes = manifest["archetypes"]
        if archetype not in archetypes:
            raise KnowledgeManifestError(f"Unknown editorial archetype: {archetype}")
        declared.append((f"archetypes.{archetype}", archetypes[archetype]))

    selected: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for field, raw_path in declared:
        relative, absolute = _as_relative_file(root, raw_path, field)
        if relative not in seen:
            selected.append((relative, absolute))
            seen.add(relative)
    return selected


def compose_agent_knowledge(
    *,
    phase: str | None = None,
    archetype: str | None = None,
    knowledge_dir: str | os.PathLike[str] | None = None,
    include_durable: bool = True,
) -> KnowledgeBundle:
    """Compose only durable modules, the current phase, and one archetype."""
    root, manifest = load_manifest(knowledge_dir)
    selected = _selected_paths(root, manifest, phase, archetype, include_durable)
    sections: list[str] = []
    for relative, absolute in selected:
        try:
            body = absolute.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise KnowledgeManifestError(
                f"Could not read editorial knowledge file: {relative}"
            ) from exc
        if not body:
            raise KnowledgeManifestError(f"Editorial knowledge file is empty: {relative}")
        sections.append(f"<!-- knowledge:{relative} -->\n{body}")

    return KnowledgeBundle(
        root=root,
        manifest=manifest,
        sources=tuple(relative for relative, _ in selected),
        content="\n\n".join(sections),
    )
