from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.ai_plane import task_evidence as evidence
from scripts.ai_plane import task_evidence_legacy as legacy


def valid_receipt(role: str = "executor", receipt_id: str | None = None, task_id: str = "task_x") -> dict:
    receipt_id = receipt_id or f"{task_id}-{role}-1"
    return {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "task_id": task_id,
        "role": role,
        "sequence": {"attempt": 1} if role == "executor" else {"round": 1},
        "actor": {"name": "Agent", "family": "codex", "tool": "Codex", "model": "GPT", "reasoning": "high"},
        "revision": {"base_commit": "a" * 40, "head_commit": "b" * 40, "diff": "a..b", "diff_fingerprint": "c" * 64},
        "environment": {"os": "Windows", "arch": "AMD64", "device": "local"},
        "decision": {"status": "ready" if role == "executor" else "accept", "outcome": "all gates passed"},
        "gates": [{"gate_id": "unit", "command": "python -m unittest", "result": "pass", "evidence_refs": []}],
        "evidence_refs": [],
        "context_items": [],
        "notes": [],
    }


def valid_evidence() -> dict:
    return {
        "schema_version": 1,
        "evidence_set_id": "task-x-evidence-1",
        "task_id": "task_x",
        "items": [{
            "evidence_id": "preview-light",
            "kind": "generated-result",
            "role": "acceptance",
            "storage": "regenerable",
            "availability": "available",
            "producer": {
                "command": "editor_shell -- preview task-x --output-dir <scratch>",
                "environment": {"os": "Windows", "arch": "AMD64", "device": "local"},
            },
            "claim": "The light-theme surface renders without overlap.",
            "acceptance_links": ["AC-visual"],
            "accessibility_text": "Light-theme task surface with no clipped controls.",
            "coverage": ["light", "1440x900"],
        }],
    }


