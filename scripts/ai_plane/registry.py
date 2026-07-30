from __future__ import annotations

import re
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import scripts.ai_plane.constants as constants
import scripts.extension_registry as extension_registry
from scripts.ai_plane.frontmatter import parse_frontmatter

REGISTRY_SCHEMA_VERSION = 2
LEGACY_BASELINE_SCHEMA_VERSION = 1

CONTROL_PLANE_CORPUS = "control-plane"
PRODUCT_CORPUS = "product"

CONTROL_PLANE_TYPES = {
    "agent", "config", "decision", "memory", "migration", "project-doc", "rule", "skill",
    "spec", "workflow",
}
PRODUCT_TYPES = {
    "product-requirements", "tutorial", "how-to", "reference", "explanation", "architecture",
    "spec", "runbook", "decision", "research", "proposal",
}
PRODUCT_AUDIENCES = {"users", "product", "engineering", "design", "operations", "contributors", "agents"}
AUTHORITIES = {"canonical", "normative", "informative", "research", "historical"}
LIFECYCLE_STATUSES = {"active", "draft", "deprecated", "archived", "superseded"}
MATURITIES = {"proposed", "adopted", "partial", "implemented"}
VISIBILITIES = {"internal", "public"}
RELATION_TYPES = {
    "depends_on", "enforced_by", "informs", "supersedes", "relates_to", "conflicts_with",
    "implements", "part_of", "references",
}
SUBJECT_TYPES = {"product", "feature", "system", "crate", "module", "task", "document"}
PRODUCT_REQUIRED_FIELDS = (
    "id", "corpus", "type", "domain", "audiences", "authority", "status", "maturity",
    "visibility", "summary", "navigation", "relations", "subjects",
)


def _repo_relative(path: Path, target_ai: Path) -> str:
    return path.relative_to(target_ai.parent).as_posix()


def resolve_registry_source_path(ai_root: Path, path_value: str) -> Path:
    """Resolve schema-v2 repository-relative paths and schema-v1 `.ai`-relative paths."""
    normalized = Path(path_value)
    if normalized.parts and normalized.parts[0] in (".ai", "project"):
        return ai_root.parent / normalized
    return ai_root / normalized


def registry_path() -> Path:
    return constants.AI / "_registry.json"


def canonical_content_records() -> list[dict[str, str]]:
    """Return ``[{id, type, path}]`` for the canonical/CORE registry documents (path repository-relative),
    so pack content composition can treat a core document as a base contributor — a pack ``replace``
    relation may supersede a core id (task_190c, design.md "Content Contributions")."""
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for dir_name in ("rules", "workflows", "agents", "project", "memory", "skills", "migration", "templates", "specs", "blueprints"):
        dir_path = constants.AI / dir_name
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.rglob("*.md")):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not text.startswith("---"):
                continue
            meta, _body = parse_frontmatter(text)
            if not isinstance(meta, dict):
                continue
            doc_id = meta.get("id")
            doc_type = meta.get("type")
            if not (isinstance(doc_id, str) and doc_id and isinstance(doc_type, str) and doc_type):
                continue
            if doc_id in seen:
                continue
            seen.add(doc_id)
            records.append({"id": doc_id, "type": doc_type,
                            "path": file_path.relative_to(constants.ROOT).as_posix()})
    return records


