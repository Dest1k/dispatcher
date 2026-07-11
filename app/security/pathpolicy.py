"""Technically-enforced file ownership for an agent workspace.

The planner may *assign* files to an agent, but assignment is only advisory
until the tool layer enforces it. A PathPolicy resolves every path (following
symlinks / Windows junctions to their real target), confines it to the
workspace root, and applies allow / deny / read-only rules. Any violation
raises PathViolation, which the tool layer turns into a structured tool error.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path


class PathViolation(Exception):
    """Raised when a path is outside the workspace or breaks an ownership rule."""


class PathPolicy:
    def __init__(self, root: str,
                 allowed: list[str] | None = None,
                 denied: list[str] | None = None,
                 read_only: list[str] | None = None):
        # realpath collapses symlinks and junctions so escapes can't hide behind them.
        self.root = Path(os.path.realpath(root))
        self.allowed = list(allowed or [])       # empty => everything under root allowed
        self.denied = list(denied or [])
        self.read_only = list(read_only or [])

    # ---- resolution -------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        candidate = os.path.realpath(os.path.join(self.root, rel or "."))
        candidate_path = Path(candidate)
        if candidate_path != self.root and self.root not in candidate_path.parents:
            raise PathViolation(f"Путь вне рабочей области: {rel}")
        # Also guard the *parent* for not-yet-existing files (write targets):
        # realpath of a missing leaf still resolves its existing ancestors,
        # so a symlinked parent pointing outside is already caught above.
        return candidate_path

    def _rel(self, resolved: Path) -> str:
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return resolved.as_posix()

    @staticmethod
    def _matches(rel: str, patterns: list[str]) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/") + "/*") \
                    or rel == pat.rstrip("/*"):
                return True
        return False

    # ---- checks -----------------------------------------------------
    def resolve_read(self, rel: str) -> Path:
        resolved = self._resolve(rel)
        r = self._rel(resolved)
        if self._matches(r, self.denied):
            raise PathViolation(f"Чтение запрещено политикой: {rel}")
        return resolved

    def resolve_write(self, rel: str) -> Path:
        resolved = self._resolve(rel)
        r = self._rel(resolved)
        if self._matches(r, self.denied):
            raise PathViolation(f"Запись запрещена (deny): {rel}")
        if self._matches(r, self.read_only):
            raise PathViolation(f"Файл только для чтения по политике: {rel}")
        if self.allowed and not self._matches(r, self.allowed):
            raise PathViolation(
                f"Путь вне зоны ответственности агента: {rel}. "
                f"Разрешено: {', '.join(self.allowed)}")
        return resolved

    def can_write(self, rel: str) -> bool:
        try:
            self.resolve_write(rel)
            return True
        except PathViolation:
            return False
