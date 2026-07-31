#!/usr/bin/env python3
"""AI Control Plane CLI compatibility facade and entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repository root is on sys.path when executed directly
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.ai_docs as ai_docs
import scripts.ai_plane.auto_dispatch as auto_dispatch
import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
import scripts.ai_plane.extension_commands as extension_commands
import scripts.ai_plane.skills as skills_module
import scripts.extension_registry as extension_registry
from scripts.ai_plane.blueprint import blueprint_spec_template, cmd_blueprint_build, cmd_blueprint_init
from scripts.ai_plane.config import (
    ADAPTERS, COMMAND_TOOL_DEFAULTS, GENERATED_DISPATCH_ROOT, MINIMUM_PYTHON, RISKS,
    TASK_CONTRACT_VOCABULARY, TOOL_DEFAULTS, TOOL_NOTES, TOOL_ROLES, TOOLS,
    ConfigError, adapter_contract_paths, configured_command, ensure_dirs,
    generated_dispatch_root, initialize_runtime_config, load_tool_registry,
    parse_config_yaml, strip_yaml_inline_comment,
)
from scripts.ai_plane.constants import AI, BLUEPRINT_DIR, CATALOG_NOTE, ROOT, WARNING
from scripts.ai_plane.doctor import (
    _load_rust_verify, collect_doctor_checks, cmd_doctor, doctor_check,
    inspect_doctor_lock, resolve_effective_scopes, resolve_registry,
)
from scripts.ai_plane.frontmatter import parse_frontmatter, render_catalog, summarize_md
from scripts.ai_plane.impact_bridge import cmd_impact
from scripts.ai_plane.manifest import (
    MANIFEST_SCHEMA_VERSION, GenerationSession, atomic_write_manifest,
    generation_session, manifest_path, parse_manifest_bytes, platform_text_bytes,
    read_generated_manifest, serialize_manifest, sha256_bytes, write_generated, write_generated_bytes,
)
from scripts.ai_plane.prompts import (
    adapter_invocation, cmd_archive, cmd_dispatch, cmd_learn, cmd_plan, cmd_qa,
    cmd_research, cmd_review, format_bullets, format_text_bullets,
    generated_prompt_pairs, prompt_common, write_dispatch_record,
)
from scripts.ai_plane.registry import REGISTRY_SCHEMA_VERSION, generate_registry, registry_path
from scripts.ai_plane.route import add_route_parser, cmd_route_explain
from scripts.ai_plane.route_apply import cmd_route_apply
from scripts.ai_plane.sync import (
    cmd_audit_framework, cmd_sync, compose_pack_content, render_adapter, resolve_command_tokens,
)
from scripts.ai_plane.routing_profile import add_tools_parser, cmd_tools_configure, cmd_tools_list, cmd_tools_status
from scripts.ai_plane.tasks import (
    TASK_STATES, add_creation_routing_arguments, all_task_files, cmd_feature_new,
    cmd_merge, cmd_task_show, cmd_tasks, create_task, find_task, format_inventory_value,
    format_rejected_value, git_value, next_task_id, parse_simple_yaml,
    task_contract_vocabulary_violations, task_list, write_simple_yaml, yaml_scalar,
)
from scripts.ai_plane.utils import (
    configure_utf8_stdio, die, generated_markdown, now_iso, read_md_dir, read_text, rel, slugify,
)



def _get_rust_verify():
    mod = sys.modules.get(__name__)
    func = getattr(mod, "_load_rust_verify", _load_rust_verify)
    try:
        return func()
    except (extension_registry.RegistryError, config_module.ConfigError) as error:
        # Fail closed with the named reason; never silently fall back to a default roster.
        die(f"Extension registry error: {error}")


def _load_verify_contract():
    """Lazily load the control-plane contract/degradation layer (scripts/verify_contract.py).
    Imported at call time to keep module load order simple."""
    import scripts.verify_contract as verify_contract

    return verify_contract


def _get_effective_scopes():
    mod = sys.modules.get(__name__)
    func = getattr(mod, "resolve_effective_scopes", resolve_effective_scopes)
    try:
        return func()
    except (extension_registry.RegistryError, config_module.ConfigError) as error:
        die(f"Extension registry error: {error}")


def _control_plane_prefixes() -> list[str]:
    """The repo-relative path prefixes the control plane manages: the `.ai/` tree, the generated
    adapters (from the config adapter registry), and any framework paths the project declares in
    `defaults.control_plane_paths`. The no-gate degradation path treats everything else as
    stack/product that requires an evidence gate. Computed here (with config access) and passed
    into the config-free substrate."""
    prefixes = {".ai"}
    for path in config_module.adapter_contract_paths():
        prefixes.add(path.rstrip("/"))
    try:
        parsed = config_module.parse_config_yaml(constants.AI / "config.yaml")
        declared = (parsed.get("defaults") or {}).get("control_plane_paths") or []
        for entry in declared:
            if isinstance(entry, str) and entry:
                prefixes.add(entry.rstrip("/"))
    except config_module.ConfigError as error:
        die(f"config error while resolving control-plane paths: {error}")
    return sorted(prefixes)


def cmd_verify(args: argparse.Namespace) -> None:
    gate = _get_rust_verify()
    if gate is None:
        # No evidence gate registered: degrade to control-plane-only verification, which
        # CLASSIFIES the changed paths (not the declared label) and fails closed on any
        # non-control-plane or unknown scope, so it cannot mask a stack change.
        scopes = _get_effective_scopes()
        _load_verify_contract().control_plane_verify(
            args, root=constants.ROOT, ai=constants.AI, effective_scopes=scopes,
            control_plane_prefixes=_control_plane_prefixes(),
            prompt_pair_fn=generated_prompt_pairs,
        )
        return
    gate.cmd_verify(args, root=constants.ROOT, ai=constants.AI)


def cmd_cargo(args: argparse.Namespace) -> None:
    gate = _get_rust_verify()
    if gate is None:
        die(
            "No 'rust' evidence gate is registered; `ai cargo` requires the rust extension "
            "enabled in .ai/config.yaml (extensions.enabled)."
        )
    gate.cmd_cargo(args, root=constants.ROOT, ai=constants.AI)


def cmd_cargo_cache(args: argparse.Namespace) -> None:
    gate = _get_rust_verify()
    if gate is None:
        die(
            "No 'rust' evidence gate is registered; `ai cargo-cache` requires the rust "
            "extension enabled in .ai/config.yaml (extensions.enabled)."
        )
    gate.cmd_cargo_cache(args, root=constants.ROOT, ai=constants.AI)


def cmd_ext(args: argparse.Namespace) -> None:
    extension_commands.cmd_ext(
        args,
        resolve_registry=resolve_registry,
        compose_pack_content=compose_pack_content,
        root=constants.ROOT,
        config_error=config_module.ConfigError,
        die=die,
    )


def cmd_init(_args: argparse.Namespace) -> None:
    ensure_dirs()
    print("Initialized .ai control-plane directories.")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai", description="Agent Control Plane unified CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Ensure .ai directory structure exists")
    sub.add_parser("sync", help="Generate every adapter declared in .ai/config.yaml")
    sub.add_parser("audit-framework", help="Show the migration audit report path")
    sub.add_parser("doctor", help="Read-only environment and scaffold diagnostics")
    impact = sub.add_parser(
        "impact", help="Query the optional sufficient-or-silent blast-radius advisory"
    )
    impact.add_argument("symbol", help="Exact indexed symbol to query")
    impact.add_argument(
        "--manifest", default="project/Cargo.toml", help="Workspace Cargo manifest"
    )
    impact.add_argument(
        "--database", default=".ai/.local/ai-impact.sqlite", help="Existing advisory index"
    )
    impact.add_argument(
        "--content-audit", action="store_true", help="Hash all indexed Rust sources"
    )

    feature = sub.add_parser("feature", help="Feature helpers")
    feature_sub = feature.add_subparsers(dest="feature_command", required=True)
    f_new = feature_sub.add_parser("new", help="Scaffold a feature planning task brief")
    f_new.add_argument("title", help="Feature title")

    research = sub.add_parser("research", help="Create a research task brief")
    research.add_argument("topic", help="Topic to research")
    research.add_argument("--tool", choices=config_module.TOOLS)

    plan = sub.add_parser("plan", help="Create a feature planning task brief")
    plan.add_argument("feature", help="Feature name")
    plan.add_argument("--tool", choices=config_module.TOOLS)
    add_creation_routing_arguments(f_new, research, plan)

    add_tools_parser(sub)
    add_route_parser(sub)

    sub.add_parser("tasks", help="List all task contracts across states")

    show = sub.add_parser("task", help="Inspect one task contract")
    show.add_argument("task_id", help="Task ID or directory name")

    ext = sub.add_parser("ext", help="Extension capabilities: list the resolved composition or run a command capability")
    ext_sub = ext.add_subparsers(dest="ext_command", required=True)
    ext_sub.add_parser("list", help="List the resolved extension composition, scopes, commands, and capabilities with origins")
    ext_run = ext_sub.add_parser("run", help="Run a registered command capability via argv (shell disabled)")
    ext_run.add_argument("name", help="Registered command name")
    ext_run.add_argument("args", nargs="*",
                         help="Caller arguments are rejected (unexpected-command-arguments); a "
                              "command's declared args are its complete invocation convention")

    skills_module.add_skills_parser(sub)

    from scripts.ai_plane.usage import add_usage_parser
    add_usage_parser(sub)

    dispatch = sub.add_parser("dispatch", help="Render dispatch prompt for an executor")
    dispatch.add_argument("task_id", help="Task ID or directory name")
    dispatch.add_argument("--tool", choices=config_module.TOOLS)
    dispatch.add_argument(
        "--auto",
        action="store_true",
        help="Also launch the assigned tool via its configured dispatch descriptor, if the project enables auto_dispatch",
    )

    review = sub.add_parser("review", help="Render review prompt for a reviewer")
    review.add_argument("task_id", help="Task ID or directory name")
    review.add_argument("--tool", choices=config_module.TOOLS)

    qa = sub.add_parser("qa", help="Scaffold QA receipt template")
    qa.add_argument("task_id", help="Task ID or directory name")
    qa.add_argument("--force", action="store_true", help="Overwrite existing receipt template")

    merge = sub.add_parser("merge", help="Check QA acceptance and merge gate criteria")
    merge.add_argument("task_id", help="Task ID or directory name")
    merge.add_argument("--approved", action="store_true", help="Acknowledge explicit owner approval for high risk")

    learn = sub.add_parser("learn", help="Append knowledge_to_capture entries from QA receipt into lessons.md")
    learn.add_argument("task_id", help="Task ID or directory name")

    archive = sub.add_parser("archive", help="Move all done tasks for a feature to archive/")
    archive.add_argument("feature", help="Feature name or slug")

    verify = sub.add_parser("verify", help="Run serialized Rust verification pipeline")
    verify.add_argument("task_id", help="Task ID or directory name")
    verify.add_argument("--base", required=True, help="Base commit or branch for evidence check")
    verify_mode = verify.add_mutually_exclusive_group(required=True)
    verify_mode.add_argument("--plan", action="store_true", help="Print verification steps without executing")
    verify_mode.add_argument("--run", action="store_true", help="Execute verification steps and write evidence record")

    cargo = sub.add_parser("cargo", help="Run a task-attributed cargo command under the verification lock")
    cargo.add_argument("task_id", help="Task ID or directory name")
    cargo.add_argument("--base", required=True, help="Base commit or branch for evidence check")
    cargo.add_argument("--label", default="adhoc", help="Label for evidence logging")
    cargo.add_argument("cargo_argv", nargs="*", help="Cargo subcommand and options")

    cargo_cache = sub.add_parser("cargo-cache", help="Manage cargo target/cache directories")
    cache_sub = cargo_cache.add_subparsers(dest="cargo_cache_command", required=True)
    cache_sub.add_parser("inspect", help="Inspect workspace and scratch cargo caches")
    clean = cache_sub.add_parser("clean", help="Clean scratch and/or workspace cargo caches")
    clean.add_argument("--scratch", action="store_true", help="Clean scratch Cargo cache directories")
    clean.add_argument("--workspace", action="store_true", help="Clean main target directory")
    clean.add_argument("--yes", action="store_true", help="Confirm deletion without prompting")

    blueprint = sub.add_parser("blueprint", help="PR Blueprint specification tools")
    bp_sub = blueprint.add_subparsers(dest="blueprint_command", required=True)
    bp_init = bp_sub.add_parser("init", help="Create a new PR Blueprint specification")
    bp_init.add_argument("feature", nargs="?", help="Feature name (omit with --from-task)")
    bp_init.add_argument("--from-task", help="Pre-fill from an exact task id in queue, active, or done")
    bp_init.add_argument("--base", help="Optional Git base for file inventory; requires --from-task")
    bp_init.add_argument("--preset", default="fullstack", choices=["frontend", "backend", "fullstack", "api-only", "engine", "tool"])
    bp_init.add_argument("--kind", default="app")
    bp_init.add_argument("--force", action="store_true", help="Overwrite existing spec")
    bp_build = bp_sub.add_parser("build", help="Build HTML report from a blueprint spec")
    bp_build.add_argument("spec", help="Path to blueprint spec Markdown file")
    bp_build.add_argument("--out", help="Output HTML file path")

    ai_docs.add_docs_parser(sub)

    return parser

# Commands that forward everything after `--` to another tool, and the attribute each stores it in.
PASSTHROUGH_DEST = {"cargo": "cargo_argv", "ext": "args"}


def split_passthrough(raw: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first `--`, before argparse sees it.

    argparse's treatment of tokens after `--` inside an `nargs="*"` positional changed between
    Python 3.11 and 3.12: on 3.11 `ai cargo t --base b -- clippy --locked` fails with
    "unrecognized arguments: --locked", while 3.12 accepts it. Splitting here gives the CLI one
    contract on every supported version instead of inheriting argparse's.
    """
    if "--" not in raw:
        return raw, []
    index = raw.index("--")
    return raw[:index], raw[index + 1:]