def _known_task_and_decision_ids(target_ai: Path) -> set[str]:
    """Return task and decision IDs from the `.ai/tasks/` folder structure and the
    decisions.md file so spec/document relations referencing tasks or decisions
    resolve cleanly."""
    known: set[str] = set()
    # Discover task IDs from folder names
    tasks_dir = target_ai / "tasks"
    if tasks_dir.exists():
        for state in ("queue", "active", "done", "archive"):
            state_dir = tasks_dir / state
            if state_dir.exists():
                for task_folder in state_dir.iterdir():
                    if task_folder.is_dir():
                        folder_name = task_folder.name
                        known.add(folder_name)
                        if "_" in folder_name:
                            parts = folder_name.split("_")
                            if len(parts) >= 2 and parts[0] == "task":
                                known.add(f"{parts[0]}_{parts[1]}")
    # Discover decision IDs from decisions.md headings and inline references
    decisions_file = target_ai / "project" / "decisions.md"
    if decisions_file.exists():
        try:
            text = decisions_file.read_text(encoding="utf-8")
            # Match patterns like D1, D2, decision-5, Decision_42 in headings or inline
            for match in re.findall(r'\b([Dd](?:ecision)?[-_]?\d+)\b', text):
                known.add(match.lower().replace(" ", "-"))
                known.add(match)
        except (OSError, UnicodeDecodeError):
            pass
    return known


def _assemble_doc_entry(rel_path: str, doc_id: str, doc_type: str, meta: dict[str, Any],
                        body: str, stem: str) -> dict[str, Any]:
    """Build one registry document entry from validated frontmatter, with the title fallback and the
    known-then-remaining key copy order. Shared by the canonical-dir scan and pack content composition
    so both index a document the same way."""
    title = meta.get("title")
    if not isinstance(title, str) or not title:
        title = ""
        for line in body.splitlines():
            if line.strip().startswith("# "):
                title = line.strip().lstrip("#").strip()
                break
        if not title:
            title = stem.replace("_", " ").replace("-", " ").title()

    doc_entry: dict[str, Any] = {"id": doc_id, "path": rel_path, "type": doc_type, "title": title}
    for key in ("domain", "status", "owner", "created", "updated", "supersedes",
                "superseded_by", "tags", "relations"):
        if key in meta and meta[key] is not None:
            doc_entry[key] = meta[key]
    for key, val in meta.items():
        if key not in doc_entry:
            doc_entry[key] = val
    return doc_entry



def _load_product_legacy_baseline(target_ai: Path) -> tuple[dict[str, str], list[str]]:
    baseline_path = target_ai / "project" / "product-doc-legacy-baseline.json"
    if not baseline_path.exists():
        return {}, []
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {}, [f"Invalid product-document legacy baseline: {error}"]
    errors: list[str] = []
    if payload.get("schema_version") != LEGACY_BASELINE_SCHEMA_VERSION:
        errors.append(
            f"Invalid product-document legacy baseline schema_version: expected {LEGACY_BASELINE_SCHEMA_VERSION}"
        )
    records = payload.get("documents")
    if not isinstance(records, list):
        return {}, errors + ["Invalid product-document legacy baseline: 'documents' must be a list"]
    baseline: dict[str, str] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"Invalid product-document legacy baseline record {index}: expected mapping")
            continue
        path_value = record.get("path")
        digest = record.get("sha256")
        valid_path = isinstance(path_value, str) and bool(
            re.fullmatch(r"project/docs/[A-Za-z0-9._/-]+\.md", path_value)
        ) and ".." not in PurePosixPath(path_value).parts
        if not valid_path:
            errors.append(f"Invalid product-document legacy baseline path at record {index}: {path_value!r}")
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"Invalid product-document legacy baseline sha256 for {path_value!r}")
            continue
        if path_value in baseline:
            errors.append(f"Duplicate product-document legacy baseline path: {path_value}")
            continue
        baseline[path_value] = digest
    return baseline, errors


def _validate_string_list(
    meta: dict[str, Any], field: str, rel_path: str, errors: list[str], *, allowed: set[str] | None = None,
    allow_empty: bool = True,
) -> None:
    value = meta.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        errors.append(f"Product document {rel_path} field '{field}' must be a list of non-empty strings")
        return
    if not allow_empty and not value:
        errors.append(f"Product document {rel_path} field '{field}' must not be empty")
    if allowed is not None:
        unknown = sorted({item for item in value if item not in allowed})
        if unknown:
            errors.append(f"Product document {rel_path} has unknown {field}: {', '.join(unknown)}")


