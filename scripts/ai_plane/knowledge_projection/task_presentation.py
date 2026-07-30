"""Human-facing task semantics derived without exposing execution locators.

The task source projection remains the lossless audit boundary.  This module
only builds the deliberately smaller object consumed by non-Source reader
surfaces.  It never repairs, summarizes, or redacts legacy contract prose.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import re
from typing import Any, Iterable, Mapping


PRESENTATION_SCHEMA_VERSION = "1"
PRESENTATION_REQUIRED_KEYS = (
    "presentation_schema_version",
    "presentation_purpose",
    "presentation_outcome",
    "presentation_scope",
    "presentation_acceptance",
)
PRESENTATION_OPTIONAL_KEYS = ("presentation_out_of_scope",)
PRESENTATION_KEYS = PRESENTATION_REQUIRED_KEYS + PRESENTATION_OPTIONAL_KEYS


class TaskPresentationError(ValueError):
    """Raised when an authored presentation namespace cannot be rendered safely."""


_GOVERNED_SOURCE_ROOTS = frozenset({
    ".agents", ".ai", ".cargo", ".claude", ".git", ".github", ".scratch",
    "app", "apps", "archive", "artifacts", "assets", "benches", "benchmark", "build",
    "crates", "docs", "fixtures", "include", "modules", "orbital", "packages",
    "plugins", "project", "public", "references", "resources", "schemas",
    "scripts", "src", "stacks", "target", "test", "tests", "tools", "vendor",
})
_PATH_TOKEN = re.compile(
    r"(?x)(?<![A-Za-z0-9_.\\/:-])"
    r"(?P<path>"
    r"(?:\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_.*?{},\[\]-]+[\\/])+"
    r"[A-Za-z0-9_.*?{},\[\]-]*"
    r")"
    r"(?![A-Za-z0-9_.-])"
)
_PATH_CONTEXT = re.compile(
    r"(?ix)(?:"
    r"\b(?:inspect|update|edit|read|write|change|modify|replace|remove|"
    r"create|review|compare|check|affects?|path|file|folder|directory)"
    r"(?:\s+the)?(?:\s+|:\s*)|"
    r"\blives\s+(?:in|under)\s+|"
    r"`"
    r")$"
)
_ABSOLUTE_PATH = re.compile(
    r"(?ix)(?:^|[\s(`'\"])"
    r"(?:"
    r"[a-z]:(?:[\\/]|(?=[^\\/\s]))|"
    r"~[\\/]|"
    r"(?:\$(?:HOME|USERPROFILE)|\$\{(?:HOME|USERPROFILE)\}|"
    r"%(?:HOME|USERPROFILE)%)[\\/]|"
    r"\\\\[^\\\s]+[\\/]|"
    r"/(?:home|users?|var|tmp|opt|workspace|repo|mnt|etc|usr|srv|root|"
    r"dev|bin|run|proc|volumes?|library|applications?|private|system|app|data)(?:/|$)"
    r")"
    r"[^\s`'\"),;]*"
)
_FILE_LOCATOR = re.compile(
    r"(?ix)(?:^|[\s(`'\"\\/])"
    r"(?:"
    r"(?:README|LICENSE|Makefile|Dockerfile)(?:\.[a-z0-9]+)?|"
    r"[a-z0-9_.-]+\.(?:"
    r"c|h|cc|cpp|cxx|hpp|hxx|cs|fs|fsx|go|java|kt|kts|swift|rb|php|scala|"
    r"lua|vue|svelte|py|pyi|rs|toml|ya?ml|json|md|txt|js|mjs|cjs|jsx|ts|"
    r"tsx|css|scss|html|xml|sql|sh|ps1|cmd|bat|lock|ini|cfg|conf|bazel|"
    r"gradle|properties|proto|graphql|gql|csv|tsv|wasm|png|jpe?g|webp|gif|"
    r"svg|mp4|webm|wav|mp3|ogg|mod|sum|nix|build|engine"
    r")"
    r")"
    r"(?::\d+(?::\d+)?|\#L\d+)?"
    r"(?=$|[\s`'\"),;:.])"
)
_GENERIC_FILE_LOCATOR = re.compile(
    r"(?ix)(?:^|[\s(`'\"\\/])"
    r"(?P<file>[a-z0-9][a-z0-9_-]*\.[a-z][a-z0-9]{0,15})"
    r"(?::\d+(?::\d+)?|\#L\d+)?"
    r"(?=$|[\s`'\"),;:.])"
)
_SPECIAL_FILE_LOCATOR = re.compile(
    r"(?x)(?:^|[\s(`'\"\\/])"
    r"(?:"
    r"(?i:\.(?:gitignore|gitattributes|editorconfig|dockerignore|npmrc|"
    r"prettierrc|env(?:\.[a-z0-9_.-]+)?|envrc|gitmodules))|"
    r"(?i:CODEOWNERS|Justfile|WORKSPACE|MODULE\.bazel|Procfile|Gemfile|"
    r"Pipfile|Rakefile|Jenkinsfile|rust-toolchain(?:\.toml)?)|"
    r"BUILD(?:\.bazel)?"
    r")"
    r"(?=$|[\s`'\"),;:.])"
)
_CODE_COMMAND = re.compile(r"`([^`\r\n]+)`")
_COMMAND_INTRODUCTION = re.compile(
    r"(?ix)(?P<intro>"
    r"\b(?:run|runs|execute|executes|invoke|invokes)"
    r"(?:\s+|:\s*)|"
    r"\b(?:use|uses)(?:\s+the\s+command)?(?:\s+|:\s*)|"
    r"\bcommand(?:\s+is|:)\s+|"
    r"\b(?:verify|validate)\s+(?:with|via)\s+|"
    r"\bvalidation\s+uses\s+|"
    r"\btests?\s+require\s+|"
    r"\bverification:\s+|"
    r"\bthen\s+"
    r")(?P<fragment>[^\r\n]+)"
)
_GENERIC_SHELL_PROMPT = re.compile(
    r"(?ix)^\s*(?:\([^)]+\)\s*)?(?P<prompt>"
    r"(?:[^@\s]+@[^$#\r\n]+[$#])|[$%]|"
    r"ps(?:\s+[^>\r\n]*)?>|\u276f"
    r")[ \t]+(?P<fragment>\S.*)$"
)
_MARKUP_SHELL_PROMPT = re.compile(
    r"^\s*(?:#|>{1,3}|[-*](?:\s+\[[ xX]\])?|[0-9]+\.)"
    r"\s+(?P<fragment>\S.*)$"
)
_QUOTED_COMMAND = re.compile(
    r"(?:\"([^\"\r\n]+)\"|'([^'\r\n]+)'|\*\*([^*\r\n]+)\*\*)"
)
_SHELL_OPERATOR = re.compile(r"(?:\|\||&&|(?<!\|)\|(?!\|))")
_TRAILING_CLAUSE = re.compile(r"[,;:]\s*(?P<fragment>[^,;:\r\n]+)$")
_EXECUTABLE_HEAD = re.compile(
    r"(?i)(?:\.{1,2}[\\/])?[a-z0-9_][a-z0-9_.+-]*$"
)
_COMMAND_ACTIONS = frozenset({
    "add", "apply", "assemble", "audit-framework", "bench", "blueprint",
    "branch", "build", "cat-file", "check", "checks", "checkout", "ci",
    "clean", "clippy", "commit", "compile", "compose", "config", "create",
    "delete", "deploy", "describe", "destroy", "diff", "dispatch", "docs",
    "down", "exec", "fetch", "fix", "fmt", "freeze", "get", "grep", "help",
    "init", "install", "inspect", "list", "log", "logs", "ls-files", "merge",
    "metadata", "migrate", "output", "package", "plan", "pr", "publish",
    "ps", "pull", "push", "qa", "rebase", "restore", "review", "rev-parse",
    "rollback", "route", "run", "save", "show", "status", "switch", "sync",
    "tag", "task", "test", "top", "tree", "uninstall", "up", "update", "upgrade",
    "validate", "verify", "version", "worktree", "dev", "format", "generate",
    "lint", "restart", "serve", "start", "stop", "watch",
})
_PRODUCT_COMMAND_NOUNS = frozenset({
    "and", "applications", "automation", "behavior", "charts", "content",
    "deployments", "ecosystem", "experience", "features", "integration",
    "integrations", "mode", "packages", "plans", "reporting", "results", "scripting",
    "support", "theme", "verification", "workflow", "workflows", "workers",
    "workloads", "tests",
})
_PRODUCT_LINK_WORDS = frozenset({"as", "for", "to", "with", "without"})
_NATURAL_SENTENCE_MARKERS = frozenset({
    "affordable", "available", "clear", "consistent", "discoverable", "easier",
    "healthy", "portable", "productive", "reliable", "supported",
    "understandable", "visible", "clearly", "consistently", "reliably",
    "safely",
})
_COMMAND_WRAPPERS = frozenset({"call", "cmd", "command", "doas", "env", "nohup", "start", "sudo", "time"})
_SHELL_BUILTIN_HEADS = frozenset({
    "cd", "cp", "dir", "echo", "export", "find", "grep", "ls", "mkdir",
    "mv", "printf", "pwd", "read", "rm", "rmdir", "set", "test", "touch",
    "type", "unset", "where", "which", "whoami",
})
_POWERSHELL_COMMAND_VERBS = frozenset({
    "add", "clear", "compare", "convert", "copy", "disable", "enable", "export",
    "find", "format", "get", "import", "invoke", "join", "measure", "move", "new",
    "out", "read", "remove", "rename", "reset", "restart", "select", "set",
    "show", "split", "start", "stop", "test", "update", "wait", "write",
})
_KNOWN_COMMAND_HEADS = frozenset({
    "ai", "ai.cmd", "bash", "bazel", "bun", "bundle", "cargo", "cmake",
    "composer", "curl", "deno", "docker", "docker-compose", "dotnet",
    "gh", "git", "go", "gradle", "gradlew", "helm", "http", "java",
    "javac", "just", "kubectl", "make", "meson", "mvn", "mvnw", "ninja",
    "node", "npm", "npx", "php", "pip", "pip3", "pnpm", "podman", "poetry",
    "powershell", "pwsh", "pytest", "python", "python3", "rg", "ruby", "ruff",
    "sh", "swift", "swiftc", "terraform", "uv", "wget", "xcodebuild", "yarn",
    "zsh",
})
_PROSE_CLAUSE_MARKERS = frozenset({
    "are", "explains", "explain", "helps", "help", "is", "provides", "provide",
    "remains", "remain", "reports", "report", "stays", "stay", "supports",
    "support", "was", "were",
})
_DOTTED_TECHNOLOGY_PROSE = re.compile(
    r"\b[A-Z][A-Za-z0-9+-]*\.(?:[Jj][Ss]|NET|Net|net)\b"
    r"(?=\s+(?:applications?|behavior|ecosystems?|features?|integrations?|support|terminology|"
    r"remains?|stays?|workers?))"
)


def _command_tokens(fragment: str) -> list[str]:
    normalized = fragment.strip().strip("`'\"*()[]{}").rstrip("`'\"*.,;:)]}")
    return [
        token.strip("`'\"*.,;:()[]{}")
        for token in re.findall(r"[\"'][^\"'\r\n]*[\"']|\S+", normalized)
        if token.strip("`'\"*.,;:()[]{}")
    ]


def _unwrap_command_tokens(tokens: list[str]) -> tuple[list[str], bool]:
    remaining = list(tokens)
    wrapped = False
    if remaining and remaining[0] == "&":
        wrapped = True
        remaining.pop(0)
    while remaining and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*=.*",
        remaining[0],
    ):
        wrapped = True
        remaining.pop(0)
    while remaining and remaining[0].casefold() in _COMMAND_WRAPPERS:
        wrapped = True
        wrapper = remaining.pop(0).casefold()
        if wrapper == "cmd":
            while remaining and re.fullmatch(r"(?i)/(?:a|c|d|k|q|s|u)", remaining[0]):
                remaining.pop(0)
            continue
        if wrapper in {"env", "sudo", "doas"}:
            while remaining and remaining[0].startswith("-"):
                remaining.pop(0)
            while remaining and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=.*",
                remaining[0],
            ):
                remaining.pop(0)
    return remaining, wrapped


def _has_command_syntax(tokens: list[str]) -> bool:
    return any(
        token.startswith(("-", "+"))
        or "://" in token
        or "=" in token
        or "\\" in token
        or "/" in token
        or token in {"|", "||", "&&", ">", ">>", "<"}
        for token in tokens
    )


def _command_fragment_is_invocation(
    fragment: str,
    *,
    context: str,
) -> bool:
    raw_tokens = _command_tokens(fragment)
    tokens, wrapped = _unwrap_command_tokens(raw_tokens)
    if not tokens or not _EXECUTABLE_HEAD.fullmatch(tokens[0]):
        return False
    head = tokens[0]
    head_lower = head.casefold().replace("\\", "/").rsplit("/", 1)[-1]
    tail = tokens[1:]
    lowered = [token.casefold() for token in tail]
    action_roots = {
        part
        for token in lowered[:3]
        for part in (token, token.split("-", 1)[0])
    }
    has_action = bool(action_roots & _COMMAND_ACTIONS)
    has_syntax = _has_command_syntax(raw_tokens)
    tail_coordinate = bool(tail) and (
        lowered[0] in _GOVERNED_SOURCE_ROOTS
        or lowered[0] in {".", ".."}
        or "/" in tail[0]
        or "\\" in tail[0]
        or bool(_GENERIC_FILE_LOCATOR.fullmatch(tail[0]))
    )
    sentence_like = any(
        token in _NATURAL_SENTENCE_MARKERS
        for token in (head_lower, *lowered)
    )
    title_predicate = (
        len(lowered) > 1
        and lowered[0].endswith("s")
        and lowered[0] not in _COMMAND_ACTIONS
    )
    prose_clause = sentence_like or title_predicate or any(
        token in _PROSE_CLAUSE_MARKERS for token in lowered[1:]
    )
    title_cased_head = head[:1].isupper() and not wrapped
    head_root = head_lower.split("-", 1)[0]
    shell_builtin = head_lower in _SHELL_BUILTIN_HEADS
    powershell_command = (
        "-" in head_lower and head_root in _POWERSHELL_COMMAND_VERBS
    )
    known_command = head_lower in _KNOWN_COMMAND_HEADS
    executable_shape = (
        head.startswith(("./", ".\\"))
        or head_lower.endswith((".bat", ".cmd", ".exe", ".ps1", ".sh"))
        or "-" in head_lower
    )

    if context == "prompt":
        return True
    if tail and not has_syntax:
        if lowered[0] in _PRODUCT_COMMAND_NOUNS:
            return False
        if context == "use" and lowered[0] in _PRODUCT_LINK_WORDS:
            return False
        if (
            context == "use"
            and lowered[0] == "version"
            and len(lowered) > 1
            and re.fullmatch(r"v?\d+(?:\.\d+)*", lowered[1])
        ):
            return False
        if (
            head_lower == "docker"
            and lowered[0] == "compose"
            and (
                len(lowered) == 1
                or any(token in _PRODUCT_LINK_WORDS for token in lowered[1:])
            )
        ):
            return False
    if (
        context == "quoted"
        and not known_command
        and not has_syntax
        and not tail_coordinate
    ):
        return False
    if title_cased_head:
        if not tail:
            return powershell_command
        return powershell_command or (
            known_command and (has_syntax or not prose_clause)
        )
    if not tail:
        return (
            wrapped
            or shell_builtin
            or powershell_command
            or known_command
            or (context == "introduced" and head == head_lower)
            or (executable_shape and context in {"code", "introduced", "use"})
        )
    if wrapped:
        return (
            known_command
            or shell_builtin
            or powershell_command
            or executable_shape
            or has_action
            or has_syntax
            or tail_coordinate
        )
    if prose_clause and not has_syntax:
        return False
    if known_command or shell_builtin or powershell_command:
        return True
    if context == "bare":
        return has_action or has_syntax or executable_shape or tail_coordinate
    if context in {"code", "introduced", "markup", "quoted", "use"}:
        return has_action or has_syntax or executable_shape or tail_coordinate
    return False


def _contains_exact_command(text: str) -> bool:
    for line in text.splitlines() or [text]:
        generic_prompt = _GENERIC_SHELL_PROMPT.match(line)
        if generic_prompt:
            prompt = generic_prompt.group("prompt")
            fragment = generic_prompt.group("fragment")
            if prompt == "$" and fragment[:1].isdigit():
                continue
            if prompt == "%" and fragment.casefold().startswith("complete "):
                continue
            if _command_fragment_is_invocation(fragment, context="prompt"):
                return True
        markup_prompt = _MARKUP_SHELL_PROMPT.match(line)
        if markup_prompt and _command_fragment_is_invocation(
            markup_prompt.group("fragment"),
            context="markup",
        ):
            return True
        if _command_fragment_is_invocation(line, context="bare"):
            return True
        trailing = _TRAILING_CLAUSE.search(line)
        if (
            trailing
            and not re.match(
                r"(?i)(?:and|or|but|for|with|without)\b",
                trailing.group("fragment"),
            )
            and _command_fragment_is_invocation(
                trailing.group("fragment"),
                context="bare",
            )
        ):
            return True
    if any(
        _command_fragment_is_invocation(match.group(1), context="code")
        for match in _CODE_COMMAND.finditer(text)
    ):
        return True
    for quoted in _QUOTED_COMMAND.finditer(text):
        fragment = next(value for value in quoted.groups() if value is not None)
        if _command_fragment_is_invocation(fragment, context="quoted"):
            return True
    for introduced in _COMMAND_INTRODUCTION.finditer(text):
        intro = introduced.group("intro").strip().casefold()
        context = "use" if intro.startswith(("use", "uses")) else "introduced"
        if _command_fragment_is_invocation(
            introduced.group("fragment"),
            context=context,
        ):
            return True
    return False


_HEX_REVISION = r"[0-9a-f]{7,40}"
_GIT_HEAD_ALIAS = (
    r"(?:HEAD|FETCH_HEAD|ORIG_HEAD|MERGE_HEAD|CHERRY_PICK_HEAD|AUTO_MERGE)"
)
_REF_SEGMENT = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9_-])?"
_REF_NAME = rf"(?:{_REF_SEGMENT}(?:/{_REF_SEGMENT})*)"
_REV_ATOM = rf"(?:{_GIT_HEAD_ALIAS}|@|{_HEX_REVISION}|{_REF_NAME})"
_REF_SUFFIX = (
    r"(?:~\d*|\^(?:-?\d+|!|@|\{[^}\r\n]*\})?|@\{[^}\r\n]+\})"
)
_REV_EXPRESSION = (
    rf"(?:"
    rf"{_REV_ATOM}(?:{_REF_SUFFIX})+|"
    rf"{_REV_ATOM}\.\.\.?{_REV_ATOM}|"
    rf"{_REV_ATOM}:[^\s`'\"]+"
    rf")"
)
_REMOTE_DEFAULT_BRANCH = (
    rf"{_REF_SEGMENT}/(?:main|master|trunk|develop|development|release|"
    rf"staging|production|prod)"
)
_BRANCH_PREFIX_REF = (
    rf"(?:feature|bugfix|hotfix|fix|release|chore|task)/{_REF_SEGMENT}"
    rf"(?:/{_REF_SEGMENT})*"
)
_REMOTE_NESTED_REF = (
    rf"(?:origin|upstream|fork)/(?:{_BRANCH_PREFIX_REF}|"
    rf"main|master|trunk|develop|development|release|staging|production|prod)"
)
_SLASH_REF = rf"{_REF_SEGMENT}/{_REF_SEGMENT}(?:/{_REF_SEGMENT})*"
_GIT_WHOLE = re.compile(
    rf"^(?:{_GIT_HEAD_ALIAS}|@|{_HEX_REVISION}|{_REV_EXPRESSION}|"
    rf"{_REMOTE_DEFAULT_BRANCH}|{_BRANCH_PREFIX_REF}|{_REMOTE_NESTED_REF})$",
    re.IGNORECASE,
)
_GIT_FULL_REF = re.compile(
    r"(?i)(?<![a-z0-9._/-])refs/[a-z0-9._/-]+"
    r"(?=$|[\s`'\"),;:.])"
)
_GIT_SPECIAL = re.compile(
    rf"(?ix)(?<![a-z0-9._/-])(?:"
    rf":/[^\s`'\"]+|"
    rf"\^(?:{_GIT_HEAD_ALIAS}|main|master|trunk|develop|release|staging)|"
    rf"@\{{[^}}\r\n]+\}}"
    rf")(?=$|[\s`'\"),;:.])"
)
_GIT_OPERATOR = re.compile(
    rf"(?ix)(?<![a-z0-9._/-])(?:"
    rf"{_GIT_HEAD_ALIAS}(?:{_REF_SUFFIX})+|"
    rf"(?:{_GIT_HEAD_ALIAS}|main|master):[a-z0-9._/-]+"
    rf")(?=$|[\s`'\"),;:.])"
)
_GIT_CONTEXT = re.compile(
    rf"(?ix)\b(?:inspect|compare|deploy|diff|read|use|write|update|checkout|"
    rf"revision|commit|sha(?:-?1)?|base|ref|branch|tag)\b"
    rf"\s*(?:(?:is|was|at|against|from|to|with)\s+|[=:]\s*)?"
    rf"(?:the\s+)?(?P<revision>"
    rf":/[^\s`'\"]+|"
    rf"\^(?:{_REF_NAME}|{_GIT_HEAD_ALIAS})|"
    rf"{_REV_EXPRESSION}|"
    rf"{_GIT_HEAD_ALIAS}|@|{_HEX_REVISION}|{_SLASH_REF}"
    rf")(?=$|[\s`'\"),;:.])"
)


def _contains_git_revision(text: str) -> bool:
    if (
        _GIT_FULL_REF.search(text)
        or _GIT_SPECIAL.search(text)
        or _GIT_OPERATOR.search(text)
    ):
        return True
    for match in _GIT_CONTEXT.finditer(text):
        revision = match.group("revision").replace("\\", "/").casefold()
        if revision.rstrip("/") not in _PRODUCT_SLASH_TERMS:
            return True
    for line in text.splitlines() or [text]:
        candidate = line.strip().strip("`'\"*()[]").rstrip(".,;")
        candidate = re.sub(
            r"^(?:[-*](?:\s+\[[ xX]\])?|[0-9]+\.|>{1,3})\s+",
            "",
            candidate,
        )
        if _GIT_WHOLE.fullmatch(candidate):
            return True
    return False

_NON_FILE_URI = re.compile(
    r"(?i)\b(?!file://)[a-z][a-z0-9+.-]*://[^\s`'\"]+"
)
_LINE_ANCHOR = re.compile(r"(?i)\b(?:line|lines)\s+\d+(?:\s*[-–]\s*\d+)?\b")
_FILE_URL = re.compile(r"(?i)\bfile://\S+")

_MEDIA_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
}


_PRODUCT_SLASH_TERMS = frozenset({
    "client/server",
    "include/exclude",
    "input/output",
    "origin/service",
    "read/write",
    "roles/admin",
    "source/configuration",
    "upstream/provider",
    "ui/ux",
})


def _contains_generic_file_locator(text: str) -> bool:
    whole = text.strip().strip("`'\"*()[]").rstrip(".,;:")
    for match in _GENERIC_FILE_LOCATOR.finditer(text):
        filename = match.group("file")
        if filename.casefold() == whole.casefold():
            return True
        prefix = text[:match.start("file")]
        if (
            _PATH_CONTEXT.search(prefix)
            or re.search(r"(?i)\bopen(?:\s+the)?\s+$", prefix)
        ):
            return True
    return False


def _contains_repository_locator(text: str) -> bool:
    if any(pattern.search(text) for pattern in (
        _ABSOLUTE_PATH,
        _FILE_LOCATOR,
        _SPECIAL_FILE_LOCATOR,
        _FILE_URL,
    )):
        return True
    if _contains_generic_file_locator(text):
        return True
    whole = text.strip().strip("`'\"*()[]").rstrip(".,;")
    for match in _PATH_TOKEN.finditer(text):
        raw_path = match.group("path")
        normalized = raw_path.replace("\\", "/")
        canonical = normalized.casefold().rstrip("/")
        segments = [segment for segment in canonical.split("/") if segment]
        if not segments or (len(segments) < 2 and not normalized.endswith("/")):
            continue
        prefix = text[:match.start()]
        contextual = bool(_PATH_CONTEXT.search(prefix))
        if canonical in _PRODUCT_SLASH_TERMS:
            continue
        if normalized.startswith(("./", "../")) or segments[0].startswith("."):
            return True
        if segments[0] in _GOVERNED_SOURCE_ROOTS:
            return True
        if contextual:
            return True
        is_whole = canonical == whole.replace("\\", "/").casefold().rstrip("/")
        if is_whole:
            return True
    return False


def contains_source_locator(text: str) -> bool:
    """Return whether text exposes a repository locator, command, or revision.

    The boundary is intentionally conservative around prose: ordinary
    slash-separated product terms and technology names such as ``Node.js`` are
    not rejected, while repository-aware paths, filenames, line anchors,
    executable command lines, file URLs, and Git revision expressions are.
    """
    if not isinstance(text, str) or not text:
        return False
    # Technology names are prose, not bare JavaScript filenames. Commands
    # inspect the original URL-bearing text because a URL can be an argument.
    # Locator and revision checks inspect a non-file-URI-free copy so remote
    # resource paths cannot masquerade as repository coordinates.
    candidate = _DOTTED_TECHNOLOGY_PROSE.sub("", text)
    if _contains_exact_command(candidate):
        return True
    locator_candidate = _NON_FILE_URI.sub("", candidate)
    return (
        _contains_git_revision(locator_candidate)
        or _contains_repository_locator(locator_candidate)
        or bool(_LINE_ANCHOR.search(locator_candidate))
    )


def _presentation_strings(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, str):
        yield "", value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield f"[{index}]", item


def presentation_contract_violations(
    contract: Mapping[str, Any],
) -> list[tuple[str, Any, tuple[str, ...]]]:
    """Validate the optional all-or-nothing human presentation namespace.

    This follows the repository task validator's stable violation tuple:
    ``(field, rejected_value, allowed_or_guidance)``.  A contract with no
    presentation-prefixed key is a valid legacy contract and returns no
    violations.
    """
    present = sorted(
        key for key in contract
        if isinstance(key, str) and key.startswith("presentation_")
    )
    if not present:
        return []

    allowed = tuple(PRESENTATION_KEYS)
    violations: list[tuple[str, Any, tuple[str, ...]]] = [
        (key, contract[key], allowed) for key in present if key not in PRESENTATION_KEYS
    ]
    for key in PRESENTATION_REQUIRED_KEYS:
        if key not in contract:
            violations.append((key, None, ("<required when presentation namespace is present>",)))

    version = contract.get("presentation_schema_version")
    if not isinstance(version, str) or version != PRESENTATION_SCHEMA_VERSION:
        violations.append((
            "presentation_schema_version",
            version,
            (PRESENTATION_SCHEMA_VERSION,),
        ))

    for key in ("presentation_purpose", "presentation_outcome"):
        value = contract.get(key)
        if not isinstance(value, str) or not value.strip():
            violations.append((key, value, ("<non-empty human semantic prose>",)))

    for key in (
        "presentation_scope",
        "presentation_acceptance",
        "presentation_out_of_scope",
    ):
        if key not in contract and key in PRESENTATION_OPTIONAL_KEYS:
            continue
        value = contract.get(key)
        if not isinstance(value, list) or (
            key not in PRESENTATION_OPTIONAL_KEYS and not value
        ):
            violations.append((key, value, ("<non-empty list of human semantic prose>",)))
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                violations.append((
                    f"{key}[{index}]",
                    item,
                    ("<non-empty human semantic prose>",),
                ))

    guidance = (
        "<human semantic prose without repository locators, exact commands, "
        "line anchors, file URLs, or revisions>",
    )
    for key in PRESENTATION_KEYS:
        if key == "presentation_schema_version" or key not in contract:
            continue
        for suffix, item in _presentation_strings(contract[key]):
            if isinstance(item, str) and contains_source_locator(item):
                violations.append((f"{key}{suffix}", item, guidance))
    for key in ("title", "feature"):
        value = contract.get(key)
        if isinstance(value, str) and contains_source_locator(value):
            violations.append((key, value, guidance))
    return violations


def _normalize_scope_locator(value: str) -> str:
    normalized = value.strip().strip("`'\"").rstrip(".").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _area_for(locator: str, task_slug: str) -> tuple[str, str] | None:
    normalized = _normalize_scope_locator(locator)
    if normalized.startswith(".ai/tasks/"):
        marker = f"/{task_slug}/"
        if marker in f"/{normalized}/":
            return "own-task-folder", "This task's own records"
        return "task-history", "Task history"
    fixed = (
        (".ai/rules/", ("control-plane-rules", "Control-plane rules")),
        (".ai/workflows/", ("control-plane-workflows", "Control-plane workflows")),
        (".ai/agents/", ("control-plane-agents", "Agent role definitions")),
        (".ai/project/", ("control-plane-docs", "Control-plane project knowledge")),
        (".ai/memory/", ("control-plane-memory", "Typed memory")),
        (".ai/skills/", ("control-plane-skills", "Skill packs")),
        (".ai/_site/", ("generated-reader", "Generated reader")),
        ("scripts/", ("control-plane-tooling", "Control-plane tooling")),
        ("project/docs/", ("product-docs", "Product documentation")),
        ("project/schemas/", ("product-schemas", "Product schemas")),
        ("project/generated/", ("product-generated", "Generated product output")),
        ("project/apps/", ("product-apps", "Product applications")),
    )
    for prefix, result in fixed:
        if normalized.startswith(prefix):
            return result
    if normalized.startswith("project/crates/"):
        parts = normalized.split("/")
        crate = parts[2].replace("**", "*") if len(parts) > 2 else "*"
        if not crate or "*" in crate:
            return "rust-crates", "Rust crates"
        return f"rust-crate:{crate}", f"Rust crate: {crate}"
    if normalized.startswith("project/"):
        return "product-source", "Product implementation"
    if normalized.startswith(".ai/"):
        return "control-plane-other", "Other control-plane records"
    return None


def _is_technical_scope_entry(value: str) -> bool:
    text = _normalize_scope_locator(value)
    return bool(
        text
        and (
            contains_source_locator(text)
            or "/" in text
            or "\\" in value
            or any(marker in text for marker in ("*", "{", "}", "[", "]"))
        )
    )


def _classified_areas(
    entries: Iterable[Any],
    task_slug: str,
) -> tuple[list[dict[str, Any]], int]:
    areas: dict[str, dict[str, Any]] = {}
    unmapped = 0
    for raw in entries:
        if not isinstance(raw, str) or not raw.strip():
            continue
        area = _area_for(raw, task_slug)
        if area is None:
            if _is_technical_scope_entry(raw):
                unmapped += 1
            continue
        key, label = area
        bucket = areas.setdefault(key, {"key": key, "label": label, "count": 0})
        bucket["count"] += 1
    return (
        sorted(areas.values(), key=lambda item: (-item["count"], item["label"], item["key"])),
        unmapped,
    )


def technical_footprint(contract: Mapping[str, Any], task_slug: str) -> dict[str, Any]:
    """Return stable area labels and source-only counts, never raw locators."""
    targets = contract.get("target_files")
    provisional = contract.get("provisional_target_files")
    target_entries = targets if isinstance(targets, list) else []
    target_provisional = not target_entries and isinstance(provisional, list) and bool(provisional)
    if target_provisional:
        target_entries = provisional
    forbidden = contract.get("forbidden_files")
    forbidden_entries = forbidden if isinstance(forbidden, list) else []
    touched, unmapped_targets = _classified_areas(target_entries, task_slug)
    off_limits, unmapped_forbidden = _classified_areas(forbidden_entries, task_slug)
    return {
        "touched": touched,
        "offLimits": off_limits,
        "unmappedTargetCount": unmapped_targets,
        "unmappedForbiddenCount": unmapped_forbidden,
        "provisional": target_provisional,
    }


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or contains_source_locator(value):
        return None
    return value


def _event_data(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("data")
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for kind in ("attempt", "round"):
        number = value.get(kind)
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            return {"kind": kind, "number": number}
    return None


def _context_dispositions(source: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    closeout = source.get("closeout")
    if not isinstance(closeout, Mapping):
        return {}
    rows = closeout.get("context_dispositions")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("context_item_id")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("context_item_id")
    }


def _context_summary(
    item: Mapping[str, Any],
    disposition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_only: list[str] = []
    result: dict[str, Any] = {
        "type": str(item.get("type") or ""),
        "blocking": item.get("blocking") if isinstance(item.get("blocking"), bool) else None,
        "severity": str(item.get("severity") or ""),
        "state": str(item.get("state") or ""),
        "sourceOnlyFields": source_only,
    }
    for source_key, output_key in (
        ("summary", "summary"),
        ("resolution", "resolution"),
        ("owner", "owner"),
    ):
        raw = item.get(source_key)
        text = _safe_text(raw)
        if text is not None:
            result[output_key] = text
        elif raw not in (None, ""):
            source_only.append(output_key)
    if disposition:
        result["disposition"] = str(disposition.get("disposition") or "")
        if "resolution" not in result:
            raw_rationale = disposition.get("rationale")
            rationale = _safe_text(raw_rationale)
            if rationale is not None:
                result["resolution"] = rationale
            elif (
                raw_rationale not in (None, "")
                and "resolution" not in source_only
            ):
                source_only.append("resolution")
        if "owner" not in result:
            raw_owner = disposition.get("owner")
            owner = _safe_text(raw_owner)
            if owner is not None:
                result["owner"] = owner
            elif raw_owner not in (None, "") and "owner" not in source_only:
                source_only.append("owner")
    return result


def receipt_summaries(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return each receipt once with only typed, locator-safe semantics."""
    events = source.get("receipt_events")
    typed_events = events if isinstance(events, list) else []
    dispositions = _context_dispositions(source)
    seen: set[tuple[Any, ...]] = set()
    summaries: list[dict[str, Any]] = []
    for index, event_value in enumerate(typed_events):
        if not isinstance(event_value, Mapping):
            continue
        data = _event_data(event_value)
        role = str(event_value.get("role") or event_value.get("legacy_role_hint") or "")
        sequence = _sequence(data.get("sequence") or event_value.get("sequence"))
        receipt_id = event_value.get("receipt_id") or data.get("receipt_id")
        if receipt_id:
            identity: Any = str(receipt_id)
        elif sequence:
            identity = (role, sequence["kind"], sequence["number"])
        else:
            identity = (role, "legacy", index)
        identity_key = (identity,) if not isinstance(identity, tuple) else identity
        if identity_key in seen:
            continue
        seen.add(identity_key)

        legacy = bool(event_value.get("legacy"))
        actor_data = data.get("actor") if isinstance(data.get("actor"), Mapping) else {}
        decision = data.get("decision") if isinstance(data.get("decision"), Mapping) else {}
        actor = _safe_text(actor_data.get("name")) or _safe_text(actor_data.get("family")) or ""
        source_only: list[str] = []
        if (
            not actor
            and any(actor_data.get(key) not in (None, "") for key in ("name", "family"))
        ):
            source_only.append("actor")
        summary: dict[str, Any] = {
            "role": role,
            "sequence": sequence,
            "actor": actor,
            "status": str(decision.get("status") or ""),
            "legacy": legacy,
            "context": [],
            "notes": [],
            "sourceOnlyFields": source_only,
            "sourceOnlyNoteCount": 0,
        }
        if not legacy:
            raw_outcome = decision.get("outcome")
            outcome = _safe_text(raw_outcome)
            if outcome is not None:
                summary["result"] = outcome
            elif raw_outcome not in (None, ""):
                source_only.append("result")
            context = data.get("context_items")
            if isinstance(context, list):
                summary["context"] = [
                    _context_summary(
                        item,
                        dispositions.get(str(item.get("context_item_id"))),
                    )
                    for item in context
                    if isinstance(item, Mapping)
                ]
            notes = data.get("notes")
            if isinstance(notes, list):
                for note in notes:
                    safe_note = _safe_text(note)
                    if safe_note is not None:
                        summary["notes"].append(safe_note)
                    else:
                        summary["sourceOnlyNoteCount"] += 1
        else:
            source_only.append("legacyReceipt")
        summaries.append(summary)
    return summaries


