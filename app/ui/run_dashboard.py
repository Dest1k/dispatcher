"""Live run dashboard: phases, plan/task-graph, agents, integration, verify.

A non-modal window fed by the main window from the orchestrator's signals, so
it reflects the current run at a glance — the run's phase, the plan (including
the DAG's layered overview), each agent's role and status, the integration
summary (changed files / conflicts / risk), and the verification result.

Pure view: it exposes public slots (`set_phase`, `set_plan`, `set_agent`,
`set_integration`, `set_verification`, `reset`) and holds no orchestration
logic, so it can be driven directly in tests.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

# Ordered run phases and their Russian labels.
PHASES = [("planning", "Планирование"), ("executing", "Выполнение"),
          ("integrating", "Интеграция"), ("verifying", "Верификация"),
          ("awaiting", "Одобрение"), ("done", "Готово")]
_PHASE_INDEX = {key: i for i, (key, _) in enumerate(PHASES)}

_RISK_RU = {"low": "низкий", "medium": "средний", "high": "высокий"}
_VERDICT_COLOR = {"pass": "#3fb950", "fail": "#f85149", "partial": "#d29922",
                  "unknown": "#8b949e", "cancelled": "#8b949e"}


class RunDashboard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Панель прогона")
        self.setMinimumSize(680, 560)
        self.current_phase = ""

        root = QVBoxLayout(self)

        # phase strip
        self._phase_row = QHBoxLayout()
        self._phase_labels: dict[str, QLabel] = {}
        for key, label in PHASES:
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setObjectName("Pill")
            self._phase_labels[key] = lbl
            self._phase_row.addWidget(lbl)
        root.addLayout(self._phase_row)

        # plan / task-graph
        root.addWidget(QLabel("План / граф задач"))
        self.plan_view = QTextBrowser()
        self.plan_view.setMaximumHeight(180)
        root.addWidget(self.plan_view)

        # agents
        root.addWidget(QLabel("Участники"))
        self.agents = QTableWidget(0, 3)
        self.agents.setHorizontalHeaderLabels(["Агент", "Роль", "Статус"])
        self.agents.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.agents.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._agent_rows: dict[str, int] = {}
        root.addWidget(self.agents, 1)

        # integration + verification summary
        self.integration_label = QLabel("Интеграция: —")
        self.integration_label.setWordWrap(True)
        root.addWidget(self.integration_label)
        self.verification_label = QLabel("Верификация: —")
        self.verification_label.setWordWrap(True)
        root.addWidget(self.verification_label)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.hide)
        row.addWidget(close)
        root.addLayout(row)

        self.reset()

    # ---- slots -------------------------------------------------------
    def reset(self) -> None:
        self.current_phase = ""
        for key, (_, _) in zip(self._phase_labels, PHASES):
            self._style_phase(key, reached=False, current=False)
        self.plan_view.setPlainText("(план ещё не готов)")
        self.agents.setRowCount(0)
        self._agent_rows.clear()
        self.integration_label.setText("Интеграция: —")
        self.verification_label.setText("Верификация: —")

    def set_phase(self, phase: str) -> None:
        if phase not in _PHASE_INDEX:
            return
        self.current_phase = phase
        idx = _PHASE_INDEX[phase]
        for key, position in _PHASE_INDEX.items():
            self._style_phase(key, reached=position < idx, current=position == idx)

    def set_plan(self, plan: dict) -> None:
        lines = []
        overview = (plan.get("overview") or "").strip()
        if overview:
            lines.append(overview)
        for a in plan.get("assignments", []):
            title = a.get("title") or a.get("provider", "")
            role = a.get("role", "")
            files = ", ".join(a.get("files") or []) or "(в своей зоне)"
            obj = a.get("objective", "")
            lines.append(f"• {title} ({role}) — {obj}\n    файлы: {files}")
        self.plan_view.setPlainText("\n".join(lines) or "(план пуст)")

    def set_agent(self, pid: str, label: str = "", role: str = "",
                  status: str = "") -> None:
        row = self._agent_rows.get(pid)
        if row is None:
            row = self.agents.rowCount()
            self.agents.insertRow(row)
            self._agent_rows[pid] = row
            self.agents.setItem(row, 0, QTableWidgetItem(label or pid))
            self.agents.setItem(row, 1, QTableWidgetItem(role))
            self.agents.setItem(row, 2, QTableWidgetItem(status))
            return
        if label:
            self.agents.setItem(row, 0, QTableWidgetItem(label))
        if role:
            self.agents.setItem(row, 1, QTableWidgetItem(role))
        if status:
            self.agents.setItem(row, 2, QTableWidgetItem(status))

    def set_integration(self, integ: dict) -> None:
        files = integ.get("changed_files") or []
        conflicts = integ.get("conflicts") or []
        risk = (integ.get("change_risk") or {}).get("level", "")
        risk_ru = _RISK_RU.get(risk, risk or "—")
        self.integration_label.setText(
            f"Интеграция: файлов {len(files)} · конфликтов {len(conflicts)} · "
            f"риск {risk_ru}"
            + (f"\n{', '.join(files[:8])}" + ("…" if len(files) > 8 else "")
               if files else ""))

    def set_verification(self, v: dict) -> None:
        status = v.get("status", "unknown")
        color = _VERDICT_COLOR.get(status, "#8b949e")
        checks = v.get("checks") or []
        passed = sum(1 for c in checks if c.get("status") == "pass")
        detail = f" ({passed}/{len(checks)} проверок пройдено)" if checks else ""
        self.verification_label.setText(f"Верификация: {status}{detail}")
        self.verification_label.setStyleSheet(f"color:{color}; font-weight:600;")

    # ---- styling -----------------------------------------------------
    def _style_phase(self, key: str, reached: bool, current: bool) -> None:
        lbl = self._phase_labels[key]
        if current:
            lbl.setStyleSheet("background:#1f6feb; color:#fff; border-radius:8px; "
                              "padding:4px 6px; font-weight:700;")
        elif reached:
            lbl.setStyleSheet("background:#238636; color:#fff; border-radius:8px; "
                              "padding:4px 6px;")
        else:
            lbl.setStyleSheet("background:#21262d; color:#8b949e; border-radius:8px; "
                              "padding:4px 6px;")
