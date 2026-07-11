"""Filesystem + shell tools the agents use to actually change the project.

All operations are confined to the project's local working directory (so a
model can't wander outside the repo by accident), but within it everything is
allowed — including running shell commands — matching the "я всё всем
разрешаю" policy.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .providers.base import ToolSpec
from .security import PathPolicy, PathViolation

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_dir",
        description="Показать список файлов и папок по указанному пути внутри проекта.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Путь относительно корня проекта. По умолчанию '.'"}},
            "required": [],
        },
    ),
    ToolSpec(
        name="read_file",
        description="Прочитать текстовое содержимое файла проекта.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="Создать или полностью перезаписать файл указанным содержимым.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit_file",
        description="Заменить первое вхождение строки find на replace в файле. find должен быть уникальным.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["path", "find", "replace"],
        },
    ),
    ToolSpec(
        name="delete_path",
        description="Удалить файл или пустую папку.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="run_command",
        description="Выполнить shell-команду в корне проекта (установка пакетов, тесты, сборка и т.п.).",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="finish",
        description="Завершить работу и вернуть краткий отчёт о том, что сделано.",
        parameters={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    ),
]

_IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
            "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}
_MAX_READ = 60_000
_MAX_OUTPUT = 8_000


class ProjectTools:
    """Executes tool calls against a single agent worktree.

    When a PathPolicy is supplied, writes are technically confined to the
    agent's assigned paths; when a sandbox is supplied, `run_command` runs in it
    (scrubbed env, no secrets, killable) instead of on the host.
    """

    def __init__(self, root: str, policy: PathPolicy | None = None,
                 sandbox=None, cancel: threading.Event | None = None):
        self.root = Path(root).resolve()
        self.policy = policy
        self.sandbox = sandbox
        self.cancel = cancel

    def _resolve(self, rel: str) -> Path:
        target = (self.root / (rel or ".")).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("Путь выходит за пределы проекта")
        return target

    def _for_read(self, rel: str) -> Path:
        return self.policy.resolve_read(rel) if self.policy else self._resolve(rel)

    def _for_write(self, rel: str) -> Path:
        return self.policy.resolve_write(rel) if self.policy else self._resolve(rel)

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"ERROR: неизвестный инструмент {name}"
        try:
            return handler(args or {})
        except PathViolation as exc:
            return f"ERROR (нарушение зоны ответственности): {exc}"
        except Exception as exc:  # never crash the agent loop on a bad tool call
            return f"ERROR: {exc}"

    # ---- individual tools -------------------------------------------
    def _tool_list_dir(self, args: dict) -> str:
        path = self._for_read(args.get("path", "."))
        if not path.exists():
            return "ERROR: путь не найден"
        if path.is_file():
            return f"{path.name} (файл, {path.stat().st_size} байт)"
        lines = []
        for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.name in _IGNORED:
                continue
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                lines.append(f"{entry.name}  ({entry.stat().st_size} байт)")
        return "\n".join(lines) or "(пусто)"

    def _tool_read_file(self, args: dict) -> str:
        path = self._for_read(args["path"])
        if not path.exists() or not path.is_file():
            return "ERROR: файл не найден"
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: {exc}"
        if len(data) > _MAX_READ:
            return data[:_MAX_READ] + "\n...(файл обрезан)"
        return data

    def _tool_write_file(self, args: dict) -> str:
        path = self._for_write(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.get("content", ""), encoding="utf-8")
        return f"Записано: {args['path']} ({len(args.get('content', ''))} символов)"

    def _tool_edit_file(self, args: dict) -> str:
        path = self._for_write(args["path"])
        if not path.exists():
            return "ERROR: файл не найден"
        data = path.read_text(encoding="utf-8", errors="replace")
        find = args["find"]
        count = data.count(find)
        if count == 0:
            return "ERROR: строка find не найдена"
        if count > 1:
            return f"ERROR: строка find встречается {count} раз, уточни её"
        path.write_text(data.replace(find, args["replace"], 1), encoding="utf-8")
        return f"Отредактировано: {args['path']}"

    def _tool_delete_path(self, args: dict) -> str:
        path = self._for_write(args["path"])
        if not path.exists():
            return "ERROR: путь не найден"
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError as exc:
            return f"ERROR: {exc}"
        return f"Удалено: {args['path']}"

    def _tool_run_command(self, args: dict) -> str:
        command = args["command"]
        if self.sandbox is not None:
            res = self.sandbox.run(command, cancel=self.cancel)
            return f"exit={res.exit_code} [{res.backend}/net:{res.network}]\n{res.output}".strip()
        # Legacy host execution (no sandbox) — only used outside a run.
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.root),
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: команда превысила лимит времени (300с)"
        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > _MAX_OUTPUT:
            out = out[:_MAX_OUTPUT] + "\n...(вывод обрезан)"
        return f"exit={proc.returncode}\n{out}".strip()

    def _tool_finish(self, args: dict) -> str:
        return "OK"