def _media_alias(task_id: str, evidence_id: str, media_type: str) -> str:
    digest = hashlib.sha256(
        f"{task_id}\0{evidence_id}".encode("utf-8")
    ).hexdigest()[:24]
    extension = _MEDIA_EXTENSIONS.get(media_type.lower(), ".bin")
    return f"assets/task-media/{digest}{extension}"


def _typed_evidence(source: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence_set = source.get("evidence_set")
    if not isinstance(evidence_set, Mapping) or evidence_set.get("schema_version") != 1:
        return {
            "items": [],
            "counts": {"total": 0, "available": 0, "unavailable": 0},
        }, []
    task_id = str(source.get("task_id") or "")
    resolutions = source.get("evidence_artifact_resolutions")
    resolutions = resolutions if isinstance(resolutions, Mapping) else {}
    source_items = evidence_set.get("items")
    source_items = source_items if isinstance(source_items, list) else []
    items: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    availability: Counter[str] = Counter()
    for item in source_items:
        if not isinstance(item, Mapping):
            continue
        state = str(item.get("availability") or "")
        availability[state] += 1
        source_only: list[str] = []
        projected: dict[str, Any] = {
            "kind": str(item.get("kind") or ""),
            "role": str(item.get("role") or ""),
            "availability": state,
            "sourceOnlyFields": source_only,
        }
        raw_claim = item.get("claim")
        claim = _safe_text(raw_claim)
        if claim is not None:
            projected["claim"] = claim
        elif raw_claim not in (None, ""):
            source_only.append("claim")
        raw_accessibility = item.get("accessibility_text")
        accessibility = _safe_text(raw_accessibility)
        if accessibility is not None:
            projected["accessibilityText"] = accessibility
        elif raw_accessibility not in (None, ""):
            source_only.append("accessibilityText")
        items.append(projected)

        if state != "available" or item.get("storage") != "committed":
            continue
        artifact = item.get("artifact")
        if not isinstance(artifact, Mapping):
            continue
        media_type = str(artifact.get("media_type") or "")
        if not media_type.startswith(("image/", "audio/", "video/")):
            continue
        recorded_path = str(artifact.get("path") or "")
        resolution = resolutions.get(recorded_path)
        if not isinstance(resolution, Mapping) or resolution.get("state") != "verified":
            continue
        evidence_id = str(item.get("evidence_id") or "")
        if not task_id or not evidence_id:
            continue
        width = artifact.get("width")
        height = artifact.get("height")
        dimensions = (
            {"width": width, "height": height}
            if isinstance(width, int) and isinstance(height, int)
            else None
        )
        alt = _safe_text(item.get("accessibility_text"))
        if alt is None:
            alt = (
                "Expected reference media."
                if item.get("kind") == "expected-reference"
                else "Evidence media."
            )
        media.append({
            "src": _media_alias(task_id, evidence_id, media_type),
            "kind": str(item.get("kind") or ""),
            "type": media_type,
            "dimensions": dimensions,
            "alt": alt,
        })
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "available": availability["available"],
            "unavailable": sum(
                count for key, count in availability.items() if key != "available"
            ),
        },
    }, media


