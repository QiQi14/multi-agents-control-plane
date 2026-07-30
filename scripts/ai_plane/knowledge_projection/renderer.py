"""Production writer for the accepted knowledge-reader presentation.

The Task 193 files remain an immutable, hash-verified authority. Production
starts from those exact files and carries only the explicitly approved owner
deltas from Task 197.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ACCEPTED_DIR = Path(__file__).with_name("reader_assets") / "accepted"
PRODUCTION_DIR = Path(__file__).with_name("reader_assets") / "production"
# Frozen accepted-design baseline. A mismatch means the asset changed without review.
# project.js was repinned once at extraction: its demo fallback path named a crate from the
# repository this plane was developed in, which does not exist for any adopter.
ACCEPTED_ASSET_SHA256 = {
    "index.html": "f3898614be529a7fabfe5db83818c4aed6ff888976b456ffac64efdbdd8b0b4c",
    "app.css": "f60ffbac9afe91d4860c5e3f43a142041d6e0fd9509d550de35b168c8eb49daa",
    "app.js": "03f2a45b9daa058d66a79b0c3fb4a3f963e591aca22365b44291f5fe3558b7b8",
    "markdown.js": "6660c8e13bcf24416169a1b4d6e9e720692a4e08eea3e8b458280ef51fb7316a",
    "project.css": "e327c0e10ff3e4bd53383b77a5c004b342a0fc659997be3d7a870c42cd408a2a",
    "project.js": "e21399430ef56acdb0fb7b21c157e1e567cc14d7b0872ae07d28b31232a3ec52",
}
PRODUCTION_ASSETS = (
    "index.html",
    "app.css",
    "app.js",
    "markdown.js",
    "project.css",
    "production-delta.css",
    "project.js",
    "task-rich.js",
)


def canonical_reader_asset_bytes(payload: bytes) -> bytes:
    """Return checkout-independent bytes for governed text assets."""
    return payload.replace(b"\r\n", b"\n")


def reader_asset_sha256(payload: bytes) -> str:
    """Hash the canonical Git representation, independent of checkout EOLs."""
    return hashlib.sha256(canonical_reader_asset_bytes(payload)).hexdigest()


def _accepted_source(name: str) -> Path:
    source = ACCEPTED_DIR / name
    actual = reader_asset_sha256(source.read_bytes())
    expected = ACCEPTED_ASSET_SHA256[name]
    if actual != expected:
        raise RuntimeError(
            f"accepted reader asset drifted: {name} expected {expected}, got {actual}"
        )
    return source


def _verify_accepted_authority() -> None:
    for name in ACCEPTED_ASSET_SHA256:
        _accepted_source(name)


def _production_source(name: str) -> Path:
    source = PRODUCTION_DIR / name
    if not source.is_file():
        raise RuntimeError(f"production reader asset is missing: {name}")
    return source


def reader_shell_html() -> str:
    """Return the accepted presentation plus the approved production deltas."""
    _verify_accepted_authority()
    return canonical_reader_asset_bytes(
        _production_source("index.html").read_bytes()
    ).decode("utf-8")


def write_reader_presentation(site_dir: Path) -> tuple[Path, Path, Path]:
    """Copy the governed local-only production presentation into a site."""
    _verify_accepted_authority()
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    index_path = site_dir / "index.html"
    index_path.write_bytes(
        canonical_reader_asset_bytes(_production_source("index.html").read_bytes())
    )
    for name in PRODUCTION_ASSETS:
        if name != "index.html":
            (assets_dir / name).write_bytes(
                canonical_reader_asset_bytes(_production_source(name).read_bytes())
            )
    return index_path, assets_dir / "app.css", assets_dir / "app.js"
