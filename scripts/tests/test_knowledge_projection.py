"""Task 195E deterministic unified reader projection tests."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_docs as ai_docs
import scripts.ai_plane.constants as constants
from scripts.ai_plane.knowledge_projection import (
    build_knowledge_projection,
    write_knowledge_assets,
)
from scripts.ai_plane.knowledge_projection.reader_accepted import (
    AcceptedReaderAdapterError,
    build_accepted_reader_payloads,
    render_accepted_reader_javascript,
    write_accepted_reader_assets,
)
from scripts.ai_plane.knowledge_projection.project import build_project_intelligence
from scripts.ai_plane.knowledge_projection.tasks import build_tasks


VECTOR_MUL_IDENTITY = "impl-header[impl Mul < Vector3 > for Vector3]::mul"
SCALAR_MUL_IDENTITY = "impl-header[impl Mul < f32 > for Vector3]::mul"
VECTOR_MUL_QUERY = f"core::math::{VECTOR_MUL_IDENTITY}"
SCALAR_MUL_QUERY = f"core::math::{SCALAR_MUL_IDENTITY}"


def project_export(package_count: int = 26) -> dict:
    packages = []
    modules = []
    files = []
    nodes = []
    hierarchy = []
    for index in range(package_count):
        cargo_name = f"crate-{index:02d}"
        rust_name = cargo_name.replace("-", "_")
        package_id = f"{cargo_name} 0.1.0 (path+file:///repo/{cargo_name})"
        path = f"project/crates/{cargo_name}/src/lib.rs"
        node_id = f"{rust_name}::root"
        packages.append({
            "package_id": package_id,
            "manifest_path": f"project/crates/{cargo_name}/Cargo.toml",
            "display_name": cargo_name,
            "symbol_namespace": rust_name,
            "purpose": {
                "value": f"Purpose for {cargo_name}.",
                "provenance": "cargo-package-description",
            },
            "related_product_document_ids": ["product-architecture"] if index == 0 else [],
        })
        modules.append({
            "path": path,
            "unit_name": rust_name,
            "module_path": rust_name,
            "purpose": {
                "value": f"Module purpose for {rust_name}.",
                "provenance": "authored-leading-inner-rustdoc",
            },
        })
        files.append({
            "path": path,
            "size_bytes": 128 + index,
            "sha256": f"{index:064x}",
            "unit_name": rust_name,
            "module_path": rust_name,
        })
        nodes.append({
            "id": node_id,
            "path": path,
            "kind": "function",
            "identity_name": "root",
            "qualified_name": node_id,
            "unit_name": rust_name,
            "module_path": rust_name,
            "start_row": index,
            "start_column": 0,
            "end_row": index,
            "end_column": 8,
        })
        hierarchy.append({
            "unit_name": rust_name,
            "module_path": rust_name,
            "path": path,
            "semantic_node_ids": [node_id],
        })
    relations = [{
        "source_id": nodes[0]["id"],
        "target_id": nodes[1]["id"],
        "kind": "calls",
        "provenance": "resolved-lexical-reference",
        "confidence": 100,
    }]
    payload = {
        "contract_version": 1,
        "source_fingerprint": "accepted-task-195c-fingerprint",
        "packages": packages,
        "modules": modules,
        "files": files,
        "semantic_nodes": nodes,
        "semantic_hierarchy": hierarchy,
        "relations": relations,
        "pending_boundaries": [{
            "owner_path": modules[0]["path"],
            "source_node_id": nodes[0]["id"],
            "spelling": "unknown_target",
            "reason": "unresolved",
            "start_row": 10,
            "start_column": 4,
        }],
        "omissions": {
            "packages_missing_cargo_description": [],
            "modules_missing_authored_purpose": [],
            "packages_without_related_product_documents": [
                package["display_name"] for package in packages[1:]
            ],
        },
    }
    return payload


def project_export_with_agent_proofs() -> dict:
    payload = project_export(2)
    path = "crates/core/src/math.rs"
    package_id = "core 0.1.0 (path+file:///repo/core)"
    payload["packages"].append({
        "package_id": package_id,
        "manifest_path": "crates/core/Cargo.toml",
        "display_name": "core",
        "symbol_namespace": "core",
        "purpose": {
            "value": "Foundational math and spatial contracts.",
            "provenance": "cargo-package-description",
        },
        "related_product_document_ids": [],
    })
    payload["modules"].append({
        "path": path,
        "unit_name": "core",
        "module_path": "math",
        "purpose": {
            "value": "Vector math.",
            "provenance": "authored-leading-inner-rustdoc",
        },
    })
    payload["files"].append({
        "path": path,
        "size_bytes": 4096,
        "sha256": "f" * 64,
        "unit_name": "core",
        "module_path": "math",
    })
    proof_nodes = [
        {
            "id": "a" * 64,
            "path": path,
            "kind": "method",
            "identity_name": VECTOR_MUL_IDENTITY,
            "qualified_name": "Vector3::mul",
            "unit_name": "core",
            "module_path": "math",
            "start_row": 100,
            "start_column": 4,
            "end_row": 102,
            "end_column": 5,
        },
        {
            "id": "b" * 64,
            "path": path,
            "kind": "method",
            "identity_name": SCALAR_MUL_IDENTITY,
            "qualified_name": "Vector3::mul",
            "unit_name": "core",
            "module_path": "math",
            "start_row": 120,
            "start_column": 4,
            "end_row": 122,
            "end_column": 5,
        },
    ]
    payload["semantic_nodes"].extend(proof_nodes)
    payload["semantic_hierarchy"].append({
        "unit_name": "core",
        "module_path": "math",
        "path": path,
        "semantic_node_ids": [node["id"] for node in proof_nodes],
    })
    return payload


def versioned_receipt(receipt_id: str, role: str, sequence: int, status: str) -> dict:
    key = "attempt" if role == "executor" else "round"
    return {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "task_id": "task_example",
        "role": role,
        "sequence": {key: sequence},
        "actor": {
            "name": "fixture",
            "family": "codex",
            "tool": "fixture",
            "model": "fixture",
            "reasoning": "high",
        },
        "revision": {"base_commit": "a", "head_commit": "b", "diff": "a..b"},
        "environment": {"os": "test", "arch": "test", "device": "test", "toolchain": "test"},
        "decision": {"status": status, "outcome": f"{role} {sequence} {status}"},
        "gates": [],
        "evidence_refs": [],
        "context_items": [{
            "context_item_id": f"{receipt_id}-context",
            "type": "finding",
            "blocking": False,
            "severity": "low",
            "summary": "Preserved fixture context.",
            "state": "resolved",
            "source_receipt_id": receipt_id,
            "locations": [],
            "evidence_refs": [],
            "resolution": "Resolved in fixture.",
        }],
        "notes": [],
    }


class KnowledgeProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".ai" / "rules").mkdir(parents=True)
        (self.root / "project" / "docs").mkdir(parents=True)
        (self.root / ".ai" / "tasks" / "done" / "task_example").mkdir(parents=True)
        (self.root / ".ai" / "project").mkdir(parents=True)


    def write_governed_ai_impact_sources(self) -> Path:
        tool = self.root / "tools" / "ai-impact"
        (tool / "src").mkdir(parents=True)
        (tool / "Cargo.toml").write_text(
            "[package]\nname='ai-impact'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
        (tool / "Cargo.lock").write_text("# governed lock\n", encoding="utf-8")
        (tool / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        (self.root / "project" / "Cargo.toml").write_text(
            "[workspace]\nmembers=[]\n",
            encoding="utf-8",
        )
        return tool

    def registry(self) -> tuple[dict, dict[str, str], list[dict]]:
        control_body = "# Control Rule\n\nSee [product architecture](product-architecture)."
        product_body = "# Product Architecture\n\nProduct body."
        registry = {
            "schema_version": 2,
            "documents": [
                {
                    "id": "control-rule",
                    "corpus": "control-plane",
                    "type": "rule",
                    "domain": "control-plane",
                    "status": "active",
                    "title": "Control Rule",
                    "path": ".ai/rules/control.md",
                    "relations": [{"type": "references", "target": "product-architecture"}],
                },
                {
                    "id": "product-architecture",
                    "corpus": "product",
                    "type": "architecture",
                    "domain": "rendering",
                    "status": "active",
                    "title": "Product Architecture",
                    "path": "project/docs/architecture.md",
                    "relations": [],
                },
            ],
            "warnings": [],
            "errors": [],
            "unresolved_references": [],
        }
        edges = [{
            "source": "control-rule",
            "target": "product-architecture",
            "type": "references",
            "provenance": "authored",
            "source_corpus": "control-plane",
            "target_corpus": "product",
            "bridge": True,
        }]
        return registry, {
            "control-rule": control_body,
            "product-architecture": product_body,
        }, edges

    def write_tasks(self) -> None:
        task_dir = self.root / ".ai" / "tasks" / "done" / "task_example"
        evidence_dir = task_dir / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        (evidence_dir / "reader.png").write_bytes(b"fixture-image")
        (task_dir / "task.yaml").write_text(
            'id: "task_example"\n'
            'title: "Example"\n'
            'feature: "Display-only feature"\n'
            'status: "accepted"\n'
            'depends_on: []\n',
            encoding="utf-8",
        )
        receipts = [
            ("receipt.executor.yaml", versioned_receipt("example-executor-1", "executor", 1, "ready")),
            ("receipt.qa.round-1.yaml", versioned_receipt("example-qa-1", "qa", 1, "revise")),
            ("receipt.executor.attempt-2.yaml", versioned_receipt("example-executor-2", "executor", 2, "ready")),
            ("receipt.qa.yaml", versioned_receipt("example-qa-2", "qa", 2, "accept")),
        ]
        for name, data in receipts:
            (task_dir / name).write_text(json.dumps(data), encoding="utf-8")
        (task_dir / "evidence.yaml").write_text(json.dumps({
            "schema_version": 1,
            "evidence_set_id": "example-evidence",
            "task_id": "task_example",
            "items": [
                {
                    "evidence_id": "result",
                    "kind": "generated-result",
                    "role": "acceptance",
                    "storage": "regenerable",
                    "availability": "available",
                    "producer": {
                        "command": "python fixture.py",
                        "environment": {"os": "test", "arch": "test", "device": "test"},
                    },
                    "claim": "Fixture result.",
                    "acceptance_links": ["reader-data"],
                    "accessibility_text": "Fixture generated result.",
                    "artifact": {
                        "path": ".ai/tasks/queue/task_example/evidence/reader.png",
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(b"fixture-image").hexdigest(),
                        "width": 1440,
                        "height": 900,
                    },
                },
                {
                    "evidence_id": "expected-design",
                    "kind": "expected-reference",
                    "role": "supporting",
                    "storage": "external",
                    "availability": "available",
                    "producer": {
                        "command": "owner-provided reference",
                        "environment": {"os": "test", "arch": "test", "device": "test"},
                    },
                    "claim": "Expected comparison reference only.",
                    "acceptance_links": [],
                    "accessibility_text": "Expected reference, not a generated result.",
                },
                {
                    "evidence_id": "missing-preview",
                    "kind": "generated-result",
                    "role": "diagnostic",
                    "storage": "regenerable",
                    "availability": "unavailable",
                    "producer": {
                        "command": "python render_preview.py",
                        "environment": {"os": "test", "arch": "test", "device": "test"},
                    },
                    "claim": "Preview can be regenerated.",
                    "acceptance_links": [],
                    "accessibility_text": "Unavailable regenerable preview.",
                },
            ],
        }), encoding="utf-8")
        context_ids = [
            f"{receipt_id}-context"
            for receipt_id in ("example-executor-1", "example-qa-1", "example-executor-2", "example-qa-2")
        ]
        (task_dir / "task-closeout.yaml").write_text(json.dumps({
            "schema_version": 1,
            "closeout_id": "example-closeout",
            "task_id": "task_example",
            "accepted_receipt_id": "example-qa-2",
            "receipt_events": [
                "example-executor-1", "example-qa-1", "example-executor-2", "example-qa-2",
            ],
            "context_dispositions": [
                {
                    "context_item_id": item,
                    "source_receipt_id": item.removesuffix("-context"),
                    "disposition": "resolved",
                    "rationale": "Resolved in fixture.",
                }
                for item in context_ids
            ],
            "closed_by": "fixture",
            "closed_at": "2026-07-29T00:00:00+07:00",
        }), encoding="utf-8")

    def build(self) -> dict:
        self.write_tasks()
        registry, bodies, edges = self.registry()
        return build_knowledge_projection(
            self.root,
            registry_data=registry,
            document_edges=edges,
            document_bodies=bodies,
            project_export=project_export(),
            revision={
                "commit": "abc123",
                "refreshed_at": "2026-07-29T00:00:00+07:00",
                "refresh_time_provenance": "fixture",
            },
        )

    def test_identical_governed_inputs_emit_byte_identical_assets(self) -> None:
        first = self.build()
        first_dir = self.root / "site-a"
        second_dir = self.root / "site-b"
        first_paths = write_knowledge_assets(first, first_dir)
        second = self.build()
        second_paths = write_knowledge_assets(second, second_dir)
        self.assertEqual(first["source"]["fingerprint"], second["source"]["fingerprint"])
        self.assertEqual(first_paths[0].read_bytes(), second_paths[0].read_bytes())
        self.assertEqual(first_paths[1].read_bytes(), second_paths[1].read_bytes())

    def test_document_corpora_are_namespaced_and_bridges_default_off(self) -> None:
        documents = self.build()["truth_systems"]["documents"]
        self.assertEqual(
            {"control-plane", "product"}, set(documents["corpora"])
        )
        control = documents["corpora"]["control-plane"]
        product = documents["corpora"]["product"]
        self.assertEqual("control-plane::control-rule", control["library"][0]["namespace_id"])
        self.assertEqual("product::product-architecture", product["library"][0]["namespace_id"])
        self.assertEqual([], control["graph"]["edges"])
        self.assertEqual([], product["graph"]["edges"])
        self.assertEqual(1, len(documents["bridges"]))
        self.assertFalse(documents["bridges"][0]["enabled_by_default"])
        self.assertEqual("authored", documents["bridges"][0]["provenance"])
        self.assertIn("Product body.", product["library"][0]["body"])

    def test_project_export_preserves_26_packages_and_layout_only_groups(self) -> None:
        project = self.build()["truth_systems"]["project_intelligence"]
        self.assertEqual(26, len(project["packages"]))
        self.assertEqual(26, len(project["files"]))
        self.assertEqual(
            {
                "path", "size_bytes", "sha256", "unit_name", "module_path",
            },
            set(project["files"][0]),
        )
        self.assertEqual(0, project["semantic_nodes"][0]["start_row"])
        first = project["packages"][0]
        self.assertNotEqual(first["display_name"], first["symbol_namespace"])
        self.assertIn("graph_counts", first)
        workspace = project["views"]["workspace"]["visible_nodes"]
        self.assertEqual("workspace-root", workspace[0]["presentation_group"]["derivation"])
        self.assertTrue(all("presentation_group" in node for node in workspace))
        file_nodes = project["views"]["files"][0]["visible_nodes"]
        self.assertEqual("file-root-owning-package-id", file_nodes[0]["presentation_group"]["derivation"])
        self.assertEqual("in-file-semantic-kind", file_nodes[1]["presentation_group"]["derivation"])
        self.assertEqual("layout-only", file_nodes[1]["presentation_group"]["authority"])
        self.assertEqual(1, len(project["relations"]))
        self.assertEqual(1, len(project["pending_boundaries"]))
        self.assertEqual("not-requested", project["agent_result_bundles"]["state"])
        self.assertEqual([], project["agent_result_bundles"]["items"])

    def test_project_export_uses_governed_source_for_cold_warm_and_stale_targets(self) -> None:
        tool = self.root / "tools" / "ai-impact"
        (tool / "src").mkdir(parents=True)
        (tool / "Cargo.toml").write_text(
            "[package]\nname='ai-impact'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
        (tool / "Cargo.lock").write_text("# governed lock\n", encoding="utf-8")
        (tool / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        (self.root / "project" / "Cargo.toml").write_text(
            "[workspace]\nmembers=[]\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(project_export(2)),
                stderr="",
            )

        results = [build_project_intelligence(self.root, run=run)]
        results.append(build_project_intelligence(self.root, run=run))
        stale = tool / "target" / "debug" / "ai-impact.exe"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale incompatible executable")
        results.append(build_project_intelligence(self.root, run=run))
        (tool / "src" / "main.rs").write_text(
            'fn main() { println!("governed edit"); }\n',
            encoding="utf-8",
        )
        results.append(build_project_intelligence(self.root, run=run))

        self.assertEqual(
            ["fresh", "fresh", "fresh", "fresh"],
            [item["boundary"]["state"] for item in results],
        )
        self.assertEqual([2, 2, 2, 2], [len(item["packages"]) for item in results])
        self.assertEqual(
            ["unavailable", "unavailable", "unavailable", "unavailable"],
            [item["agent_result_bundles"]["state"] for item in results],
        )
        self.assertEqual(4, len(calls))
        targets = []
        for command in calls:
            self.assertEqual(["cargo", "run", "--quiet", "--locked"], command[:4])
            self.assertEqual(
                "tools/ai-impact/Cargo.toml",
                command[command.index("--manifest-path") + 1],
            )
            target = command[command.index("--target-dir") + 1]
            self.assertTrue(target.startswith(".ai/.local/ai-impact-build/"))
            targets.append(target)
            self.assertEqual(
                "project/Cargo.toml",
                command[command.index("--manifest") + 1],
            )
            self.assertNotIn(str(stale), command)
        self.assertEqual(1, len(set(targets[:3])))
        self.assertNotEqual(targets[0], targets[3])


    def test_project_export_captures_ready_exact_agent_proof_bundles(self) -> None:
        tool = self.write_governed_ai_impact_sources()
        stale = tool / "target" / "debug" / "ai-impact.exe"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"ambient stale binary must not be used")
        export = project_export_with_agent_proofs()
        exact_stdout = {
            VECTOR_MUL_QUERY: "sync vector proof\ndefinition vector-vector exact\n",
            SCALAR_MUL_QUERY: "sync scalar proof\ndefinition scalar-vector exact\n",
        }
        calls: list[list[str]] = []

        def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            action = command[command.index("--") + 1]
            database = Path(command[command.index("--database") + 1])
            if action == "export":
                database.write_bytes(b"fixture SQLite index")
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(export), stderr="",
                )
            self.assertEqual("explore", action)
            self.assertTrue(database.is_file(), "proof query must reuse the live export index")
            query = command[-1]
            return subprocess.CompletedProcess(
                command, 0, stdout=exact_stdout[query], stderr="",
            )

        project = build_project_intelligence(self.root, run=run)

        self.assertEqual("fresh", project["boundary"]["state"])
        bundle = project["agent_result_bundles"]
        self.assertEqual("ready", bundle["state"])
        self.assertTrue(bundle["exact"])
        self.assertTrue(bundle["nonfabricated"])
        self.assertFalse(bundle["fabricated"])
        self.assertEqual(["vectorExplore", "scalarExplore"], [
            item["name"] for item in bundle["items"]
        ])
        self.assertEqual([VECTOR_MUL_QUERY, SCALAR_MUL_QUERY], [
            item["query"] for item in bundle["items"]
        ])
        self.assertEqual([0, 0], [item["exit"] for item in bundle["items"]])
        self.assertEqual(
            [exact_stdout[VECTOR_MUL_QUERY], exact_stdout[SCALAR_MUL_QUERY]],
            [item["stdout"] for item in bundle["items"]],
        )
        for item in bundle["items"]:
            self.assertEqual("ready", item["state"])
            self.assertTrue(item["exact"])
            self.assertTrue(item["nonfabricated"])
            self.assertEqual("cargo", item["command"][0])
            self.assertEqual("explore", item["command"][item["command"].index("--") + 1])
            self.assertIn("--content-audit", item["command"])
            self.assertEqual(
                "<temporary-ai-impact-index>",
                item["command"][item["command"].index("--database") + 1],
            )
            self.assertNotIn(str(stale), item["command"])
            self.assertEqual("verbatim subprocess stdout and stderr", item["provenance"]["capture"])

        self.assertEqual(3, len(calls))
        live_databases = [
            command[command.index("--database") + 1] for command in calls
        ]
        self.assertEqual(1, len(set(live_databases)))
        self.assertFalse(Path(live_databases[0]).parent.exists())

        model = self.build()
        model["truth_systems"]["project_intelligence"] = project
        _cp_data, accepted_project = build_accepted_reader_payloads(model)
        self.assertEqual(bundle, accepted_project["agentResultBundles"])
        self.assertEqual("ready", accepted_project["agentContext"]["state"])
        for name, query in (
            ("vectorExplore", VECTOR_MUL_QUERY),
            ("scalarExplore", SCALAR_MUL_QUERY),
        ):
            ui_query = accepted_project["agentContext"]["queries"][name]
            self.assertEqual(query, ui_query["query"])
            self.assertEqual(exact_stdout[query], ui_query["stdout"])
            self.assertEqual(0, ui_query["exitCode"])
            self.assertEqual("ready", ui_query["state"])
            self.assertTrue(ui_query["exact"])
            self.assertTrue(ui_query["nonfabricated"])

    def test_agent_proof_failure_is_honest_without_invalidating_project(self) -> None:
        self.write_governed_ai_impact_sources()
        export = project_export_with_agent_proofs()
        calls: list[list[str]] = []

        def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            action = command[command.index("--") + 1]
            database = Path(command[command.index("--database") + 1])
            if action == "export":
                database.write_bytes(b"fixture SQLite index")
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(export), stderr="",
                )
            query = command[-1]
            if query == VECTOR_MUL_QUERY:
                return subprocess.CompletedProcess(
                    command,
                    7,
                    stdout="partial exact process stdout\n",
                    stderr="exact explore failure\n",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="definition scalar-vector exact\n",
                stderr="",
            )

        project = build_project_intelligence(self.root, run=run)

        self.assertEqual("fresh", project["boundary"]["state"])
        self.assertGreater(len(project["packages"]), 0)
        bundle = project["agent_result_bundles"]
        self.assertEqual("error", bundle["state"])
        self.assertFalse(bundle["exact"])
        self.assertTrue(bundle["nonfabricated"])
        self.assertFalse(bundle["fabricated"])
        vector, scalar = bundle["items"]
        self.assertEqual("error", vector["state"])
        self.assertEqual(7, vector["exit"])
        self.assertEqual("partial exact process stdout\n", vector["stdout"])
        self.assertEqual("exact explore failure\n", vector["stderr"])
        self.assertFalse(vector["exact"])
        self.assertTrue(vector["nonfabricated"])
        self.assertEqual("ready", scalar["state"])
        self.assertEqual(0, scalar["exit"])
        self.assertTrue(scalar["exact"])
        self.assertEqual(3, len(calls))

    def test_project_export_reports_missing_cargo_actionably(self) -> None:
        tool = self.root / "tools" / "ai-impact"
        (tool / "src").mkdir(parents=True)
        (tool / "Cargo.toml").write_text(
            "[package]\nname='ai-impact'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
        (tool / "Cargo.lock").write_text("# governed lock\n", encoding="utf-8")
        (tool / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        (self.root / "project" / "Cargo.toml").write_text(
            "[workspace]\nmembers=[]\n",
            encoding="utf-8",
        )

        def missing_cargo(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("cargo is not on PATH")

        project = build_project_intelligence(self.root, run=missing_cargo)
        self.assertEqual("unavailable", project["boundary"]["state"])
        self.assertIn("could not launch Cargo", project["boundary"]["errors"][0])
        self.assertIn("Cargo --locked", project["boundary"]["rebuild_guidance"])

    def test_task_delivery_rounds_context_and_closeout_remain_distinct(self) -> None:
        tasks = self.build()["truth_systems"]["tasks_features"]
        task = tasks["tasks"][0]
        self.assertEqual("done", task["lifecycle"])
        self.assertIn('id: "task_example"', task["raw_contract"])
        self.assertEqual(len(task["raw_contract"].encode("utf-8")), task["source_bytes"])
        self.assertEqual(64, len(task["source_sha256"]))
        int(task["source_sha256"], 16)
        self.assertEqual(
            ["example-executor-1", "example-qa-1", "example-executor-2", "example-qa-2"],
            [event["receipt_id"] for event in task["receipt_events"]],
        )
        self.assertTrue(task["delivery_stage"]["executed_artifact_present"])
        self.assertTrue(task["delivery_stage"]["reviewed_artifact_present"])
        self.assertTrue(task["delivery_stage"]["accepted_review"])
        self.assertTrue(task["delivery_stage"]["closed"])
        self.assertEqual(4, len(task["context_items"]))
        self.assertEqual(4, len(task["closeout"]["context_dispositions"]))
        resolution = task["evidence_artifact_resolutions"][
            ".ai/tasks/queue/task_example/evidence/reader.png"
        ]
        self.assertEqual("verified", resolution["state"])
        self.assertEqual(
            ".ai/tasks/done/task_example/evidence/reader.png",
            resolution["resolved_path"],
        )
        self.assertTrue(resolution["sha256_verified"])
        self.assertEqual("generated-result", task["evidence_set"]["items"][0]["kind"])
        self.assertEqual(["expected-design"], task["evidence_inventory"]["expected_references"])
        self.assertEqual(
            "missing-preview", task["evidence_inventory"]["unavailable_regenerable"][0]["evidence_id"]
        )
        self.assertIn("render_preview.py", task["evidence_inventory"]["unavailable_regenerable"][0]["producer_command"])
        self.assertIsNone(task["feature_link"]["feature_id"])
        self.assertEqual("legacy-display-label-only", task["feature_link"]["identity_state"])

    def test_moved_evidence_with_changed_bytes_is_not_reader_resolvable(self) -> None:
        self.write_tasks()
        evidence_path = (
            self.root / ".ai" / "tasks" / "done" / "task_example" / "evidence" / "reader.png"
        )
        evidence_path.write_bytes(b"tampered-after-receipt")
        task = build_tasks(self.root)["tasks"][0]
        resolution = task["evidence_artifact_resolutions"][
            ".ai/tasks/queue/task_example/evidence/reader.png"
        ]
        self.assertEqual("hash-mismatch", resolution["state"])
        self.assertIsNone(resolution["resolved_path"])
        self.assertFalse(resolution["sha256_verified"])

    def test_cross_task_expected_reference_resolves_after_lifecycle_move(self) -> None:
        self.write_tasks()
        referenced = self.root / ".ai" / "tasks" / "done" / "task_reference"
        (referenced / "visual-reference").mkdir(parents=True)
        artifact_bytes = b"accepted-reference"
        (referenced / "visual-reference" / "accepted.png").write_bytes(artifact_bytes)
        evidence_path = (
            self.root / ".ai" / "tasks" / "done" / "task_example" / "evidence.yaml"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["items"].append({
            "evidence_id": "cross-task-reference",
            "kind": "expected-reference",
            "role": "supporting",
            "storage": "committed",
            "availability": "available",
            "producer": {
                "command": "owner-provided reference",
                "environment": {"os": "test", "arch": "test", "device": "test"},
            },
            "claim": "Accepted comparison reference.",
            "acceptance_links": [],
            "accessibility_text": "Accepted comparison reference.",
            "artifact": {
                "path": ".ai/tasks/queue/task_reference/visual-reference/accepted.png",
                "media_type": "image/png",
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            },
        })
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        task = build_tasks(self.root)["tasks"][0]
        resolution = task["evidence_artifact_resolutions"][
            ".ai/tasks/queue/task_reference/visual-reference/accepted.png"
        ]
        self.assertEqual("verified", resolution["state"])
        self.assertEqual(
            ".ai/tasks/done/task_reference/visual-reference/accepted.png",
            resolution["resolved_path"],
        )
        self.assertTrue(resolution["sha256_verified"])

    def test_fresh_stale_partial_and_error_states_are_explicit(self) -> None:
        self.write_tasks()
        registry, bodies, edges = self.registry()
        common = {
            "registry_data": registry,
            "document_edges": edges,
            "document_bodies": bodies,
            "project_export": project_export(),
            "revision": {
                "commit": "abc123",
                "refreshed_at": "2026-07-29T00:00:00+07:00",
                "refresh_time_provenance": "fixture",
            },
        }
        fresh = build_knowledge_projection(self.root, **common)
        self.assertEqual("fresh", fresh["source"]["state"])
        for state in ("stale", "partial", "error"):
            model = build_knowledge_projection(
                self.root, **common, source_states={"project_intelligence": state}
            )
            self.assertEqual(state, model["source"]["state"])
            boundary = model["truth_systems"]["project_intelligence"]["boundary"]
            self.assertEqual(state, boundary["state"])
            self.assertEqual("explicit-source-probe", boundary["state_provenance"])
            self.assertIsNotNone(boundary["fingerprint"])
            self.assertIn("rebuild_guidance", boundary)
    def test_asset_fingerprint_covers_semantic_payload(self) -> None:
        first = self.build()
        second = json.loads(json.dumps(first))
        second["truth_systems"]["project_intelligence"]["packages"][0]["purpose"]["value"] = "Changed."
        second["source"]["fingerprint"] = ""
        canonical = json.dumps(second, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        changed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertNotEqual(first["source"]["fingerprint"], changed)


    def test_accepted_runtime_adapter_preserves_rich_truth_and_runtime_fields(self) -> None:
        model = self.build()
        task_source = model["truth_systems"]["tasks_features"]["tasks"][0]
        cp_data, project = build_accepted_reader_payloads(model)

        self.assertEqual(2, cp_data["counts"]["docs"])
        self.assertEqual(
            {"product", "control-plane"}, set(cp_data["corpora"])
        )
        self.assertEqual([], cp_data["graph"]["edges"])
        self.assertEqual(1, len(cp_data["graph"]["bridges"]))
        self.assertFalse(cp_data["graph"]["bridgesEnabledByDefault"])

        task = cp_data["tasks"][0]
        self.assertEqual("legacy-label:Display-only feature", task["featureKey"])
        self.assertEqual("reviewed", task["delivery"]["stage"])
        self.assertEqual(4, len(task["receipts"]))
        self.assertEqual(2, len(task["review"]["rounds"]))
        self.assertEqual(4, len(task["review"]["findings"]))
        self.assertEqual(task["review"], task["reviewAndFollowups"])
        self.assertEqual("example-qa-2", task["review"]["acceptedReceiptId"])
        self.assertEqual("available", task["rawBoundary"]["state"])
        self.assertEqual(task_source["source_sha256"], task["rawBoundary"]["sha256"])
        self.assertEqual(task_source["raw_contract"], task["raw"])
        self.assertEqual(task["raw"], task["sources"]["contract"]["raw"])
        self.assertEqual(
            ["result", "missing-preview"],
            [item["evidence_id"] for item in task["evidence"]["generatedResults"]],
        )
        self.assertEqual(
            ["expected-design"],
            [item["evidence_id"] for item in task["evidence"]["expectedReferences"]],
        )
        self.assertEqual(1, len(task["evidence"]["visuals"]))
        self.assertEqual(
            ".ai/tasks/queue/task_example/evidence/reader.png",
            task["evidence"]["visuals"][0]["path"],
        )
        self.assertEqual(
            ".ai/tasks/done/task_example/evidence/reader.png",
            task["evidence"]["visuals"][0]["resolvedPath"],
        )
        self.assertEqual(
            "../tasks/done/task_example/evidence/reader.png",
            task["evidence"]["visuals"][0]["readerHref"],
        )
        self.assertEqual("verified", task["evidence"]["visuals"][0]["readerResolution"]["state"])
        self.assertTrue(task["evidence"]["visuals"][0]["readerResolution"]["sha256_verified"])
        self.assertNotIn(
            "expected-design",
            {item["evidence_id"] for item in task["evidence"]["generatedResults"]},
        )

        self.assertEqual("current", project["status"]["state"])
        self.assertEqual(26, project["counts"]["clusters"])
        self.assertEqual(26, project["counts"]["files"])
        self.assertGreater(project["counts"]["nodes"], 0)
        self.assertGreater(project["counts"]["edges"], 0)
        first_file = project["files"][0]
        self.assertTrue(first_file["sizeAvailable"])
        self.assertTrue(first_file["sha256Available"])
        self.assertEqual(128, first_file["size"])
        self.assertFalse(first_file["sourceAvailable"])
        self.assertEqual("available", first_file["fileMetadataBoundary"]["state"])
        self.assertTrue(project["nodes"][0]["sourcePositionAvailable"])
        self.assertEqual(
            "Purpose for crate-00.",
            next(item for item in project["clusters"] if item["label"] == "crate-00")["purpose"]["value"],
        )

    def test_accepted_runtime_adapter_is_deterministic_and_rejects_false_freshness(self) -> None:
        model = self.build()
        first = render_accepted_reader_javascript(model)
        second = render_accepted_reader_javascript(json.loads(json.dumps(model)))
        self.assertEqual(first, second)
        first_dir = self.root / "accepted-a"
        second_dir = self.root / "accepted-b"
        first_paths = write_accepted_reader_assets(model, first_dir)
        second_paths = write_accepted_reader_assets(model, second_dir)
        self.assertEqual(first_paths[0].read_bytes(), second_paths[0].read_bytes())
        self.assertEqual(first_paths[1].read_bytes(), second_paths[1].read_bytes())
        self.assertIn("window.CP_DATA = ", first[0])
        self.assertIn("window.CONTROL_PLANE_PROJECT = ", first[1])

        broken = json.loads(json.dumps(model))
        project = broken["truth_systems"]["project_intelligence"]
        for key in ("packages", "files", "semantic_nodes", "semantic_hierarchy"):
            project[key] = []
        with self.assertRaisesRegex(
            AcceptedReaderAdapterError, "fresh Project Intelligence"
        ):
            build_accepted_reader_payloads(broken)
        project["boundary"]["state"] = "unavailable"
        project["relations"] = []
        project["pending_boundaries"] = []
        _cp_data, unavailable = build_accepted_reader_payloads(broken)
        self.assertEqual("unavailable", unavailable["status"]["state"])
        self.assertEqual(0, unavailable["counts"]["nodes"])


class AcceptedReaderRendererTests(unittest.TestCase):
    def test_accepted_authority_assets_keep_the_frozen_task_193_hashes(self) -> None:
        from scripts.ai_plane.knowledge_projection.renderer import (
            ACCEPTED_ASSET_SHA256,
            ACCEPTED_DIR,
            canonical_reader_asset_bytes,
        )

        for name, expected in ACCEPTED_ASSET_SHA256.items():
            source = ACCEPTED_DIR / name
            self.assertTrue(source.is_file(), name)
            self.assertEqual(
                expected,
                hashlib.sha256(
                    canonical_reader_asset_bytes(source.read_bytes())
                ).hexdigest(),
            )

    def test_accepted_authority_is_checkout_line_ending_independent(self) -> None:
        from scripts.ai_plane.knowledge_projection.renderer import (
            ACCEPTED_ASSET_SHA256,
            ACCEPTED_DIR,
            reader_asset_sha256,
        )

        for name, expected in ACCEPTED_ASSET_SHA256.items():
            canonical = (ACCEPTED_DIR / name).read_bytes().replace(b"\r\n", b"\n")
            crlf_checkout = canonical.replace(b"\n", b"\r\n")
            self.assertEqual(expected, reader_asset_sha256(canonical), name)
            self.assertEqual(expected, reader_asset_sha256(crlf_checkout), name)

    def test_writer_emits_canonical_production_asset_bytes(self) -> None:
        from scripts.ai_plane.knowledge_projection.renderer import (
            PRODUCTION_ASSETS,
            PRODUCTION_DIR,
            canonical_reader_asset_bytes,
            write_reader_presentation,
        )

        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp)
            index, css, javascript = write_reader_presentation(site)
            self.assertEqual(site / "index.html", index)
            self.assertEqual(site / "assets" / "app.css", css)
            self.assertEqual(site / "assets" / "app.js", javascript)
            for name in PRODUCTION_ASSETS:
                output = site / name if name == "index.html" else site / "assets" / name
                self.assertTrue(output.is_file(), name)
                self.assertEqual(
                    canonical_reader_asset_bytes((PRODUCTION_DIR / name).read_bytes()),
                    output.read_bytes(),
                )

    def test_entry_point_defaults_to_project_and_loads_the_local_runtime(self) -> None:
        from scripts.ai_plane.knowledge_projection.renderer import reader_shell_html

        html = reader_shell_html()
        self.assertIn('href="#/project"', html)
        for asset in (
            "data.js", "project-data.js", "markdown.js", "project.js",
            "task-rich.js", "app.js",
        ):
            self.assertIn(f'src="assets/{asset}"', html)
        self.assertIn('href="assets/production-delta.css"', html)
        self.assertNotIn("reader.css", html)
        self.assertNotIn("reader.js", html)
        self.assertNotRegex(html, r"https?://")

    def test_project_graph_models_use_governed_view_group_keys(self) -> None:
        """Display/route IDs stay local while layout groups come only from P.views."""
        repo_root = Path(__file__).resolve().parents[2]
        source_path = (
            repo_root
            / "scripts"
            / "ai_plane"
            / "knowledge_projection"
            / "reader_assets"
            / "production"
            / "project.js"
        )
        fixture = {
            # Two modules, not one: with a single module the package level correctly collapses
            # the pass-through and shows the file itself, which would exercise no module group.
            "nodes": [{
                "id": "symbol-id",
                "path": "crates/display/src/lib.rs",
                "kind": "function",
                "crate": "rust_raw",
                "module": "mod",
                "pending": 0,
                "incoming": 0,
                "outgoing": 0,
                "public": "rust_raw::mod::item",
                "qualifiedName": "item",
            }, {
                "id": "other-symbol-id",
                "path": "crates/display/src/other.rs",
                "kind": "function",
                "crate": "rust_raw",
                "module": "other",
                "pending": 0,
                "incoming": 0,
                "outgoing": 0,
                "public": "rust_raw::other::item",
                "qualifiedName": "item",
            }],
            "files": [{
                "path": "crates/display/src/lib.rs",
                "name": "lib.rs",
                "crate": "rust_raw",
                "module": "mod",
                "nodes": 1,
                "pending": 0,
                "incoming": 0,
                "outgoing": 0,
            }, {
                "path": "crates/display/src/other.rs",
                "name": "other.rs",
                "crate": "rust_raw",
                "module": "other",
                "nodes": 1,
                "pending": 0,
                "incoming": 0,
                "outgoing": 0,
            }],
            "edges": [],
            "clusters": [{
                "id": "display-route",
                "label": "Display crate",
                "unitName": "rust_raw",
                "packageId": "raw-package-id",
                "purpose": {"value": "Governed object-shaped crate purpose."},
                "nodes": 2,
            }],
            "counts": {"nodes": 2},
            "proofSelection": {"candidates": []},
            "views": {
                "workspace": {"visible_nodes": [
                    {
                        "identity": "workspace",
                        "label": "Workspace",
                        "presentation_group": {"key": "from-view-workspace"},
                        "source_context": {},
                    },
                    {
                        "identity": "crate:raw-package-id",
                        "label": "Display crate",
                        "presentation_group": {"key": "from-view-workspace-crate"},
                        "source_context": {
                            "package_id": "raw-package-id",
                            "symbol_namespace": "rust_raw",
                        },
                    },
                ]},
                "crates": [{
                    "package_id": "raw-package-id",
                    "visible_nodes": [
                        {
                            "identity": "crate:raw-package-id",
                            "label": "Display crate",
                            "presentation_group": {"key": "from-view-crate-root"},
                            "source_context": {"package_id": "raw-package-id"},
                        },
                        {
                            "identity": "module:rust_raw:mod",
                            "label": "mod",
                            "presentation_group": {"key": "from-view-crate-module"},
                            "source_context": {
                                "package_id": "raw-package-id",
                                "unit_name": "rust_raw",
                            },
                        },
                        {
                            "identity": "module:rust_raw:other",
                            "label": "other",
                            "presentation_group": {"key": "from-view-crate-module-other"},
                            "source_context": {
                                "package_id": "raw-package-id",
                                "unit_name": "rust_raw",
                            },
                        },
                    ],
                }],
                "modules": [{
                    "unit_name": "rust_raw",
                    "module_path": "mod",
                    "visible_nodes": [
                        {
                            "identity": "module:rust_raw:mod",
                            "label": "mod",
                            "presentation_group": {"key": "from-view-module-root"},
                            "source_context": {"unit_name": "rust_raw"},
                        },
                        {
                            "identity": "file:crates/display/src/lib.rs",
                            "label": "crates/display/src/lib.rs",
                            "presentation_group": {"key": "from-view-module-file"},
                            "source_context": {
                                "unit_name": "rust_raw",
                                "module_path": "mod",
                            },
                        },
                    ],
                }],
                "files": [{
                    "path": "crates/display/src/lib.rs",
                    "visible_nodes": [
                        {
                            "identity": "file:crates/display/src/lib.rs",
                            "label": "crates/display/src/lib.rs",
                            "presentation_group": {"key": "from-view-file-root"},
                            "source_context": {
                                "unit_name": "rust_raw",
                                "module_path": "mod",
                            },
                        },
                        {
                            "identity": "symbol-id",
                            "label": "item",
                            "presentation_group": {"key": "from-view-file-symbol"},
                            "source_context": {
                                "path": "crates/display/src/lib.rs",
                                "semantic_kind": "function",
                            },
                        },
                    ],
                }],
            },
        }
        node_script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const marker = 'window.CPProjectUI = { render: render, teardown: teardown, recolour: recolour };';
if (!source.includes(marker)) throw new Error('Project UI export marker changed');
const testExport = `window.CPProjectUI = {
  render: render, teardown: teardown, recolour: recolour,
  __setQuery: function (value) { routeState = { query: value || {} }; },
  __models: {
    workspace: workspaceModel, crate: crateModel, module: moduleModel, file: fileModel
  },
  __authoredCratePurpose: authoredCratePurpose
};`;
const context = { window: {
  CONTROL_PLANE_PROJECT: __FIXTURE__,
  CP_DATA: {}
}};
vm.createContext(context);
vm.runInContext(source.replace(marker, testExport), context);
const ui = context.window.CPProjectUI;
ui.__setQuery({});
const result = {
  workspace: ui.__models.workspace(),
  crate: ui.__models.crate('raw-package-id'),
  module: ui.__models.module('raw-package-id', 'mod'),
  file: ui.__models.file('crates/display/src/lib.rs'),
  purpose: ui.__authoredCratePurpose(__FIXTURE__.clusters[0], 'display-route')
};
process.stdout.write(JSON.stringify(result));
""".replace("__FIXTURE__", json.dumps(fixture))
        completed = subprocess.run(
            ["node", "-e", node_script, str(source_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        models = json.loads(completed.stdout)

        self.assertEqual(
            ["from-view-workspace", "from-view-workspace-crate"],
            [node["group"] for node in models["workspace"]["nodes"]],
        )
        self.assertEqual(
            ["from-view-crate-root", "from-view-crate-module",
             "from-view-crate-module-other"],
            [node["group"] for node in models["crate"]["nodes"]],
        )
        self.assertEqual(
            ["from-view-module-root", "from-view-module-file"],
            [node["group"] for node in models["module"]["nodes"]],
        )
        self.assertEqual(
            ["from-view-file-root", "from-view-file-symbol"],
            [node["group"] for node in models["file"]["nodes"]],
        )
        self.assertEqual(
            ["crate:display-route", "module:display-route|mod",
             "module:display-route|other"],
            [node["id"] for node in models["crate"]["nodes"]],
        )
        self.assertEqual("Governed object-shaped crate purpose.", models["purpose"])


class ProjectDrilldownBehaviourTests(unittest.TestCase):
    """What the reader actually RENDERS, level by level.

    The source-level guards in test_ai_reader_graph could not see that a module level selected its
    files by whole-path equality: every assertion stayed green while a real branch rendered
    one node and hid the 113 files beneath it. These run the shipped project.js and walk the
    descent.
    """

    def project_js(self) -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "scripts" / "ai_plane" / "knowledge_projection"
            / "reader_assets" / "production" / "project.js"
        )

    def walk(self, files: list[tuple[str, str]], crate: str = "app") -> dict:
        """Descend every level of one package and report what each one showed.

        `files` is (path, module_path) -- the only two fields the tiering reads.
        """
        fixture = {
            "nodes": [], "edges": [], "counts": {"nodes": len(files)},
            "proofSelection": {"candidates": []},
            "clusters": [{"id": crate, "label": crate, "unitName": crate,
                          "packageId": crate, "nodes": len(files)}],
            "files": [{"path": path, "name": path.split("/")[-1], "crate": crate,
                       "module": module, "nodes": 1, "pending": 0,
                       "incoming": 0, "outgoing": 0} for path, module in files],
            "views": {"workspace": None, "crates": [], "modules": [], "files": []},
        }
        for index, (path, module) in enumerate(files):
            fixture["nodes"].append({
                "id": f"symbol-{index}", "path": path, "kind": "function", "crate": crate,
                "module": module, "pending": 0, "incoming": 0, "outgoing": 0,
                "public": f"{module}::item{index}", "qualifiedName": f"item{index}",
            })
        script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const marker = 'window.CPProjectUI = { render: render, teardown: teardown, recolour: recolour };';
if (!source.includes(marker)) throw new Error('Project UI export marker changed');
const testExport = `window.CPProjectUI = {
  __setQuery: function (value) { routeState = { query: value || {} }; },
  __models: { crate: crateModel, module: moduleModel }
};`;
const context = { window: { CONTROL_PLANE_PROJECT: __FIXTURE__, CP_DATA: {} } };
vm.createContext(context);
vm.runInContext(source.replace(marker, testExport), context);
const ui = context.window.CPProjectUI;
ui.__setQuery({});
const CRATE = __CRATE__;
const seen = new Set(), reached = new Set(), levels = [];
const queue = [{ scope: 'crate' }];
while (queue.length && levels.length < 400) {
  const q = queue.shift();
  const key = q.scope === 'crate' ? '(package)' : q.module;
  if (seen.has(key)) continue;
  seen.add(key);
  const model = q.scope === 'crate' ? ui.__models.crate(CRATE)
                                    : ui.__models.module(CRATE, q.module);
  const children = model.nodes.slice(1);
  levels.push({ level: key, labels: children.map(n => n.label) });
  children.forEach(function (node) {
    if (node.data.type === 'file') { reached.add(node.data.file.path); return; }
    if (node.data.type === 'module') queue.push({ scope: 'module', module: node.data.module });
  });
}
process.stdout.write(JSON.stringify({ levels: levels, reached: [...reached].sort() }));
""".replace("__FIXTURE__", json.dumps(fixture)).replace("__CRATE__", json.dumps(crate))
        completed = subprocess.run(
            ["node", "-e", script, str(self.project_js())],
            check=True, capture_output=True, text=True,
        )
        result = json.loads(completed.stdout)
        result["byLevel"] = {entry["level"]: entry["labels"] for entry in result["levels"]}
        return result

    def test_a_package_whose_only_folder_is_src_shows_what_is_inside_src(self) -> None:
        """`src` is not a choice, so it is not a level. A stray config file beside it is a file,
        not a fork -- charging a level for it put a click in front of every FSD folder."""
        walked = self.walk([
            ("client/vite.config.ts", "(root)"),
            ("client/src/widgets/chart/index.ts", "src/widgets/chart"),
            ("client/src/pages/home/index.ts", "src/pages/home"),
        ], crate="client")
        self.assertEqual(["pages", "widgets", "vite.config.ts"],
                         walked["byLevel"]["(package)"])

    def test_a_package_with_src_and_test_keeps_both(self) -> None:
        """A fork is a real choice; collapsing past it would hide a sibling outright."""
        walked = self.walk([
            ("client/src/widgets/chart/index.ts", "src/widgets/chart"),
            ("client/test/widgets/chart.spec.ts", "test/widgets"),
        ], crate="client")
        self.assertEqual(["src", "test"], walked["byLevel"]["(package)"])

    def test_descending_a_module_reaches_its_whole_subtree(self) -> None:
        """The defect this class exists for: a level that renders one node and hides everything
        beneath it, while every count elsewhere still looks right."""
        files = [
            ("crate/src/shell/state/overlay/modal.rs", "shell::state::overlay"),
            ("crate/src/shell/state/window.rs", "shell::state"),
            ("crate/src/shell/chrome.rs", "shell"),
            ("crate/src/widgets/graph/palette.rs", "widgets::graph"),
            ("crate/src/lib.rs", "(root)"),
        ]
        walked = self.walk(files)
        self.assertEqual(["shell", "widgets", "lib.rs"], walked["byLevel"]["(package)"])
        self.assertEqual(["state", "chrome.rs"], walked["byLevel"]["shell"])
        self.assertEqual(["overlay", "window.rs"], walked["byLevel"]["shell::state"])
        self.assertEqual([path for path, _ in sorted(files)], walked["reached"],
                         "every file must be reachable by descending; none may be stranded")

    def test_a_module_file_is_never_shown_beside_its_own_grandchildren(self) -> None:
        """Letting a file ride along with a collapse is right for a package's build files and
        wrong below that: `shell/chrome.rs` beside `shell/state/overlay/modal.rs` claims the two
        are siblings. The package-root exception must not apply at every depth."""
        walked = self.walk([
            ("crate/src/shell/state/overlay/modal.rs", "shell::state::overlay"),
            ("crate/src/shell/state/window.rs", "shell::state"),
            ("crate/src/shell/chrome.rs", "shell"),
            # A second top-level folder, so `shell` is a level of its own rather than the package's
            # single wrapper -- which the package rule would legitimately merge away.
            ("crate/src/widgets/graph/palette.rs", "widgets::graph"),
        ])
        self.assertEqual(["shell", "widgets"], walked["byLevel"]["(package)"])
        self.assertEqual(["state", "chrome.rs"], walked["byLevel"]["shell"])
        self.assertNotIn("modal.rs", walked["byLevel"]["shell"])
        self.assertEqual(["overlay", "window.rs"], walked["byLevel"]["shell::state"])

    def test_no_level_leads_nowhere(self) -> None:
        """A level with no children is a dead end for whatever sits under it."""
        walked = self.walk([
            ("crate/a/b/c/deep.rs", "a::b::c"),
            ("crate/a/b/other.rs", "a::b"),
            ("crate/z.rs", "(root)"),
        ])
        empty = [entry["level"] for entry in walked["levels"] if not entry["labels"]]
        self.assertEqual([], empty, f"levels that render nothing: {empty}")


class RequiredProjectIntelligenceDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()

        root_patch = mock.patch.object(constants, "ROOT", self.root)
        ai_patch = mock.patch.object(constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)

    def test_required_project_build_preserves_previous_reader_on_failure(self) -> None:
        model = {
            "schema_version": 1,
            "source": {"state": "partial"},
            "truth_systems": {
                "project_intelligence": {
                    "boundary": {
                        "state": "unavailable",
                        "errors": ["unsupported command 'export'"],
                        "rebuild_guidance": "Rebuild from governed source.",
                    },
                    "packages": [],
                    "semantic_hierarchy": [],
                    "views": {"workspace": {"visible_nodes": []}},
                }
            },
        }
        site = self.ai / "_site"
        site.mkdir()
        previous = site / "reader-data.json"
        previous.write_text('{"previous": true}\n', encoding="utf-8")
        with mock.patch.object(ai_docs, "build_knowledge_projection", return_value=model):
            with self.assertRaises(ai_docs.RequiredProjectIntelligenceError) as raised:
                ai_docs.cmd_docs_build(
                    self.ai,
                    require_project_intelligence=True,
                )
        self.assertIn("state=unavailable, packages=0", str(raised.exception))
        self.assertIn("unsupported command 'export'", str(raised.exception))
        self.assertEqual(
            {"previous": True},
            json.loads(previous.read_text(encoding="utf-8")),
        )

    def test_docs_cli_requires_project_and_exits_without_traceback(self) -> None:
        # A repository that DECLARES Project Intelligence must still fail closed when the
        # governed export is unavailable -- and fail with guidance, never a traceback.
        error = ai_docs.RequiredProjectIntelligenceError("governed export unavailable")
        with mock.patch.object(ai_docs, "project_intelligence_declared", return_value=True):
            with mock.patch.object(ai_docs, "cmd_docs_build", side_effect=error) as build:
                with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    with self.assertRaises(SystemExit) as raised:
                        ai_docs.cmd_docs(argparse.Namespace(docs_command="build"))
        self.assertEqual(1, raised.exception.code)
        build.assert_called_once_with(require_project_intelligence=True)
        self.assertIn(
            "docs build failed: governed export unavailable",
            stderr.getvalue(),
        )

    def test_docs_cli_degrades_when_project_intelligence_is_not_declared(self) -> None:
        # A repository with no Project Intelligence source is not a broken repository: the
        # control-plane reader still builds. Requiring a source the project never had would
        # make `docs build` unusable for every adopter that is not a Rust workspace.
        with mock.patch.object(ai_docs, "project_intelligence_declared", return_value=False):
            with mock.patch.object(ai_docs, "cmd_docs_build") as build:
                ai_docs.cmd_docs(argparse.Namespace(docs_command="build"))
        build.assert_called_once_with(require_project_intelligence=False)


if __name__ == "__main__":
    unittest.main()