def _humanized_task_id(task_id: str) -> str:
    label = re.sub(r"^task_\d+_", "", task_id, flags=re.IGNORECASE)
    label = re.sub(r"[_-]+", " ", label).strip()
    if not label:
        return "Legacy task"
    return label[:1].upper() + label[1:]


def _presentation_identity(
    source: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, str]:
    task_id = str(source.get("task_id") or contract.get("id") or "")
    raw_title = contract.get("title")
    safe_title = _safe_text(raw_title)
    if safe_title is not None:
        title = safe_title
        title_state = "recorded"
    else:
        title = _humanized_task_id(task_id)
        title_state = "source-only" if raw_title not in (None, "") else "derived"

    feature_link = source.get("feature_link")
    feature_link = feature_link if isinstance(feature_link, Mapping) else {}
    raw_feature = contract.get("feature")
    if raw_feature in (None, ""):
        raw_feature = feature_link.get("display_label")
    feature_id = contract.get("feature_id") or feature_link.get("feature_id")
    safe_feature = _safe_text(raw_feature)
    if safe_feature is not None:
        feature_label = safe_feature
        feature_state = "recorded"
    elif raw_feature not in (None, ""):
        feature_label = "Feature label available in Source"
        feature_state = "source-only"
    else:
        feature_label = ""
        feature_state = "unavailable"
    feature_seed = str(feature_id or raw_feature or "")
    if isinstance(feature_id, str) and feature_id and not contains_source_locator(feature_id):
        feature_key = feature_id
    elif safe_feature is not None:
        feature_key = f"legacy-label:{safe_feature}"
    elif feature_seed:
        feature_key = "feature:" + hashlib.sha256(
            feature_seed.encode("utf-8")
        ).hexdigest()[:16]
    else:
        feature_key = ""
    return {
        "title": title,
        "titleState": title_state,
        "featureLabel": feature_label,
        "featureState": feature_state,
        "featureKey": feature_key,
    }


