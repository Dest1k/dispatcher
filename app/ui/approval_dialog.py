"""Human approval gate: shows the full diff + verification evidence before any
commit/push. Publication of unverified work is blocked here, not in a prompt."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

_STATUS = {
    "pass": ("#3fb950", "верификация пройдена"),
    "partial": ("#d29922", "верификация частичная — требуется явное одобрение"),
    "fail": ("#f85149", "верификация ПРОВАЛЕНА — публикация заблокирована"),
    "unknown": ("#f85149", "верификация не выполнена — публикация заблокирована"),
    "cancelled": ("#d29922", "верификация прервана — публикация заблокирована"),
}


class ApprovalDialog(QDialog):
    def __init__(self, payload: dict, diff: str, parent=None):
        super().__init__(parent)
        self.decision = ("reject", False)
        self.setWindowTitle("Одобрение изменений")
        self.setMinimumSize(820, 640)

        v = QVBoxLayout(self)
        status = payload.get("status", "unknown")
        color, label = _STATUS.get(status, ("#8b949e", status))
        head = QLabel(f"● {label}")
        head.setStyleSheet(f"color:{color}; font-size:15px; font-weight:700;")
        v.addWidget(head)

        files = payload.get("changed_files", []) or []
        v.addWidget(QLabel(f"Изменённые файлы ({len(files)}): "
                           + (", ".join(files[:40]) or "нет")))

        conflicts = payload.get("conflicts", []) or []
        if conflicts:
            cw = QLabel("⚠ Конфликты интеграции: "
                        + "; ".join(c.get("provider", "") for c in conflicts))
            cw.setStyleSheet("color:#f85149;")
            v.addWidget(cw)

        checks = (payload.get("verification", {}) or {}).get("checks", [])
        if checks:
            ev = "  ·  ".join(f"{c['name']}: {c['status']}" for c in checks)
            v.addWidget(QLabel("Проверки: " + ev))

        v.addWidget(QLabel("Полный дифф (в исходный репозиторий пока НЕ применён):"))
        browser = QTextBrowser()
        browser.setStyleSheet("font-family:'Consolas','Menlo',monospace; font-size:12px;")
        browser.setPlainText(diff or "(изменений нет)")
        v.addWidget(browser, 1)

        can_publish = payload.get("can_autopublish", False)
        row = QHBoxLayout()
        reject = QPushButton("Отклонить")
        reject.setObjectName("Danger")
        reject.clicked.connect(self._reject)
        row.addWidget(reject)
        row.addStretch(1)

        approve_local = QPushButton("Одобрить (коммит в ветку, без пуша)")
        approve_local.setObjectName("Ghost")
        approve_local.clicked.connect(lambda: self._approve(False))
        row.addWidget(approve_local)

        approve_push = QPushButton("Одобрить и запушить ветку")
        approve_push.setObjectName("Primary")
        approve_push.setEnabled(can_publish)
        if not can_publish:
            approve_push.setToolTip("Пуш заблокирован: верификация не пройдена.")
        approve_push.clicked.connect(lambda: self._approve(True))
        row.addWidget(approve_push)
        v.addLayout(row)

    def _approve(self, push: bool) -> None:
        self.decision = ("approve", push)
        self.accept()

    def _reject(self) -> None:
        self.decision = ("reject", False)
        self.reject()
