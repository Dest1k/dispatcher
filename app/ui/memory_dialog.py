"""Project memory browser: decisions, bugs, solutions, approaches, lessons.

A read-only window over `MemoryGraph` for one project — filter by kind, search,
and inspect a node with its *reasoned* links to other nodes. This surfaces the
accumulated project knowledge (why decisions were made, what failed, what was
rejected) that the orchestrator records automatically.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout,
)

from ..memory_graph import NODE_KINDS, MemoryGraph

_KIND_RU = {"decision": "решение", "bug": "проблема", "solution": "решение-фикс",
            "approach": "подход", "lesson": "урок", "task": "задача"}
_REL_RU = {"relates_to": "связано с", "caused_by": "вызвано",
           "solved_by": "решено через", "supersedes": "заменяет",
           "rejected_for": "отклонено в пользу", "learned_from": "урок из"}


class MemoryDialog(QDialog):
    def __init__(self, project: dict, graph: MemoryGraph | None = None,
                 parent=None):
        super().__init__(parent)
        self.project = project
        self.project_id = project.get("id", "")
        self.graph = graph or MemoryGraph()
        self._owns_graph = graph is None
        self.setWindowTitle(f"Память проекта — {project.get('name', '')}")
        self.setMinimumSize(780, 500)

        v = QVBoxLayout(self)

        filt = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem("Все типы", "")
        for k in NODE_KINDS:
            self.kind.addItem(_KIND_RU.get(k, k), k)
        self.kind.currentIndexChanged.connect(self._reload)
        filt.addWidget(QLabel("Тип:"))
        filt.addWidget(self.kind)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по заголовку и тексту…")
        self.search.textChanged.connect(self._reload)
        filt.addWidget(self.search, 1)
        v.addLayout(filt)

        self.summary = QLabel("")
        self.summary.setObjectName("Meta")
        v.addWidget(self.summary)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Тип", "Заголовок", "Статус", "Дата"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_selected)
        v.addWidget(self.table, 2)

        self.detail = QTextBrowser()
        self.detail.setStyleSheet("font-size:13px;")
        v.addWidget(self.detail, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        kind = self.kind.currentData()
        query = self.search.text().strip()
        if query:
            nodes = self.graph.search(self.project_id, query, limit=200)
            if kind:
                nodes = [n for n in nodes if n.kind == kind]
        elif kind:
            nodes = self.graph.recent(self.project_id, limit=200, kinds=(kind,))
        else:
            nodes = self.graph.recent(self.project_id, limit=200)
        self._nodes = nodes
        self.table.setRowCount(len(nodes))
        for i, n in enumerate(nodes):
            dt = (datetime.fromtimestamp(n.created_at).strftime("%Y-%m-%d %H:%M")
                  if n.created_at else "")
            cells = [_KIND_RU.get(n.kind, n.kind), n.title[:90], n.status, dt]
            for col, text in enumerate(cells):
                self.table.setItem(i, col, QTableWidgetItem(text))
        counts = self.graph.counts(self.project_id)
        total = sum(counts.values())
        by_kind = ", ".join(f"{_KIND_RU.get(k, k)}: {c}"
                            for k, c in sorted(counts.items())) or "пусто"
        self.summary.setText(f"Всего узлов: {total} ({by_kind}) · показано: {len(nodes)}")
        if not nodes:
            self.detail.setPlainText(
                "Память проекта пуста или ничего не найдено. Узлы появляются "
                "автоматически после прогонов и обсуждений совета.")

    def _show_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        node = self._nodes[rows[0].row()]
        parts = [f"<h3>{_esc(node.title)}</h3>",
                 f"<p><b>Тип:</b> {_KIND_RU.get(node.kind, node.kind)} · "
                 f"<b>статус:</b> {node.status}</p>"]
        if node.body:
            parts.append(f"<pre style='white-space:pre-wrap'>{_esc(node.body)}</pre>")
        rels = self.graph.neighbors(node.id)
        if rels:
            parts.append("<p><b>Связи:</b></p><ul>")
            for edge, other in rels:
                rel = _REL_RU.get(edge.rel, edge.rel)
                reason = f" <i>(причина: {_esc(edge.reason)})</i>" if edge.reason else ""
                parts.append(f"<li>{rel}: «{_esc(other.title[:80])}»"
                             f" [{_KIND_RU.get(other.kind, other.kind)}]{reason}</li>")
            parts.append("</ul>")
        self.detail.setHtml("".join(parts))

    def closeEvent(self, event) -> None:       # noqa: N802 (Qt override)
        if self._owns_graph:
            self.graph.close()
        super().closeEvent(event)


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
