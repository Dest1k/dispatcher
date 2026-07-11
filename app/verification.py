"""Deterministic verification gate.

A model saying "tests pass" is not evidence. Before any commit / push / PR, real
project commands are executed in a sandbox and their exit codes captured. The
structured result blocks automatic publication on `fail`, `unknown` or
`cancelled`; `partial` requires explicit human approval.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import make_sandbox

BLOCKING = {"fail", "unknown", "cancelled"}


@dataclass
class Check:
    name: str
    command: str
    status: str            # pass | fail | cancelled | skipped
    exit_code: int | None
    summary: str
    output: str = ""


@dataclass
class VerificationResult:
    status: str            # pass | fail | partial | unknown | cancelled
    checks: list[Check] = field(default_factory=list)
    risk: str = "unknown"
    unverified: list[str] = field(default_factory=list)

    def blocks_publication(self) -> bool:
        return self.status in BLOCKING

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "risk": self.risk,
            "unverified": self.unverified,
            "checks": [
                {"name": c.name, "status": c.status, "exit_code": c.exit_code,
                 "summary": c.summary}
                for c in self.checks
            ],
        }


def detect_commands(root: str) -> list[dict]:
    """Best-effort detection of a project's verification commands."""
    r = Path(root)
    cmds: list[dict] = []

    def has(name: str) -> bool:
        return (r / name).exists()

    is_python = (has("pyproject.toml") or has("pytest.ini") or has("setup.cfg")
                 or has("tox.ini") or (r / "tests").is_dir()
                 or any(r.glob("test_*.py")) or any(r.glob("*/test_*.py")))
    if is_python:
        cmds.append({"name": "pytest", "command": "python -m pytest -q"})
        if has("pyproject.toml") or has(".ruff.toml") or has("ruff.toml"):
            cmds.append({"name": "ruff", "command": "ruff check . || true"})

    if has("package.json"):
        try:
            pkg = json.loads((r / "package.json").read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if "test" in scripts:
            cmds.append({"name": "npm test", "command": "npm test --silent"})
        if "build" in scripts:
            cmds.append({"name": "npm build", "command": "npm run build --silent"})
        if "lint" in scripts:
            cmds.append({"name": "npm lint", "command": "npm run lint --silent"})

    if has("Makefile"):
        try:
            mk = (r / "Makefile").read_text(encoding="utf-8")
        except OSError:
            mk = ""
        if "\ntest:" in mk or mk.startswith("test:"):
            cmds.append({"name": "make test", "command": "make test"})

    if has("go.mod"):
        cmds.append({"name": "go test", "command": "go test ./..."})
    if has("Cargo.toml"):
        cmds.append({"name": "cargo test", "command": "cargo test"})
    return cmds


def _summary(res) -> str:
    tail = (res.output or "").strip().splitlines()[-1:] if res.output else []
    note = tail[0] if tail else ""
    return f"exit={res.exit_code} {note}".strip()


def run_verification(root: str, commands: list[dict], mode: str = "restricted",
                     allow_network: bool = False,
                     cancel: threading.Event | None = None,
                     timeout: int = 600) -> VerificationResult:
    if not commands:
        return VerificationResult("unknown", [], "high",
                                  ["Команды верификации не заданы и не обнаружены"])
    sandbox = make_sandbox(mode, root, allow_network=allow_network, timeout=timeout)
    checks: list[Check] = []
    cancelled = False
    try:
        for spec in commands:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            res = sandbox.run(spec["command"], cancel=cancel)
            if res.cancelled:
                status = "cancelled"
                cancelled = True
            elif res.ok:
                status = "pass"
            else:
                status = "fail"
            checks.append(Check(spec["name"], spec["command"], status,
                                res.exit_code, _summary(res), res.output))
            if cancelled:
                break
    finally:
        sandbox.close()
    return _aggregate(checks, cancelled)


def _aggregate(checks: list[Check], cancelled: bool) -> VerificationResult:
    if cancelled:
        return VerificationResult("cancelled", checks, "high",
                                  ["Верификация прервана"])
    if not checks:
        return VerificationResult("unknown", checks, "high",
                                  ["Ни одна проверка не выполнена"])
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        status, risk = "fail", "high"
    elif statuses == {"pass"}:
        status, risk = "pass", "low"
    else:
        status, risk = "partial", "medium"
    unverified = [f"{c.name}: {c.status}" for c in checks if c.status != "pass"]
    return VerificationResult(status, checks, risk, unverified)
