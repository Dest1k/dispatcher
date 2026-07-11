"""Create / edit a project: local working dir + GitHub repo."""
from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import git_service


def _parse_github(url_or_repo: str) -> tuple[str, str]:
    """Return (owner/repo, https_url) from either an owner/repo slug or a URL."""
    text = url_or_repo.strip()
    if not text:
        return "", ""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", text)
    if m:
        slug = f"{m.group(1)}/{m.group(2)}"
        return slug, f"https://github.com/{slug}.git"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        return text, f"https://github.com/{text}.git"
    return text, text  # arbitrary git URL


class ProjectDialog(QDialog):
    def __init__(self, config, project: dict | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.project = project
        self.setWindowTitle("Проект" if project else "Новый проект")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(project.get("name", "") if project else "")
        self.name.setPlaceholderText("Название проекта")
        form.addRow("Название", self.name)

        self.local_path = QLineEdit(project.get("local_path", "") if project else "")
        self.local_path.setPlaceholderText("C:\\projects\\my-app")
        path_row = QHBoxLayout()
        path_row.addWidget(self.local_path, 1)
        browse = QPushButton("Выбрать…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        path_wrap = QWidget()
        path_wrap.setLayout(path_row)
        form.addRow("Локальная папка", path_wrap)

        self.github = QLineEdit(project.get("github_repo", "") if project else "")
        self.github.setPlaceholderText("owner/repo  или  https://github.com/owner/repo")
        form.addRow("GitHub репозиторий", self.github)

        self.branch = QLineEdit(project.get("branch", "main") if project else "main")
        form.addRow("Ветка для пуша", self.branch)

        self.token = QLineEdit(project.get("github_token", "") if project else "")
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("GitHub token (для пуша по HTTPS; можно оставить пустым, если настроен git)")
        form.addRow("GitHub token", self.token)

        root.addLayout(form)

        self.hint = QLabel("")
        self.hint.setObjectName("Meta")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        self.clone_btn = QPushButton("Клонировать репозиторий в папку")
        self.clone_btn.setObjectName("Ghost")
        self.clone_btn.clicked.connect(self._clone)
        buttons.addWidget(self.clone_btn)
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выбери папку проекта",
                                                self.local_path.text() or "")
        if path:
            self.local_path.setText(path)

    def _clone(self) -> None:
        slug, url = _parse_github(self.github.text())
        dest = self.local_path.text().strip()
        if not url or not dest:
            self.hint.setText("Укажи GitHub-репозиторий и локальную папку для клонирования.")
            return
        self.hint.setText("Клонирую…")
        self.clone_btn.setEnabled(False)
        try:
            git_service.clone(url, dest, self.token.text().strip())
            self.hint.setText(f"✔ Репозиторий склонирован в {dest}")
        except Exception as exc:
            self.hint.setText(f"Ошибка клонирования: {exc}")
        finally:
            self.clone_btn.setEnabled(True)

    def _save(self) -> None:
        name = self.name.text().strip()
        local = self.local_path.text().strip()
        if not name or not local:
            QMessageBox.warning(self, "Проверь поля",
                                "Нужны название и локальная папка.")
            return
        slug, url = _parse_github(self.github.text())
        data = {
            "name": name,
            "local_path": local,
            "github_repo": slug,
            "github_url": url,
            "branch": self.branch.text().strip() or "main",
            "github_token": self.token.text().strip(),
        }
        if self.project:
            self.config.update_project(self.project["id"], **data)
            self.saved_id = self.project["id"]
        else:
            created = self.config.add_project(
                name, local, slug, url, data["branch"], data["github_token"])
            self.saved_id = created["id"]
        self.accept()
