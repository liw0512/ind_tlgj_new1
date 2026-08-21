from __future__ import annotations


TOKENS = {
    "bg": "#0B1220",
    "panel": "#111827",
    "panel_alt": "#172033",
    "border": "#263449",
    "text": "#E8EEF7",
    "muted": "#8FA0B8",
    "accent": "#3B82F6",
    "accent_hover": "#2563EB",
    "success": "#2DD4BF",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "offline": "#64748B",
}


def build_stylesheet() -> str:
    t = TOKENS
    return f"""
    QWidget {{
        color: {t['text']};
        background: transparent;
        font-size: 14px;
    }}

    QMainWindow, QWidget#appRoot {{
        background: {t['bg']};
    }}

    QFrame#sidebar {{
        background: {t['panel']};
        border-right: 1px solid {t['border']};
    }}

    QLabel#brandTitle {{
        color: {t['text']};
        font-size: 19px;
        font-weight: 700;
    }}

    QLabel#brandSubtitle,
    QLabel[role="muted"] {{
        color: {t['muted']};
    }}

    QPushButton[role="nav"] {{
        text-align: left;
        padding: 11px 14px;
        border: 1px solid transparent;
        border-radius: 8px;
        color: {t['muted']};
        background: transparent;
        font-weight: 500;
    }}

    QPushButton[role="nav"]:hover {{
        color: {t['text']};
        background: {t['panel_alt']};
    }}

    QPushButton[role="nav"]:checked {{
        color: {t['text']};
        background: {t['panel_alt']};
        border: 1px solid {t['border']};
        font-weight: 700;
    }}

    QFrame[role="card"] {{
        background: {t['panel']};
        border: 1px solid {t['border']};
        border-radius: 12px;
    }}

    QLabel[role="sectionTitle"] {{
        font-size: 16px;
        font-weight: 700;
    }}

    QLabel[role="metricTitle"] {{
        color: {t['muted']};
        font-size: 13px;
    }}

    QLabel[role="metricValue"] {{
        color: {t['text']};
        font-size: 28px;
        font-weight: 700;
    }}

    QLabel[role="metricUnit"] {{
        color: {t['muted']};
        font-size: 12px;
    }}

    QLabel[role="pill"] {{
        padding: 5px 10px;
        border-radius: 10px;
        font-size: 12px;
        font-weight: 700;
    }}

    QLabel[role="pill"][state="normal"] {{
        color: {t['success']};
        background: rgba(45, 212, 191, 0.12);
        border: 1px solid rgba(45, 212, 191, 0.35);
    }}

    QLabel[role="pill"][state="warning"] {{
        color: {t['warning']};
        background: rgba(245, 158, 11, 0.12);
        border: 1px solid rgba(245, 158, 11, 0.35);
    }}

    QLabel[role="pill"][state="danger"] {{
        color: {t['danger']};
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.35);
    }}

    QLabel[role="pill"][state="offline"] {{
        color: {t['offline']};
        background: rgba(100, 116, 139, 0.12);
        border: 1px solid rgba(100, 116, 139, 0.35);
    }}

    QFrame#topBar {{
        background: {t['bg']};
        border-bottom: 1px solid {t['border']};
    }}

    QScrollArea {{
        border: none;
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}

    QScrollBar::handle:vertical {{
        background: {t['border']};
        border-radius: 4px;
        min-height: 36px;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    """
