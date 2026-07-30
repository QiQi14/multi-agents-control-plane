#!/usr/bin/env python3
"""Control-plane contract verification (task_181 Q181-5 cohesion split).

Extracted verbatim from scripts/verify_core.py: task-contract discovery, target/forbidden glob
matching, generated-file manifest exemptions, git change-set discovery, and the no-gate
control-plane degradation path (which CLASSIFIES the changed paths, never trusting the scope
label). Depends one-directionally on the generic substrate (scripts/verify_core.py) and the
config-free primitives; the substrate never imports this module. The one tool-aware step — mapping a
changed task's regenerated prompt pairs — is NOT imported here: it is INJECTED as the optional
`prompt_pair_fn` callback by the caller (the CLI supplies scripts/ai_plane/prompts.py's
generated_prompt_pairs), so this module's own load-time import graph stays context-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ai_plane.primitives import (  # noqa: E402
    TASK_STATES,
    parse_manifest_bytes,
    parse_simple_yaml,
    serialize_manifest,
    task_list,
)
from scripts.ai_plane.task_evidence_legacy import changed_task_artifact_violations  # noqa: E402
from scripts.verify_core import (  # noqa: E402
    GitError,
    VerifyError,
    die,
    git_output,
    to_posix_rel,
)


def find_task(ai: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    for state in TASK_STATES:
        state_dir = ai / "tasks" / state
        if not state_dir.exists():
            continue
        for task_file in sorted(state_dir.glob("*/task.yaml")):
            data = parse_simple_yaml(task_file)
            if data.get("id") == task_id or task_file.parent.name == task_id:
                return task_file.parent, data
    raise VerifyError(f"Task not found: {task_id}")


def declared_scope(data: dict[str, Any], config: dict[str, Any]) -> str:
    raw = data.get("verification_scope")
    if raw in (None, ""):
        return config["default_scope"]
    scope = str(raw)
    if scope not in config["verification_scopes"]:
        raise VerifyError(
            f"Unknown verification_scope '{scope}'; allowed: "
            + ", ".join(config["verification_scopes"])
        )
    return scope


def extract_glob_tokens(items: list[str]) -> list[str]:
    """Extract every path-like glob from contract items that may bundle prose."""
    tokens: list[str] = []
    for item in items:
        text = str(item).strip()
        matches = re.findall(r"[A-Za-z0-9_.*?\[\]-]+(?:/[A-Za-z0-9_.*?\[\]-]+)*", text)
        for candidate in matches:
            candidate = candidate.rstrip(".")
            path_shaped = any(char in candidate for char in ("/", "*", "?", ".", "["))
            exact_bare_path = text.strip("`'\"") == candidate
            if candidate and (path_shaped or exact_bare_path):
                tokens.append(candidate)
    return tokens


def path_glob_regex(glob: str) -> re.Pattern[str]:
    """Compile a repository glob where only an explicit ** may cross '/'."""
    pattern = ""
    index = 0
    while index < len(glob):
        char = glob[index]
        if char == "*":
            if index + 1 < len(glob) and glob[index + 1] == "*":
                pattern += ".*"
                index += 2
            else:
                pattern += "[^/]*"
                index += 1
        elif char == "?":
            pattern += "[^/]"
            index += 1
        elif char == "[":
            end = glob.find("]", index + 1)
            if end < 0:
                pattern += r"\["
                index += 1
            else:
                content = glob[index + 1:end]
                negate = content.startswith(("!", "^"))
                if negate:
                    content = content[1:]
                content = content.replace("\\", r"\\").replace("]", r"\]")
                pattern += f"(?!/)[{'^' if negate else ''}{content}]"
                index = end + 1
        else:
            pattern += re.escape(char)
            index += 1
    return re.compile(f"^{pattern}$")


def matches_glob(path: str, glob: str) -> bool:
    return path_glob_regex(glob).fullmatch(path) is not None


def matches_any(path: str, globs: list[str]) -> bool:
    return any(matches_glob(path, glob) for glob in globs)


GENERATED_MANIFEST_REL = ".ai/_manifest.json"


def current_manifest(root: Path) -> tuple[dict[str, dict[str, str]], bytes] | None:
    path = root / GENERATED_MANIFEST_REL
    if not path.is_file():
        return None
    try:
        content = path.read_bytes()
        return parse_manifest_bytes(content, GENERATED_MANIFEST_REL), content
    except (OSError, ValueError):
        return None


def base_manifest(
    root: Path,
    base: str,
    git_fn: Callable[[Path, list[str]], str],
) -> dict[str, dict[str, str]]:
    try:
        content = git_fn(root, ["show", f"{base}:{GENERATED_MANIFEST_REL}"])
        return parse_manifest_bytes(content.encode("utf-8"), f"{base}:{GENERATED_MANIFEST_REL}")
    except (GitError, ValueError):
        return {}


def exact_file_entry(root: Path, entry: dict[str, str]) -> bool:
    path = root / entry["path"]
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def manifest_exemptions(
    root: Path,
    ai: Path,
    task_dir: Path,
    data: dict[str, Any],
    change_paths: list[str],
    base: str,
    git_fn: Callable[[Path, list[str]], str],
    prompt_pair_fn: Callable[..., list] | None = None,
) -> set[str]:
    """Return exact generated paths authorized independently of the current diff.

    The dispatch/review prompt-pair exemption is tool-aware (it reconstructs a generated prompt's
    command from the tool roster), so it is INJECTED as `prompt_pair_fn` by the caller (the CLI or
    the gate, which have tool access) rather than imported here. This keeps maw_core free of any
    runtime dependency on the config/tool/prompt graph (Q181-5, round 3): with no callback, only the
    generic hash-based exemption applies."""
    current = current_manifest(root)
    if current is None:
        return set()
    manifest, manifest_bytes = current
    base_entries = base_manifest(root, base, git_fn)
    changed = set(change_paths)
    exemptions = {
        path
        for path, entry in base_entries.items()
        if path in changed and exact_file_entry(root, entry)
    }

    pairs = prompt_pair_fn(root, ai, task_dir, data, changed, manifest) if prompt_pair_fn else []
    if pairs:
        expected = dict(base_entries)
        for _adapter_path, _task_prompt, adapter_entry, task_entry in pairs:
            expected[adapter_entry["path"]] = adapter_entry
            expected[task_entry["path"]] = task_entry
        if manifest_bytes == serialize_manifest(expected):
            for adapter_path, task_prompt, _adapter_entry, _task_entry in pairs:
                exemptions.add(adapter_path)
                exemptions.add(task_prompt)
            if GENERATED_MANIFEST_REL in changed:
                exemptions.add(GENERATED_MANIFEST_REL)
    return exemptions


def contract_check(
    change_paths: list[str],
    data: dict[str, Any],
    *,
    root: Path,
    ai: Path,
    task_dir: Path,
    base: str,
    git_fn: Callable[[Path, list[str]], str],
    prompt_pair_fn: Callable[..., list] | None = None,
) -> dict[str, list[str]]:
    targets = extract_glob_tokens(task_list(data.get("target_files")))
    forbidden = extract_glob_tokens(task_list(data.get("forbidden_files")))
    exempted = manifest_exemptions(root, ai, task_dir, data, change_paths, base, git_fn, prompt_pair_fn)
    target_violations = sorted(
        path for path in change_paths
        if path not in exempted and targets and not matches_any(path, targets)
    )
    forbidden_violations = sorted(
        path for path in change_paths
        if path not in exempted and matches_any(path, forbidden)
    )
    artifact_violations = changed_task_artifact_violations(root, change_paths, base, git_fn)
    forbidden_violations.extend(f"task-artifact: {violation}" for violation in artifact_violations)
    return {
        "target_violations": target_violations,
        "forbidden_violations": forbidden_violations,
        "manifest_exemptions": sorted(exempted),
    }


def parse_name_status_z(payload: str, origin: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    tokens = payload.split("\0")
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            if index + 1 >= len(tokens):
                break
            old_path, new_path = tokens[index], tokens[index + 1]
            index += 2
            entries.append({
                "origin": origin,
                "status": status,
                "old_path": old_path.replace("\\", "/"),
                "path": new_path.replace("\\", "/"),
            })
        else:
            if index >= len(tokens):
                break
            path = tokens[index]
            index += 1
            if not path:
                continue
            entries.append({
                "origin": origin,
                "status": status,
                "old_path": None,
                "path": path.replace("\\", "/"),
            })
    return entries


def collect_change_set(
    root: Path,
    base: str,
    git_fn: Callable[[Path, list[str]], str] | None = None,
) -> dict[str, Any]:
    git_fn = git_fn or git_output
    if not base or any(ch.isspace() for ch in base):
        raise GitError(f"Invalid base ref: {base!r}")
    try:
        git_fn(root, ["cat-file", "-e", f"{base}^{{commit}}"])
    except GitError as error:
        raise GitError(f"Invalid base commit '{base}': {error}") from error

    head_start = git_fn(root, ["rev-parse", "HEAD"]).strip()

    entries: list[dict[str, Any]] = []
    entries += parse_name_status_z(
        git_fn(root, ["diff", "--name-status", "-z", base, head_start]), "committed")
    entries += parse_name_status_z(
        git_fn(root, ["diff", "--name-status", "-z", "--cached", head_start]), "staged")
    entries += parse_name_status_z(
        git_fn(root, ["diff", "--name-status", "-z"]), "unstaged")
    for token in git_fn(
        root, ["ls-files", "--others", "--exclude-standard", "-z"]
    ).split("\0"):
        if token:
            entries.append({
                "origin": "untracked",
                "status": "?",
                "old_path": None,
                "path": token.replace("\\", "/"),
            })

    head_end = git_fn(root, ["rev-parse", "HEAD"]).strip()
    if head_end != head_start:
        raise GitError(
            f"HEAD moved during change-set discovery ({head_start} -> {head_end}); retry on a stable HEAD."
        )

    entries.sort(key=lambda e: (e["path"], e["old_path"] or "", e["origin"], e["status"]))
    fingerprint_payload = json.dumps(
        {"base": base, "head": head_start, "entries": entries},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "base_commit": base,
        "head_commit": head_start,
        "entries": entries,
        "paths": sorted({e["path"] for e in entries} | {e["old_path"] for e in entries if e["old_path"]}),
        "diff_fingerprint": hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest(),
    }


def is_control_plane_path(path: str, control_plane_prefixes: list[str]) -> bool:
    """A path is control-plane-managed only if it is exactly, or under, one of the declared
    control-plane prefixes (`.ai/`, the generated adapters, and any repo-declared framework
    paths). Everything else — product/stack code, or anything unclassifiable — is NOT
    control-plane and therefore requires an evidence gate."""
    for prefix in control_plane_prefixes:
        pre = prefix.rstrip("/")
        if path == pre or path.startswith(pre + "/"):
            return True
    return False


def control_plane_verify(
    args: Any,
    *,
    root: Path,
    ai: Path,
    effective_scopes: list[str],
    control_plane_prefixes: list[str],
    git_fn: Callable[[Path, list[str]], str] | None = None,
    prompt_pair_fn: Callable[..., list] | None = None,
) -> None:
    """Run the gate-agnostic control-plane checks when no evidence gate is registered.

    The core can only honor genuinely control-plane work. It fails closed, with a named reason,
    and — critically — it does NOT trust the task's declared scope label: it CLASSIFIES every
    changed path. A path is control-plane only if it is under a declared control-plane prefix
    (`.ai/`, generated adapters, declared framework paths). It fails closed on:
      - an unknown scope value (outside the effective vocabulary);
      - ANY changed path that is not control-plane-classified — a product/stack/unclassifiable
        path, or a mixed change set — regardless of the declared label (a mislabeled
        `control-plane` task touching product code cannot mask the stack change); and
      - a declared scope other than `control-plane` (the task itself asserts a gate is required).
    Only a change set that is entirely control-plane-classified AND declares `control-plane`
    scope proceeds to the contract check; even then no build/test evidence is produced and none
    is claimed. This is honest degradation by classification, never a label-trusting mask."""
    git_fn = git_fn or git_output
    try:
        task_dir, data = find_task(ai, args.task_id)
        change_set = collect_change_set(root, args.base, git_fn)
    except VerifyError as error:
        die(str(error))

    raw_scope = data.get("verification_scope")
    scope = raw_scope if raw_scope not in (None, "") else "affected-plus-neighbors"
    if scope not in effective_scopes:
        die(
            f"unknown-scope: verification_scope '{scope}' is not in the effective vocabulary "
            f"({', '.join(effective_scopes)}); refusing to guess."
        )

    # Authoritative safety gate: classify the ACTUAL changed paths, never the declared label.
    non_control_plane = sorted(
        p for p in change_set["paths"] if not is_control_plane_path(p, control_plane_prefixes)
    )
    if non_control_plane:
        die(
            "missing-evidence-gate: "
            f"{len(non_control_plane)} changed path(s) are not control-plane-managed and require a "
            "registered evidence gate, but none is enabled — refusing to report a control-plane "
            f"pass (would mask a stack change): {non_control_plane}"
        )
    if scope != "control-plane":
        die(
            f"missing-evidence-gate: verification_scope '{scope}' requires a registered evidence "
            "gate, but none is enabled. Declare verification_scope: control-plane for docs/"
            "framework-only work, or enable a gate in .ai/config.yaml extensions.enabled. Refusing "
            "to report a control-plane pass for stack-relevant scope."
        )

    try:
        check = contract_check(
            change_set["paths"], data,
            root=root, ai=ai, task_dir=task_dir, base=args.base, git_fn=git_fn,
            prompt_pair_fn=prompt_pair_fn,
        )
    except VerifyError as error:
        die(str(error))
    violations = check["target_violations"] + check["forbidden_violations"]

    if getattr(args, "plan", False):
        payload = {
            "mode": "plan",
            "task_id": args.task_id,
            "gate": None,
            "verification_scope": scope,
            "control_plane_prefixes": sorted(p.rstrip("/") for p in control_plane_prefixes),
            "base_commit": change_set["base_commit"],
            "head_commit": change_set["head_commit"],
            "diff_fingerprint": change_set["diff_fingerprint"],
            "change_set": change_set["entries"],
            "contract_check": check,
            "selection": None,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        if violations:
            die(f"Contract check failed for changed paths: {violations}")
        return

    if violations:
        die(f"Contract check failed: {violations}")
    print(
        "No evidence gate is registered for this project; ran the control-plane contract "
        "check only. No build or test evidence was produced."
    )
    print("Control-plane verification passed.")