def build_task_presentation(
    source: Mapping[str, Any],
    contract: Mapping[str, Any],
    task_slug: str,
) -> dict[str, Any]:
    """Build the sole non-Source task meaning payload or fail closed."""
    authored = any(
        isinstance(key, str) and key.startswith("presentation_")
        for key in contract
    )
    identity = _presentation_identity(source, contract)
    evidence, media = _typed_evidence(source)
    receipts = receipt_summaries(source)
    stage_data = source.get("delivery_stage")
    if authored:
        violations = presentation_contract_violations(contract)
        if violations:
            fields = ", ".join(sorted({field for field, _value, _allowed in violations}))
            raise TaskPresentationError(
                "invalid authored task presentation namespace; rejected fields: "
                + fields
            )
    stage_data = stage_data if isinstance(stage_data, Mapping) else {}
    executor_count = sum(item["role"] == "executor" for item in receipts)
    qa_count = sum(item["role"] == "qa" for item in receipts)
    if qa_count:
        stage = "reviewed"
        label = "Reviewed · accepted" if stage_data.get("accepted_review") else "Reviewed"
    elif executor_count:
        stage = "executed"
        label = "Executed · awaiting review"
    else:
        stage = "planned"
        label = "Planned · awaiting execution"
    unavailable = None if authored else {
        "label": "Human presentation unavailable",
        "guidance": (
            "This legacy task has no authored presentation contract. "
            "Open Source to inspect its complete execution record."
        ),
        "sourceActionLabel": "Open Source",
    }
    source_only = {
        "receiptFieldCount": sum(len(item["sourceOnlyFields"]) for item in receipts),
        "contextFieldCount": sum(
            len(context["sourceOnlyFields"]) for item in receipts for context in item["context"]
        ),
        "evidenceFieldCount": sum(len(item["sourceOnlyFields"]) for item in evidence["items"]),
        "noteCount": sum(item["sourceOnlyNoteCount"] for item in receipts),
    }
    return {
        **identity,
        "state": "authored" if authored else "legacy-unavailable",
        "schemaVersion": (
            contract.get("presentation_schema_version") if authored else None
        ),
        "purpose": contract.get("presentation_purpose") if authored else None,
        "outcome": contract.get("presentation_outcome") if authored else None,
        "scope": (
            list(contract.get("presentation_scope", [])) if authored else []
        ),
        "outOfScope": (
            list(contract.get("presentation_out_of_scope", [])) if authored else []
        ),
        "acceptance": (
            list(contract.get("presentation_acceptance", [])) if authored else []
        ),
        "unavailable": unavailable,
        "technicalFootprint": technical_footprint(contract, task_slug),
        "delivery": {
            "stage": stage,
            "label": label,
            "receiptCount": len(receipts),
            "executorReceiptCount": executor_count,
            "qaReceiptCount": qa_count,
            "closed": bool(stage_data.get("closed")),
            "accepted": bool(stage_data.get("accepted_review")),
        },
        "receipts": receipts,
        "evidence": evidence,
        "media": media,
        "sourceOnly": source_only,
    }


