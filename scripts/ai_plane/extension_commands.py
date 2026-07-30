"""CLI handlers for extension registry commands."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

import scripts.extension_registry as extension_registry


def cmd_ext(
    args: argparse.Namespace,
    *,
    resolve_registry: Callable[[], Any],
    compose_pack_content: Callable[[Any], tuple[Any, Any, Any, Any]],
    root: Any,
    config_error: type[Exception],
    die: Callable[[str], None],
) -> None:
    """Explain resolved extensions or run one registered command capability."""
    try:
        resolved = resolve_registry()
    except (extension_registry.RegistryError, config_error) as error:
        die(f"Extension registry error: {error}")
    if args.ext_command == "list":
        _files, _docs, content_report, _superseded = compose_pack_content(resolved)
        print(
            json.dumps(
                extension_registry.resolver_report(resolved, content_report=content_report),
                indent=2,
                sort_keys=True,
            )
        )
        return
    try:
        code = extension_registry.run_command_capability(
            resolved,
            args.name,
            root,
            args.args,
        )
    except extension_registry.RegistryError as error:
        die(str(error))
    if code:
        raise SystemExit(code)
