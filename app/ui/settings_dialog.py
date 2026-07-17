"""Settings: per-model config (model id, effort, key, price) + orchestration."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..providers import make_adapter


class ProviderForm(QWidget):
    def __init__(self, provider: dict):
        super().__init__()
        self.provider = provider
        self.is_cli = provider.get("kind") == "cli"
        form = QFormLayout(self)
        form.setSpacing(10)

        self.enabled = QCheckBox("Использовать этот ИИ")
        self.enabled.setChecked(provider.get("enabled", True))
        form.addRow(self.enabled)

        self.api_key = None
        if self.is_cli:
            # Official CLI session — no keys are requested or stored.
            self.session_label = QLabel(self._session_text())
            self.session_label.setWordWrap(True)
            form.addRow("Авторизация", self.session_label)
        else:
            self.api_key = QLineEdit(provider.get("api_key", ""))
            self.api_key.setEchoMode(QLineEdit.Password)
            self.api_key.setPlaceholderText("API-ключ")
            key_row = QHBoxLayout()
            key_row.addWidget(self.api_key, 1)
            self.show_key = QPushButton("👁")
            self.show_key.setObjectName("IconBtn")
            self.show_key.setCheckable(True)
            self.show_key.toggled.connect(
                lambda on: self.api_key.setEchoMode(
                    QLineEdit.Normal if on else QLineEdit.Password))
            key_row.addWidget(self.show_key)
            key_wrap = QWidget()
            key_wrap.setLayout(key_row)
            form.addRow("API-ключ", key_wrap)

        if self.is_cli:
            # Model choice from the CLI's own local cache (editable: any id).
            self.model = QComboBox()
            self.model.setEditable(True)
            self._cli_efforts: dict[str, list[str]] = {}
            self._populate_cli_models()
            form.addRow("Модель", self.model)
        else:
            self.model = QLineEdit(provider.get("model", ""))
            form.addRow("Модель", self.model)

        self.catalog_label = QLabel("")
        self.catalog_label.setObjectName("Meta")
        self.catalog_label.setWordWrap(True)
        if self.is_cli:
            self.model.currentTextChanged.connect(self._update_catalog)
        else:
            self.model.textChanged.connect(self._update_catalog)
        form.addRow("", self.catalog_label)
        self._update_catalog()

        self.effort = QComboBox()
        self._populate_efforts(self._model_text())
        if self.is_cli:
            self.model.currentTextChanged.connect(self._populate_efforts)
        form.addRow("Effort / уровень рассуждений", self.effort)

        self.base_url = None
        self.max_tokens = None
        self.price_in = self.price_out = None
        if not self.is_cli:
            self.base_url = QLineEdit(provider.get("base_url", ""))
            form.addRow("Base URL", self.base_url)

            self.max_tokens = QSpinBox()
            self.max_tokens.setRange(256, 128000)
            self.max_tokens.setSingleStep(1000)
            self.max_tokens.setValue(int(provider.get("max_tokens", 16000)))
            form.addRow("Max tokens (на ответ)", self.max_tokens)

            price_row = QHBoxLayout()
            self.price_in = QDoubleSpinBox()
            self.price_in.setRange(0, 1000)
            self.price_in.setDecimals(2)
            self.price_in.setValue(float(provider.get("price_in", 0)))
            self.price_out = QDoubleSpinBox()
            self.price_out.setRange(0, 1000)
            self.price_out.setDecimals(2)
            self.price_out.setValue(float(provider.get("price_out", 0)))
            price_row.addWidget(QLabel("вход $/1M"))
            price_row.addWidget(self.price_in)
            price_row.addWidget(QLabel("выход $/1M"))
            price_row.addWidget(self.price_out)
            price_wrap = QWidget()
            price_wrap.setLayout(price_row)
            form.addRow("Цена", price_wrap)
        else:
            billing = QLabel("Оплата: включено в подписку "
                             f"({provider.get('subscription_tier') or 'CLI'}). "
                             "Квота подпиской не раскрывается — Dispatcher не "
                             "выдумывает остаток.")
            billing.setWordWrap(True)
            billing.setObjectName("Meta")
            form.addRow("Биллинг", billing)

            self.cli_timeout = QSpinBox()
            self.cli_timeout.setRange(60, 14400)
            self.cli_timeout.setSuffix(" с")
            self.cli_timeout.setValue(int(provider.get("cli_timeout", 1200)))
            self.cli_timeout.setToolTip(
                "Максимум на один вызов CLI; максимальные уровни рассуждений "
                "могут думать долго.")
            form.addRow("Таймаут вызова CLI", self.cli_timeout)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Проверить подключение")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test)
        self.test_result = QLabel("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        test_wrap = QWidget()
        test_wrap.setLayout(test_row)
        form.addRow("", test_wrap)

    # ---- CLI-session helpers -------------------------------------------
    def _session_text(self) -> str:
        from ..cliagents import detect
        st = detect(self.provider.get("cli_flavor", ""), run_commands=False)
        if not st.installed:
            return ("✗ CLI не установлен — установи официальный клиент и "
                    "выполни вход. Ключи не нужны.")
        if st.authenticated is True:
            return f"✔ Сессия официального CLI: {st.auth_source}"
        return (f"⚠ CLI установлен ({st.binary}), но вход не выполнен — "
                "запусти его и авторизуйся. Ключи не нужны.")

    def _model_text(self) -> str:
        return (self.model.currentText() if self.is_cli
                else self.model.text()).strip()

    def _populate_cli_models(self) -> None:
        from ..cliagents import discover_models
        models, efforts, _src = discover_models(self.provider.get("cli_flavor", ""))
        self._cli_efforts = efforts
        current = self.provider.get("model", "")
        items = list(dict.fromkeys(([current] if current else []) + models))
        self.model.addItems(items)
        if current:
            self.model.setCurrentText(current)

    def _populate_efforts(self, model_text: str = "") -> None:
        current = (self.effort.currentText()
                   or self.provider.get("effort", ""))
        options = None
        if self.is_cli:
            options = (self._cli_efforts.get(model_text.strip())
                       or self._cli_efforts.get("*"))
        if not options:
            options = self.provider.get("effort_options", [])
        self.effort.blockSignals(True)
        self.effort.clear()
        self.effort.addItems(list(options) + ["none"])
        idx = self.effort.findText(current)
        self.effort.setCurrentIndex(idx if idx >= 0 else 0)
        self.effort.blockSignals(False)

    def _test(self) -> None:
        self.test_result.setText("проверяю…")
        self.test_result.setStyleSheet("color:#8b949e;")
        self.test_btn.setEnabled(False)
        cfg = self.collect()
        try:
            adapter = make_adapter(cfg)
            adapter.ping()
            self.test_result.setText("✔ подключение работает")
            self.test_result.setStyleSheet("color:#3fb950;")
        except Exception as exc:
            self.test_result.setText(f"✗ {exc}"[:120])
            self.test_result.setStyleSheet("color:#f85149;")
        finally:
            self.test_btn.setEnabled(True)

    def _update_catalog(self) -> None:
        model = self._model_text()
        if self.is_cli:
            from ..capabilities import describe
            self.catalog_label.setText("ℹ " + describe(model))
        else:
            from ..catalog import describe
            self.catalog_label.setText("ℹ " + describe(model))

    def collect(self) -> dict:
        updated = dict(self.provider)
        updated.update({
            "enabled": self.enabled.isChecked(),
            "model": self._model_text(),
            "effort": self.effort.currentText(),
        })
        if self.is_cli:
            updated["cli_timeout"] = self.cli_timeout.value()
        else:
            updated.update({
                "api_key": self.api_key.text().strip(),
                "base_url": self.base_url.text().strip(),
                "max_tokens": self.max_tokens.value(),
                "price_in": self.price_in.value(),
                "price_out": self.price_out.value(),
            })
        return updated


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(560)
        self.setMinimumHeight(560)

        root = QVBoxLayout(self)
        title = QLabel("Модели и оркестрация")
        title.setStyleSheet("font-size:16px; font-weight:700; color:#f0f6fc;")
        root.addWidget(title)

        from ..security import secret_store
        if secret_store.is_secure():
            sec_text = f"🔒 Ключи хранятся в системном хранилище ({secret_store.backend_name()})"
            sec_color = "#3fb950"
        else:
            sec_text = ("⚠ Системное хранилище недоступно — ключи в файле с правами 0600. "
                        "Установи пакет keyring для хранения в ОС.")
            sec_color = "#d29922"
        sec_label = QLabel(sec_text)
        sec_label.setWordWrap(True)
        sec_label.setStyleSheet(f"color:{sec_color}; font-size:12px;")
        root.addWidget(sec_label)

        self.tabs = QTabWidget()
        self.forms: dict[str, ProviderForm] = {}
        for pid in config.provider_order():
            provider = config.providers.get(pid)
            if provider is None:
                continue
            form = ProviderForm(provider)
            self.forms[pid] = form
            self.tabs.addTab(form, provider["short"])

        # Orchestration tab
        orch_tab = QWidget()
        orch_form = QFormLayout(orch_tab)
        o = config.orchestration
        self.mode = QComboBox()
        self.mode.addItem("Ведущий (Claude раздаёт роли)", "lead")
        self.mode.addItem("Каждый сам за себя", "auto")
        self.mode.setCurrentIndex(0 if o.get("mode") == "lead" else 1)
        orch_form.addRow("Режим", self.mode)

        self.lead = QComboBox()
        order = [pid for pid in config.provider_order()
                 if pid in config.providers]
        for pid in order:
            self.lead.addItem(config.providers[pid]["short"], pid)
        lead_pid = o.get("lead_provider", "claude_cli")
        self.lead.setCurrentIndex(order.index(lead_pid)
                                  if lead_pid in order else 0)
        orch_form.addRow("Ведущая модель", self.lead)

        self.execution_mode = QComboBox()
        self.execution_mode.addItem("Один исполнитель (solo)", "solo")
        self.execution_mode.addItem("Пара: исполнитель + ревьюер (pair)", "pair")
        self.execution_mode.addItem("Адаптивно (adaptive)", "adaptive")
        self.execution_mode.addItem("Полный консилиум (full_council)", "full_council")
        em_idx = self.execution_mode.findData(o.get("execution_mode", "pair"))
        self.execution_mode.setCurrentIndex(max(0, em_idx))
        orch_form.addRow("Режим выполнения", self.execution_mode)

        self.sandbox_mode = QComboBox()
        self.sandbox_mode.addItem("Ограниченный (без секретов, без сети) — рекомендуется", "restricted")
        self.sandbox_mode.addItem("Docker · контейнер на агента (состояние сохраняется)", "docker")
        self.sandbox_mode.addItem("Docker · контейнер на команду (без состояния)", "docker_command")
        self.sandbox_mode.addItem("⚠ Небезопасный локальный (полное окружение хоста)", "unsafe_local")
        sb_idx = self.sandbox_mode.findData(o.get("sandbox_mode", "restricted"))
        self.sandbox_mode.setCurrentIndex(max(0, sb_idx))
        self.sandbox_mode.currentIndexChanged.connect(self._on_sandbox_changed)
        orch_form.addRow("Песочница команд", self.sandbox_mode)

        self.sandbox_warning = QLabel("")
        self.sandbox_warning.setWordWrap(True)
        orch_form.addRow("", self.sandbox_warning)

        self.allow_network = QCheckBox("Разрешить сеть в песочнице (установка пакетов и т.п.)")
        self.allow_network.setChecked(o.get("allow_network", False))
        orch_form.addRow(self.allow_network)

        self.require_verification = QCheckBox("Требовать верификацию перед публикацией")
        self.require_verification.setChecked(o.get("require_verification", True))
        orch_form.addRow(self.require_verification)

        self.council_planning = QCheckBox(
            "Совет по плану: red team критикует план перед выполнением "
            "(+1 вызов модели)")
        self.council_planning.setChecked(o.get("council_planning", False))
        orch_form.addRow(self.council_planning)

        self.deliberate = QCheckBox(
            "Обсуждение подхода советом до реализации "
            "(архитектор → red team → осуществимость → синтез)")
        self.deliberate.setChecked(o.get("deliberate", False))
        orch_form.addRow(self.deliberate)

        self.dag_execution = QCheckBox(
            "Граф задач: разбить на подзадачи с зависимостями и выполнять "
            "послойно (зависимые видят результат предыдущих)")
        self.dag_execution.setChecked(o.get("dag_execution", False))
        orch_form.addRow(self.dag_execution)

        self.budget_usd = QDoubleSpinBox()
        self.budget_usd.setRange(0.0, 10000.0)
        self.budget_usd.setDecimals(2)
        self.budget_usd.setSingleStep(0.5)
        self.budget_usd.setValue(float(o.get("budget_usd", 0.0)))
        self.budget_usd.setToolTip("0 = без ограничения. При достижении лимита "
                                   "агенты плавно останавливаются.")
        orch_form.addRow("Бюджет прогона, $", self.budget_usd)

        self.stream = QCheckBox("Стриминг ответов (OpenAI-совместимые; Claude — без стриминга)")
        self.stream.setChecked(o.get("stream", False))
        orch_form.addRow(self.stream)

        self.auto_push = QCheckBox("Автоматически пушить (не рекомендуется)")
        self.auto_push.setChecked(o.get("auto_push", False))
        orch_form.addRow(self.auto_push)

        self.auto_draft_pr = QCheckBox(
            "Открывать черновой PR после пуша (нужен github-токен и репозиторий)")
        self.auto_draft_pr.setChecked(o.get("auto_draft_pr", False))
        orch_form.addRow(self.auto_draft_pr)

        self.max_iters = QSpinBox()
        self.max_iters.setRange(4, 100)
        self.max_iters.setValue(int(o.get("max_tool_iterations", 24)))
        orch_form.addRow("Лимит шагов инструментов на модель", self.max_iters)

        self.command_timeout = QSpinBox()
        self.command_timeout.setRange(5, 7200)
        self.command_timeout.setSuffix(" с")
        self.command_timeout.setValue(int(o.get("command_timeout", 300)))
        self.command_timeout.setToolTip("Максимум на одну команду в песочнице; "
                                        "по истечении процесс и его дерево убиваются.")
        orch_form.addRow("Таймаут команды", self.command_timeout)

        self.verify_timeout = QSpinBox()
        self.verify_timeout.setRange(10, 14400)
        self.verify_timeout.setSuffix(" с")
        self.verify_timeout.setValue(int(o.get("verify_timeout", 900)))
        self.verify_timeout.setToolTip("Максимум на весь этап верификации перед "
                                       "публикацией.")
        orch_form.addRow("Таймаут верификации", self.verify_timeout)

        self.commit_prefix = QLineEdit(o.get("commit_prefix", ""))
        self.commit_prefix.setPlaceholderText("напр. [AI] ")
        orch_form.addRow("Префикс коммита", self.commit_prefix)

        self.tabs.addTab(orch_tab, "Оркестрация")
        self._on_sandbox_changed()
        root.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _on_sandbox_changed(self) -> None:
        mode = self.sandbox_mode.currentData()
        if mode == "unsafe_local":
            self.sandbox_warning.setText(
                "⚠ ОПАСНО: команды моделей получат полное окружение хоста "
                "(включая ключи и токены) и доступ к сети. Используй только для "
                "доверенных задач.")
            self.sandbox_warning.setStyleSheet("color:#f85149; font-weight:600;")
        elif mode == "docker":
            self.sandbox_warning.setText(
                "Требуется установленный Docker. Если он недоступен — будет "
                "использована ограниченная песочница.")
            self.sandbox_warning.setStyleSheet("color:#8b949e;")
        else:
            self.sandbox_warning.setText(
                "Команды выполняются без секретов хоста; сеть по умолчанию "
                "не гарантированно изолирована без Docker.")
            self.sandbox_warning.setStyleSheet("color:#8b949e;")

    def _save(self) -> None:
        for pid, form in self.forms.items():
            self.config.providers[pid] = form.collect()
        self.config.orchestration.update({
            "mode": self.mode.currentData(),
            "lead_provider": self.lead.currentData(),
            "execution_mode": self.execution_mode.currentData(),
            "sandbox_mode": self.sandbox_mode.currentData(),
            "allow_network": self.allow_network.isChecked(),
            "require_verification": self.require_verification.isChecked(),
            "council_planning": self.council_planning.isChecked(),
            "deliberate": self.deliberate.isChecked(),
            "dag_execution": self.dag_execution.isChecked(),
            "budget_usd": self.budget_usd.value(),
            "stream": self.stream.isChecked(),
            "auto_push": self.auto_push.isChecked(),
            "auto_draft_pr": self.auto_draft_pr.isChecked(),
            "max_tool_iterations": self.max_iters.value(),
            "command_timeout": self.command_timeout.value(),
            "verify_timeout": self.verify_timeout.value(),
            "commit_prefix": self.commit_prefix.text(),
        })
        self.config.save()
        self.accept()