def _validate_product_metadata(meta: dict[str, Any], rel_path: str) -> list[str]:
    errors: list[str] = []
    for field in PRODUCT_REQUIRED_FIELDS:
        if field not in meta:
            errors.append(f"Product document {rel_path} missing required metadata field '{field}'")

    doc_id = meta.get("id")
    if not isinstance(doc_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", doc_id):
        errors.append(f"Product document {rel_path} field 'id' must be a stable kebab-case slug")

    scalar_enums = {
        "corpus": {PRODUCT_CORPUS},
        "type": PRODUCT_TYPES,
        "authority": AUTHORITIES,
        "status": LIFECYCLE_STATUSES,
        "maturity": MATURITIES,
        "visibility": VISIBILITIES,
    }
    for field, allowed in scalar_enums.items():
        value = meta.get(field)
        if not isinstance(value, str) or value not in allowed:
            errors.append(
                f"Product document {rel_path} has unknown {field} {value!r}; expected one of {', '.join(sorted(allowed))}"
            )

    domain = meta.get("domain")
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", domain):
        errors.append(f"Product document {rel_path} field 'domain' must be a topical kebab-case slug")
    summary = meta.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"Product document {rel_path} field 'summary' must be a non-empty string")

    _validate_string_list(meta, "audiences", rel_path, errors, allowed=PRODUCT_AUDIENCES, allow_empty=False)
    _validate_string_list(meta, "navigation", rel_path, errors)

    relations = meta.get("relations")
    if not isinstance(relations, list):
        errors.append(f"Product document {rel_path} field 'relations' must be a list")
    else:
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                errors.append(f"Product document {rel_path} relation {index} must be a mapping")
                continue
            relation_type = relation.get("type")
            target = relation.get("target")
            if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES:
                errors.append(f"Product document {rel_path} relation {index} has unknown type {relation_type!r}")
            if not isinstance(target, str) or not target:
                errors.append(f"Product document {rel_path} relation {index} requires a non-empty target")
            if "note" in relation and not isinstance(relation["note"], str):
                errors.append(f"Product document {rel_path} relation {index} note must be a string")

    subjects = meta.get("subjects")
    if not isinstance(subjects, list):
        errors.append(f"Product document {rel_path} field 'subjects' must be a list")
    else:
        for index, subject in enumerate(subjects):
            if not isinstance(subject, dict):
                errors.append(f"Product document {rel_path} subject {index} must be a mapping")
                continue
            subject_type = subject.get("type")
            target = subject.get("target")
            if not isinstance(subject_type, str) or subject_type not in SUBJECT_TYPES:
                errors.append(f"Product document {rel_path} subject {index} has unknown type {subject_type!r}")
            if not isinstance(target, str) or not target:
                errors.append(f"Product document {rel_path} subject {index} requires a non-empty target")
    return errors


def _legacy_product_entry(file_path: Path, rel_path: str, body: str, digest: str) -> dict[str, Any]:
    title = file_path.stem.replace("_", " ").replace("-", " ").title()
    for line in body.splitlines():
        if line.strip().startswith("# "):
            title = line.strip().lstrip("#").strip()
            break
    return {
        "id": "legacy-product-" + re.sub(
            r"[^a-z0-9]+", "-", rel_path.removeprefix("project/docs/").removesuffix(".md").lower()
        ).strip("-"),
        "path": rel_path,
        "corpus": PRODUCT_CORPUS,
        "type": "legacy-untyped",
        "title": title,
        "domain": "unclassified",
        "audiences": [],
        "authority": "unclassified",
        "status": "legacy-untyped",
        "maturity": "unclassified",
        "visibility": "internal",
        "summary": "Legacy product document awaiting governed metadata.",
        "navigation": [],
        "relations": [],
        "subjects": [],
        "legacy": True,
        "content_sha256": digest,
        "warning": "Grandfathered legacy document; no authored authority or public visibility is inferred.",
    }

