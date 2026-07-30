#!/usr/bin/env python3
"""Stack-agnostic verification substrate for the Agent Control Plane (maw_core).

This module owns the reusable coordination primitives that any evidence gate builds on: the
cross-platform advisory lock with heartbeat and holder metadata, the atomic versioned JSON
evidence-record writer, the argv-array-only run/git/process seams (including the generic
`run_argv` command runner), the fail-closed VerifyError taxonomy, and repository-relative
identity helpers. The control-plane contract check, generated-file manifest exemptions, git
change-set discovery, and the no-gate degradation path live in the sibling
scripts/verify_contract.py, which imports this module one-directionally.

It contains no language, stack, vendor, or build-tool policy, and no module-level dependency on
the config/tool roster, prompts, or dispatch. The reference Cargo/Rust gate
(scripts/rust_verify.py) imports these primitives; this module never imports it. Stdlib +
config-free primitives only. All subprocess interaction is an argv array behind a seam so fixture
tests inject git/subprocess/lock/process-scan behavior without a real repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# maw_core (the generic substrate) has NO module-level dependency on config, the tool roster,
# prompts, dispatch, or any extension — that is what makes it cleanly extractable (Q181-5). The
# contract/change-set/degradation layer that needs the config-free primitives lives in the sibling
# scripts/verify_contract.py, which imports FROM this module one-directionally.


class VerifyError(Exception):
    """Fail-closed error; surfaced as exit 1 without a traceback."""


class GitError(VerifyError):
    pass


class ConfigError(VerifyError):
    pass


class LockError(VerifyError):
    pass


class ProcessActiveError(VerifyError):
    pass


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_iso_precise() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Seams (patched by fixture tests; never shell strings anywhere).
# ---------------------------------------------------------------------------


def os_name() -> str:
    return os.name


def temp_dir() -> Path:
    return Path(tempfile.gettempdir())


def run_command(argv: list[str], cwd: Path) -> dict[str, Any]:
    """Run one argv array (never a shell string) and capture diagnostics."""
    started = now_iso()
    monotonic = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        executable = argv[0] if argv else "<empty argv>"
        raise VerifyError(
            f"Unable to launch '{executable}'. Ensure it is installed, executable, "
            f"and available on PATH: {error}"
        ) from error
    duration = time.monotonic() - monotonic
    output = proc.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return {
        "argv": list(argv),
        "started_utc": started,
        "duration_seconds": round(duration, 3),
        "exit_status": proc.returncode,
        "output": output,
    }


def run_argv(argv: list[str], cwd: Path, *, timeout: float | None = None) -> int:
    """Generic argv execution seam for command capabilities: the shell is disabled, an optional
    timeout is honored, and the process exit status is returned. Never constructs a shell string.
    Distinguishes a timeout from a launch failure so callers can report the precise reason."""
    try:
        result = subprocess.run(argv, cwd=str(cwd), shell=False, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise VerifyError(f"command-timed-out: {argv[0]!r} exceeded {timeout}s: {error}") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise VerifyError(f"command-launch-failed: could not launch {argv[0]!r}: {error}") from error
    return result.returncode


def git_output(root: Path, args: list[str]) -> str:
    """Run one git argv array and return raw stdout; raise GitError on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {' '.join(args)} failed: {error}") from error
    return proc.stdout.decode("utf-8", errors="replace")


