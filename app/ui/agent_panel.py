"""Live activity panel for a single AI, with a per-model disable button."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

_STATUS_COLORS = {
    "ожидание": ("#8b949e", "#21262d"),
    "работает": ("#79c0ff", "#1f6feb22"),
    "готово": ("#3fb950", "#23863622"),
    "ошибка": ("#f85149", "#da363322"),
    "отключён": ("#d29922", "#bb800922"),
    "отключён пользователем": ("#d29922", "#bb800922"),
    "остановлено": ("#d29922", "#bb800922"),
    "достигнут лимит шагов": ("#d29922", "#bb800922"),
}


class AgentPanel(QWidget):
    toggle_requested = Signal(str)      # provider_id — join or drop this model

    def __init__(self, provider: dict):
        super().__init__()
        self.provider = provider
        self.pid = provider["id"]
        self.live = False
        self.run_active = False
        self.enabled_flag = provider.get("enabled", True)
        self.setObjectName("AgentPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {provider.get('accent', '#79c0ff')}; font-size: 15px;")
        header.addWidget(dot)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        billing = {
            "api": "API-оплата",
            "subscription": provider.get("subscription_tier") or "подписка",
            "local": "локально · без оплаты",
        }.get(provider.get("billing_source"), "")
        name_html = provider["label"]
        if billing:
            name_html += f' <span style="color:#6e7681; font-weight:400;">· {billing}</span>'
        self.name_label = QLabel(name_html)
        self.name_label.setObjectName("AgentName")
        self.name_label.setTextFormat(Qt.RichText)
        self.role_label = QLabel("ожидание задачи")
        self.role_label.setObjectName("AgentRole")
        self.meta_label = QLabel("лимит: —")
        self.meta_label.setObjectName("AgentRole")
        title_box.addWidget(self.name_label)
        title_box.addWidget(self.role_label)
        title_box.addWidget(self.meta_label)
        header.addLayout(title_box, 1)
        self._limits_text = ""
        self._spent_text = ""

        self.status_pill = QLabel("ожидание")
        self.status_pill.setObjectName("Pill")
        header.addWidget(self.status_pill)

        self.toggle_btn = QPushButton("В команде")
        self.toggle_btn.setObjectName("Ghost")
        self.toggle_btn.clicked.connect(lambda: self.toggle_requested.emit(self.pid))
        header.addWidget(self.toggle_btn)

        root.addLayout(header)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)

        self.set_status("ожидание")

    # ---- state ------------------------------------------------------
    def set_status(self, status: str) -> None:
        fg, bg = _STATUS_COLORS.get(status, ("#8b949e", "#21262d"))
        self.status_pill.setText(status)
        self.status_pill.setStyleSheet(
            f"#Pill {{ color: {fg}; background: {bg}; }}")

    def set_run_active(self, active: bool) -> None:
        self.run_active = active
        self._refresh_button()

    def set_live(self, live: bool) -> None:
        self.live = live
        self._refresh_button()

    def set_enabled_flag(self, enabled: bool) -> None:
        self.enabled_flag = enabled
        self._refresh_button()

    def _refresh_button(self) -> None:
        if self.run_active:
            self.toggle_btn.setText("⏻ Отключить" if self.live else "＋ Подключить")
        else:
            self.toggle_btn.setText("✓ В команде" if self.enabled_flag else "＋ В команду")

    def set_role(self, assignment: dict) -> None:
        title = assignment.get("title") or self.provider["short"]
        role = assignment.get("role") or ""
        self.role_label.setText(f"{title} · {role}"[:90])

    def set_limits(self, limits: dict) -> None:
        parts = []
        if limits.get("tok_remaining") is not None:
            tr = limits["tok_remaining"]
            tl = limits.get("tok_limit")
            parts.append(f"токены {_fmt(tr)}" + (f"/{_fmt(tl)}" if tl else "") + " ост.")
        if limits.get("req_remaining") is not None:
            rr = limits["req_remaining"]
            rl = limits.get("req_limit")
            parts.append(f"запросы {_fmt(rr)}" + (f"/{_fmt(rl)}" if rl else ""))
        self._limits_text = "лимит: " + (" · ".join(parts) if parts else "—")
        self._refresh_meta()

    def set_limits_error(self, msg: str) -> None:
        self._limits_text = f"лимит: не получен ({msg[:40]})"
        self._refresh_meta()

    def set_spent(self, text: str) -> None:
        self._spent_text = text
        self._refresh_meta()

    def _refresh_meta(self) -> None:
        pieces = [p for p in (self._limits_text, self._spent_text) if p]
        self.meta_label.setText("   ·   ".join(pieces) or "лимит: —")

    def reset(self) -> None:
        self.log.clear()
        self.role_label.setText("ожидание задачи")
        self._spent_text = ""
        self._refresh_meta()
        self.set_status("ожидание")

    # ---- streaming events ------------------------------------------
    def add_event(self, kind: str, payload: str) -> None:
        accent = self.provider.get("accent", "#79c0ff")
        if kind == "status":
            self.set_status(payload)
            self._append(f'<span style="color:#8b949e">— {payload}</span>')
        elif kind == "thinking":
            # Provider-supported reasoning *summary* — not raw chain-of-thought.
            self._append(f'<span style="color:#8b949e"><i>💭 сводка рассуждений: '
                         f'{_esc(payload)[:600]}</i></span>')
        elif kind == "delta":
            # incremental streamed text — append inline to the current line
            self.log.moveCursor(QTextCursor.End)
            self.log.insertPlainText(payload)
        elif kind == "text":
            self._append(f'<span style="color:#e6edf3">{_esc(payload)[:1200]}</span>')
        elif kind == "tool":
            try:
                data = json.loads(payload)
                name = data.get("name", "?")
                args = data.get("args", {})
                arg_preview = _tool_arg_preview(name, args)
                self._append(
                    f'<span style="color:{accent}">🔧 <b>{name}</b> '
                    f'<span style="color:#8b949e">{_esc(arg_preview)}</span></span>')
            except json.JSONDecodeError:
                self._append(f'🔧 {_esc(payload)}')
        elif kind == "tool_result":
            self._append(f'<span style="color:#6e7681">↳ {_esc(payload)[:400]}</span>')
        elif kind == "steering":
            self._append(f'<span style="color:#d2a8ff">✉ инструкция: {_esc(payload)[:300]}</span>')
        elif kind == "error":
            self._append(f'<span style="color:#f85149">⚠ {_esc(payload)}</span>')
        elif kind == "usage":
            pass  # tracked silently
        self._scroll_bottom()

    def _append(self, html: str) -> None:
        self.log.append(html)

    def _scroll_bottom(self) -> None:
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())


def _fmt(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _tool_arg_preview(name: str, args: dict) -> str:
    if name in ("read_file", "write_file", "edit_file", "delete_path", "list_dir"):
        return args.get("path", "")
    if name == "run_command":
        return args.get("command", "")[:80]
    if name == "finish":
        return "готово"
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in args.items())
