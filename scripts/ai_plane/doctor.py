from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.ai_plane.config as config_module
import scripts.ai_plane.tool_detection as tool_detection
import scripts.ai_plane.routing_profile as tool_profile
import scripts.ai_plane.constants as constants
import scripts.extension_registry as extension_registry
from scripts.ai_plane.manifest import parse_manifest_bytes, sha256_bytes
from scripts.ai_plane.registry import generate_registry
from scripts.ai_plane.tasks import TASK_STATES
from scripts.ai_plane.utils import die


def _resolve_extensions():
    """Parse the authoritative config and resolve the enabled extension composition through
    the registry, enforcing the host platform. Fails closed (RegistryError/ConfigError) on an
    invalid or missing roster or manifest; never substitutes a default roster."""
    parsed = config_module.parse_config_yaml(constants.AI / "config.yaml")
    return extension_registry.resolve(parsed, constants.ROOT, platform_name=os.name)


def resolve_effective_scopes() -> list[str]:
    """The effective verification-scope vocabulary = core scopes + every enabled gate's additions.
    Used by the no-gate degradation path to validate a task's declared scope."""
    return _resolve_extensions()["scopes"]


def resolve_registry() -> dict:
    """The full resolved extension composition (fail-closed on an invalid/missing roster). Used by
    the `ai ext` capability commands."""
    return _resolve_extensions()


def _load_rust_verify():
    """Resolve the project's single registered evidence gate through the extension registry —
    generically, by config, NOT by a hardcoded id. Returns the gate module, or ``None`` when no
    gate is enabled (the caller then degrades to control-plane-only verification). Registration is
    explicit-config only: there is no import-time side effect or ambient discovery, and a gate is
    loaded solely for an id the project enabled in ``.ai/config.yaml``. Exactly one gate is
    supported per project; more than one fails closed (the selection would be ambiguous). The name
    ``_load_rust_verify`` is retained for the delegation seam the wiring tests patch."""
    resolved = _resolve_extensions()
    gate_ids = resolved["gate_ids"]
    if not gate_ids:
        return None
    if len(gate_ids) > 1:
        die(
            "Multiple evidence gates enabled (" + ", ".join(gate_ids) + "); exactly one gate is "
            "supported per project. Disable all but one in .ai/config.yaml extensions.enabled."
        )
    return extension_registry.load_gate_module(resolved, gate_ids[0], constants.ROOT)



def doctor_check(status: str, name: str, detail: str, guidance: str = "") -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail, "guidance": guidance}


