from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.ai_plane.knowledge_projection.reader_accepted import (
    AcceptedReaderAdapterError,
    _tasks_payload,
    write_task_media_aliases,
)
from scripts.ai_plane.knowledge_projection.task_presentation import (
    TaskPresentationError,
    build_task_presentation,
    contains_source_locator,
    presentation_contract_violations,
    technical_footprint,
)


def authored_contract() -> dict:
    return {
        "id": "task_example",
        "title": "Example",
        "presentation_schema_version": "1",
        "presentation_purpose": "Help product owners understand delivery intent.",
        "presentation_outcome": "The reader separates meaning from audit detail.",
        "presentation_scope": ["Task meaning", "Evidence interpretation"],
        "presentation_out_of_scope": [],
        "presentation_acceptance": ["Stakeholders can understand the task without source knowledge."],
        "target_files": [".ai/rules/**", "vendor/private.engine"],
        "forbidden_files": [".ai/tasks/done/**", "Do not redesign the reader."],
    }


def typed_source() -> dict:
    safe_context = {
        "context_item_id": "finding-1",
        "type": "finding",
        "blocking": False,
        "severity": "low",
        "summary": "The stakeholder label needs clearer language.",
        "state": "resolved",
        "source_receipt_id": "qa-1",
        "locations": [],
        "evidence_refs": [],
        "resolution": "The label now names the delivery state.",
        "owner": "Reader team",
    }
    unsafe_context = {
        "context_item_id": "finding-2",
        "type": "limitation",
        "blocking": False,
        "severity": "medium",
        "summary": "See scripts/ai_cli.py:35 for the missing behavior.",
        "state": "deferred",
        "source_receipt_id": "qa-1",
        "locations": ["scripts/ai_cli.py:35"],
        "evidence_refs": [],
        "resolution": "Run python scripts/ai_cli.py docs build.",
        "owner": "C:/Projects/example/owner",
    }
    receipt = {
        "receipt_id": "qa-1",
        "path": ".ai/tasks/done/task_example/receipt.qa.yaml",
        "role": "qa",
        "sequence": {"round": 1},
        "decision": {"status": "accept", "outcome": "Accepted for stakeholder review."},
        "legacy": False,
        "data": {
            "schema_version": 1,
            "receipt_id": "qa-1",
            "role": "qa",
            "sequence": {"round": 1},
            "actor": {
                "name": "QA reviewer",
                "family": "codex",
                "tool": "codex",
                "model": "fixture",
                "reasoning": "high",
            },
            "revision": {"base_commit": "abcdef1", "head_commit": "abcdef2", "diff": "abcdef1..abcdef2"},
            "decision": {"status": "accept", "outcome": "Accepted for stakeholder review."},
            "gates": [{
                "gate_id": "focused",
                "command": "python scripts/tests/test_task_reader_semantics.py",
                "result": "pass",
                "evidence_refs": [],
            }],
            "context_items": [safe_context, unsafe_context],
            "notes": [
                "A nonblocking follow-up remains visible.",
                "Inspect scripts/ai_cli.py for implementation detail.",
            ],
        },
    }
    return {
        "task_id": "task_example",
        "source_path": ".ai/tasks/done/task_example/task.yaml",
        "raw_contract": "input_contract: scripts/ai_cli.py plus README.md\n",
        "source_sha256": "a" * 64,
        "source_bytes": 49,
        "lifecycle": "done",
        "lifecycle_lineage": ["task_example"],
        "feature_link": {
            "feature_id": None,
            "display_label": "Reader",
            "identity_state": "legacy-display-label-only",
        },
        "document_links": [],
        "dependencies": [],
        "reverse_dependencies": [],
        "delivery_stage": {
            "planned": True,
            "executed_artifact_present": True,
            "reviewed_artifact_present": True,
            "accepted_review": True,
            "closed": True,
        },
        "receipt_events": [receipt, json.loads(json.dumps(receipt))],
        "evidence_set": None,
        "evidence_inventory": {},
        "evidence_artifact_resolutions": {},
        "context_items": [safe_context, unsafe_context],
        "closeout": {
            "accepted_receipt_id": "qa-1",
            "context_dispositions": [
                {
                    "context_item_id": "finding-1",
                    "source_receipt_id": "qa-1",
                    "disposition": "resolved",
                    "rationale": "Verified with a stakeholder review.",
                },
                {
                    "context_item_id": "finding-2",
                    "source_receipt_id": "qa-1",
                    "disposition": "deferred",
                    "rationale": "Tracked in project/docs/follow-up.md.",
                    "owner": "C:/Projects/example/owner",
                },
            ],
        },
        "legacy_boundary": {"incomplete": False, "label": None},
    }


