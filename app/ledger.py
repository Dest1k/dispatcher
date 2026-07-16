"""Context integrity ledger: claims must be backed by recorded evidence.

Every run keeps a ledger of what was *actually* touched: repository state,
files read/written, commands and tests executed, permissions in effect,
provider calls. Reports then include a machine-generated evidence section,
and claim checks (`can_claim`) let the report layer refuse wording like
"I inspected the repository" when nothing was ever read.

The ledger itself is in-memory per run; an optional sink persists entries
(the orchestrator plugs in `RunStore.add_event`). It never stores secret
values — details are short, human-readable descriptions.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

EVIDENCE_KINDS = ("repo", "branch", "commit", "file_read", "file_write",
                  "command", "test", "permission", "provider_call")

# Claims the report layer may assert, mapped to the evidence that backs them.
_CLAIMS = {
    "inspected_repository": ("file_read", "command"),
    "modified_files": ("file_write",),
    "executed_commands": ("command",),
    "ran_tests": ("test",),
    "called_providers": ("provider_call",),
}

_KIND_RU = {"repo": "репозиторий", "branch": "ветка", "commit": "коммит",
            "file_read": "чтений файлов", "file_write": "записей файлов",
            "command": "команд выполнено", "test": "проверок запущено",
            "permission": "разрешений зафиксировано",
            "provider_call": "вызовов моделей"}


@dataclass
class Evidence:
    kind: str
    detail: str
    agent: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail,
                "agent": self.agent, "ts": self.ts}


class ContextLedger:
    def __init__(self, sink=None):
        """`sink(kind, payload)` optionally persists each entry."""
        self._items: list[Evidence] = []
        self._lock = threading.Lock()
        self._sink = sink

    # ---- recording -----------------------------------------------------
    def record(self, kind: str, detail: str, agent: str = "") -> None:
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"неизвестный тип свидетельства: {kind}")
        ev = Evidence(kind=kind, detail=str(detail)[:500], agent=agent)
        with self._lock:
            self._items.append(ev)
        if self._sink is not None:
            try:
                label = f"{agent}: {ev.detail}" if agent else ev.detail
                self._sink(kind, label)
            except Exception:
                pass

    def record_tool(self, agent: str, tool_name: str, args: dict) -> None:
        """Map an agent tool call onto evidence (used by the orchestrator)."""
        path = str(args.get("path", ""))
        if tool_name in ("read_file", "list_dir"):
            self.record("file_read", path or ".", agent)
        elif tool_name in ("write_file", "edit_file", "delete_path"):
            self.record("file_write", path, agent)
        elif tool_name == "run_command":
            self.record("command", str(args.get("command", ""))[:200], agent)

    # ---- queries ---------------------------------------------------------
    def count(self, kind: str) -> int:
        with self._lock:
            return sum(1 for e in self._items if e.kind == kind)

    def entries(self, kind: str | None = None) -> list[Evidence]:
        with self._lock:
            return [e for e in self._items if kind is None or e.kind == kind]

    def can_claim(self, claim: str) -> bool:
        kinds = _CLAIMS.get(claim)
        if kinds is None:
            return False
        return any(self.count(k) > 0 for k in kinds)

    def attest(self, claim: str, text: str) -> str:
        """Return `text` when the claim is evidence-backed; otherwise an
        honest replacement stating the absence of evidence."""
        if self.can_claim(claim):
            return text
        return f"(нет свидетельств для утверждения: {claim})"

    # ---- reporting ---------------------------------------------------------
    def summary_md(self) -> str:
        """Russian evidence section for run reports."""
        lines = ["## Свидетельства контекста (ledger)"]
        with self._lock:
            items = list(self._items)
        if not items:
            return "\n".join(lines + ["- свидетельств не зафиксировано"])
        for kind in ("repo", "branch", "commit"):
            for e in items:
                if e.kind == kind:
                    lines.append(f"- {_KIND_RU[kind]}: {e.detail}")
        counters = {}
        for e in items:
            if e.kind in ("file_read", "file_write", "command", "test",
                          "provider_call", "permission"):
                counters[e.kind] = counters.get(e.kind, 0) + 1
        for kind, n in counters.items():
            lines.append(f"- {_KIND_RU[kind]}: {n}")
        tests = [e for e in items if e.kind == "test"]
        for e in tests[:6]:
            lines.append(f"  · тест: {e.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        with self._lock:
            return {"entries": [e.to_dict() for e in self._items]}