def inspect_doctor_lock(
    root: Path,
    ai: Path,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Inspect the verifier lock without creating, deleting, or rewriting lock files."""
    mod = (
        sys.modules.get("scripts.ai_cli")
        or sys.modules.get("ai_cli")
        or sys.modules.get("task_111_ai_cli")
        or sys.modules.get(__name__)
    )
    loader = getattr(mod, "_load_rust_verify", _load_rust_verify)
    rust_verify = loader()

    if rust_verify is None:
        return doctor_check(
            "PASS",
            "Verification lock",
            "no evidence gate registered; lock inspection is gate-provided and skipped",
        )

    try:
        config = rust_verify.load_config(ai)
    except Exception as error:
        return doctor_check("FAIL", "Verification lock", f"configuration unreadable: {error}")

    lock = rust_verify.VerificationLock(root, config, "<doctor>", ["ai", "doctor"])
    if not lock.path.exists():
        return doctor_check("PASS", "Verification lock", "free (no lock file present)")

    holder = lock.holder_metadata()
    fd: int | None = None
    held = False
    try:
        operations = rust_verify.lock_operations(rust_verify.os_name())
        fd = os.open(str(lock.path), os.O_RDWR)
        try:
            operations[0](lock.path, fd)
        except OSError:
            held = True
        else:
            operations[1](lock.path, fd)
    except OSError as error:
        return doctor_check("FAIL", "Verification lock", f"lock file unreadable: {error}")
    finally:
        if fd is not None:
            os.close(fd)

    inspect_command = "python scripts/ai_cli.py cargo-cache inspect"
    if not held:
        if holder:
            return doctor_check(
                "WARN",
                "Verification lock",
                "free; stale holder metadata remains from an inactive process",
                f"No repair is required. Confirm the idle state with: {inspect_command}",
            )
        return doctor_check(
            "WARN",
            "Verification lock",
            "free; an idle lock file is present",
            f"No repair is required. Confirm the idle state with: {inspect_command}",
        )

    heartbeat = holder.get("heartbeat_utc") if isinstance(holder, dict) else None
    age_seconds: float | None = None
    if isinstance(heartbeat, str):
        try:
            heartbeat_at = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            reference = now or datetime.now(timezone.utc)
            age_seconds = max(0.0, (reference - heartbeat_at).total_seconds())
        except ValueError:
            age_seconds = None
    holder_text = (
        f"pid={holder.get('pid')} task={holder.get('task_id')} heartbeat={heartbeat}"
        if isinstance(holder, dict)
        else "holder metadata unavailable"
    )
    stale_after = max(30.0, float(config["lock"].get("heartbeat_seconds", 5)) * 3)
    if age_seconds is None or age_seconds > stale_after:
        age_text = "unknown" if age_seconds is None else f"{age_seconds:.1f}s"
        return doctor_check(
            "WARN",
            "Verification lock",
            f"held with a stale or unreadable heartbeat (age {age_text}); {holder_text}",
            f"Inspect the holder before retrying work: {inspect_command}",
        )
    return doctor_check(
        "PASS",
        "Verification lock",
        f"held by an active process; heartbeat age {age_seconds:.1f}s; {holder_text}",
    )


def collect_doctor_checks(
    root: Path | None = None,
    ai: Path | None = None,
    *,
    version_info: tuple[int, ...] | None = None,
    which: Any = shutil.which,
    lock_inspector: Any = inspect_doctor_lock,
    transport_detector: Any = tool_detection.detect_tool_transports,
    platform_name: str | None = None,
) -> list[dict[str, str]]:
    """Collect read-only environment checks; WARN is an expected, repairable gap."""
    root = root if root is not None else constants.ROOT
    ai = ai if ai is not None else constants.AI
    checks: list[dict[str, str]] = []
    version = tuple(version_info or sys.version_info[:3])
    version_text = ".".join(str(part) for part in version[:3])
    floor_text = ".".join(str(part) for part in config_module.MINIMUM_PYTHON)
    if version[:2] >= config_module.MINIMUM_PYTHON:
        checks.append(doctor_check("PASS", "Python", f"{version_text} (supported floor {floor_text})"))
    else:
        checks.append(
            doctor_check(
                "FAIL",
                "Python",
                f"{version_text} is below supported floor {floor_text}",
                "Install Python 3.10+ and run: python scripts/ai_cli.py doctor",
            )
        )

    git_path = which("git")
    if git_path:
        checks.append(doctor_check("PASS", "Git", f"found via PATH/PATHEXT: {git_path}"))
    else:
        checks.append(
            doctor_check(
                "FAIL",
                "Git",
                "git was not found via PATH/PATHEXT",
                "Install Git, reopen the terminal, then run: python scripts/ai_cli.py doctor",
            )
        )

    scaffold_readable = False
    if not ai.is_dir():
        checks.append(doctor_check("FAIL", "Scaffold", ".ai/ is missing or is not a directory"))
    else:
        try:
            list(ai.iterdir())
            scaffold_readable = True
            checks.append(doctor_check("PASS", "Scaffold", ".ai/ is readable"))
        except OSError as error:
            checks.append(doctor_check("FAIL", "Scaffold", f".ai/ is unreadable: {error}"))

    missing_states = [state for state in TASK_STATES if not (ai / "tasks" / state).is_dir()]
    if missing_states:
        checks.append(
            doctor_check(
                "FAIL",
                "Task states",
                "missing directories: " + ", ".join(missing_states),
                "Restore the repository scaffold before running any task command.",
            )
        )
    else:
        checks.append(doctor_check("PASS", "Task states", "queue/active/done/archive are present"))
        try:
            queued = sorted(path for path in (ai / "tasks" / "queue").iterdir() if path.is_dir())
        except OSError as error:
            checks.append(doctor_check("FAIL", "Queue", f"queue directory is unreadable: {error}"))
        else:
            if queued:
                checks.append(doctor_check("PASS", "Queue", f"{len(queued)} queued task(s)"))
            else:
                checks.append(
                    doctor_check(
                        "WARN",
                        "Queue",
                        "empty queue",
                        'Create the next task graph with: python scripts/ai_cli.py feature new "Describe the next feature"',
                    )
                )

    config_ok = False
    profile_known = False
    enabled_tools: set[str] = set()
    config_path = ai / "config.yaml"
    try:
        tool_registry = config_module.load_tool_registry(config_path)
        config_ok = True
        checks.append(doctor_check("PASS", "Config", ".ai/config.yaml parses and validates"))
    except config_module.ConfigError as error:
        checks.append(doctor_check("FAIL", "Config", str(error), "Repair .ai/config.yaml, then rerun doctor."))

    if config_ok:
        try:
            profile = tool_profile.load_profile(tuple(tool_registry), ai_root=ai)
        except tool_profile.ToolProfileError as error:
            checks.append(
                doctor_check(
                    "WARN",
                    "Tool profile",
                    f"{error.reason}: {error.detail}",
                    tool_profile.CONFIGURE_GUIDANCE,
                )
            )
        else:
            profile_known = True
            enabled_tools = set(profile.enabled_tools if profile is not None else ())
            if profile is None:
                checks.append(
                    doctor_check(
                        "WARN",
                        "Tool profile",
                        f"{tool_profile.PROFILE_RELATIVE_PATH} is missing; zero providers are locally enabled",
                        tool_profile.CONFIGURE_GUIDANCE,
                    )
                )
            else:
                checks.append(
                    doctor_check("PASS", "Tool profile", f"valid; {len(profile.enabled_tools)} tool(s) locally enabled")
                )
        descriptors = {
            tool: entry["dispatch"]
            for tool, entry in tool_registry.items()
            if entry.get("dispatch") is not None
        }
        try:
            detections = transport_detector(
                tuple(tool_registry),
                descriptors,
                which=which,
                platform_name=platform_name or sys.platform,
            )
        except Exception as error:
            checks.append(
                doctor_check(
                    "WARN",
                    "Tool detection",
                    f"advisory detector error ({type(error).__name__}): {error}",
                    "Use manual provider checks; detection never authorizes routing or launch.",
                )
            )
        else:
            for tool in tool_registry:
                enabled = (
                    ("yes" if tool in enabled_tools else "no")
                    if profile_known
                    else "unknown"
                )
                for result in detections[tool]:
                    status = "WARN" if result.status in ("absent", "error") else "PASS"
                    checks.append(
                        doctor_check(
                            status,
                            f"Tool detection ({tool}/{result.detector})",
                            f"supported=yes; enabled={enabled}; detected={result.status}; "
                            f"reason={result.reason}; launch-attempted=no; "
                            "submitted/running=unknown",
                            "Detection is advisory only; configure tools explicitly and retain manual fallback."
                            if status == "WARN"
                            else "",
                        )
                    )
    registry_file = ai / "_registry.json"
    if scaffold_readable and config_ok:
        try:
            expected_registry = (json.dumps(generate_registry(ai), indent=2, sort_keys=True) + "\n").encode("utf-8")
            current_registry = registry_file.read_bytes()
            json.loads(current_registry.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            checks.append(
                doctor_check(
                    "WARN",
                    "Registry",
                    f"missing or unreadable: {error}",
                    "Regenerate it with: python scripts/ai_cli.py sync",
                )
            )
        else:
            if current_registry == expected_registry:
                checks.append(doctor_check("PASS", "Registry", ".ai/_registry.json is fresh"))
            else:
                checks.append(
                    doctor_check(
                        "WARN",
                        "Registry",
                        ".ai/_registry.json is stale against canonical documents",
                        "Regenerate it with: python scripts/ai_cli.py sync",
                    )
                )

    manifest_file = ai / "_manifest.json"
    try:
        manifest_entries = parse_manifest_bytes(manifest_file.read_bytes(), ".ai/_manifest.json")
    except (OSError, ValueError) as error:
        checks.append(
            doctor_check(
                "WARN",
                "Manifest",
                f"missing or unreadable: {error}",
                "Regenerate it with: python scripts/ai_cli.py sync",
            )
        )
    else:
        stale_paths: list[str] = []
        for path_value, entry in manifest_entries.items():
            target = root / path_value
            try:
                digest = sha256_bytes(target.read_bytes())
            except OSError:
                stale_paths.append(path_value)
                continue
            if digest != entry["sha256"]:
                stale_paths.append(path_value)
        if ".ai/_registry.json" not in manifest_entries:
            stale_paths.append(".ai/_registry.json (untracked by manifest)")
        if stale_paths:
            preview = ", ".join(stale_paths[:5])
            suffix = "" if len(stale_paths) <= 5 else f" (+{len(stale_paths) - 5} more)"
            checks.append(
                doctor_check(
                    "WARN",
                    "Manifest",
                    f"stale generated paths: {preview}{suffix}",
                    "Regenerate them with: python scripts/ai_cli.py sync",
                )
            )
        else:
            checks.append(
                doctor_check(
                    "PASS",
                    "Manifest",
                    f"{len(manifest_entries)} generated path hash(es) match the current tree",
                )
            )

    checks.extend(product_topology_checks(root))
    checks.append(lock_inspector(root, ai))
    return checks


def product_topology_checks(root: Path) -> list[dict[str, str]]:
    """What the plane thinks the product is, and whether it can actually index it.

    Two silent states cost a whole adoption. A workspace that is also a product worktree looks fine
    until Git, ignore policy, and agent instructions start colliding. And an index capability that
    was merely PRESENT got reported as working, when in truth it can be declared, unindexed, or
    unavailable -- three situations with three different remedies.
    """
    from scripts.ai_plane import products
    from scripts.ai_plane.knowledge_projection import index_adapters

    checks: list[dict[str, str]] = []
    mixed = products.mixed_install_conflicts(root)
    discovered = products.discover_products(root)
    if mixed:
        checks.append(doctor_check(
            "WARN", "Topology",
            "workspace root is also a product worktree: " + "; ".join(mixed),
            f"Move the product under {products.PROJECTS_ROOT}/<product-id>/ so the control plane "
            "and the product keep separate Git, ignore policy, and agent instructions.",
        ))
    elif discovered:
        checks.append(doctor_check("PASS", "Topology", ", ".join(
            f"{item.product_id} at {item.relative_path} [{', '.join(item.stacks) or 'no stack'}]"
            for item in discovered)))
    else:
        checks.append(doctor_check(
            "WARN", "Topology", "no product manifest was discovered",
            f"Place the product under {products.PROJECTS_ROOT}/<product-id>/ so Project "
            "Intelligence indexes your application rather than the plane's own scripts.",
        ))

    adapters = index_adapters.candidates(root)
    marker = root / ".codegraph"
    if not adapters:
        checks.append(doctor_check(
            "WARN", "Project Intelligence", "unavailable: no adapter matches any discovered stack",
            index_adapters.unavailable_boundary_fields(root)["rebuild_guidance"],
        ))
        if marker.exists():
            checks.append(doctor_check(
                "WARN", "CodeGraph",
                ".codegraph/ exists but nothing here can query it",
                "That marker is an index artifact, not a capability. Remove it, or install the "
                "tool that wrote it.",
            ))
        return checks
    adapter = adapters[0]
    if not (root / ".ai" / "_site" / "assets" / "project-data.js").is_file():
        checks.append(doctor_check(
            "WARN", "Project Intelligence",
            f"declared but not indexed: {adapter.adapter_id} would index "
            f"{', '.join(adapter.indexed_roots)}",
            "Build the index with: python scripts/ai_cli.py docs build",
        ))
    else:
        checks.append(doctor_check(
            "PASS", "Project Intelligence",
            f"indexed by {adapter.adapter_id} ({adapter.stack}) for product "
            f"{adapter.product_id}: {', '.join(adapter.indexed_roots)}",
        ))
    return checks


def cmd_doctor(_args: argparse.Namespace) -> None:
    mod = (
        sys.modules.get("scripts.ai_cli")
        or sys.modules.get("ai_cli")
        or sys.modules.get("task_111_ai_cli")
        or sys.modules.get(__name__)
    )
    collector = getattr(mod, "collect_doctor_checks", collect_doctor_checks)
    checks = collector()


    print("AI doctor")
    for check in checks:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
        if check["guidance"]:
            print(f"       {check['guidance']}")
    failures = sum(check["status"] == "FAIL" for check in checks)
    warnings = sum(check["status"] == "WARN" for check in checks)
    passes = sum(check["status"] == "PASS" for check in checks)
    print(f"Summary: {passes} passed, {warnings} guidance item(s), {failures} failure(s)")
    if failures:
        raise SystemExit(1)

