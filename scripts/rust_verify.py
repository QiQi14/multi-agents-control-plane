#!/usr/bin/env python3
"""Selective Rust verification pipeline for the Agent Control Plane.

Stdlib only (`.ai/project/infrastructure.md`). All subprocess interaction is an
argv array behind a seam so fixture tests inject git/cargo/lock/process-scan
behavior without a real repository. Raw Cargo output is diagnostic only; the
versioned JSON evidence record in the task folder is the canonical receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
for _p in (str(_REPO_ROOT), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ai_cli  # noqa: E402  (sibling control-plane CLI; pure helpers only)

# The stack-agnostic verification substrate (task_181). rust_verify is the reference
# "rust" gate plugin: it composes these generic primitives with Cargo-specific selection,
# escalation, and command construction. verify_core never imports this module. The canonical
# package path `scripts.verify_core` is used everywhere (gate, CLI, tests) so there is exactly
# one substrate module object and one VerifyError class regardless of how the gate is loaded
# (Q181-5).
from scripts.verify_core import (  # noqa: E402  -- generic substrate (seams, lock, evidence, identity)
    ConfigError,
    GitError,
    LockError,
    ProcessActiveError,
    VerificationLock,
    VerifyError,
    append_invocation,
    canonical_root_key,
    die,
    evidence_path,
    getcwd,
    git_output,
    lock_file_name,
    lock_operations,
    now_iso,
    now_iso_precise,
    os_name,
    platform_block,
    process_listing,
    repo_key_sha256,
    run_command,
    scrub_invocation,
    temp_dir,
    to_posix_rel,
)
from scripts.verify_contract import (  # noqa: E402  -- control-plane contract/change-set/degradation
    GENERATED_MANIFEST_REL,
    base_manifest,
    collect_change_set,
    contract_check,
    current_manifest,
    declared_scope,
    exact_file_entry,
    extract_glob_tokens,
    find_task,
    manifest_exemptions,
    matches_any,
    matches_glob,
    parse_name_status_z,
    path_glob_regex,
)


ROOT = Path(__file__).resolve().parents[1]
AI = ROOT / ".ai"
CONFIG_REL_PATH = ".ai/project/rust-verification.json"
# Fallback only. The authoritative value is config["workspace_manifest"]; an adopting
# repository may put its workspace manifest anywhere.
MANIFEST_REL = "Cargo.toml"


class MetadataError(VerifyError):
    pass


def has_canonical_evidence(task_dir: Path, config: dict[str, Any]) -> bool:
    """Rust-gate receipt check: only the versioned JSON record counts, never raw Cargo output.
    A passing invocation either ran gate commands or was a legitimate no-Cargo run (the
    `schedule_cargo` selection marker is a Cargo-gate concept, so this lives with the gate)."""
    path = evidence_path(task_dir, config)
    if not path.exists():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if record.get("kind") != config["evidence"]["kind"]:
        return False
    invocations = record.get("invocations")
    if not isinstance(invocations, list) or not invocations:
        return False
    return any(
        isinstance(inv, dict)
        and inv.get("result", {}).get("exit_status") == 0
        and (
            inv.get("commands")
            or not (inv.get("selection") or {}).get("schedule_cargo", True)
        )
        for inv in invocations
    )


def load_config(ai: Path) -> dict[str, Any]:
    path = ai / "project" / "rust-verification.json"
    if not path.exists():
        raise ConfigError(f"Rust verification config missing: {CONFIG_REL_PATH}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Rust verification config unreadable: {error}") from error
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ConfigError("Rust verification config has an unsupported schema_version.")

    def require(key: str, expected: type) -> Any:
        value = config.get(key)
        if not isinstance(value, expected):
            raise ConfigError(f"Rust verification config key '{key}' missing or wrong type.")
        return value

    scopes = require("verification_scopes", list)
    for canonical in ("control-plane", "affected", "affected-plus-neighbors", "workspace"):
        if canonical not in scopes:
            raise ConfigError(f"Rust verification config missing canonical scope '{canonical}'.")
    if require("default_scope", str) not in scopes:
        raise ConfigError("Rust verification config default_scope is not a canonical scope.")
    for section, keys in (
        ("path_classes", ("control_plane_prefixes", "control_plane_files", "docs_prefixes",
                          "docs_suffixes", "rust_relevant_prefixes", "rust_ignored_prefixes")),
        ("commands", ("clippy_deny_flags", "clippy_extra_args", "test_extra_args")),
        ("lock", ("name_prefix",)),
        ("scratch", ("dir_prefix", "marker_file", "marker_schema_version")),
        ("evidence", ("file_name", "schema_version", "kind")),
        ("cache_log", ("path", "schema_version", "kind")),
    ):
        section_value = require(section, dict)
        for key in keys:
            if key not in section_value:
                raise ConfigError(f"Rust verification config '{section}.{key}' missing.")
    escalation = require("escalation", dict)
    threshold = escalation.get("half_workspace_threshold")
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ConfigError("Rust verification config escalation.half_workspace_threshold invalid.")
    for key in ("root_manifest_path", "lockfile_path", "toolchain_paths",
                "cargo_config_prefixes", "crate_manifest_glob", "reasons"):
        if key not in escalation:
            raise ConfigError(f"Rust verification config 'escalation.{key}' missing.")
    if not isinstance(config.get("workspace_manifest"), str):
        raise ConfigError("Rust verification config workspace_manifest missing.")
    return config


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


def classify_path(path: str, config: dict[str, Any]) -> str:
    classes = config["path_classes"]
    for prefix in classes["rust_ignored_prefixes"]:
        if path.startswith(prefix):
            return "ignored"
    for prefix in classes["rust_relevant_prefixes"]:
        if path.startswith(prefix):
            return "rust-relevant"
    if path in classes["control_plane_files"]:
        return "control-plane"
    for prefix in classes["control_plane_prefixes"]:
        if path.startswith(prefix):
            return "control-plane"
    for prefix in classes["docs_prefixes"]:
        if path.startswith(prefix):
            return "docs"
    for suffix in classes["docs_suffixes"]:
        if path.endswith(suffix):
            return "docs"
    return "other"


# ---------------------------------------------------------------------------
# Cargo metadata and package selection
# ---------------------------------------------------------------------------


def load_workspace_metadata(
    root: Path,
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runner = runner or run_command
    argv = ["cargo", "metadata", "--no-deps", "--format-version", "1", "--manifest-path", str(root / MANIFEST_REL)]
    result = runner(argv, root)
    if result["exit_status"] != 0:
        raise MetadataError(
            f"cargo metadata failed with exit status {result['exit_status']}."
        )
    try:
        metadata = json.loads(result["output"])
    except json.JSONDecodeError as error:
        raise MetadataError(f"cargo metadata returned invalid JSON: {error}") from error
    if not isinstance(metadata, dict) or not isinstance(metadata.get("packages"), list):
        raise MetadataError("cargo metadata JSON has no packages list.")
    return metadata


def workspace_packages(
    root: Path, metadata: dict[str, Any], config: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Map package name -> {manifest_dir}; fail closed on malformed entries.

    The member roots come from the project's `rust_relevant_prefixes`, never a hardcoded
    directory name: an adopting repository may keep its crates anywhere.
    """
    prefixes = tuple((config or {}).get("path_classes", {}).get("rust_relevant_prefixes") or ())
    packages: dict[str, dict[str, Any]] = {}
    for package in metadata["packages"]:
        name = package.get("name")
        manifest_path = package.get("manifest_path")
        deps = package.get("dependencies")
        if not isinstance(name, str) or not isinstance(manifest_path, str) or not isinstance(deps, list):
            raise MetadataError("cargo metadata package entry is malformed.")
        manifest_dir = to_posix_rel(root, Path(manifest_path).parent)
        if prefixes and not manifest_dir.startswith(prefixes):
            continue  # not a workspace member under a declared Rust root
        packages[name] = {"manifest_dir": manifest_dir, "dependencies": deps}
    if not packages:
        where = " or ".join(prefixes) if prefixes else "the declared Rust roots"
        raise MetadataError(f"cargo metadata listed no workspace packages under {where}.")
    return packages


