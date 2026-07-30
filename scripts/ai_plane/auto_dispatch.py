from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
from scripts.ai_plane.utils import rel


class LaunchOutcome(NamedTuple):
    """Result of one automatic-dispatch attempt, or the reason none was made."""

    lane: str  # "manual" | "auto-exec" | "auto-deeplink"
    attempted: bool
    success: bool
    detail: str
    argv: list[str] | None
    url: str | None


def placeholder_context(task_id: str, tool: str, prompt_path: Path, prompt_text: str) -> dict[str, str]:
    prompt_rel = rel(prompt_path)
    return {
        "task_id": task_id,
        "tool": tool,
        "prompt_path": prompt_rel,
        "prompt_text": prompt_text,
        "prompt_encoded": quote(prompt_text, safe=""),
        "prompt_path_encoded": quote(prompt_rel, safe=""),
    }


def render_template(template: str, context: dict[str, str]) -> str:
    """Substitute only known `{name}` tokens; never evaluates format-spec/attribute access."""

    def replace(match: Any) -> str:
        return context[match.group(1)]

    return config_module.PLACEHOLDER_PATTERN.sub(replace, template)


def attempt_exec_launch(argv_template: list[str], context: dict[str, str]) -> LaunchOutcome:
    argv = [render_template(item, context) for item in argv_template]
    try:
        result = subprocess.run(argv, cwd=constants.ROOT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return LaunchOutcome("auto-exec", True, False, f"launch failed: {error}", argv, None)
    if result.returncode != 0:
        return LaunchOutcome("auto-exec", True, False, f"exited with code {result.returncode}", argv, None)
    return LaunchOutcome("auto-exec", True, True, "launched", argv, None)


def attempt_deeplink_launch(url_template: str, context: dict[str, str]) -> LaunchOutcome:
    url = render_template(url_template, context)
    try:
        opened = webbrowser.open(url)
    except Exception as error:  # webbrowser backends raise assorted OS-specific errors
        return LaunchOutcome("auto-deeplink", True, False, f"launch failed: {error}", None, url)
    if not opened:
        return LaunchOutcome("auto-deeplink", True, False, "no deeplink handler available", None, url)
    return LaunchOutcome("auto-deeplink", True, True, "launched", None, url)


def perform_auto_dispatch(tool: str, task_id: str, prompt_path: Path, prompt_text: str) -> LaunchOutcome:
    """Launch the assigned tool via its configured descriptor, or report why not.

    Never raises: a disabled gate, an absent descriptor, or a failed launch all return a
    non-attempted or unsuccessful outcome so the caller can fall back to the manual handoff
    that was already written to disk.
    """
    if not config_module.AUTO_DISPATCH_ENABLED:
        return LaunchOutcome(
            "manual", False, False, "auto_dispatch is disabled by project config (defaults.auto_dispatch: false)", None, None
        )
    descriptor = config_module.DISPATCH_DESCRIPTORS.get(tool)
    if descriptor is None:
        return LaunchOutcome("manual", False, False, f"tool '{tool}' has no configured dispatch descriptor", None, None)

    context = placeholder_context(task_id, tool, prompt_path, prompt_text)
    if "exec" in descriptor:
        return attempt_exec_launch(descriptor["exec"]["argv"], context)
    return attempt_deeplink_launch(descriptor["deeplink"]["url_template"], context)
