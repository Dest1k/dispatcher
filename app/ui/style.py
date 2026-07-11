"""Dark theme stylesheet."""

APP_QSS = """
* { font-family: "Segoe UI", "Inter", system-ui, sans-serif; font-size: 13px; }

QMainWindow, QWidget { background: #0e1116; color: #e6edf3; }

#Sidebar { background: #0a0d12; border-right: 1px solid #1c2530; }

QLabel#Brand { font-size: 15px; font-weight: 700; color: #f0f6fc; padding: 4px 2px; }
QLabel#BrandSub { color: #7d8792; font-size: 11px; }

QLabel#SectionTitle { color: #7d8792; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px; padding: 8px 2px 2px 2px; }

QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { padding: 9px 10px; border-radius: 8px; margin: 2px 0; color: #c9d1d9; }
QListWidget::item:selected { background: #1f6feb33; color: #ffffff; }
QListWidget::item:hover { background: #161b22; }

QPushButton { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #2d333b; border-color: #3d444d; }
QPushButton:disabled { color: #6e7681; background: #161b22; }

QPushButton#Primary { background: #238636; border-color: #2ea043; color: white; font-weight: 600; }
QPushButton#Primary:hover { background: #2ea043; }
QPushButton#Primary:disabled { background: #1a3a24; color: #8b9a90; }

QPushButton#Danger { background: #b62324; border-color: #da3633; color: white; }
QPushButton#Danger:hover { background: #da3633; }

QPushButton#Ghost { background: transparent; border: 1px solid #30363d; }
QPushButton#Ghost:hover { background: #161b22; }

QPushButton#IconBtn { background: transparent; border: none; padding: 4px 8px; font-size: 14px; }
QPushButton#IconBtn:hover { background: #21262d; border-radius: 6px; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 8px; padding: 7px 10px; selection-background-color: #1f6feb; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: #388bfd; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #161b22; border: 1px solid #30363d;
    selection-background-color: #1f6feb; }

QScrollArea, #ChatScroll, #ChatInner { background: #0e1116; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #30363d; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #484f58; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }

#ProjectHeader { background: #0a0d12; border-bottom: 1px solid #1c2530; }
QLabel#ProjectName { font-size: 17px; font-weight: 700; color: #f0f6fc; }
QLabel#Meta { color: #8b949e; font-size: 12px; }
QLabel#MetaKey { color: #6e7681; font-size: 11px; }

#Bubble { border-radius: 12px; }
QTextBrowser { background: transparent; border: none; }

#AgentPanel { background: #0a0d12; border: 1px solid #1c2530; border-radius: 12px; }
QLabel#AgentName { font-weight: 700; font-size: 13px; }
QLabel#AgentRole { color: #8b949e; font-size: 11px; }
QLabel#Pill { border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }

#Composer { background: #0a0d12; border-top: 1px solid #1c2530; }
QLabel#RunningBanner { background: #1f6feb22; color: #79c0ff; border-radius: 8px;
    padding: 6px 10px; font-weight: 600; }

QCheckBox { spacing: 8px; }
QTabWidget::pane { border: 1px solid #30363d; border-radius: 8px; top: -1px; }
QTabBar::tab { background: #0d1117; padding: 8px 16px; border: 1px solid #30363d;
    border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; }
QTabBar::tab:selected { background: #161b22; color: #79c0ff; }

QDialog { background: #0e1116; }
QGroupBox { border: 1px solid #30363d; border-radius: 10px; margin-top: 12px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #c9d1d9; font-weight: 700; }
"""
