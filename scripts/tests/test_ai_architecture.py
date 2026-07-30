from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
AI_PLANE_DIR = SCRIPTS_DIR / "ai_plane"
TESTS_DIR = SCRIPTS_DIR / "tests"


def check_facade_line_ceiling(facade_path: Path, max_lines: int = 400) -> None:
    lines = facade_path.read_text(encoding="utf-8").splitlines()
    if len(lines) > max_lines:
        raise AssertionError(
            f"{facade_path.name} facade has {len(lines)} lines, exceeding ceiling of {max_lines} lines"
        )


def check_module_line_ceilings(directory: Path, pattern: str, max_lines: int) -> None:
    for py_file in sorted(directory.glob(pattern)):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            raise AssertionError(
                f"{directory.name}/{py_file.name} has {len(lines)} lines, exceeding ceiling of {max_lines} lines"
            )


def check_no_facade_imports(directory: Path) -> None:
    for py_file in sorted(directory.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("ai_cli", "scripts.ai_cli"):
                        raise AssertionError(
                            f"{directory.name}/{py_file.name} imports '{alias.name}', breaking control-plane acyclicity"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in ("ai_cli", "scripts.ai_cli"):
                    raise AssertionError(
                        f"{directory.name}/{py_file.name} imports from '{mod}', breaking control-plane acyclicity"
                    )


def find_ai_plane_import_cycles(directory: Path) -> list[list[str]]:
    """Build directed graph of imports between modules in directory and return elementary cycles."""
    module_names = {p.stem for p in directory.glob("*.py") if p.is_file()}
    graph: dict[str, set[str]] = {m: set() for m in module_names}

    for py_file in directory.glob("*.py"):
        if not py_file.is_file():
            continue
        src_module = py_file.stem
        content = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith("scripts.ai_plane."):
                        target = name.split(".")[-1]
                    elif name.startswith("ai_plane."):
                        target = name.split(".")[-1]
                    elif name in module_names:
                        target = name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("scripts.ai_plane") or mod.startswith("ai_plane") or mod in (".", ".."):
                    parts = mod.split(".")
                    if len(parts) >= 2 and parts[-1] in module_names:
                        target = parts[-1]
                    elif parts[0] in module_names:
                        target = parts[0]
                elif mod in module_names:
                    target = mod

            if target and target in module_names and target != src_module:
                graph[src_module].add(target)

    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []
    in_stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        stack.append(node)
        in_stack.add(node)

        for neighbor in sorted(graph.get(node, [])):
            if neighbor in in_stack:
                cycle_start = stack.index(neighbor)
                cycle = stack[cycle_start:] + [neighbor]
                cycles.append(cycle)
            elif neighbor not in visited:
                dfs(neighbor)

        stack.pop()
        in_stack.remove(node)

    for mod in sorted(module_names):
        if mod not in visited:
            dfs(mod)

    return cycles


def check_import_cycles(directory: Path) -> None:
    cycles = find_ai_plane_import_cycles(directory)
    if cycles:
        cycle_strs = [" -> ".join(c) for c in cycles]
        raise AssertionError(f"Import cycle(s) detected in {directory.name}: {'; '.join(cycle_strs)}")


class AiCliArchitectureTests(unittest.TestCase):
    """Enforce architecture ratchets, line-count ceilings, and acyclic imports."""

    def test_facade_line_count_ceiling(self) -> None:
        check_facade_line_ceiling(SCRIPTS_DIR / "ai_cli.py", 400)

    def test_plane_modules_line_count_ceilings(self) -> None:
        check_module_line_ceilings(AI_PLANE_DIR, "*.py", 600)

    def test_test_modules_line_count_ceilings(self) -> None:
        check_module_line_ceilings(TESTS_DIR, "test_ai_*.py", 700)

    def test_import_graph_acyclicity_no_plane_module_imports_facade(self) -> None:
        check_no_facade_imports(AI_PLANE_DIR)

    def test_import_graph_acyclicity_no_plane_internal_cycles(self) -> None:
        check_import_cycles(AI_PLANE_DIR)

    def test_routing_revision_has_real_headroom_and_one_profile_module(self) -> None:
        self.assertLessEqual(
            len((SCRIPTS_DIR / "ai_cli.py").read_text(encoding="utf-8").splitlines()),
            390,
        )
        config_source = (AI_PLANE_DIR / "config.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(config_source.splitlines()), 500)
        self.assertNotIn("profile_preferences", config_source)
        self.assertTrue((AI_PLANE_DIR / "routing_schema.py").is_file())
        self.assertTrue((AI_PLANE_DIR / "routing_profile.py").is_file())
        self.assertFalse((AI_PLANE_DIR / "tool_profile.py").exists())

    def test_routing_sources_have_no_mojibake_markers(self) -> None:
        markers = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\ufffd")
        for name in ("config.py", "route.py", "routing_profile.py", "routing_schema.py"):
            source = (AI_PLANE_DIR / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                for marker in markers:
                    self.assertNotIn(marker, source)

    def test_negative_fixture_oversized_facade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            facade = Path(tmp_dir) / "ai_cli.py"
            facade.write_text("\n".join(["# line"] * 401) + "\n", encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                check_facade_line_ceiling(facade, 400)
            self.assertIn("exceeding ceiling of 400 lines", str(ctx.exception))

    def test_negative_fixture_oversized_extracted_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plane_dir = Path(tmp_dir) / "ai_plane"
            plane_dir.mkdir()
            module = plane_dir / "big_module.py"
            module.write_text("\n".join(["# line"] * 601) + "\n", encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                check_module_line_ceilings(plane_dir, "*.py", 600)
            self.assertIn("exceeding ceiling of 600 lines", str(ctx.exception))

    def test_negative_fixture_oversized_test_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tests_dir = Path(tmp_dir) / "tests"
            tests_dir.mkdir()
            test_file = tests_dir / "test_ai_big.py"
            test_file.write_text("\n".join(["# line"] * 701) + "\n", encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                check_module_line_ceilings(tests_dir, "test_ai_*.py", 700)
            self.assertIn("exceeding ceiling of 700 lines", str(ctx.exception))

    def test_negative_fixture_facade_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plane_dir = Path(tmp_dir) / "ai_plane"
            plane_dir.mkdir()
            bad_module = plane_dir / "bad.py"
            bad_module.write_text("import scripts.ai_cli\n", encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                check_no_facade_imports(plane_dir)
            self.assertIn("imports 'scripts.ai_cli'", str(ctx.exception))

    def test_negative_fixture_import_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plane_dir = Path(tmp_dir) / "ai_plane"
            plane_dir.mkdir()
            mod_a = plane_dir / "mod_a.py"
            mod_b = plane_dir / "mod_b.py"
            mod_a.write_text("import scripts.ai_plane.mod_b\n", encoding="utf-8")
            mod_b.write_text("import scripts.ai_plane.mod_a\n", encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                check_import_cycles(plane_dir)
            self.assertIn("Import cycle(s) detected", str(ctx.exception))
            self.assertIn("mod_a -> mod_b -> mod_a", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