def reverse_dependency_map(root: Path, packages: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Direct workspace reverse dependents across normal, build, AND dev edges."""
    dir_to_name = {info["manifest_dir"]: name for name, info in packages.items()}
    reverse: dict[str, set[str]] = {name: set() for name in packages}
    for name, info in packages.items():
        for dep in info["dependencies"]:
            if not isinstance(dep, dict):
                raise MetadataError("cargo metadata dependency entry is malformed.")
            dep_path = dep.get("path")
            if not isinstance(dep_path, str):
                continue  # registry dependency, not a workspace edge
            dep_dir = to_posix_rel(root, Path(dep_path))
            owner = dir_to_name.get(dep_dir)
            if owner is not None and owner != name:
                reverse[owner].add(name)
    return reverse


# ---------------------------------------------------------------------------
# Selection and escalation
# ---------------------------------------------------------------------------


def select_packages(
    root: Path,
    change_set: dict[str, Any],
    scope: str,
    config: dict[str, Any],
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute the selected package set, escalation reasons, and cargo schedule."""
    runner = runner or run_command
    classes = {path: classify_path(path, config) for path in change_set["paths"]}
    rust_paths = sorted(p for p, c in classes.items() if c == "rust-relevant")

    base_result: dict[str, Any] = {
        "declared_scope": scope,
        "effective_scope": scope,
        "owners": [],
        "reverse_dependents": [],
        "packages": [],
        "workspace_package_count": 0,
        "escalations": [],
        "schedule_cargo": False,
        "path_classes": {p: classes[p] for p in sorted(classes)},
    }

    if scope == "control-plane":
        return base_result
    if not rust_paths:
        return base_result  # docs/control-plane/other-only changes: no Cargo

    escalation_cfg = config["escalation"]
    reasons = escalation_cfg["reasons"]
    escalations: list[dict[str, str]] = []

    def escalate(reason: str, detail: str) -> None:
        escalations.append({"reason": reason, "detail": detail})

    crate_manifest_glob = escalation_cfg["crate_manifest_glob"]
    entry_by_path: dict[str, list[dict[str, Any]]] = {}
    for entry in change_set["entries"]:
        entry_by_path.setdefault(entry["path"], []).append(entry)
        if entry["old_path"]:
            entry_by_path.setdefault(entry["old_path"], []).append(entry)

    for path in rust_paths:
        if path == escalation_cfg["root_manifest_path"]:
            escalate(reasons["root_manifest"], path)
        elif path == escalation_cfg["lockfile_path"]:
            escalate(reasons["lockfile"], path)
        elif path in escalation_cfg["toolchain_paths"]:
            escalate(reasons["toolchain"], path)
        elif any(path.startswith(prefix) for prefix in escalation_cfg["cargo_config_prefixes"]):
            escalate(reasons["cargo_config"], path)
        elif matches_glob(path, crate_manifest_glob):
            for entry in entry_by_path.get(path, []):
                if entry["status"].startswith(("A", "D")) or entry["origin"] == "untracked":
                    escalate(reasons["crate_add_remove"], f"{entry['status']} {path}")
                    break

    metadata = load_workspace_metadata(root, runner)
    packages = workspace_packages(root, metadata, config)
    reverse = reverse_dependency_map(root, packages)
    base_result["workspace_package_count"] = len(packages)

    dir_to_name = {info["manifest_dir"]: name for name, info in packages.items()}
    known_dirs = sorted(dir_to_name, key=len, reverse=True)
    owners: set[str] = set()
    for path in rust_paths:
        if path in (escalation_cfg["root_manifest_path"], escalation_cfg["lockfile_path"]):
            continue
        if path in escalation_cfg["toolchain_paths"]:
            continue
        if any(path.startswith(prefix) for prefix in escalation_cfg["cargo_config_prefixes"]):
            continue
        matched = False
        for manifest_dir in known_dirs:
            if path == manifest_dir or path.startswith(manifest_dir + "/"):
                owners.add(dir_to_name[manifest_dir])
                matched = True
                break
        if not matched:
            escalate(reasons["unknown_rust_path"], path)

    if scope == "workspace":
        selected = set(packages)
        neighbors: set[str] = set()
    elif scope == "affected":
        selected = set(owners)
        neighbors = set()
    else:  # affected-plus-neighbors
        neighbors = set().union(*(reverse.get(owner, set()) for owner in owners)) if owners else set()
        neighbors -= owners
        selected = set(owners) | neighbors

    threshold = escalation_cfg["half_workspace_threshold"]
    if (
        scope != "workspace"
        and not escalations
        and len(selected) >= threshold * len(packages)
        and len(packages) > 0
    ):
        escalate(
            reasons["half_workspace"],
            f"{len(selected)} of {len(packages)} workspace packages selected",
        )

    if escalations:
        base_result["effective_scope"] = "workspace"
        selected = set(packages)
        neighbors = set(selected) - set(owners)

    base_result["owners"] = sorted(owners)
    base_result["reverse_dependents"] = sorted(neighbors)
    base_result["packages"] = sorted(selected)
    base_result["escalations"] = escalations
    base_result["schedule_cargo"] = bool(selected)
    return base_result


# ---------------------------------------------------------------------------
# Verification pipelines (compose the generic substrate with the Cargo gate)
# ---------------------------------------------------------------------------


def probe_toolchain(
    root: Path,
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
) -> dict[str, str]:
    runner = runner or run_command

    def probe(argv: list[str]) -> str:
        try:
            result = runner(argv, root)
        except Exception:
            return "recorded-unavailable"
        if result["exit_status"] != 0:
            return "recorded-unavailable"
        return (
            result["output"].strip().splitlines()[0]
            if result["output"].strip()
            else "recorded-unavailable"
        )

    return {"rustc": probe(["rustc", "--version"]), "cargo": probe(["cargo", "--version"])}


def build_run_commands(packages: list[str], config: dict[str, Any]) -> list[list[str]]:
    manifest = config["workspace_manifest"]
    commands_cfg = config["commands"]
    package_args = [arg for package in packages for arg in ("--package", package)]
    fmt = ["cargo", "fmt", "--manifest-path", manifest, *package_args, "--", "--check"]
    clippy = [
        "cargo", "clippy", "--manifest-path", manifest, "--locked",
        *package_args, *commands_cfg["clippy_extra_args"], "--",
        *commands_cfg["clippy_deny_flags"],
    ]
    test = [
        "cargo", "test", "--manifest-path", manifest, "--locked",
        *package_args, *commands_cfg["test_extra_args"],
    ]
    return [fmt, clippy, test]


def prepare_verification(
    root: Path,
    ai: Path,
    task_id: str,
    base: str,
    *,
    git_fn: Callable[[Path, list[str]], str] | None = None,
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Shared pipeline: task contract, change set, contract check, selection."""
    git_fn = git_fn or git_output
    runner = runner or run_command
    config = load_config(ai)
    task_dir, data = find_task(ai, task_id)
    scope = declared_scope(data, config)
    change_set = collect_change_set(root, base, git_fn)
    check = contract_check(
        change_set["paths"],
        data,
        root=root,
        ai=ai,
        task_dir=task_dir,
        base=base,
        git_fn=git_fn,
        prompt_pair_fn=ai_cli.generated_prompt_pairs,
    )
    selection: dict[str, Any] | None = None
    if not check["target_violations"] and not check["forbidden_violations"]:
        selection = select_packages(root, change_set, scope, config, runner)
    return {
        "config": config,
        "task_dir": task_dir,
        "task_data": data,
        "scope": scope,
        "change_set": change_set,
        "contract_check": check,
        "selection": selection,
    }


def plan_payload(prepared: dict[str, Any], task_id: str) -> dict[str, Any]:
    change_set = prepared["change_set"]
    selection = prepared["selection"]
    payload = {
        "mode": "plan",
        "task_id": task_id,
        "base_commit": change_set["base_commit"],
        "head_commit": change_set["head_commit"],
        "diff_fingerprint": change_set["diff_fingerprint"],
        "change_set": change_set["entries"],
        "contract_check": prepared["contract_check"],
        "selection": selection,
        "schedule_cargo": bool(selection and selection["schedule_cargo"]),
    }
    return payload


def cmd_verify(args: argparse.Namespace, *, root: Path, ai: Path) -> None:
    try:
        prepared = prepare_verification(root, ai, args.task_id, args.base)
    except VerifyError as error:
        die(str(error))
    check = prepared["contract_check"]
    violations = check["target_violations"] + check["forbidden_violations"]

    if args.plan:
        # Plan mode: stable sorted-key JSON on stdout, no Cargo verification, no lock.
        payload = plan_payload(prepared, args.task_id)
        print(json.dumps(payload, indent=2, sort_keys=True))
        if violations:
            die(f"Contract check failed for changed paths: {violations}")
        return

    # Run mode.
    if violations:
        die(f"Contract check failed before invoking Cargo: {violations}")
    selection = prepared["selection"]
    assert selection is not None
    config = prepared["config"]
    started = now_iso()
    monotonic = time.monotonic()
    lock = VerificationLock(root, config, args.task_id, ["ai", "verify", args.task_id, "--run"])
    invocation: dict[str, Any] = {
        "task_id": args.task_id,
        "mode": "run",
        "label": None,
        "repository_key_sha256": repo_key_sha256(root),
        "base_commit": prepared["change_set"]["base_commit"],
        "head_commit": prepared["change_set"]["head_commit"],
        "diff_fingerprint": prepared["change_set"]["diff_fingerprint"],
        "change_set": prepared["change_set"]["entries"],
        "contract_check": check,
        "selection": selection,
        "commands": [],
        "platform": platform_block(),
        "toolchain": {},
        "lock": {},
        "timing": {},
        "result": {},
    }
    exit_status = 0
    failure_error: str | None = None
    acquired = False
    try:
        lock.acquire()
        acquired = True
        lock_acquired = now_iso()
        try:
            invocation["toolchain"] = probe_toolchain(root)
            if selection["schedule_cargo"]:
                for argv in build_run_commands(selection["packages"], config):
                    lock.heartbeat()
                    result = run_command(argv, root)
                    invocation["commands"].append({
                        "argv": result["argv"],
                        "started_utc": result["started_utc"],
                        "duration_seconds": result["duration_seconds"],
                        "exit_status": result["exit_status"],
                    })
                    if result["exit_status"] != 0:
                        exit_status = result["exit_status"]
                        break
            else:
                print("No Cargo verification scheduled for this change set.")
        finally:
            lock_released = now_iso()
            invocation["lock"] = {
                "name": lock.path.name,
                "key_sha256": repo_key_sha256(root),
                "acquired_utc": lock_acquired,
                "released_utc": lock_released,
            }
            lock.release()
    except LockError as error:
        die(str(error))
    except (VerifyError, OSError) as error:
        exit_status = 1
        failure_error = (
            str(error)
            if isinstance(error, VerifyError)
            else "Unable to launch 'cargo'. Ensure it is installed, executable, "
                 f"and available on PATH: {error}"
        )
    if acquired:
        invocation["timing"] = {
            "started_utc": started,
            "ended_utc": now_iso(),
            "duration_seconds": round(time.monotonic() - monotonic, 3),
        }
        invocation["result"] = {"exit_status": exit_status}
        path = append_invocation(prepared["task_dir"], config, scrub_invocation(invocation, root))
        print(f"Evidence: {to_posix_rel(root, path)}")
    if failure_error:
        die(f"{failure_error}; see evidence record.")
    if exit_status:
        die(f"Rust verification failed with exit status {exit_status}; see evidence record.")
    print("Rust verification passed.")


def cargo_subcommand(cargo_argv: list[str]) -> str | None:
    """Locate the cargo subcommand, skipping +toolchain and leading global flags."""
    for token in cargo_argv:
        if token.startswith("+"):
            continue  # +toolchain selector
        if token.startswith("-"):
            continue  # global flag (e.g. --locked, -Zfoo) before the subcommand
        return token
    return None


def cmd_cargo(args: argparse.Namespace, *, root: Path, ai: Path) -> None:
    cargo_argv = [str(item) for item in (args.cargo_argv or [])]
    while cargo_argv and cargo_argv[0] == "--":
        cargo_argv = cargo_argv[1:]
    if not cargo_argv:
        die("ai cargo requires a cargo argv after `--`.")
    if cargo_subcommand(cargo_argv) == "clean":
        die("ai cargo rejects `clean`; use `ai cargo-cache clean --scratch|--workspace --yes`.")
    # The executable is always cargo; the post-`--` argv is subcommand-first.
    argv = ["cargo", *cargo_argv]
    try:
        prepared = prepare_verification(root, ai, args.task_id, args.base)
    except VerifyError as error:
        die(str(error))
    check = prepared["contract_check"]
    violations = check["target_violations"] + check["forbidden_violations"]
    if violations:
        die(f"Contract check failed before invoking Cargo: {violations}")

    config = prepared["config"]
    started = now_iso()
    monotonic = time.monotonic()
    lock = VerificationLock(root, config, args.task_id, ["ai", "cargo", args.task_id, *cargo_argv])
    invocation: dict[str, Any] = {
        "task_id": args.task_id,
        "mode": "cargo",
        "label": args.label,
        "repository_key_sha256": repo_key_sha256(root),
        "base_commit": prepared["change_set"]["base_commit"],
        "head_commit": prepared["change_set"]["head_commit"],
        "diff_fingerprint": prepared["change_set"]["diff_fingerprint"],
        "change_set": prepared["change_set"]["entries"],
        "contract_check": check,
        "selection": prepared["selection"],
        "commands": [],
        "platform": platform_block(),
        "toolchain": {},
        "lock": {},
        "timing": {},
        "result": {},
    }
    exit_status = 0
    failure_error: str | None = None
    acquired = False
    try:
        lock.acquire()
        acquired = True
        lock_acquired = now_iso()
        try:
            invocation["toolchain"] = probe_toolchain(root)
            lock.heartbeat()
            result = run_command(argv, root)
            invocation["commands"].append({
                "argv": result["argv"],
                "started_utc": result["started_utc"],
                "duration_seconds": result["duration_seconds"],
                "exit_status": result["exit_status"],
            })
            exit_status = result["exit_status"]
        finally:
            lock_released = now_iso()
            invocation["lock"] = {
                "name": lock.path.name,
                "key_sha256": repo_key_sha256(root),
                "acquired_utc": lock_acquired,
                "released_utc": lock_released,
            }
            lock.release()
    except LockError as error:
        die(str(error))
    except (VerifyError, OSError) as error:
        exit_status = 1
        failure_error = (
            str(error)
            if isinstance(error, VerifyError)
            else "Unable to launch 'cargo'. Ensure it is installed, executable, "
                 f"and available on PATH: {error}"
        )
    if acquired:
        invocation["timing"] = {
            "started_utc": started,
            "ended_utc": now_iso(),
            "duration_seconds": round(time.monotonic() - monotonic, 3),
        }
        invocation["result"] = {"exit_status": exit_status}
        path = append_invocation(prepared["task_dir"], config, scrub_invocation(invocation, root))
        print(f"Evidence: {to_posix_rel(root, path)}")
    if failure_error:
        die(f"{failure_error}; see evidence record.")
    if exit_status:
        raise SystemExit(exit_status)


# ---------------------------------------------------------------------------
# Scratch roots and cache lifecycle
# ---------------------------------------------------------------------------


def create_scratch_root(root: Path, config: dict[str, Any], justification: str) -> Path:
    """Isolated target dir in OS temp with a repository/schema marker."""
    if not justification.strip():
        raise VerifyError("Scratch roots require an explicit justification.")
    scratch_cfg = config["scratch"]
    temp = temp_dir()
    name = f"{scratch_cfg['dir_prefix']}{repo_key_sha256(root)[:8]}-{os.getpid()}-{int(time.time() * 1000)}"
    path = temp / name
    path.mkdir(parents=True, exist_ok=False)
    marker = {
        "schema_version": scratch_cfg["marker_schema_version"],
        "repo_key_sha256": repo_key_sha256(root),
        "created_utc": now_iso(),
        "justification": justification,
    }
    (path / scratch_cfg["marker_file"]).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


@contextmanager
def isolated_target(root: Path, config: dict[str, Any], justification: str):
    """Scratch target removed in a finally path."""
    path = create_scratch_root(root, config, justification)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def is_link_or_reparse(path: Path) -> bool:
    if os.path.islink(path):
        return True
    try:
        stat_result = os.lstat(path)
    except OSError:
        return False
    # FILE_ATTRIBUTE_REPARSE_POINT (Windows junctions etc.)
    return bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)


def validate_scratch_candidate(
    path: Path, temp: Path, config: dict[str, Any], root: Path
) -> tuple[bool, str]:
    scratch_cfg = config["scratch"]
    temp_resolved = temp.resolve()
    resolved = path.resolve()
    if resolved == temp_resolved or resolved == Path(resolved.anchor):
        return False, "root"
    try:
        resolved.relative_to(temp_resolved)
    except ValueError:
        return False, "out-of-prefix"
    cwd = getcwd().resolve()
    if resolved == cwd or resolved in cwd.parents:
        return False, "current-dir"
    if is_link_or_reparse(path):
        return False, "symlink-or-reparse-point"
    if not path.name.startswith(scratch_cfg["dir_prefix"]):
        return False, "out-of-prefix"
    marker_path = path / scratch_cfg["marker_file"]
    if not marker_path.exists():
        return False, "unmarked"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "malformed-marker"
    if not isinstance(marker, dict) or marker.get("schema_version") != scratch_cfg["marker_schema_version"]:
        return False, "malformed-marker"
    if marker.get("repo_key_sha256") != repo_key_sha256(root):
        return False, "foreign-marker"
    return True, "marker-owned"


def detect_unwrapped_cargo(listing: str) -> list[str]:
    """Parse a process listing for live cargo/rustc processes."""
    found: list[str] = []
    for line in listing.splitlines():
        stripped = line.strip().strip('"')
        if not stripped:
            continue
        image = stripped.split(",")[0].strip().strip('"')
        base = image.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in ("cargo", "cargo.exe", "rustc", "rustc.exe"):
            found.append(image)
    return sorted(set(found))


def refuse_if_cargo_active(name: str | None = None) -> None:
    listing = process_listing(name if name is not None else os_name())
    active = detect_unwrapped_cargo(listing)
    if active:
        raise ProcessActiveError(
            "Refusing cleanup while a live unwrapped cargo/rustc process is active: "
            + ", ".join(active)
        )


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames if not is_link_or_reparse(Path(dirpath) / d)]
        for filename in filenames:
            try:
                total += (Path(dirpath) / filename).lstat().st_size
            except OSError:
                continue
    return total


def cache_inspect_report(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Read-only: never deletes anything."""
    workspace_root = (root / config.get("workspace_manifest", MANIFEST_REL)).parent
    target_rel = to_posix_rel(root, workspace_root / "target")
    target = workspace_root / "target"
    contributors: list[dict[str, Any]] = []
    total = 0
    if target.is_dir() and not is_link_or_reparse(target):
        total = dir_size(target)
        try:
            children = sorted(target.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and not is_link_or_reparse(child):
                contributors.append({
                    "path": to_posix_rel(root, child),
                    "bytes": dir_size(child),
                })
            elif child.is_file():
                contributors.append({"path": to_posix_rel(root, child), "bytes": child.stat().st_size})
        contributors.sort(key=lambda item: -item["bytes"])

    nested: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(workspace_root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not is_link_or_reparse(Path(dirpath) / d)]
        current = Path(dirpath)
        if current == target:
            dirnames[:] = []
            continue
        if "target" in dirnames:
            candidate = current / "target"
            if candidate.resolve() != target.resolve():
                nested.append(to_posix_rel(root, candidate))
            dirnames.remove("target")

    scratch_cfg = config["scratch"]
    temp = temp_dir()
    scratch_roots: list[dict[str, Any]] = []
    try:
        entries = sorted(temp.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.name.startswith(scratch_cfg["dir_prefix"]):
            continue
        ok, reason = validate_scratch_candidate(entry, temp, config, root)
        info: dict[str, Any] = {"name": entry.name, "valid": ok, "reason": reason}
        if ok:
            info["bytes"] = dir_size(entry)
        scratch_roots.append(info)

    lock = VerificationLock(root, config, "<inspect>", ["ai", "cargo-cache", "inspect"])
    holder = lock.holder_metadata()
    held = False
    fd = None
    try:
        ops = lock_operations(os_name())
        fd = os.open(str(lock.path), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            ops[0](lock.path, fd)
        except OSError:
            held = True
        else:
            ops[1](lock.path, fd)
    except OSError:
        held = False
    finally:
        if fd is not None:
            os.close(fd)

    return {
        "schema_version": 1,
        "kind": "maw-rust-cache-inspect",
        "repository_key_sha256": repo_key_sha256(root),
        "project_target": {
            "path": target_rel,
            "exists": target.is_dir(),
            "total_bytes": total,
            "contributors": contributors,
        },
        "nested_target_dirs": nested,
        "scratch_roots": scratch_roots,
        "lock": {
            "name": lock.path.name,
            "held": held,
            "holder": holder,
        },
    }


def append_cache_log(ai: Path, config: dict[str, Any], entry: dict[str, Any]) -> Path:
    rel_path = config["cache_log"]["path"]
    path = ai.parent / rel_path
    if path.exists():
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VerifyError(f"Cache log is corrupt; refusing to overwrite: {error}") from error
    else:
        log = {
            "schema_version": config["cache_log"]["schema_version"],
            "kind": config["cache_log"]["kind"],
            "entries": [],
        }
    log["entries"].append(entry)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def cmd_cargo_cache(args: argparse.Namespace, *, root: Path, ai: Path) -> None:
    try:
        config = load_config(ai)
    except VerifyError as error:
        die(str(error))

    if args.cargo_cache_command == "inspect":
        print(json.dumps(cache_inspect_report(root, config), indent=2, sort_keys=True))
        return

    # clean
    if not args.yes:
        die("cargo-cache clean is destructive and requires --yes.")
    if args.scratch == args.workspace:
        die("Choose exactly one of --scratch or --workspace.")

    lock = VerificationLock(root, config, "<cargo-cache>", ["ai", "cargo-cache", "clean"])
    try:
        if args.workspace:
            # Never clean while another process owns the lock: refuse immediately.
            lock.acquire(wait_seconds=0)
        else:
            lock.acquire()
    except LockError as error:
        die(str(error))
    try:
        try:
            refuse_if_cargo_active()
        except ProcessActiveError as error:
            die(str(error))

        if args.scratch:
            temp = temp_dir()
            deleted: list[str] = []
            rejected: list[dict[str, str]] = []
            try:
                entries = sorted(temp.iterdir())
            except OSError:
                entries = []
            for entry in entries:
                if not entry.name.startswith(config["scratch"]["dir_prefix"]):
                    continue
                ok, reason = validate_scratch_candidate(entry, temp, config, root)
                if ok:
                    shutil.rmtree(entry)
                    deleted.append(entry.name)
                else:
                    rejected.append({"name": entry.name, "reason": reason})
            append_cache_log(ai, config, {
                "action": "clean-scratch",
                "recorded_at_utc": now_iso(),
                "deleted": deleted,
                "rejected": rejected,
                "lock": lock.path.name,
            })
            print(json.dumps({"deleted": deleted, "rejected": rejected}, indent=2, sort_keys=True))
            return

        # --workspace: canonical manifest only, cargo clean as an argv array.
        manifest_rel = config["workspace_manifest"]
        manifest = root / manifest_rel
        if not manifest.exists() or "[workspace]" not in manifest.read_text(encoding="utf-8"):
            die(f"Canonical workspace manifest missing or invalid: {manifest_rel}")
        argv = ["cargo", "clean", "--manifest-path", manifest_rel]
        result = run_command(argv, root)
        append_cache_log(ai, config, {
            "action": "clean-workspace",
            "recorded_at_utc": now_iso(),
            "argv": argv,
            "exit_status": result["exit_status"],
            "lock": lock.path.name,
        })
        if result["exit_status"] != 0:
            raise SystemExit(result["exit_status"])
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rust_verify", description="Selective Rust verification")
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="Plan or run selective verification")
    p_verify.add_argument("task_id")
    p_verify.add_argument("--base", required=True)
    mode = p_verify.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")

    p_cargo = sub.add_parser("cargo", help="Wrapped cargo invocation with evidence")
    p_cargo.add_argument("task_id")
    p_cargo.add_argument("--base", required=True)
    p_cargo.add_argument("--label")
    # argv after `--` is split off in main() before argparse runs.

    p_cache = sub.add_parser("cargo-cache", help="Cargo cache lifecycle")
    cache_sub = p_cache.add_subparsers(dest="cargo_cache_command", required=True)
    cache_sub.add_parser("inspect", help="Read-only cache report")
    p_clean = cache_sub.add_parser("clean", help="Destructive cache cleanup (requires --yes)")
    p_clean.add_argument("--scratch", action="store_true")
    p_clean.add_argument("--workspace", action="store_true")
    p_clean.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    ai_cli.configure_utf8_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    cargo_argv: list[str] | None = None
    if raw and raw[0] == "cargo" and "--" in raw:
        split = raw.index("--")
        cargo_argv = raw[split + 1:]
        raw = raw[:split]
    args = build_parser().parse_args(raw)
    if args.command == "cargo":
        args.cargo_argv = cargo_argv if cargo_argv is not None else []
    if args.command == "verify":
        cmd_verify(args, root=ROOT, ai=AI)
    elif args.command == "cargo":
        cmd_cargo(args, root=ROOT, ai=AI)
    elif args.command == "cargo-cache":
        cmd_cargo_cache(args, root=ROOT, ai=AI)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