def generate_registry(ai_root: Path | None = None, *,
                      extension_documents: list[dict[str, Any]] | None = None,
                      superseded_ids: set[str] | None = None) -> dict[str, Any]:
    """Build the registry from the control-plane and product corpora plus composed pack content.

    Each ``extension_documents`` entry is ``{rel_path, text, origin}`` for a rules/workflows/skills
    content contribution. Pack content remains fail-closed on invalid frontmatter or identity
    collisions. ``superseded_ids`` are canonical IDs replaced by composed pack content.
    """
    target_ai = ai_root if ai_root is not None else constants.AI
    superseded = superseded_ids or set()
    canonical_dirs = [
        "rules", "workflows", "agents", "project", "memory", "skills", "migration",
        "templates", "specs", "blueprints",
    ]
    documents_map: dict[str, dict[str, Any]] = {}
    seen_ids: dict[str, str] = {}
    warnings: list[str] = []
    errors: list[str] = []
    unresolved_references: set[str] = set()

    for dir_name in canonical_dirs:
        dir_path = target_ai / dir_name
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.rglob("*.md")):
            if not file_path.is_file():
                continue
            rel_path = _repo_relative(file_path, target_ai)
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                msg = f"Unreadable file {rel_path}: {error}"
                warnings.append(msg)
                print(f"WARNING: {msg}", file=sys.stderr)
                continue

            if not text.startswith("---"):
                continue

            meta, body = parse_frontmatter(text)
            if not meta or not isinstance(meta, dict):
                msg = f"Invalid frontmatter in {rel_path}"
                warnings.append(msg)
                print(f"WARNING: {msg}", file=sys.stderr)
                continue

            doc_id = meta.get("id")
            doc_type = meta.get("type")
            if not isinstance(doc_id, str) or not doc_id or not isinstance(doc_type, str) or not doc_type:
                msg = f"File {rel_path} frontmatter missing required 'id' or 'type'"
                warnings.append(msg)
                print(f"WARNING: {msg}", file=sys.stderr)
                continue

            if doc_id in superseded:
                continue

            if doc_id in seen_ids:
                msg = f"Duplicate document id '{doc_id}' found in {rel_path} (already in {seen_ids[doc_id]})"
                errors.append(msg)
                print(f"ERROR: {msg}", file=sys.stderr)
                continue

            seen_ids[doc_id] = rel_path
            doc_entry = _assemble_doc_entry(rel_path, doc_id, doc_type, meta, body, file_path.stem)
            doc_entry["corpus"] = CONTROL_PLANE_CORPUS
            doc_entry["source_metadata_version"] = 1
            if doc_type not in CONTROL_PLANE_TYPES:
                errors.append(f"Control-plane document {rel_path} has unknown type {doc_type!r}")
            status = meta.get("status")
            if status is not None and status not in LIFECYCLE_STATUSES:
                errors.append(f"Control-plane document {rel_path} has unknown status {status!r}")
            documents_map[doc_id] = doc_entry

    for entry in extension_documents or []:
        rel_path = entry["rel_path"]
        origin = entry["origin"]
        meta, body = parse_frontmatter(entry["text"])
        if not meta or not isinstance(meta, dict):
            raise extension_registry.RegistryError(
                "invalid-content", f"pack {origin!r} content {rel_path!r} has invalid frontmatter")
        doc_id = meta.get("id")
        doc_type = meta.get("type")
        if not isinstance(doc_id, str) or not doc_id or not isinstance(doc_type, str) or not doc_type:
            raise extension_registry.RegistryError(
                "invalid-content", f"pack {origin!r} content {rel_path!r} is missing frontmatter 'id' or 'type'")
        if doc_id in seen_ids:
            raise extension_registry.RegistryError(
                "content-id-conflict",
                f"pack {origin!r} content id {doc_id!r} ({rel_path}) collides with {seen_ids[doc_id]!r}")
        seen_ids[doc_id] = rel_path
        doc_entry = _assemble_doc_entry(rel_path, doc_id, doc_type, meta, body, Path(rel_path).stem)
        doc_entry["origin"] = origin
        doc_entry["corpus"] = CONTROL_PLANE_CORPUS
        doc_entry["source_metadata_version"] = 1
        documents_map[doc_id] = doc_entry

    baseline, baseline_errors = _load_product_legacy_baseline(target_ai)
    errors.extend(baseline_errors)
    product_root = target_ai.parent / "project" / "docs"
    if product_root.exists():
        for file_path in sorted(product_root.rglob("*.md")):
            rel_path = _repo_relative(file_path, target_ai)
            try:
                raw = file_path.read_bytes()
                text = raw.decode("utf-8-sig")
            except (OSError, UnicodeDecodeError) as error:
                errors.append(f"Unreadable product document {rel_path}: {error}")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            meta, body = parse_frontmatter(text)
            if meta:
                metadata_errors = _validate_product_metadata(meta, rel_path)
                if metadata_errors:
                    errors.extend(metadata_errors)
                    continue
                doc_id = str(meta["id"])
                if doc_id in seen_ids:
                    errors.append(
                        f"Duplicate document id '{doc_id}' found in {rel_path} (already in {seen_ids[doc_id]})"
                    )
                    continue
                seen_ids[doc_id] = rel_path
                doc_entry = _assemble_doc_entry(
                    rel_path, doc_id, str(meta["type"]), meta, body, file_path.stem
                )
                doc_entry["source_metadata_version"] = REGISTRY_SCHEMA_VERSION
                doc_entry["content_sha256"] = digest
                documents_map[doc_id] = doc_entry
                continue

            if baseline.get(rel_path) == digest:
                doc_entry = _legacy_product_entry(file_path, rel_path, body, digest)
                doc_id = str(doc_entry["id"])
                if doc_id in seen_ids:
                    errors.append(
                        f"Duplicate document id '{doc_id}' found in {rel_path} (already in {seen_ids[doc_id]})"
                    )
                    continue
                seen_ids[doc_id] = rel_path
                documents_map[doc_id] = doc_entry
                warnings.append(f"Legacy product document {rel_path} is indexed as legacy-untyped")
                continue

            reason = (
                "content hash changed from the legacy baseline"
                if rel_path in baseline else "path is not in the legacy baseline"
            )
            errors.append(f"Product document {rel_path} missing required metadata: {reason}")

    registered_ids = set(documents_map)
    known_external = _known_task_and_decision_ids(target_ai)
    for doc in documents_map.values():
        relations = doc.get("relations")
        if not isinstance(relations, list):
            continue
        for rel_item in relations:
            if not isinstance(rel_item, dict):
                continue
            target = rel_item.get("target")
            if isinstance(target, str) and target not in registered_ids and target not in known_external:
                unresolved_references.add(target)
                msg = f"Unresolved relation target '{target}' in document '{doc['id']}' ({doc['path']})"
                warnings.append(msg)
                errors.append(msg)
                print(f"WARNING: {msg}", file=sys.stderr)

    def sanitize_json_obj(obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {str(key): sanitize_json_obj(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [sanitize_json_obj(value) for value in obj]
        return obj

    raw_payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "generator": "ai sync",
        "corpora": [
            {"id": CONTROL_PLANE_CORPUS, "path_root": ".ai/"},
            {"id": PRODUCT_CORPUS, "path_root": "project/docs/"},
        ],
        "documents": [documents_map[key] for key in sorted(documents_map)],
        "unresolved_references": sorted(unresolved_references),
        "warnings": sorted(set(warnings)),
        "errors": sorted(set(errors)),
    }
    return sanitize_json_obj(raw_payload)