def verified_media_alias_records(
    model: Mapping[str, Any],
) -> list[tuple[str, str, str | None]]:
    """Return ``(alias, repository-relative source, verified hash)`` records.

    These records are build internals and must never be placed in the
    presentation payload.
    """
    truth = model.get("truth_systems")
    tasks_system = truth.get("tasks_features") if isinstance(truth, Mapping) else None
    source_tasks = tasks_system.get("tasks") if isinstance(tasks_system, Mapping) else None
    records: list[tuple[str, str, str | None]] = []
    for source in source_tasks if isinstance(source_tasks, list) else []:
        if not isinstance(source, Mapping):
            continue
        evidence_set = source.get("evidence_set")
        if not isinstance(evidence_set, Mapping) or evidence_set.get("schema_version") != 1:
            continue
        resolutions = source.get("evidence_artifact_resolutions")
        resolutions = resolutions if isinstance(resolutions, Mapping) else {}
        for item in evidence_set.get("items", []):
            if not isinstance(item, Mapping):
                continue
            if item.get("availability") != "available" or item.get("storage") != "committed":
                continue
            artifact = item.get("artifact")
            if not isinstance(artifact, Mapping):
                continue
            media_type = str(artifact.get("media_type") or "")
            if not media_type.startswith(("image/", "audio/", "video/")):
                continue
            resolution = resolutions.get(str(artifact.get("path") or ""))
            if not isinstance(resolution, Mapping) or resolution.get("state") != "verified":
                continue
            task_id = str(source.get("task_id") or "")
            evidence_id = str(item.get("evidence_id") or "")
            resolved_path = resolution.get("resolved_path")
            if not task_id or not evidence_id or not isinstance(resolved_path, str):
                continue
            records.append((
                _media_alias(task_id, evidence_id, media_type),
                resolved_path,
                (
                    str(resolution.get("actual_sha256"))
                    if resolution.get("actual_sha256")
                    else None
                ),
            ))
    return sorted(set(records))


__all__ = [
    "PRESENTATION_KEYS",
    "PRESENTATION_SCHEMA_VERSION",
    "build_task_presentation",
    "contains_source_locator",
    "presentation_contract_violations",
    "receipt_summaries",
    "technical_footprint",
    "TaskPresentationError",
    "verified_media_alias_records",
]
