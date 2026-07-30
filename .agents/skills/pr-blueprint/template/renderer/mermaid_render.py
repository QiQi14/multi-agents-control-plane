# GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`.
"""Optional, pinned Mermaid rendering for self-contained blueprint reports."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MERMAID_VERSION = "11.16.0"
MMDC_TIMEOUT_SECONDS = 20
PROBE_TIMEOUT_SECONDS = 5
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / f"mermaid-{MERMAID_VERSION}"
RUNTIME_PATH = VENDOR_DIR / "mermaid.min.js"
PROVENANCE_PATH = VENDOR_DIR / "provenance.json"
RUNTIME_SHA256 = "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
PROCESSING_INSTRUCTION_RE = re.compile(r"<\?([A-Za-z_:][A-Za-z0-9_.:-]*)\b", re.IGNORECASE)
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


@dataclass(frozen=True)
class DiagramResult:
    title: str
    description: str
    source: str
    state: str
    warning: str
    svg: str = ""


def prepare_diagrams(diagrams: list[dict[str, str]]) -> tuple[list[DiagramResult], str, list[str]]:
    """Render safe static SVG where possible and prepare one verified fallback runtime."""
    if not diagrams:
        return [], "", []

    mmdc, cli_reason = probe_mmdc()
    results: list[DiagramResult] = []
    for diagram in diagrams:
        title = diagram.get("title", "")
        description = diagram.get("description", "")
        source = diagram.get("diagram", "")
        if mmdc is None:
            # An empty reason means the CLI is simply not installed. That is a supported mode: the
            # vendored runtime renders in the browser, offline, with nothing to install. A
            # non-empty reason means a CLI IS present and unusable -- a real misconfiguration.
            warning = diagram_warning(title, cli_reason, "browser fallback used") if cli_reason else ""
            results.append(DiagramResult(title, description, source, "browser-fallback", warning))
            continue
        svg, render_reason = render_static_svg(mmdc, title, source)
        if svg is None:
            warning = diagram_warning(title, render_reason, "browser fallback used")
            results.append(DiagramResult(title, description, source, "browser-fallback", warning))
        else:
            results.append(DiagramResult(title, description, source, "static-svg", "", svg))

    if not any(result.state == "browser-fallback" for result in results):
        return results, "", []

    runtime, runtime_reason = verified_runtime()
    if runtime is None:
        source_only: list[DiagramResult] = []
        for result in results:
            if result.state != "browser-fallback":
                source_only.append(result)
                continue
            warning = f"{result.warning}; {runtime_reason}; source-only fallback used."
            source_only.append(
                DiagramResult(result.title, result.description, result.source, "source-only", warning)
            )
        results = source_only
        return results, "", [result.warning for result in results if result.warning]

    fallback_seed = hashlib.sha256(
        b"\0".join(diagram_seed_bytes(result.title, result.source) for result in results if result.state == "browser-fallback")
    ).hexdigest()
    encoded = base64.b64encode(runtime).decode("ascii")
    runtime_html = (
        f'<script data-mermaid-runtime="{MERMAID_VERSION}" '
        f'src="data:text/javascript;base64,{encoded}"></script>\n'
        "<script>\n"
        "window.mermaid.initialize({"
        'securityLevel:"strict",startOnLoad:true,deterministicIds:true,'
        f'deterministicIDSeed:"{fallback_seed}",theme:"neutral",'
        "flowchart:{htmlLabels:false}});\n"
        "</script>"
    )
    return results, runtime_html, [result.warning for result in results if result.warning]


def probe_mmdc() -> tuple[str | None, str]:
    executable = shutil.which("mmdc")
    if not executable:
        # Not installed is the ordinary case, not a fault: the vendored runtime renders the
        # diagram in the browser. An EMPTY reason means "nothing is wrong"; every other return
        # below describes a CLI that IS installed and could not be used, which is worth reporting.
        return None, ""
    try:
        with tempfile.TemporaryDirectory(prefix="pr-blueprint-mmdc-probe-") as temp_dir:
            completed = subprocess.run(
                [executable, "--version"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
    except subprocess.TimeoutExpired:
        return None, "Mermaid CLI unavailable: version check timed out"
    except OSError:
        return None, "Mermaid CLI unavailable: version check failed"
    if completed.returncode != 0:
        return None, "Mermaid CLI unavailable: version check failed"
    version = completed.stdout.strip()
    if version != MERMAID_VERSION:
        shown = version if version else "empty output"
        return None, f"Mermaid CLI incompatible: expected {MERMAID_VERSION}, got {shown}"
    return executable, ""


def render_static_svg(mmdc: str, title: str, source: str) -> tuple[str | None, str]:
    seed = hashlib.sha256(diagram_seed_bytes(title, source)).hexdigest()
    config = {
        "securityLevel": "strict",
        "deterministicIds": True,
        "deterministicIDSeed": seed,
        "htmlLabels": False,
        "flowchart": {"htmlLabels": False},
        "theme": "neutral",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="pr-blueprint-mmdc-render-") as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "input.mmd"
            output_path = directory / "output.svg"
            config_path = directory / "config.json"
            input_path.write_text(source, encoding="utf-8")
            config_path.write_text(
                json.dumps(config, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [mmdc, "-i", str(input_path), "-o", str(output_path), "-c", str(config_path)],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=MMDC_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return None, "Mermaid CLI render timed out"
            except OSError:
                return None, "Mermaid CLI render failed to start"
            if completed.returncode != 0:
                return None, "Mermaid CLI render failed"
            try:
                raw = output_path.read_bytes()
            except OSError:
                return None, "Mermaid CLI produced no SVG"
            svg, unsafe_reason = sanitize_svg(raw)
            if svg is None:
                return None, f"Mermaid CLI output rejected: {unsafe_reason}"
            for path in (str(directory), str(input_path), str(output_path), str(config_path)):
                if path in svg:
                    return None, "Mermaid CLI output rejected: contained temporary path"
            return svg, ""
    except OSError:
        return None, "Mermaid CLI temporary workspace failed"


def sanitize_svg(raw: bytes) -> tuple[str | None, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "SVG was not UTF-8"
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        return None, "DOCTYPE or ENTITY is forbidden"
    for instruction in PROCESSING_INSTRUCTION_RE.finditer(text):
        if instruction.group(1).lower() != "xml":
            return None, "XML processing instruction is forbidden"
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, "SVG XML was malformed"
    if local_name(root.tag).lower() != "svg":
        return None, "root element was not svg"
    for element in root.iter():
        name = local_name(element.tag).lower()
        if name in {"script", "foreignobject"}:
            return None, f"{name} element is forbidden"
        values = list(element.attrib.values())
        if element.text:
            values.append(element.text)
        if element.tail:
            values.append(element.tail)
        for attribute, value in element.attrib.items():
            attribute_name = local_name(attribute).lower()
            if attribute_name.startswith("on"):
                return None, f"{attribute_name} event handler is forbidden"
            if attribute == f"{{{XML_NAMESPACE}}}base":
                return None, "xml:base is forbidden"
            if attribute_name in {"href", "src"} and not safe_fragment(value):
                return None, f"non-fragment {attribute_name} is forbidden"
        for value in values:
            if re.search(r"@\s*import\b", value, re.IGNORECASE):
                return None, "CSS import is forbidden"
            for match in CSS_URL_RE.finditer(value):
                if not safe_fragment(match.group(2).strip()):
                    return None, "external CSS url is forbidden"
    try:
        canonical = ET.canonicalize(text, with_comments=False)
    except (ET.ParseError, ValueError):
        return None, "SVG canonicalization failed"
    if re.search(r"@\s*import\b", canonical, re.IGNORECASE):
        return None, "CSS import is forbidden"
    for match in CSS_URL_RE.finditer(canonical):
        if not safe_fragment(match.group(2).strip()):
            return None, "external CSS url is forbidden"
    return canonical, ""


def verified_runtime() -> tuple[bytes | None, str]:
    try:
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "vendored Mermaid provenance missing or invalid"
    expected = {
        "package": "mermaid",
        "version": MERMAID_VERSION,
        "license": "MIT",
        "asset_path": "dist/mermaid.min.js",
        "asset_sha256": RUNTIME_SHA256,
        "registry_integrity": "sha512-Zvm3kbstgdpvIJPPItlL7fppIZ3kibvc1oZIGxdvk9t6UFz6flv+Jw7FtRGKwfcI8OckmH04LqG6LlS6X4B1pA==",
        "registry_tarball": "https://registry.npmjs.org/mermaid/-/mermaid-11.16.0.tgz",
    }
    expected_cli = {
        "package": "@mermaid-js/mermaid-cli",
        "version": MERMAID_VERSION,
        "registry_integrity": "sha512-0InK2nbVIMtzVzCugmdvPkAuvS6wRUqU6Utntff1n8c7lgfRZAdhKY6PSKvcIK9nFmuOUzAgB5+x/XWcroZ7Zg==",
        "registry_tarball": "https://registry.npmjs.org/@mermaid-js/mermaid-cli/-/mermaid-cli-11.16.0.tgz",
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        return None, "vendored Mermaid provenance mismatch"
    if provenance.get("compatible_cli") != expected_cli:
        return None, "vendored Mermaid CLI provenance mismatch"
    try:
        runtime = RUNTIME_PATH.read_bytes()
    except OSError:
        return None, "vendored Mermaid runtime missing"
    if hashlib.sha256(runtime).hexdigest() != RUNTIME_SHA256:
        return None, "vendored Mermaid runtime SHA-256 mismatch"
    if provenance.get("asset_bytes") != len(runtime):
        return None, "vendored Mermaid runtime length mismatch"
    return runtime, ""


def diagram_seed_bytes(title: str, source: str) -> bytes:
    return title.encode("utf-8") + b"\0" + source.encode("utf-8")


def diagram_warning(title: str, reason: str, outcome: str) -> str:
    return f"Architecture diagram '{title}': {reason}; {outcome}."


def safe_fragment(value: str) -> bool:
    return bool(re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", value.strip()))


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]
