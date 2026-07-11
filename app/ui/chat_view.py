"""Markdown chat transcript."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLabel, QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

_BUBBLE_CSS = """
h1,h2,h3 { color:#f0f6fc; } h1{font-size:18px;} h2{font-size:16px;} h3{font-size:14px;}
p,li { color:#e6edf3; line-height:150%; }
code { background:#161b22; color:#79c0ff; padding:1px 4px; border-radius:4px;
       font-family:'Consolas','Menlo',monospace; }
pre { background:#161b22; padding:10px; border-radius:8px;
      font-family:'Consolas','Menlo',monospace; color:#c9d1d9; }
a { color:#58a6ff; }
table { border-collapse:collapse; } td,th { border:1px solid #30363d; padding:4px 8px; }
blockquote { border-left:3px solid #30363d; margin-left:0; padding-left:10px; color:#8b949e; }
"""

_ROLE_META = {
    "user":   ("Ты", "#1f6feb"),
    "assistant": ("Отчёт", "#238636"),
    "system": ("Система", "#6e7681"),
    "steering": ("Инструкция на ходу", "#8957e5"),
}


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str):
        super().__init__()
        label_text, color = _ROLE_META.get(role, ("Система", "#6e7681"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        head = QLabel(label_text)
        head.setStyleSheet(f"color:{color}; font-weight:700; font-size:11px; "
                           "text-transform:uppercase; letter-spacing:.5px;")
        layout.addWidget(head)

        body = QTextBrowser()
        body.setObjectName("Bubble")
        body.setOpenExternalLinks(True)
        body.document().setDefaultStyleSheet(_BUBBLE_CSS)
        body.setMarkdown(text)
        body.setFrameShape(QFrame.NoFrame)
        # Size the browser to its content so the bubble grows naturally.
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.document().setTextWidth(720)
        height = int(body.document().size().height()) + 12
        body.setFixedHeight(max(40, min(height, 20000)))
        bg = "#12161c" if role != "user" else "#0d1b2f"
        body.setStyleSheet(f"#Bubble {{ background:{bg}; border:1px solid #1c2530; "
                           "border-radius:12px; padding:10px 14px; }}")
        layout.addWidget(body)


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setObjectName("ChatScroll")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("ChatInner")
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(24, 18, 24, 18)
        self.vbox.setSpacing(14)
        self.vbox.addStretch(1)
        self.setWidget(self.inner)

    def clear(self) -> None:
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_message(self, role: str, text: str) -> None:
        bubble = MessageBubble(role, text)
        self.vbox.insertWidget(self.vbox.count() - 1, bubble)
        self._scroll_bottom()

    def load(self, messages: list[dict]) -> None:
        self.clear()
        for msg in messages:
            self.add_message(msg.get("role", "system"), msg.get("text", ""))

    def _scroll_bottom(self) -> None:
        bar = self.verticalScrollBar()
        # defer so layout settles first
        from PySide6.QtCore import QTimer
        QTimer.singleShot(30, lambda: bar.setValue(bar.maximum()))
