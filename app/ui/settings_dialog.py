"""Settings: per-model config (model id, effort, key, price) + orchestration."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..config import PROVIDER_ORDER
from ..providers import make_adapter


class ProviderForm(QWidget):
    def __init__(self, provider: dict):
        super().__init__()
        self.provider = provider
        form = QFormLayout(self)
        form.setSpacing(10)

        self.enabled = QCheckBox("Использовать этот ИИ")
        self.enabled.setChecked(provider.get("enabled", True))
        form.addRow(self.enabled)

        self.api_key = QLineEdit(provider.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("API-ключ")
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key, 1)
        self.show_key = QPushButton("👁")
        self.show_key.setObjectName("IconBtn")
        self.show_key.setCheckable(True)
        self.show_key.toggled.connect(
            lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        form.addRow("API-ключ", key_wrap)

        self.model = QLineEdit(provider.get("model", ""))
        form.addRow("Модель", self.model)

        self.catalog_label = QLabel("")
        self.catalog_label.setObjectName("Meta")
        self.catalog_label.setWordWrap(True)
        self.model.textChanged.connect(self._update_catalog)
        form.addRow("", self.catalog_label)
        self._update_catalog()

        self.effort = QComboBox()
        self.effort.addItems(provider.get("effort_options", []) + ["none"])
        current = provider.get("effort", "")
        idx = self.effort.findText(current)
        if idx >= 0:
            self.effort.setCurrentIndex(idx)
        form.addRow("Effort / уровень рассуждений", self.effort)

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
        from ..catalog import describe
        self.catalog_label.setText("ℹ " + describe(self.model.text().strip()))

    def collect(self) -> dict:
        updated = dict(self.provider)
        updated.update({
            "enabled": self.enabled.isChecked(),
            "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(),
            "effort": self.effort.currentText(),
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
        for pid in PROVIDER_ORDER:
            provider = config.providers[pid]
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
        for pid in PROVIDER_ORDER:
            self.lead.addItem(config.providers[pid]["short"], pid)
        li = PROVIDER_ORDER.index(o.get("lead_provider", "anthropic")) \
            if o.get("lead_provider") in PROVIDER_ORDER else 0
        self.lead.setCurrentIndex(li)
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
        self.sandbox_mode.addItem("Docker (строгая изоляция, нужен Docker)", "docker")
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

        self.max_iters = QSpinBox()
        self.max_iters.setRange(4, 100)
        self.max_iters.setValue(int(o.get("max_tool_iterations", 24)))
        orch_form.addRow("Лимит шагов инструментов на модель", self.max_iters)

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
            "budget_usd": self.budget_usd.value(),
            "stream": self.stream.isChecked(),
            "auto_push": self.auto_push.isChecked(),
            "max_tool_iterations": self.max_iters.value(),
            "commit_prefix": self.commit_prefix.text(),
        })
        self.config.save()
        self.accept()
