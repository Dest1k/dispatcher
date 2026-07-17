"""A colored, per-file unified-diff viewer.

Renders a unified diff with added/removed/hunk/file-header lines styled, and a
file selector to jump to a single file's changes (or view all). Reused by the
approval dialog and the runs history. Pure-ish: `split_diff` parses the diff
and `_render_html` builds the markup, both testable without a widget.
"""
from __future__ import annotations

from html import escape

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget,
)


def _path_from_gitline(line: str) -> str | None:
    body = line[len("diff --git "):]
    if body.startswith("a/") and " b/" in body:
        return body.split(" b/", 1)[1].strip().strip('"')
    return None


def split_diff(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (file_path, section_text) pairs."""
    sections: list[tuple[str, str]] = []
    cur: list[str] = []
    path: str | None = None

    def flush() -> None:
        if cur:
            sections.append((path or "(файл)", "\n".join(cur)))

    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            flush()
            cur = [line]
            path = _path_from_gitline(line)
        elif not cur:
            if line.strip():                       # a diff without a git header
                cur = [line]
        else:
            cur.append(line)
            if (path is None or path == "(файл)") and line.startswith("+++ b/"):
                path = line[len("+++ b/"):].strip().strip('"')
    flush()
    return sections


def _line_style(line: str) -> str:
    if line.startswith(("diff --git", "index ", "--- ", "+++ ")):
        return "color:#8b949e; font-weight:600;"
    if line.startswith("@@"):
        return "color:#79c0ff;"
    if line.startswith("+"):
        return "background:#0f2c17; color:#56d364;"
    if line.startswith("-"):
        return "background:#3d1418; color:#f85149;"
    return "color:#c9d1d9;"


def _render_html(text: str) -> str:
    if not text.strip():
        return ("<div style='color:#8b949e; font-family:monospace;'>"
                "(изменений нет)</div>")
    out = ["<div style='font-family:Consolas,Menlo,monospace; font-size:12px;'>"]
    for line in text.split("\n"):
        esc = escape(line) or "&nbsp;"
        out.append(f"<div style='white-space:pre-wrap; {_line_style(line)}'>{esc}</div>")
    out.append("</div>")
    return "".join(out)


class DiffView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sections: list[tuple[str, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Файл:"))
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self._render)
        bar.addWidget(self.selector, 1)
        self.stat = QLabel("")
        self.stat.setObjectName("Meta")
        bar.addWidget(self.stat)
        root.addLayout(bar)

        self.browser = QTextBrowser()
        root.addWidget(self.browser, 1)

    def set_diff(self, diff: str) -> None:
        self._sections = split_diff(diff or "")
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItem(f"Все файлы ({len(self._sections)})", None)
        for path, _ in self._sections:
            self.selector.addItem(path, path)
        self.selector.blockSignals(False)
        self._render()

    def changed_files(self) -> list[str]:
        return [p for p, _ in self._sections]

    def _current_text(self) -> str:
        sel = self.selector.currentData()
        if sel is None:
            return "\n".join(t for _, t in self._sections)
        return next((t for p, t in self._sections if p == sel), "")

    def _render(self, *_args) -> None:
        text = self._current_text()
        added = sum(1 for ln in text.split("\n")
                    if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in text.split("\n")
                      if ln.startswith("-") and not ln.startswith("---"))
        self.stat.setText(f"+{added} −{removed}" if (added or removed) else "")
        self.browser.setHtml(_render_html(text))