class PresentationContractTests(unittest.TestCase):
    def test_authored_fields_pass_through_without_rewriting(self) -> None:
        contract = authored_contract()
        presentation = build_task_presentation(typed_source(), contract, "task_example")
        self.assertEqual("authored", presentation["state"])
        for source_key, output_key in (
            ("presentation_purpose", "purpose"),
            ("presentation_outcome", "outcome"),
            ("presentation_scope", "scope"),
            ("presentation_out_of_scope", "outOfScope"),
            ("presentation_acceptance", "acceptance"),
        ):
            self.assertEqual(contract[source_key], presentation[output_key])
        self.assertEqual([], presentation_contract_violations(contract))

    def test_partial_namespace_and_source_locators_fail_without_redaction(self) -> None:
        partial = {"presentation_purpose": "Edit scripts/ai_cli.py at line 35."}
        violations = presentation_contract_violations(partial)
        fields = {field for field, _value, _allowed in violations}
        self.assertIn("presentation_schema_version", fields)
        self.assertIn("presentation_outcome", fields)
        self.assertIn("presentation_scope", fields)
        self.assertIn("presentation_acceptance", fields)
        self.assertIn("presentation_purpose", fields)
        self.assertEqual("Edit scripts/ai_cli.py at line 35.", partial["presentation_purpose"])
        identity = authored_contract()
        identity["title"] = "Split preview.rs"
        identity["feature"] = "docs/028"
        identity_fields = {
            field for field, _value, _allowed
            in presentation_contract_violations(identity)
        }
        self.assertEqual({"title", "feature"}, identity_fields)

    def test_malformed_authored_namespace_fails_closed_before_reader_render(self) -> None:
        malformed: list[tuple[str, dict]] = []
        partial = {"presentation_purpose": "Stakeholders understand the change."}
        malformed.append(("partial", partial))
        unknown = authored_contract()
        unknown["presentation_summary"] = "An undeclared display alias."
        malformed.append(("unknown", unknown))
        numeric_schema = authored_contract()
        numeric_schema["presentation_schema_version"] = 1
        malformed.append(("schema-type", numeric_schema))
        malformed_scope = authored_contract()
        malformed_scope["presentation_scope"] = ("Not a YAML list",)
        malformed.append(("scope-type", malformed_scope))
        locator = authored_contract()
        locator["presentation_outcome"] = "Update vendor/foo.rs for delivery."
        malformed.append(("locator", locator))

        for label, contract in malformed:
            with self.subTest(label=label):
                self.assertTrue(presentation_contract_violations(contract))
                with self.assertRaises(TaskPresentationError) as direct:
                    build_task_presentation(typed_source(), contract, "task_example")
                self.assertIn("rejected fields", str(direct.exception))
                self.assertNotIn("vendor/foo.rs", str(direct.exception))

                source = typed_source()
                source["contract"] = contract
                with self.assertRaises(AcceptedReaderAdapterError) as adapter:
                    _tasks_payload({"tasks": [source]})
                self.assertIn("invalid authored task presentation namespace", str(adapter.exception))

    def test_schema_version_requires_exact_string_value(self) -> None:
        for version in (1, 1.0, True, None, "01"):
            with self.subTest(version=version):
                contract = authored_contract()
                contract["presentation_schema_version"] = version
                fields = {
                    field for field, _value, _allowed
                    in presentation_contract_violations(contract)
                }
                self.assertIn("presentation_schema_version", fields)

    def test_locator_detector_keeps_product_prose_urls_routes_and_nodejs_safe(self) -> None:
        safe = (
            "Developers and product owners can review Node.js behavior at "
            "https://example.test/reader/#/tasks?view=spec."
        )
        self.assertFalse(contains_source_locator(safe))
        self.assertFalse(contains_source_locator("Make task delivery understandable to stakeholders."))
        self.assertFalse(contains_source_locator(
            "Adopt the extracted control-plane package in this repository."
        ))
        self.assertFalse(contains_source_locator("Cargo verification remains understandable."))
        self.assertFalse(contains_source_locator("cargo verification remains understandable."))
        self.assertFalse(contains_source_locator("python integration stays optional."))
        self.assertFalse(contains_source_locator("The theme uses CSS color #12345678."))
        self.assertFalse(contains_source_locator("UI /UX behavior remains consistent."))
        self.assertFalse(contains_source_locator("UI / UX"))
        self.assertFalse(contains_source_locator("The /settings route remains available."))
        self.assertFalse(contains_source_locator("/settings"))
        self.assertFalse(contains_source_locator("https://example.test/api"))
        self.assertFalse(contains_source_locator(
            "Docker and Maven remain supported technology choices."
        ))
        self.assertFalse(contains_source_locator(
            "curl behavior remains understandable to operators."
        ))
        self.assertFalse(contains_source_locator("Use Docker for portable deployment."))
        self.assertFalse(contains_source_locator("ID 123e4567-e89b-12d3-a456-426614174000 stays visible."))
        for unsafe in (
            ".ai/tasks/done/task_x/task.yaml",
            "scripts/ai_cli.py:35",
            r"C:\Projects\example\README.md",
            "file:///C:/Projects/example/.ai/_site/index.html",
            "Run `python scripts/ai_cli.py docs build`.",
            "make verify",
            "Compare abcdef1..abcdef2.",
            "README.md",
            "vendor/foo.rs",
            "examples/demo.rs",
            ".github/workflows/ci.yml",
            "/usr/local/bin/tool",
            r".\ai.cmd sync",
            "./ai docs build",
            "cargo test",
            "python -m unittest",
            "Run cargo +nightly test.",
            "Run git -C repo status.",
            "Run python -c print(1).",
            "Execute powershell Get-Date.",
            "Run ai audit-framework.",
            "Run curl https://example.test/api.",
            "Execute docker compose up.",
            "Run mvn test.",
            "Inspect HEAD~1 before approval.",
            "Compare main^ with the release.",
            "Update vendor/private.engine for delivery.",
            "Inspect source/main.go for the behavior.",
            "Inspect lib/widget.cpp for the behavior.",
            "Open config/settings.ini before delivery.",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertTrue(contains_source_locator(unsafe))

    def test_round_two_locator_grammar_rejects_complete_presentation_namespace(self) -> None:
        unsafe_cases = (
            "Use `curl https://example.test/api`.",
            "curl https://example.test/api",
            "$ docker compose up",
            "Invoke docker compose up.",
            "Use mvn test.",
            "Inspect HEAD^2 before approval.",
            "Inspect HEAD~ before approval.",
            "Inspect HEAD@{1} before approval.",
            "Compare refs/heads/main before approval.",
            "Open vendor/Makefile before delivery.",
            "Inspect include/generated before delivery.",
            "Update vendor/assets/ before delivery.",
            ".github/CODEOWNERS",
            "Compare refs/tags/v2.0.0 before approval.",
            "Compare refs/remotes/origin/release before approval.",
            "Compare origin/release before approval.",
            "Inspect upstream/main~2^ before approval.",
            "$ cargo test",
            "% curl --fail https://example.test/api",
            "PS> mvn verify",
            "> git status",
            "Inspect HEAD^{tree} before approval.",
            "Compare main...origin/release.",
        )
        for unsafe in unsafe_cases:
            with self.subTest(unsafe=unsafe):
                self.assertTrue(contains_source_locator(unsafe))
                contract = authored_contract()
                contract["presentation_outcome"] = unsafe
                rejected_fields = {
                    field
                    for field, _value, _guidance
                    in presentation_contract_violations(contract)
                }
                self.assertIn("presentation_outcome", rejected_fields)

    def test_locator_grammar_rejects_adjacent_commands_revisions_and_paths(self) -> None:
        unsafe_cases = (
            "`cargo test`",
            "`git status`",
            "cargo test.",
            "$ cargo +nightly test",
            "$ git -C repo status",
            '$ python -c "print(1)"',
            "$ powershell Get-Date",
            "(venv) $ python -m unittest",
            "user@host:~$ cargo test",
            "# cargo test",
            "$ bespoke-tool frobnicate",
            "Use kubectl get pods.",
            "cmd /c cargo test",
            "poetry run pytest",
            "ruff check .",
            "gh pr checks",
            "Run cd",
            "Execute ls",
            "Use grep",
            "Invoke powershell Get-Date.",
            'Use `curl "https://example.test/api"`.',
            "./mvnw test",
            "./gradlew test",
            "CI=1 cargo test",
            "sudo cargo test",
            "bash -lc 'cargo test'",
            "wget https://example.test/archive",
            "docker-compose up",
            "> npm test.",
            "PS> git status.",
            "Run terraform plan.",
            "terraform plan",
            "pip install package",
            "npx vite build",
            "podman run image",
            "helm list",
            "just verify",
            "git cat-file HEAD",
            "git worktree list",
            "Verify with curl https://example.test/api.",
            "Validation uses mvn test.",
            "The required command is cargo test.",
            "Then cargo test.",
            "Run uv sync.",
            "`uv sync`",
            "uv sync",
            "Verify via cargo test.",
            "Before approval, git status.",
            "Tests require cargo test.",
            "echo ok | cargo test",
            "env -i cargo test",
            "docker version",
            "go version",
            "npm version",
            "Run docker version.",
            "docker version --format json",
            "Inspect feature/reader~2 before approval.",
            "Inspect release^2 before approval.",
            "Inspect topic@{upstream} before approval.",
            "Inspect @~2 before approval.",
            "Compare refs/pull/42/head before approval.",
            "Compare refs/notes/review before approval.",
            "Inspect v2.0.0^{commit} before approval.",
            "Inspect main:features/reader before approval.",
            "Compare main...feature/reader before approval.",
            "HEAD",
            "FETCH_HEAD",
            "ORIG_HEAD",
            "release..staging",
            "feature/a...feature/b",
            "release~2",
            "@{-1}",
            "HEAD:path/to/file",
            "HEAD^{}",
            "HEAD^{/fix bug}",
            "release...staging",
            ":/fix-reader",
            "^main",
            "fork/main",
            "refs/Heads/Main",
            "Commit DEADBEEF.",
            ".git/HEAD",
            ".git/config",
            ".cargo/config",
            ".agents/workflows",
            ".claude/settings",
            "artifacts/screenshots",
            "stacks/editor",
            "Inspect orbital/runtime before delivery.",
            "Review orbital/runtime before delivery.",
            "Compare artifacts/screenshots before delivery.",
            "Visit stacks/editor before delivery.",
            "Artifacts/Screenshots",
            ".Agents/Workflows",
            "Inspect Orbital/Runtime before delivery.",
            "Open dockerfile before delivery.",
            "modules/runtime/generated",
            "custom/CODEOWNERS",
            "Inspect foo/bar/ before delivery.",
            "include/generated",
            "Inspect fixtures/golden before delivery.",
            "Update assets/icons before delivery.",
            "Inspect resources/shaders before delivery.",
            "App/project lifecycle remains source-only.",
            "py -m unittest",
            "py -3 -m unittest",
            "wrangler deploy",
            "eslint src",
            "perl script.pl",
            "mix test",
            "sbt test",
            "Open notebook.ipynb before delivery.",
            "Inspect requirements.in before delivery.",
            "notebook.ipynb",
            "requirements.in",
            "custom/extensionless",
            "foo/bar/baz",
            "Run tox.",
            "Run jest.",
            "Run vite.",
            "Run mocha.",
            "Run pylint.",
            "Run black.",
            "Open public/index before delivery.",
            "Inspect build/output before delivery.",
            "Open target/debug before delivery.",
            "Inspect src/ before delivery.",
            "Inspect vendor/ before delivery.",
            "Open CODEOWNERS before delivery.",
            "Update .gitignore before delivery.",
            "Update .editorconfig before delivery.",
            "Open Justfile before delivery.",
            "Open BUILD.bazel before delivery.",
            "Open WORKSPACE before delivery.",
        )
        for unsafe in unsafe_cases:
            with self.subTest(unsafe=unsafe):
                self.assertTrue(contains_source_locator(unsafe))
                contract = authored_contract()
                contract["presentation_outcome"] = unsafe
                rejected_fields = {
                    field
                    for field, _value, _guidance
                    in presentation_contract_violations(contract)
                }
                self.assertIn("presentation_outcome", rejected_fields)

    def test_locator_grammar_preserves_adjacent_product_prose_and_uris(self) -> None:
        safe_cases = (
            "https://example.test/docs/guide.md",
            "ftp://example.test/docs/guide.md",
            "ssh://git@example.test/src/lib.rs",
            "https://example.test/refs/heads/main/vendor/assets/src/main.py",
            "Teams use Docker Compose for local development.",
            "Maven test reports explain quality.",
            "Run Python integrations without downtime.",
            "Users execute Java content in a sandbox.",
            "Operators run Docker workloads reliably.",
            "A command palette helps users navigate.",
            "Use Docker version 2 for portable deployment.",
            "Docker Compose remains supported.",
            "The origin/service relationship remains visible.",
            "The upstream/provider status is healthy.",
            "The surface was defaced during resize.",
            "The label was effaced by the overlay.",
            "Include/exclude filters are available.",
            "Source/configuration stays consistent.",
            "The include/exclude choice stays visible.",
            "The source/configuration experience stays consistent.",
            "Examples/demo journeys remain discoverable.",
            "React.js integration remains supported.",
            "ASP.NET remains supported.",
            "Electron.js applications remain available.",
            "Svelte.js support remains visible.",
            "Use example.com as the public domain.",
            "For example, e.g. users can retry.",
            "make build quality visible to managers.",
            "Use docker compose for local development.",
            "Invoke powershell automation for operators.",
            "Use mvn reporting to explain quality.",
            "The /settings route remains available.",
            "/api/v1/users",
            "UI / UX remains consistent.",
            "The theme uses CSS color #12345678.",
            "ID 123e4567-e89b-12d3-a456-426614174000 stays visible.",
            "The UI...UX transition is animated.",
            "loading...done remains visible.",
            "loading..done remains visible.",
            "The formula x^2 remains visible.",
            "Product ID abc1234 remains visible.",
            "Python applications remain available.",
            "Docker deployments remain reliable.",
            "Java applications remain portable.",
            "Make delivery easier for teams.",
            "Node workers remain healthy.",
            "# Git workflows",
            "> Docker Compose",
            "$5 per month",
            "% complete for this release",
            "Run background tasks reliably.",
            "Visit account/settings/profile.",
            "Open account/settings.",
            "The HEAD label identifies a table column.",
            "origin/service",
            "Version v2.0.0 remains supported.",
            "Account ID deadbeef remains visible.",
        )
        for safe in safe_cases:
            with self.subTest(safe=safe):
                self.assertFalse(contains_source_locator(safe))
                contract = authored_contract()
                contract["presentation_outcome"] = safe
                self.assertEqual([], presentation_contract_violations(contract))

    def test_optional_out_of_scope_can_be_empty(self) -> None:
        contract = authored_contract()
        contract["presentation_out_of_scope"] = []
        self.assertEqual([], presentation_contract_violations(contract))


class PresentationProjectionTests(unittest.TestCase):
    def test_legacy_contract_has_honest_unavailable_state_and_raw_is_lossless(self) -> None:
        source = typed_source()
        legacy_contract = {
            "id": "task_183_fixture",
            "title": "Split preview.rs",
            "feature": "docs/028",
            "input_contract": "The implementation lives in scripts/ai_cli.py.",
            "output_contract": "Replace README.md and project/crates/core/src/lib.rs.",
            "acceptance_tests": ["Run python scripts/ai_cli.py docs build."],
            "known_risks": "Revision abcdef1 may need inspection.",
            "status": "done",
        }
        source["task_id"] = "task_183_fixture"
        source["source_path"] = ".ai/tasks/done/task_183_fixture/task.yaml"
        source["raw_contract"] = json.dumps(legacy_contract)
        source["contract"] = legacy_contract
        tasks, _features, _areas = _tasks_payload({"tasks": [source]})
        task = tasks[0]
        presentation_text = json.dumps(task["presentation"], sort_keys=True)
        for sentinel in ("scripts/ai_cli.py", "README.md", "lib.rs", "abcdef1"):
            self.assertNotIn(sentinel, presentation_text)
            self.assertIn(sentinel, task["raw"])
        self.assertEqual("legacy-unavailable", task["presentation"]["state"])
        self.assertIsNone(task["presentation"]["purpose"])
        self.assertEqual([], task["presentation"]["acceptance"])
        self.assertEqual("Fixture", task["presentation"]["title"])
        self.assertEqual("source-only", task["presentation"]["titleState"])
        self.assertEqual("Feature label available in Source", task["presentation"]["featureLabel"])
        self.assertEqual("source-only", task["presentation"]["featureState"])
        self.assertEqual(task["presentation"]["title"], task["title"])
        self.assertEqual(task["presentation"]["featureLabel"], task["featureLabel"])
        self.assertRegex(task["featureKey"], r"^feature:[0-9a-f]{16}$")

        canonical = task["sourceProjection"]
        self.assertEqual(
            {"contract", "receipts", "evidenceSet", "closeout", "legacyBoundary"},
            set(canonical),
        )
        self.assertEqual(1, len(canonical["receipts"]))
        self.assertEqual(task["raw"], canonical["contract"]["raw"])
        for duplicate in (
            "parsed", "context_items", "evidence_inventory", "evidence_artifact_resolutions",
            "delivery_stage", "dependencies", "feature_link",
        ):
            self.assertNotIn(duplicate, canonical)

    def test_receipts_are_deduplicated_and_unsafe_typed_prose_is_source_only(self) -> None:
        source = typed_source()
        presentation = build_task_presentation(source, authored_contract(), "task_example")
        self.assertEqual(1, len(presentation["receipts"]))
        receipt = presentation["receipts"][0]
        self.assertEqual(
            {
                "role", "sequence", "actor", "status", "result", "legacy", "context",
                "notes", "sourceOnlyFields", "sourceOnlyNoteCount",
            },
            set(receipt),
        )
        self.assertEqual(["A nonblocking follow-up remains visible."], receipt["notes"])
        self.assertEqual(1, receipt["sourceOnlyNoteCount"])
        serialized = json.dumps(presentation, sort_keys=True)
        for forbidden in (
            "scripts/ai_cli.py",
            "project/docs/follow-up.md",
            "C:/Projects/example",
            "base_commit",
            "sourceEvent",
            "receipt.qa.yaml",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual("The stakeholder label needs clearer language.", receipt["context"][0]["summary"])
        self.assertEqual("resolved", receipt["context"][0]["disposition"])
        self.assertNotIn("summary", receipt["context"][1])
        self.assertNotIn("resolution", receipt["context"][1])
        self.assertNotIn("owner", receipt["context"][1])
        self.assertEqual("deferred", receipt["context"][1]["disposition"])

        self.assertEqual(
            ["summary", "resolution", "owner"],
            receipt["context"][1]["sourceOnlyFields"],
        )
        self.assertEqual(1, presentation["sourceOnly"]["noteCount"])
    def test_unsafe_receipt_result_has_visible_source_only_marker(self) -> None:
        source = typed_source()
        for event in source["receipt_events"]:
            event["data"]["decision"]["outcome"] = "Inspect scripts/ai_cli.py for the result."
        presentation = build_task_presentation(source, authored_contract(), "task_example")
        receipt = presentation["receipts"][0]
        self.assertNotIn("result", receipt)
        self.assertEqual(["result"], receipt["sourceOnlyFields"])
        self.assertEqual(1, presentation["sourceOnly"]["receiptFieldCount"])
        self.assertNotIn(
            "scripts/ai_cli.py",
            json.dumps(presentation, sort_keys=True),
        )

    def test_typed_evidence_claims_are_locator_safe(self) -> None:
        source = typed_source()
        source["evidence_set"] = {
            "schema_version": 1,
            "items": [
                {
                    "evidence_id": "safe",
                    "kind": "generated-result",
                    "role": "acceptance",
                    "availability": "available",
                    "claim": "The reader presents stakeholder intent.",
                },
                {
                    "evidence_id": "unsafe",
                    "kind": "comparison-diff",
                    "role": "diagnostic",
                    "availability": "unavailable",
                    "claim": "Inspect project/docs/result.md.",
                    "accessibility_text": "Screenshot from project/docs/result.md.",
                },
            ],
        }
        presentation = build_task_presentation(source, authored_contract(), "task_example")
        self.assertEqual(
            "The reader presents stakeholder intent.",
            presentation["evidence"]["items"][0]["claim"],
        )
        self.assertNotIn("claim", presentation["evidence"]["items"][1])
        self.assertEqual(
            {"total": 2, "available": 1, "unavailable": 1},
            presentation["evidence"]["counts"],
        )
        self.assertEqual(
            ["claim", "accessibilityText"],
            presentation["evidence"]["items"][1]["sourceOnlyFields"],
        )
        self.assertEqual(2, presentation["sourceOnly"]["evidenceFieldCount"])

    def test_scope_classifies_dot_ai_and_hides_unmapped_technical_entries(self) -> None:
        footprint = technical_footprint(authored_contract(), "task_example")
        touched = {item["key"]: item for item in footprint["touched"]}
        self.assertIn("control-plane-rules", touched)
        self.assertEqual(1, footprint["unmappedTargetCount"])
        self.assertIn("task-history", {item["key"] for item in footprint["offLimits"]})
        serialized = json.dumps(footprint)
        self.assertNotIn(".ai/", serialized)
        self.assertNotIn("vendor/private.engine", serialized)
        self.assertNotIn("Do not redesign", serialized)

    def test_verified_media_uses_opaque_alias_and_copies_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / ".ai" / "tasks" / "done" / "task_example" / "evidence" / "shot.png"
            source_path.parent.mkdir(parents=True)
            media_bytes = b"\x89PNG\r\nfixture"
            source_path.write_bytes(media_bytes)
            digest = hashlib.sha256(media_bytes).hexdigest()
            source = typed_source()
            source["evidence_set"] = {
                "schema_version": 1,
                "items": [{
                    "evidence_id": "visual-proof",
                    "kind": "generated-result",
                    "role": "acceptance",
                    "availability": "available",
                    "storage": "committed",
                    "claim": "The task reader displays the accepted state.",
                    "accessibility_text": "Accepted task reader state.",
                    "artifact": {
                        "path": ".ai/tasks/queue/task_example/evidence/shot.png",
                        "media_type": "image/png",
                        "sha256": digest,
                        "width": 1440,
                        "height": 900,
                    },
                }],
            }
            source["evidence_artifact_resolutions"] = {
                ".ai/tasks/queue/task_example/evidence/shot.png": {
                    "state": "verified",
                    "resolved_path": ".ai/tasks/done/task_example/evidence/shot.png",
                    "actual_sha256": digest,
                },
            }
            model = {"truth_systems": {"tasks_features": {"tasks": [source]}}}
            presentation = build_task_presentation(source, authored_contract(), "task_example")
            media = presentation["media"][0]
            self.assertEqual(
                {"src", "kind", "type", "dimensions", "alt"},
                set(media),
            )
            self.assertEqual("generated-result", media["kind"])
            self.assertRegex(media["src"], r"^assets/task-media/[0-9a-f]{24}\.png$")
            self.assertNotIn("task_example", media["src"])
            assets = root / ".ai" / "_site" / "assets"
            written = write_task_media_aliases(model, assets, repository_root=root)
            self.assertEqual(1, len(written))
            self.assertEqual(media_bytes, written[0].read_bytes())
            self.assertEqual(root / ".ai" / "_site" / media["src"], written[0])

    def test_media_requires_available_committed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = (
                root
                / ".ai"
                / "tasks"
                / "done"
                / "task_example"
                / "evidence"
                / "reference.png"
            )
            source_path.parent.mkdir(parents=True)
            media_bytes = b"\x89PNG\r\neligible-reference"
            source_path.write_bytes(media_bytes)
            digest = hashlib.sha256(media_bytes).hexdigest()
            source = typed_source()
            cases = (
                ("eligible-reference", "expected-reference", "available", "committed"),
                ("unavailable-result", "generated-result", "unavailable", "committed"),
                ("regenerable-result", "generated-result", "available", "regenerable"),
                ("external-result", "generated-result", "available", "external"),
            )
            items = []
            resolutions = {}
            for evidence_id, kind, availability, storage in cases:
                recorded_path = (
                    f".ai/tasks/queue/task_example/evidence/{evidence_id}.png"
                )
                items.append({
                    "evidence_id": evidence_id,
                    "kind": kind,
                    "role": "acceptance",
                    "availability": availability,
                    "storage": storage,
                    "claim": "The media eligibility state remains explicit.",
                    "accessibility_text": "Reference task reader state.",
                    "artifact": {
                        "path": recorded_path,
                        "media_type": "image/png",
                        "sha256": digest,
                        "width": 1440,
                        "height": 900,
                    },
                })
                resolutions[recorded_path] = {
                    "state": "verified",
                    "resolved_path": (
                        ".ai/tasks/done/task_example/evidence/reference.png"
                    ),
                    "actual_sha256": digest,
                }
            source["evidence_set"] = {"schema_version": 1, "items": items}
            source["evidence_artifact_resolutions"] = resolutions
            presentation = build_task_presentation(
                source,
                authored_contract(),
                "task_example",
            )
            self.assertEqual(1, len(presentation["media"]))
            self.assertEqual(
                "expected-reference",
                presentation["media"][0]["kind"],
            )
            self.assertEqual(
                {"total": 4, "available": 3, "unavailable": 1},
                presentation["evidence"]["counts"],
            )
            model = {"truth_systems": {"tasks_features": {"tasks": [source]}}}
            assets = root / ".ai" / "_site" / "assets"
            written = write_task_media_aliases(model, assets, repository_root=root)
            self.assertEqual(1, len(written))
            self.assertEqual(media_bytes, written[0].read_bytes())


class ReaderNavigationTests(unittest.TestCase):
    def test_home_governance_cards_use_complete_control_plane_routes(self) -> None:
        app = (
            Path(__file__).parents[1]
            / "ai_plane"
            / "knowledge_projection"
            / "reader_assets"
            / "production"
            / "app.js"
        ).read_text(encoding="utf-8")
        routes = (
            ("The rules that will gate you", "Rules & Governance", "rule-task-contracts"),
            ("Plan, dispatch, execute, review", "Roles & Workflows", "workflow-planning"),
        )
        for title, group, selection in routes:
            with self.subTest(title=title):
                start = app.index(f"t: '{title}'")
                end = app.index("}) },", start) + len("}) },")
                card = app[start:end]
                self.assertIn("corpus: 'control-plane'", card)
                self.assertIn(f"group: '{group}'", card)
                self.assertIn(f"sel: '{selection}'", card)
                self.assertLess(
                    card.index("corpus: 'control-plane'"),
                    card.index(f"sel: '{selection}'"),
                )



if __name__ == "__main__":
    unittest.main()