def main(argv: list[str] | None = None) -> None:
    configure_utf8_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    raw, passthrough = split_passthrough(raw)
    try:
        initialize_runtime_config()
    except ConfigError as error:
        roster_free_commands = {"verify", "cargo", "cargo-cache", "audit-framework", "doctor", "impact", "blueprint", "docs"}
        if not raw or raw[0] not in roster_free_commands:
            die(str(error))
    parser = build_parser()
    args = parser.parse_args(raw)

    if passthrough:
        dest = PASSTHROUGH_DEST.get(getattr(args, "command", ""))
        if dest is None:
            die(f"'{getattr(args, 'command', '')}' takes no arguments after '--'")
        setattr(args, dest, [*(getattr(args, dest, None) or []), *passthrough])

    if args.command == "init":
        cmd_init(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "audit-framework":
        cmd_audit_framework(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "impact":
        cmd_impact(args)
    elif args.command == "tools":
        {"list": cmd_tools_list, "status": cmd_tools_status, "configure": cmd_tools_configure}[args.tools_command](args)
    elif args.command == "route" and args.route_command == "explain":
        cmd_route_explain(args)
    elif args.command == "route" and args.route_command == "apply":
        cmd_route_apply(args)
    elif args.command == "feature" and args.feature_command == "new":
        cmd_feature_new(args)
    elif args.command == "research":
        cmd_research(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "tasks":
        cmd_tasks(args)
    elif args.command == "task":
        cmd_task_show(args)
    elif args.command == "ext":
        cmd_ext(args)
    elif args.command == "usage":
        from scripts.ai_plane.usage import cmd_usage_show

        cmd_usage_show(args, die=die)
    elif args.command == "skills":
        skills_module.cmd_skills(args, root=ROOT, ai=AI, die=die)
    elif args.command == "dispatch":
        cmd_dispatch(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "qa":
        cmd_qa(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "learn":
        cmd_learn(args)
    elif args.command == "archive":
        cmd_archive(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "cargo":
        cmd_cargo(args)
    elif args.command == "cargo-cache":
        cmd_cargo_cache(args)
    elif args.command == "blueprint":
        if args.blueprint_command == "init":
            cmd_blueprint_init(args)
        elif args.blueprint_command == "build":
            cmd_blueprint_build(args)
    elif args.command == "docs":
        ai_docs.cmd_docs(args)


if __name__ == "__main__":
    main()
