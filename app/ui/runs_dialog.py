"""Run history for a project: status, cost, and review/resume of parked runs."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from ..domain import RunState, is_resumable
from ..resume import discard_resumed, publish_resumed
from .approval_dialog import ApprovalDialog


class RunsDialog(QDialog):
    def __init__(self, store, config, project: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.config = config
        self.project = project
        self.setWindowTitle(f"История прогонов — {project.get('name', '')}")
        self.setMinimumSize(760, 460)

        v = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setObjectName("Meta")
        v.addWidget(self.hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Задача", "Статус", "Дата", "Стоимость", "ID"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_events)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.review_btn = QPushButton("Просмотреть / решить незавершённый")
        self.review_btn.setObjectName("Primary")
        self.review_btn.clicked.connect(self._review_selected)
        row.addWidget(self.review_btn)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        runs = self.store.list_runs(self.project.get("id", ""))
        self._records = runs
        self.table.setRowCount(len(runs))
        resumable = 0
        for i, rec in enumerate(runs):
            cost = sum(u["cost"] for u in self.store.usage_for(rec.id))
            dt = datetime.fromtimestamp(rec.created_at).strftime("%Y-%m-%d %H:%M") \
                if rec.created_at else ""
            if is_resumable(RunState(rec.state)):
                resumable += 1
            for col, text in enumerate([rec.task[:80], rec.state, dt,
                                        f"${cost:.4f}", rec.id]):
                self.table.setItem(i, col, QTableWidgetItem(text))
        self.hint.setText(
            f"Прогонов: {len(runs)}"
            + (f" · незавершённых, ждущих решения: {resumable}" if resumable else ""))

    def _open_events(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._records):
            EventsDialog(self.store, self._records[row], parent=self).exec()

    def _review_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Выбор", "Выбери прогон в таблице.")
            return
        rec = self._records[rows[0].row()]
        if not is_resumable(RunState(rec.state)):
            QMessageBox.information(
                self, "Нечего решать",
                f"Прогон в состоянии «{rec.state}» — решение не требуется.")
            return
        status = rec.verification.get("status", "unknown")
        payload = {
            "status": status,
            "can_autopublish": status not in ("fail", "unknown", "cancelled"),
            "changed_files": rec.changed_files,
            "conflicts": [],
            "verification": rec.verification,
        }
        dlg = ApprovalDialog(payload, rec.diff, parent=self)
        dlg.setWindowTitle("Решение по незавершённому прогону")
        dlg.exec()
        action, push = dlg.decision
        if action == "approve":
            res = publish_resumed(self.store, rec, self.project, push)
        else:
            res = discard_resumed(self.store, rec, self.project)
        QMessageBox.information(self, "Готово", res.get("message", ""))
        self._reload()


class EventsDialog(QDialog):
    """Structured event timeline for one run."""

    def __init__(self, store, record, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Лента событий — {record.task[:60]}")
        self.setMinimumSize(640, 440)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Статус: {record.state}"))
        browser = QTextBrowser()
        browser.setStyleSheet("font-family:'Consolas','Menlo',monospace; font-size:12px;")
        lines = []
        for e in store.get_events(record.id):
            ts = datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S") if e["ts"] else ""
            payload = (e["payload"] or "")[:200]
            lines.append(f"{ts}  [{e['kind']}]  {payload}")
        browser.setPlainText("\n".join(lines) or "(событий нет)")
        v.addWidget(browser, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        v.addWidget(close)
