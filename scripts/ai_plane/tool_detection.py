from __future__ import annotations

import importlib
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Callable

DETECTION_STATES = ("present", "absent", "unknown", "error")
_FIXED_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")


@dataclass(frozen=True)
class DetectionResult:
    status: str
    detector: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in DETECTION_STATES:
            raise ValueError(f"invalid detection status: {self.status!r}")

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "detector": self.detector,
            "reason": self.reason,
        }


def _error_reason(prefix: str, error: BaseException) -> str:
    detail = str(error).strip()
    suffix = f": {detail}" if detail else ""
    return f"{prefix} ({type(error).__name__}){suffix}"


def detect_exec(
    descriptor: dict[str, Any],
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> DetectionResult:
    """Inspect only a descriptor's fixed, bare argv[0] through PATH/PATHEXT."""
    token = descriptor["exec"]["argv"][0]
    if "{" in token or "}" in token:
        return DetectionResult(
            "unknown",
            "exec-path",
            "exec argv[0] is templated rather than a fixed executable name",
        )
    if "/" in token or "\\" in token or ":" in token or token in (".", ".."):
        return DetectionResult(
            "unknown",
            "exec-path",
            "exec argv[0] is not a bare executable name",
        )
    try:
        resolved = which(token)
    except Exception as error:
        return DetectionResult(
            "error",
            "exec-path",
            _error_reason(f"PATH/PATHEXT lookup failed for fixed token {token!r}", error),
        )
    if resolved:
        return DetectionResult(
            "present",
            "exec-path",
            f"fixed executable token {token!r} resolves through PATH/PATHEXT",
        )
    return DetectionResult(
        "absent",
        "exec-path",
        f"fixed executable token {token!r} does not resolve through PATH/PATHEXT",
    )


def _windows_uri_registration(
    scheme: str,
    *,
    winreg_module: Any | None = None,
) -> DetectionResult:
    """Inspect per-user and machine URI registrations without opening the URI."""
    try:
        registry = (
            winreg_module
            if winreg_module is not None
            else importlib.import_module("winreg")
        )
    except Exception as error:
        return DetectionResult(
            "error",
            "deeplink-registry",
            _error_reason("Windows registry API is unavailable", error),
        )

    locations = (
        ("current-user", registry.HKEY_CURRENT_USER),
        ("local-machine", registry.HKEY_LOCAL_MACHINE),
    )
    registrations: list[str] = []
    key_path = rf"Software\Classes\{scheme}"
    for label, root in locations:
        handle = None
        try:
            handle = registry.OpenKey(root, key_path, 0, registry.KEY_READ)
            registry.QueryValueEx(handle, "URL Protocol")
        except FileNotFoundError:
            continue
        except OSError as error:
            return DetectionResult(
                "error",
                "deeplink-registry",
                _error_reason(f"read-only URI registry lookup failed in {label}", error),
            )
        else:
            registrations.append(label)
        finally:
            if handle is not None:
                try:
                    registry.CloseKey(handle)
                except OSError:
                    pass

    if not registrations:
        return DetectionResult(
            "absent",
            "deeplink-registry",
            f"no read-only Windows URI-handler registration was found for scheme {scheme!r}",
        )
    if len(registrations) > 1:
        return DetectionResult(
            "unknown",
            "deeplink-registry",
            f"ambiguous Windows URI-handler registrations exist for scheme {scheme!r}",
        )
    return DetectionResult(
        "present",
        "deeplink-registry",
        f"one read-only Windows URI-handler registration exists for scheme {scheme!r}",
    )


def detect_uri_scheme(
    scheme: str,
    *,
    platform_name: str = sys.platform,
    windows_probe: Callable[[str], DetectionResult] | None = None,
) -> DetectionResult:
    if platform_name != "win32":
        return DetectionResult(
            "unknown",
            "deeplink-registry",
            f"read-only URI-handler detection is unsupported on platform {platform_name!r}",
        )
    probe = windows_probe or _windows_uri_registration
    try:
        return probe(scheme)
    except Exception as error:
        return DetectionResult(
            "error",
            "deeplink-registry",
            _error_reason("read-only URI-handler detector failed", error),
        )


def detect_deeplink(
    descriptor: dict[str, Any],
    *,
    platform_name: str = sys.platform,
    uri_detector: Callable[..., DetectionResult] = detect_uri_scheme,
) -> DetectionResult:
    template = descriptor["deeplink"]["url_template"]
    match = _FIXED_SCHEME.match(template)
    if match is None:
        return DetectionResult(
            "unknown",
            "deeplink-registry",
            "deeplink URL template has no fixed URI scheme",
        )
    scheme = match.group(1).lower()
    try:
        return uri_detector(scheme, platform_name=platform_name)
    except Exception as error:
        return DetectionResult(
            "error",
            "deeplink-registry",
            _error_reason("read-only URI-handler detector failed", error),
        )


def detect_tool_transports(
    supported_tools: tuple[str, ...],
    descriptors: dict[str, dict[str, Any]],
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str = sys.platform,
    uri_detector: Callable[..., DetectionResult] = detect_uri_scheme,
) -> dict[str, tuple[DetectionResult, ...]]:
    """Return stable, descriptor-derived advisory observations for every supported tool."""
    detected: dict[str, tuple[DetectionResult, ...]] = {}
    for tool in supported_tools:
        descriptor = descriptors.get(tool)
        if descriptor is None:
            detected[tool] = (
                DetectionResult("unknown", "none", "no configured transport"),
            )
            continue
        results: list[DetectionResult] = []
        if "exec" in descriptor:
            results.append(detect_exec(descriptor, which=which))
        if "deeplink" in descriptor:
            results.append(
                detect_deeplink(
                    descriptor,
                    platform_name=platform_name,
                    uri_detector=uri_detector,
                )
            )
        detected[tool] = tuple(results)
    return detected
