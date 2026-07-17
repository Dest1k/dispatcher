"""Main application window."""
from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from .. import git_service
from ..config import Config
from ..orchestrator import Orchestrator
from ..persistence import RunStore
from ..providers import make_adapter
from ..resume import discard_resumed, publish_resumed
from .agent_panel import AgentPanel
from .approval_dialog import ApprovalDialog
from .chat_view import ChatView
from .project_dialog import ProjectDialog
from .settings_dialog import SettingsDialog


class LimitsChecker(QThread):
    """Fetches remaining rate-limit windows for each provider off the UI thread."""
    result = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(self, providers: list[dict]):
        super().__init__()
        self.providers = providers

    def run(self) -> None:
        for provider in self.providers:
            try:
                limits = make_adapter(provider).fetch_limits()
                self.result.emit(provider["id"], limits)
            except Exception as exc:
                self.failed.emit(provider["id"], str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config.load()
        self.orchestrator: Orchestrator | None = None
        self.running_project_id: str | None = None
        self.agent_panels: dict[str, AgentPanel] = {}
        self.live_usage: dict[str, dict] = {}
        self._limits_checker: LimitsChecker | None = None
        self._last_diff: str = ""
        self.run_store = RunStore()
        self.dashboard = None          # lazily created RunDashboard

        self.setWindowTitle("Multi-AI Control Center — Claude · ChatGPT · Grok")
        self.resize(1360, 860)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_main(), 1)

        self.statusBar().showMessage("Готов")
        self._refresh_projects()
        # No automatic paid ping on startup (see SECURITY.md / brief §8).
        # Rate-limit badges fill from real responses during a run, or on demand.
        self._recover_runs()

    # ================= sidebar =================
    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(270)
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 16, 14, 14)
        v.setSpacing(6)

        brand = QLabel("⬢ Multi-AI Center")
        brand.setObjectName("Brand")
        v.addWidget(brand)
        sub = QLabel("Центр управления тремя ИИ")
        sub.setObjectName("BrandSub")
        v.addWidget(sub)

        section = QLabel("Проекты")
        section.setObjectName("SectionTitle")
        v.addWidget(section)

        self.project_list = QListWidget()
        self.project_list.itemClicked.connect(self._on_project_clicked)
        v.addWidget(self.project_list, 1)

        new_btn = QPushButton("＋ Новый проект")
        new_btn.setObjectName("Ghost")
        new_btn.clicked.connect(self._new_project)
        v.addWidget(new_btn)

        runs_btn = QPushButton("🗂 История прогонов")
        runs_btn.setObjectName("Ghost")
        runs_btn.clicked.connect(self._open_runs)
        v.addWidget(runs_btn)

        memory_btn = QPushButton("🧠 Память проекта")
        memory_btn.setObjectName("Ghost")
        memory_btn.clicked.connect(self._open_memory)
        v.addWidget(memory_btn)

        dashboard_btn = QPushButton("📊 Панель прогона")
        dashboard_btn.setObjectName("Ghost")
        dashboard_btn.clicked.connect(self._open_dashboard)
        v.addWidget(dashboard_btn)

        settings_btn = QPushButton("⚙ Настройки моделей")
        settings_btn.setObjectName("Ghost")
        settings_btn.clicked.connect(self._open_settings)
        v.addWidget(settings_btn)

        return panel

    # ================= main area =================
    def _build_main(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_header())

        splitter = QSplitter(Qt.Vertical)
        self.chat = ChatView()
        splitter.addWidget(self.chat)
        splitter.addWidget(self._build_activity())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        v.addWidget(splitter, 1)

        v.addWidget(self._build_composer())
        return wrap

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("ProjectHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 14, 20, 14)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.project_name = QLabel("Нет проекта")
        self.project_name.setObjectName("ProjectName")
        info.addWidget(self.project_name)
        self.project_meta = QLabel("")
        self.project_meta.setObjectName("Meta")
        info.addWidget(self.project_meta)
        h.addLayout(info, 1)

        self.open_btn = QPushButton("📂 Папка")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.clicked.connect(self._open_folder)
        self.status_btn = QPushButton("⌥ Git-статус")
        self.status_btn.setObjectName("Ghost")
        self.status_btn.clicked.connect(self._git_status)
        self.edit_btn = QPushButton("✎ Изменить")
        self.edit_btn.setObjectName("Ghost")
        self.edit_btn.clicked.connect(self._edit_project)
        for b in (self.open_btn, self.status_btn, self.edit_btn):
            h.addWidget(b)
        return header

    def _build_activity(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(16, 8, 16, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("Активность моделей")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.usage_label = QLabel("")
        self.usage_label.setObjectName("Meta")
        top.addWidget(self.usage_label)
        self.limits_btn = QPushButton("↻ Лимиты (платный запрос)")
        self.limits_btn.setObjectName("Ghost")
        self.limits_btn.setToolTip(
            "Обновление лимитов делает по одному минимальному запросу к каждому "
            "провайдеру — он тарифицируется как обычный запрос.")
        self.limits_btn.clicked.connect(self._check_limits)
        top.addWidget(self.limits_btn)
        v.addLayout(top)

        self.panels_row = QHBoxLayout()
        self.panels_row.setSpacing(10)
        panels_wrap = QWidget()
        panels_wrap.setLayout(self.panels_row)
        v.addWidget(panels_wrap, 1)
        return wrap

    def _build_composer(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("Composer")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(20, 12, 20, 14)
        v.setSpacing(8)

        self.running_banner = QLabel("")
        self.running_banner.setObjectName("RunningBanner")
        self.running_banner.hide()
        v.addWidget(self.running_banner)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            "Опиши задачу для трёх ИИ… (Ctrl+Enter — отправить)")
        self.input.setFixedHeight(84)
        v.addWidget(self.input)

        row = QHBoxLayout()
        mode_label = QLabel("Режим:")
        mode_label.setObjectName("Meta")
        row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Ведущий (Claude раздаёт роли)", "lead")
        self.mode_combo.addItem("Каждый сам", "auto")
        self.mode_combo.setCurrentIndex(
            0 if self.config.orchestration.get("mode") == "lead" else 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self.mode_combo)
        row.addStretch(1)

        self.stop_btn = QPushButton("⏹ Стоп")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self._stop_task)
        self.stop_btn.hide()
        row.addWidget(self.stop_btn)

        self.send_btn = QPushButton("Отправить трём ИИ →")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.send_btn)
        v.addLayout(row)

        send_sc = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        send_sc.activated.connect(self._send)
        return wrap

    # ================= projects =================
    def _refresh_projects(self) -> None:
        self.project_list.clear()
        for project in self.config.projects:
            item = QListWidgetItem(f"◆  {project['name']}")
            item.setData(Qt.UserRole, project["id"])
            self.project_list.addItem(item)
        active = self.config.data.get("active_project")
        if active and self.config.get_project(active):
            self._select_project(active)
        elif self.config.projects:
            self._select_project(self.config.projects[0]["id"])
        else:
            self._render_empty()

    def _on_project_clicked(self, item: QListWidgetItem) -> None:
        self._select_project(item.data(Qt.UserRole))

    def _select_project(self, project_id: str) -> None:
        self.config.data["active_project"] = project_id
        self.config.save()
        for i in range(self.project_list.count()):
            it = self.project_list.item(i)
            if it.data(Qt.UserRole) == project_id:
                self.project_list.setCurrentItem(it)
        self._render_project()

    def _current_project(self) -> dict | None:
        pid = self.config.data.get("active_project")
        return self.config.get_project(pid) if pid else None

    def _render_empty(self) -> None:
        self.project_name.setText("Нет проекта")
        self.project_meta.setText("Создай проект: укажи локальную папку и GitHub-репозиторий.")
        self.chat.clear()
        self._rebuild_agent_panels()
        self.send_btn.setEnabled(False)

    def _render_project(self) -> None:
        project = self._current_project()
        if not project:
            self._render_empty()
            return
        self.send_btn.setEnabled(True)
        self.project_name.setText(project["name"])
        repo = project.get("github_repo") or "GitHub не указан"
        branch = project.get("branch", "main")
        local = project.get("local_path", "")
        self.project_meta.setText(f"📁 {local}    🔗 {repo}    🌿 {branch}")
        self.chat.load(project.get("chat", []))
        self._rebuild_agent_panels()

    def _new_project(self) -> None:
        dlg = ProjectDialog(self.config, parent=self)
        if dlg.exec():
            self._refresh_projects()
            self._select_project(dlg.saved_id)

    def _edit_project(self) -> None:
        project = self._current_project()
        if not project:
            return
        dlg = ProjectDialog(self.config, project=project, parent=self)
        if dlg.exec():
            self._refresh_projects()
            self._select_project(project["id"])

    def _open_folder(self) -> None:
        project = self._current_project()
        if project and project.get("local_path"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(project["local_path"]))

    def _git_status(self) -> None:
        project = self._current_project()
        if not project:
            return
        path = project.get("local_path", "")
        try:
            text = git_service.status(path) if git_service.has_repo(path) \
                else "Это ещё не git-репозиторий."
        except Exception as exc:
            text = f"Ошибка: {exc}"
        QMessageBox.information(self, "Git статус", text or "(чисто)")

    # ================= settings =================
    def _open_runs(self) -> None:
        project = self._current_project()
        if not project:
            return
        from .runs_dialog import RunsDialog
        RunsDialog(self.run_store, self.config, project, parent=self).exec()

    def _open_memory(self) -> None:
        project = self._current_project()
        if not project:
            return
        from .memory_dialog import MemoryDialog
        MemoryDialog(project, parent=self).exec()

    def _dash(self):
        """The (lazily created) run dashboard — a live, non-modal view fed from
        the orchestrator signal handlers below."""
        if self.dashboard is None:
            from .run_dashboard import RunDashboard
            self.dashboard = RunDashboard(self)
        return self.dashboard

    def _open_dashboard(self) -> None:
        dash = self._dash()
        dash.show()
        dash.raise_()
        dash.activateWindow()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, parent=self)
        if dlg.exec():
            self.mode_combo.setCurrentIndex(
                0 if self.config.orchestration.get("mode") == "lead" else 1)
            self._rebuild_agent_panels()
            self.statusBar().showMessage("Настройки сохранены")

    def _on_mode_changed(self) -> None:
        self.config.orchestration["mode"] = self.mode_combo.currentData()
        self.config.save()

    # ================= agent panels =================
    def _rebuild_agent_panels(self) -> None:
        while self.panels_row.count():
            item = self.panels_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.agent_panels.clear()
        available = self.config.available_providers()
        if not available:
            hint = QLabel("Нет ИИ с ключом. Добавь API-ключи в «Настройки моделей».")
            hint.setObjectName("Meta")
            hint.setAlignment(Qt.AlignCenter)
            self.panels_row.addWidget(hint)
            return
        for provider in available:
            panel = AgentPanel(provider)
            panel.set_enabled_flag(provider.get("enabled", True))
            panel.toggle_requested.connect(self._on_panel_toggle)
            self.agent_panels[provider["id"]] = panel
            self.panels_row.addWidget(panel)

    def _check_limits(self) -> None:
        if self._limits_checker is not None and self._limits_checker.isRunning():
            return
        providers = self.config.available_providers()
        if not providers:
            return
        for p in providers:
            panel = self.agent_panels.get(p["id"])
            if panel:
                panel.set_limits_error("проверяю…")
        self.limits_btn.setEnabled(False)
        checker = LimitsChecker([dict(p) for p in providers])
        checker.result.connect(self._on_limits_result)
        checker.failed.connect(self._on_limits_failed)
        checker.finished.connect(lambda: self.limits_btn.setEnabled(True))
        self._limits_checker = checker
        checker.start()

    def _on_limits_result(self, provider_id: str, limits: dict) -> None:
        panel = self.agent_panels.get(provider_id)
        if panel:
            panel.set_limits(limits)

    def _on_limits_failed(self, provider_id: str, message: str) -> None:
        panel = self.agent_panels.get(provider_id)
        if panel:
            panel.set_limits_error(message)

    def _on_panel_toggle(self, provider_id: str) -> None:
        panel = self.agent_panels.get(provider_id)
        if panel is None:
            return
        if self.orchestrator is not None:
            # Live council: drop or hot-join the model.
            if panel.live:
                self.orchestrator.disable_agent(provider_id)
                panel.set_live(False)
            else:
                provider = self.config.providers.get(provider_id)
                if provider and self.orchestrator.add_agent(provider):
                    panel.set_live(True)
                    panel.set_status("работает")
        else:
            # Idle: toggle whether this model takes part in the next run.
            provider = self.config.providers.get(provider_id)
            if provider is not None:
                provider["enabled"] = not provider.get("enabled", True)
                self.config.save()
                panel.set_enabled_flag(provider["enabled"])

    # ================= running a task =================
    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        project = self._current_project()
        if not project:
            QMessageBox.warning(self, "Нет проекта", "Сначала создай проект.")
            return

        if self.orchestrator is not None:
            if self.running_project_id == project["id"]:
                # mid-run steering
                self.orchestrator.add_steering(text)
                self.chat.add_message("steering", text)
                self.config.add_chat_message(project["id"], "steering", text)
                self.input.clear()
            else:
                QMessageBox.information(
                    self, "Идёт задача",
                    "Дождись завершения текущей задачи в другом проекте.")
            return

        if not self.config.active_providers():
            QMessageBox.warning(self, "Нет ИИ",
                                "Добавь хотя бы один API-ключ в настройках.")
            return
        self.input.clear()
        self._start_task(project, text)

    def _start_task(self, project: dict, instruction: str) -> None:
        self.chat.add_message("user", instruction)
        self.config.add_chat_message(project["id"], "user", instruction)

        self._rebuild_agent_panels()
        active_ids = {p["id"] for p in self.config.active_providers()}
        for pid, panel in self.agent_panels.items():
            panel.reset()
            panel.set_run_active(True)
            panel.set_live(pid in active_ids)
        self.live_usage.clear()
        self.usage_label.setText("")

        dash = self._dash()
        dash.reset()
        dash.set_phase("planning")
        for p in self.config.active_providers():
            dash.set_agent(p["id"], label=p.get("label", p["id"]),
                           status="ожидание")

        self.orchestrator = Orchestrator(self.config, project, instruction,
                                         store=self.run_store)
        self.running_project_id = project["id"]
        orc = self.orchestrator
        orc.plan_ready.connect(self._on_plan_ready)
        orc.agent_role.connect(self._on_agent_role)
        orc.agent_event.connect(self._on_agent_event)
        orc.log.connect(self._on_log)
        orc.integration_ready.connect(self._on_integration_ready)
        orc.verification_ready.connect(self._on_verification_ready)
        orc.awaiting_approval.connect(self._on_awaiting_approval)
        orc.report_ready.connect(self._on_report_ready)
        orc.run_finished.connect(self._on_run_finished)
        orc.run_error.connect(self._on_run_error)

        self._set_running(True)
        orc.start()

    def _set_running(self, running: bool) -> None:
        self.stop_btn.setVisible(running)
        self.send_btn.setText("Отправить работающим ИИ →" if running
                              else "Отправить трём ИИ →")
        if running:
            self.running_banner.setText(
                "⚡ Задача выполняется — новое сообщение уйдёт работающим ИИ как доп. инструкция")
            self.running_banner.show()
        else:
            self.running_banner.hide()

    def _stop_task(self) -> None:
        if self.orchestrator:
            self.orchestrator.cancel()

    # ================= orchestrator signals =================
    def _on_plan_ready(self, plan: dict) -> None:
        overview = plan.get("overview", "")
        if overview:
            self._on_log(f"План: {overview}")
        lines = ["**📋 План работы консилиума**"]
        if overview:
            lines.append(overview)
        for a in plan.get("assignments", []):
            title = a.get("title") or a.get("provider", "")
            role = a.get("role", "")
            files = ", ".join(a.get("files") or []) or "—"
            lines.append(f"- **{title}** ({role}): {a.get('objective', '')}  \n  файлы: `{files}`")
        text = "\n".join(lines)
        self.chat.add_message("system", text)
        if self.running_project_id:
            self.config.add_chat_message(self.running_project_id, "system", text)
        self._dash().set_plan(plan)
        self._dash().set_phase("executing")

    def _on_agent_role(self, provider_id: str, assignment: dict) -> None:
        panel = self.agent_panels.get(provider_id)
        if panel:
            panel.set_role(assignment)
        self._dash().set_agent(
            provider_id, label=self.config.providers.get(provider_id, {}).get("label"),
            role=assignment.get("role", ""), status="работает")

    def _on_agent_event(self, provider_id: str, kind: str, payload: str) -> None:
        panel = self.agent_panels.get(provider_id)
        if kind == "status":
            self._dash().set_agent(provider_id, status=payload)
        if panel:
            panel.add_event(kind, payload)
            if kind == "status" and payload in (
                    "готово", "ошибка", "отключён", "отключён пользователем",
                    "остановлено"):
                panel.set_live(False)
            if kind == "limits":
                import json
                try:
                    panel.set_limits(json.loads(payload))
                except json.JSONDecodeError:
                    pass
        if kind == "usage":
            import json
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return
            acc = self.live_usage.setdefault(provider_id, {"in": 0, "out": 0})
            acc["in"] += data.get("in", 0)
            acc["out"] += data.get("out", 0)
            self._update_usage_label()

    def _update_usage_label(self) -> None:
        total_in = total_out = 0
        cost = 0.0
        for pid, u in self.live_usage.items():
            provider = self.config.providers.get(pid, {})
            total_in += u["in"]
            total_out += u["out"]
            pcost = u["in"] / 1e6 * provider.get("price_in", 0) \
                + u["out"] / 1e6 * provider.get("price_out", 0)
            cost += pcost
            panel = self.agent_panels.get(pid)
            if panel:
                panel.set_spent(f"сессия: {u['in']}→{u['out']} тк · ${pcost:.4f}")
        self.usage_label.setText(
            f"всего за сессию: {total_in} → {total_out} тк   ·   ~${cost:.4f}")

    def _on_log(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_integration_ready(self, integ: dict) -> None:
        self._last_diff = integ.get("diff", "")
        files = integ.get("changed_files", [])
        conflicts = integ.get("conflicts", [])
        msg = f"Интеграция: {len(files)} файлов изменено"
        if conflicts:
            msg += f", конфликтов: {len(conflicts)}"
        self._on_log(msg)
        self._dash().set_phase("integrating")
        self._dash().set_integration(integ)

    def _on_verification_ready(self, verification: dict) -> None:
        status = verification.get("status", "unknown")
        checks = verification.get("checks", [])
        summary = "  ·  ".join(f"{c['name']}: {c['status']}" for c in checks) or "нет проверок"
        text = f"**Верификация: {status}**  \n{summary}"
        self.chat.add_message("system", text)
        if self.running_project_id:
            self.config.add_chat_message(self.running_project_id, "system", text)
        self._dash().set_phase("verifying")
        self._dash().set_verification(verification)

    def _on_awaiting_approval(self, payload: dict) -> None:
        self._dash().set_phase("awaiting")
        dlg = ApprovalDialog(payload, self._last_diff, parent=self)
        dlg.exec()
        action, push = dlg.decision
        if self.orchestrator is None:
            return
        if action == "approve":
            self.orchestrator.approve(push)
            self._on_log("Изменения одобрены" + (" и пушатся" if push else " (локально)"))
        else:
            self.orchestrator.reject()
            self._on_log("Изменения отклонены — исходный репозиторий не тронут")

    def _on_report_ready(self, report: str, usage: dict) -> None:
        self.chat.add_message("assistant", report)
        if self.running_project_id:
            self.config.add_chat_message(
                self.running_project_id, "assistant", report,
                meta={"usage": usage})

    def _on_run_finished(self, result: dict) -> None:
        self._dash().set_phase("done")
        message = result.get("message", "")
        status = result.get("status")
        icon = {"done": "✔", "partial": "⚠", "cancelled": "⏹"}.get(status, "")
        summary = f"{icon} {message}".strip()
        if summary:
            self.chat.add_message("system", summary)
            if self.running_project_id:
                self.config.add_chat_message(self.running_project_id, "system", summary)
        self._on_log(summary or "Задача завершена")
        self._teardown_run()

    def _on_run_error(self, message: str) -> None:
        self.chat.add_message("system", f"⚠ {message}")
        if self.running_project_id:
            self.config.add_chat_message(self.running_project_id, "system", f"⚠ {message}")
        self._on_log(message)
        self._teardown_run()

    def _teardown_run(self) -> None:
        self.orchestrator = None
        self.running_project_id = None
        self._set_running(False)
        for panel in self.agent_panels.values():
            panel.set_run_active(False)
            panel.set_live(False)

    def _recover_runs(self) -> None:
        """After a restart, resolve runs left at the approval gate."""
        try:
            recon = self.run_store.reconcile_on_startup()
        except Exception:
            return
        for run_id in recon.get("resumable", []):
            record = self.run_store.get(run_id)
            if record is None:
                continue
            project = self.config.get_project(record.project_id)
            if not project or not project.get("local_path"):
                continue
            status = record.verification.get("status", "unknown")
            payload = {
                "status": status,
                "can_autopublish": status not in ("fail", "unknown", "cancelled"),
                "changed_files": record.changed_files,
                "conflicts": [],
                "verification": record.verification,
            }
            self.statusBar().showMessage(
                f"Незавершённый прогон для «{project['name']}» — требуется решение")
            dlg = ApprovalDialog(payload, record.diff, parent=self)
            dlg.setWindowTitle("Восстановление прогона — одобрение")
            dlg.exec()
            action, push = dlg.decision
            if action == "approve":
                res = publish_resumed(self.run_store, record, project, push)
            else:
                res = discard_resumed(self.run_store, record, project)
            self._on_log(res.get("message", ""))

    def closeEvent(self, event) -> None:
        if self.orchestrator:
            self.orchestrator.cancel()
            self.orchestrator.wait(3000)
        if self._limits_checker and self._limits_checker.isRunning():
            self._limits_checker.wait(2000)
        try:
            self.run_store.close()
        except Exception:
            pass
        super().closeEvent(event)