def lock_operations(name: str) -> tuple[Callable[[Path, int], None], Callable[[Path, int], None]]:
    """Return (acquire, release) advisory-lock callables for the platform seam."""
    if name == "nt":
        import msvcrt

        def acquire_nt(_path: Path, fd: int) -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

        def release_nt(_path: Path, fd: int) -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

        return acquire_nt, release_nt

    import fcntl

    def acquire_posix(_path: Path, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_posix(_path: Path, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    return acquire_posix, release_posix


def process_listing(name: str) -> str:
    """Return a raw process listing or fail closed when it is unobtainable."""
    if name == "nt":
        argv = ["tasklist", "/fo", "csv", "/nh"]
    else:
        argv = ["ps", "-eo", "comm="]
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ProcessActiveError(
            "process-listing-unavailable: refusing cleanup because the live "
            f"process listing could not be obtained: {error}"
        ) from error
    return proc.stdout.decode("utf-8", errors="replace")


def getcwd() -> Path:
    return Path(os.getcwd())


# ---------------------------------------------------------------------------
# Identity helpers (never store an absolute checkout path in evidence).
# ---------------------------------------------------------------------------


def canonical_root_key(root: Path) -> str:
    text = os.path.normcase(str(Path(root).resolve())).replace("\\", "/")
    return text


def repo_key_sha256(root: Path) -> str:
    return hashlib.sha256(canonical_root_key(root).encode("utf-8")).hexdigest()


def lock_file_name(root: Path, config: dict[str, Any]) -> str:
    prefix = config["lock"]["name_prefix"]
    return f"{prefix}-{repo_key_sha256(root)[:16]}.lock"


def to_posix_rel(root: Path, path: Path) -> str:
    return os.path.relpath(str(path), str(root)).replace("\\", "/")


# ---------------------------------------------------------------------------
# Cross-platform advisory lock
# ---------------------------------------------------------------------------


class VerificationLock:
    """One advisory lock per repository in the OS temp dir, with holder metadata."""

    def __init__(
        self,
        root: Path,
        config: dict[str, Any],
        task_id: str,
        argv: list[str],
        *,
        name: str | None = None,
        temp: Path | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.task_id = task_id
        self.argv = list(argv)
        self.platform_name = name if name is not None else os_name()
        self.temp = Path(temp) if temp is not None else temp_dir()
        self.path = self.temp / lock_file_name(root, config)
        self.meta_path = self.temp / (self.path.name + ".meta.json")
        self._fd: int | None = None
        self._ops: tuple[Callable[[Path, int], None], Callable[[Path, int], None]] | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _write_meta(self) -> None:
        meta = {
            "pid": os.getpid(),
            "task_id": self.task_id,
            "argv": self.argv,
            "started_utc": getattr(self, "_started_utc", now_iso_precise()),
            "heartbeat_utc": now_iso_precise(),
            "repo_key_sha256": repo_key_sha256(self.root),
            "lock_file": self.path.name,
        }
        tmp = self.meta_path.with_name(self.meta_path.name + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.meta_path)
        except OSError:
            pass

    def heartbeat(self) -> None:
        if self._fd is not None:
            self._write_meta()

    def _heartbeat_loop(self, interval: float) -> None:
        while not self._heartbeat_stop.wait(interval):
            self.heartbeat()

    def holder_metadata(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def acquire(self, wait_seconds: float | None = None, poll_seconds: float | None = None) -> None:
        lock_cfg = self.config["lock"]
        if wait_seconds is None:
            wait_seconds = float(lock_cfg.get("wait_seconds", 600))
        if poll_seconds is None:
            poll_seconds = float(lock_cfg.get("wait_poll_seconds", 1))
        self._ops = lock_operations(self.platform_name)
        acquire_op, _ = self._ops
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o666)
        deadline = time.monotonic() + wait_seconds
        reported = False
        while True:
            try:
                acquire_op(self.path, self._fd)
                break
            except OSError:
                holder = self.holder_metadata()
                if holder:
                    print(
                        "verification lock held by "
                        f"pid={holder.get('pid')} task={holder.get('task_id')} "
                        f"argv={holder.get('argv')} started={holder.get('started_utc')} "
                        f"heartbeat={holder.get('heartbeat_utc')}",
                        file=sys.stderr,
                    )
                else:
                    print("verification lock held; holder metadata unavailable", file=sys.stderr)
                reported = True
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise LockError(
                        f"Timed out waiting for verification lock {self.path.name}; "
                        "another process owns it."
                    )
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        if reported:
            print("verification lock acquired after wait", file=sys.stderr)
        self._started_utc = now_iso_precise()
        self._write_meta()
        heartbeat_seconds = float(lock_cfg.get("heartbeat_seconds", 5))
        if heartbeat_seconds > 0:
            self._heartbeat_stop.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, args=(heartbeat_seconds,), daemon=True
            )
            self._heartbeat_thread.start()

    def release(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)
            self._heartbeat_thread = None
        if self._fd is None:
            return
        assert self._ops is not None
        _, release_op = self._ops
        try:
            release_op(self.path, self._fd)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None
        try:
            self.meta_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "VerificationLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Evidence record (atomic, versioned; repository-relative paths only)
# ---------------------------------------------------------------------------


def evidence_path(task_dir: Path, config: dict[str, Any]) -> Path:
    return task_dir / config["evidence"]["file_name"]


def append_invocation(task_dir: Path, config: dict[str, Any], invocation: dict[str, Any]) -> Path:
    path = evidence_path(task_dir, config)
    record: dict[str, Any]
    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VerifyError(
                f"Existing evidence record is corrupt; refusing to overwrite: {error}"
            ) from error
        if record.get("kind") != config["evidence"]["kind"]:
            raise VerifyError("Existing evidence record has an unexpected kind; refusing.")
    else:
        record = {
            "schema_version": config["evidence"]["schema_version"],
            "kind": config["evidence"]["kind"],
            "task_id": invocation.get("task_id"),
            "repository_key_sha256": invocation.get("repository_key_sha256"),
            "invocations": [],
        }
    record["invocations"].append(invocation)
    record["updated_at_utc"] = now_iso()
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def platform_block() -> dict[str, str]:
    return {
        "os": platform.system() or "unknown",
        "os_name": os_name(),
        "arch": platform.machine() or "unknown",
        "python": platform.python_version(),
    }


def scrub_invocation(invocation: dict[str, Any], root: Path) -> dict[str, Any]:
    """Defense in depth: evidence must never contain an absolute checkout path."""
    text = json.dumps(invocation)
    raw = str(root)
    variants = {raw, raw.replace("\\", "/"), canonical_root_key(root)}
    escaped = raw
    for _level in range(3):
        escaped = escaped.replace("\\", "\\\\")
        variants.add(escaped)
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            text = text.replace(variant, "<repo>")
    return json.loads(text)