def write_json_yaml(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ReceiptSchemaTests(unittest.TestCase):
    def test_valid_executor_and_qa_receipts_pass(self) -> None:
        self.assertEqual([], evidence.validate_receipt(valid_receipt("executor")))
        self.assertEqual([], evidence.validate_receipt(valid_receipt("qa")))

    def test_role_specific_attempt_and_round_are_required(self) -> None:
        executor = valid_receipt("executor")
        executor["sequence"] = {"round": 1}
        qa = valid_receipt("qa")
        qa["sequence"] = {"attempt": 1}
        self.assertTrue(any("attempt" in error for error in evidence.validate_receipt(executor)))
        self.assertTrue(any("round" in error for error in evidence.validate_receipt(qa)))

    def test_unknown_or_missing_fields_fail_closed(self) -> None:
        receipt = valid_receipt()
        del receipt["environment"]
        receipt["invented"] = "silent"
        errors = evidence.validate_receipt(receipt)
        self.assertTrue(any("environment" in error and "missing" in error for error in errors))
        self.assertTrue(any("invented" in error and "unknown" in error for error in errors))

    def test_context_is_typed_and_nonblocking_is_not_discarded(self) -> None:
        receipt = valid_receipt("qa")
        receipt["context_items"] = [{
            "context_item_id": "q1-observation",
            "type": "observation",
            "blocking": False,
            "severity": "info",
            "summary": "Useful follow-up remains visible after acceptance.",
            "state": "open",
            "source_receipt_id": receipt["receipt_id"],
            "locations": ["scripts/example.py:10"],
            "evidence_refs": [],
        }]
        self.assertEqual([], evidence.validate_receipt(receipt))

    def test_context_item_requires_owning_source_receipt(self) -> None:
        for source in (None, 42, "other-receipt"):
            receipt = valid_receipt("qa")
            receipt["context_items"] = [{
                "context_item_id": "q1-observation",
                "type": "observation",
                "blocking": False,
                "severity": "info",
                "summary": "Ownership must not be inferred from file position.",
                "state": "open",
                "source_receipt_id": source,
            }]
            with self.subTest(source=source):
                self.assertTrue(any(
                    "source_receipt_id" in error for error in evidence.validate_receipt(receipt)
                ))

    def test_schema_yaml_is_strict_json_compatible_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.yaml"
            write_json_yaml(path, valid_receipt())
            self.assertEqual("task_x-executor-1", evidence.load_yaml(path)["receipt_id"])
            path.write_text("schema_version: 1\n", encoding="utf-8")
            with self.assertRaises(evidence.TaskEvidenceError):
                evidence.load_yaml(path)


class EvidenceSetTests(unittest.TestCase):
    def test_regenerable_preview_needs_no_committed_png(self) -> None:
        artifact = valid_evidence()
        self.assertNotIn("artifact", artifact["items"][0])
        self.assertEqual([], evidence.validate_evidence_set(artifact))

    def test_evidence_kinds_and_storage_are_closed_vocabularies(self) -> None:
        artifact = valid_evidence()
        artifact["items"][0]["kind"] = "screenshot-ish"
        artifact["items"][0]["storage"] = "somewhere"
        errors = evidence.validate_evidence_set(artifact)
        self.assertTrue(any("screenshot-ish" in error for error in errors))
        self.assertTrue(any("somewhere" in error for error in errors))

    def test_informative_evidence_requires_text_alternative_and_claim(self) -> None:
        artifact = valid_evidence()
        artifact["items"][0]["accessibility_text"] = ""
        artifact["items"][0]["claim"] = ""
        artifact["items"][0]["acceptance_links"] = []
        errors = evidence.validate_evidence_set(artifact)
        self.assertTrue(any("accessibility_text" in error for error in errors))
        self.assertTrue(any("claim or acceptance linkage" in error for error in errors))

    def test_committed_evidence_requires_artifact_identity(self) -> None:
        artifact = valid_evidence()
        item = artifact["items"][0]
        item["storage"] = "committed"
        errors = evidence.validate_evidence_set(artifact)
        self.assertTrue(any("artifact identity" in error for error in errors))
        item["artifact"] = {"path": "result.png", "media_type": "image/png", "width": 0}
        errors = evidence.validate_evidence_set(artifact)
        self.assertTrue(any("width" in error for error in errors))

    def test_expected_reference_and_generated_result_stay_distinct(self) -> None:
        artifact = valid_evidence()
        expected = copy.deepcopy(artifact["items"][0])
        expected["evidence_id"] = "expected-light"
        expected["kind"] = "expected-reference"
        expected["storage"] = "committed"
        expected["artifact"] = {"path": "reference.png", "media_type": "image/png", "sha256": "d" * 64, "width": 1440, "height": 900}
        artifact["items"].append(expected)
        self.assertEqual([], evidence.validate_evidence_set(artifact))
        self.assertNotEqual(artifact["items"][0]["kind"], artifact["items"][1]["kind"])


class CloseoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / ".ai" / "tasks" / "active" / "task_x"
        self.task.mkdir(parents=True)
        (self.task / "task.yaml").write_text('id: "task_x"\n', encoding="utf-8")
        self.executor = valid_receipt("executor")
        self.qa = valid_receipt("qa")
        self.qa["context_items"] = [{
            "context_item_id": "q1-risk",
            "type": "risk",
            "blocking": False,
            "severity": "low",
            "summary": "Accepted work retains one explicit residual risk.",
            "state": "open",
            "source_receipt_id": self.qa["receipt_id"],
        }]
        write_json_yaml(self.task / "receipt.executor.yaml", self.executor)
        write_json_yaml(self.task / "receipt.qa.yaml", self.qa)

    def closeout(self) -> dict:
        return {
            "schema_version": 1,
            "closeout_id": "task-x-closeout-1",
            "task_id": "task_x",
            "accepted_receipt_id": self.qa["receipt_id"],
            "receipt_events": [self.executor["receipt_id"], self.qa["receipt_id"]],
            "context_dispositions": [{
                "context_item_id": "q1-risk",
                "source_receipt_id": self.qa["receipt_id"],
                "disposition": "accepted-risk",
                "rationale": "The bounded limitation is explicit and does not invalidate acceptance.",
                "owner": "control-plane owner",
            }],
            "closed_by": "chain coordinator",
            "closed_at": "2026-07-29T00:00:00+07:00",
        }

    def test_acceptance_retains_and_dispositions_nonblocking_context(self) -> None:
        self.assertEqual([], evidence.validate_closeout(self.closeout(), self.task, self.root))

    def test_missing_context_disposition_fails(self) -> None:
        closeout = self.closeout()
        closeout["context_dispositions"] = []
        errors = evidence.validate_closeout(closeout, self.task, self.root)
        self.assertTrue(any("undispositioned" in error for error in errors))

    def test_accepted_risk_requires_rationale_and_owner(self) -> None:
        closeout = self.closeout()
        closeout["context_dispositions"][0]["rationale"] = ""
        del closeout["context_dispositions"][0]["owner"]
        errors = evidence.validate_closeout(closeout, self.task, self.root)
        self.assertTrue(any("rationale" in error for error in errors))
        self.assertTrue(any("owner" in error for error in errors))

    def test_all_receipt_events_are_ordered_and_preserved(self) -> None:
        revise = copy.deepcopy(self.qa)
        revise["receipt_id"] = "task-x-qa-1"
        revise["decision"] = {"status": "revise", "outcome": "bounded correction required"}
        revise["context_items"][0]["source_receipt_id"] = revise["receipt_id"]
        accepted = valid_receipt("qa", "task-x-qa-2")
        accepted["sequence"] = {"round": 2}
        write_json_yaml(self.task / "receipt.qa.round-1.yaml", revise)
        write_json_yaml(self.task / "receipt.qa.yaml", accepted)
        (self.task / "receipt.qa.yaml").write_text(json.dumps(accepted), encoding="utf-8")
        closeout = self.closeout()
        closeout["accepted_receipt_id"] = accepted["receipt_id"]
        closeout["receipt_events"] = [self.executor["receipt_id"], revise["receipt_id"], accepted["receipt_id"]]
        closeout["context_dispositions"][0]["source_receipt_id"] = revise["receipt_id"]
        self.assertEqual([], evidence.validate_closeout(closeout, self.task, self.root))

    def test_accepting_receipt_must_be_final_and_rounds_in_order(self) -> None:
        revise = copy.deepcopy(self.qa)
        revise["receipt_id"] = "task-x-qa-2"
        revise["sequence"] = {"round": 2}
        revise["decision"] = {"status": "revise", "outcome": "late revision"}
        revise["context_items"] = []
        write_json_yaml(self.task / "receipt.qa.round-2.yaml", revise)
        closeout = self.closeout()
        closeout["receipt_events"] = [self.executor["receipt_id"], revise["receipt_id"], self.qa["receipt_id"]]
        errors = evidence.validate_closeout(closeout, self.task, self.root)
        self.assertTrue(any("increasing causal order" in error for error in errors))

        closeout["receipt_events"] = [self.executor["receipt_id"], self.qa["receipt_id"], revise["receipt_id"]]
        errors = evidence.validate_closeout(closeout, self.task, self.root)
        self.assertTrue(any("final causal event" in error for error in errors))

    def test_transfer_requires_real_reciprocal_target(self) -> None:
        closeout = self.closeout()
        item = closeout["context_dispositions"][0]
        item.update({"disposition": "transferred", "target_task": "task_y", "target_context_item_id": "received-risk"})
        errors = evidence.validate_closeout(closeout, self.task, self.root)
        self.assertTrue(any("no real task" in error for error in errors))
        target = self.root / ".ai" / "tasks" / "queue" / "task_y"
        target.mkdir(parents=True)
        (target / "task.yaml").write_text('id: "task_y"\n', encoding="utf-8")
        target_receipt = valid_receipt("executor", "task-y-executor-1", "task_y")
        target_receipt["context_items"] = [{
            "context_item_id": "received-risk",
            "type": "follow-up",
            "blocking": False,
            "severity": "low",
            "summary": "Transferred risk is explicitly received.",
            "state": "open",
            "source_receipt_id": target_receipt["receipt_id"],
            "related_context_item_id": "task_x/q1-risk",
        }]
        write_json_yaml(target / "receipt.executor.yaml", target_receipt)
        self.assertEqual([], evidence.validate_closeout(closeout, self.task, self.root))


if __name__ == "__main__":
    unittest.main()