"""Selective stack-pack installation.

Packs are optional: a Rust workspace must not be forced to carry React guidance. These tests pin
the two behaviours that make that true — content is distributed to the right control-plane home by
kind, and an uninstall reverses the install exactly, leaving no orphan file or hollow directory.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.ai_plane import skills as skills_module

REPO_ROOT = Path(__file__).resolve().parents[2]


def die(message: str) -> None:
    raise AssertionError(message)


class SkillsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ai = self.root / ".ai"
        for name in ("rules", "workflows", "skills"):
            (self.ai / name).mkdir(parents=True)
        self.catalog = self.root / skills_module.CATALOG_DIRNAME
        self.catalog.mkdir()

    def write(self, relative: str, text: str = "body\n") -> None:
        path = self.catalog / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_cmd(self, command: str, *names: str, force: bool = False, from_dir: str | None = None) -> str:
        args = argparse.Namespace(
            skills_command=command, names=list(names), force=force, from_dir=from_dir
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            skills_module.cmd_skills(args, root=self.root, ai=self.ai, die=die)
        return buffer.getvalue()

    def files(self) -> set[str]:
        return {
            p.relative_to(self.ai).as_posix()
            for p in self.ai.rglob("*")
            if p.is_file()
        }


class InstallPlanTests(SkillsFixture):
    def test_rules_and_workflows_go_to_their_canonical_homes(self) -> None:
        # A pack shaped as rules+workflows must NOT be dumped under .ai/skills/: those kinds are
        # indexed by id, and a second copy under skills/ produces duplicate-document-id errors.
        self.write("kmp/rules/protocol.md")
        self.write("kmp/workflows/compose.md")
        self.write("kmp/README.md")
        self.write("kmp/skills/session/SKILL.md")
        destinations = {
            dest.relative_to(self.ai).as_posix()
            for _src, dest in skills_module.install_plan(self.root, self.ai, "kmp")
        }
        self.assertEqual(
            {
                "rules/protocol.md",
                "workflows/compose.md",
                "skills/kmp/README.md",
                "skills/kmp/skills/session/SKILL.md",
            },
            destinations,
        )

    def test_each_source_file_has_exactly_one_destination(self) -> None:
        self.write("kmp/rules/protocol.md")
        self.write("kmp/workflows/compose.md")
        pairs = skills_module.install_plan(self.root, self.ai, "kmp")
        self.assertEqual(len(pairs), len({dest for _s, dest in pairs}))
        self.assertEqual(len(pairs), len({src for src, _d in pairs}))

    def test_a_top_level_rules_file_is_skill_content_not_a_rule(self) -> None:
        # Only `rules/<file>` is a rule. A file literally named `rules` at the pack root is not a
        # directory of rules and must not be promoted into .ai/rules/.
        self.write("solo/rules")
        destinations = [
            dest.relative_to(self.ai).as_posix()
            for _src, dest in skills_module.install_plan(self.root, self.ai, "solo")
        ]
        self.assertEqual(["skills/solo/rules"], destinations)


class InstallRoundTripTests(SkillsFixture):
    def build_pack(self) -> None:
        self.write("kmp/rules/protocol.md")
        self.write("kmp/workflows/compose.md")
        self.write("kmp/SKILL.md")
        self.write("kmp/skills/session/SKILL.md")

    def test_install_then_remove_restores_the_exact_starting_state(self) -> None:
        self.build_pack()
        before = self.files()
        self.run_cmd("add", "kmp")
        self.assertIn("rules/protocol.md", self.files())
        self.assertIn("workflows/compose.md", self.files())
        self.assertIn("skills/kmp/SKILL.md", self.files())

        self.run_cmd("remove", "kmp")
        self.assertEqual(before, self.files())

    def test_remove_leaves_no_empty_directory_behind(self) -> None:
        # A hollow `skills/kmp/` still reads as an installed pack to a human and to `skills list`.
        self.build_pack()
        self.run_cmd("add", "kmp")
        self.run_cmd("remove", "kmp")
        empty = [p for p in self.ai.rglob("*") if p.is_dir() and not any(p.iterdir())]
        self.assertEqual(
            [], [p.relative_to(self.ai).as_posix() for p in empty if p.name not in {"rules", "workflows", "skills"}]
        )
        self.assertFalse((self.ai / "skills" / "kmp").exists())

    def test_remove_does_not_touch_another_packs_files(self) -> None:
        self.build_pack()
        self.write("other/rules/other-rule.md")
        self.run_cmd("add", "kmp", "other")
        self.run_cmd("remove", "kmp")
        self.assertIn("rules/other-rule.md", self.files())

    def test_reinstall_is_refused_without_force(self) -> None:
        self.build_pack()
        self.run_cmd("add", "kmp")
        output = self.run_cmd("add", "kmp")
        self.assertIn("already installed", output)

    def test_unknown_pack_fails_closed_on_add_and_remove(self) -> None:
        self.build_pack()
        with self.assertRaises(AssertionError):
            self.run_cmd("add", "nope")
        with self.assertRaises(AssertionError):
            self.run_cmd("remove", "nope")


class CustomPackTests(SkillsFixture):
    """A project must be able to install its OWN pack without staging it into the shipped
    catalog, which an upgrade would overwrite."""

    def build_custom(self) -> Path:
        outside = self.root / "elsewhere"
        (outside / "house-style" / "rules").mkdir(parents=True)
        (outside / "house-style" / "rules" / "house.md").write_text("x\n", encoding="utf-8")
        (outside / "house-style" / "SKILL.md").write_text("y\n", encoding="utf-8")
        return outside

    def test_install_from_a_directory_of_packs(self) -> None:
        outside = self.build_custom()
        self.run_cmd("add", "house-style", from_dir=str(outside))
        self.assertIn("rules/house.md", self.files())
        self.assertIn("skills/house-style/SKILL.md", self.files())

    def test_install_from_the_pack_directory_itself(self) -> None:
        outside = self.build_custom()
        self.run_cmd("add", "house-style", from_dir=str(outside / "house-style"))
        self.assertIn("rules/house.md", self.files())

    def test_custom_pack_removal_with_from_reverses_everything(self) -> None:
        outside = self.build_custom()
        before = self.files()
        self.run_cmd("add", "house-style", from_dir=str(outside))
        self.run_cmd("remove", "house-style", from_dir=str(outside))
        self.assertEqual(before, self.files())

    def test_removal_without_from_says_what_it_cannot_trace(self) -> None:
        # Without the source, contributed rules cannot be located. Removing the skill folder and
        # implying a clean uninstall would silently leave an orphaned rule behind.
        outside = self.build_custom()
        self.run_cmd("add", "house-style", from_dir=str(outside))
        output = self.run_cmd("remove", "house-style")
        self.assertIn("skill content only", output)
        self.assertIn("rules/house.md", self.files())

    def test_unknown_custom_pack_fails_closed(self) -> None:
        with self.assertRaises(AssertionError):
            self.run_cmd("add", "nope", from_dir=str(self.root / "elsewhere"))


class CatalogListingTests(SkillsFixture):
    def test_listing_counts_only_catalog_packs(self) -> None:
        # `.ai/skills/` also holds core surface (pr-blueprint) that is not an optional pack; it
        # must not be counted as an installed catalog pack.
        self.write("kmp/SKILL.md")
        (self.ai / "skills" / "pr-blueprint").mkdir(parents=True)
        (self.ai / "skills" / "pr-blueprint" / "SKILL.md").write_text("x\n", encoding="utf-8")
        output = self.run_cmd("list")
        self.assertIn("0 of 1 installed", output)

    def test_listing_marks_installed_packs(self) -> None:
        self.write("kmp/SKILL.md")
        self.run_cmd("add", "kmp")
        output = self.run_cmd("list")
        self.assertIn("1 of 1 installed", output)
        self.assertIn("[installed] kmp", output)


class ShippedCatalogTests(unittest.TestCase):
    def test_every_shipped_pack_is_installable_and_reversible(self) -> None:
        # Guards the real catalog: a pack whose plan collides with another destination, or whose
        # content lands nowhere, would break `skills add` for a real adopter.
        catalog = REPO_ROOT / skills_module.CATALOG_DIRNAME
        if not catalog.is_dir():
            self.skipTest("no shipped pack catalog in this checkout")
        packs = skills_module.available(REPO_ROOT)
        self.assertTrue(packs, "the shipped catalog is empty")
        for pack in packs:
            with self.subTest(pack=pack):
                pairs = skills_module.install_plan(REPO_ROOT, REPO_ROOT / ".ai", pack)
                self.assertTrue(pairs, f"{pack} contributes no files")
                self.assertEqual(len(pairs), len({dest for _s, dest in pairs}),
                                 f"{pack} maps two sources onto one destination")


if __name__ == "__main__":
    unittest.main()
