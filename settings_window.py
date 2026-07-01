"""Settings dialog (port of SettingsView.swift, MVP subset)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from settings import DEFAULT_API_BASE, DEFAULT_GEMINI_MODEL, LANGUAGES, AppSettings

ACCENT = "#7C5CFF"
ACCENT_HOVER = "#9D7FFF"
ACCENT_PRESSED = "#6A4DE0"

# Global QSS applied to the dialog so all child widgets share the same theme.
DIALOG_QSS = f"""
QDialog {{
    background-color: #F4F5F8;
}}
QLabel {{
    color: #1F2330;
    font-size: 13px;
}}
QLabel#sectionTitle {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 0 6px 0;
}}
QFrame#card {{
    background: white;
    border: 1px solid #E3E6EC;
    border-radius: 10px;
}}
QLineEdit, QComboBox, QTextEdit {{
    background: white;
    color: #1F2330;
    border: 1px solid #D2D6DF;
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: white;
    font-size: 13px;
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #6A6F7D;
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: white;
    color: #1F2330;
    border: 1px solid #D2D6DF;
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: white;
    outline: none;
    padding: 4px;
}}
QCheckBox {{
    spacing: 8px;
    color: #1F2330;
    font-size: 13px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid #C3C8D2;
    border-radius: 4px;
    background: white;
}}
QCheckBox::indicator:hover {{
    border: 1px solid {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    image: none;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: #E3E6EC;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: white;
    border: 2px solid {ACCENT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_HOVER};
    border: 2px solid {ACCENT_HOVER};
}}
QPushButton {{
    background: white;
    color: #1F2330;
    border: 1px solid #D2D6DF;
    border-radius: 7px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    border: 1px solid {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background: #EEF0F4;
}}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
    border: 1px solid {ACCENT_HOVER};
    color: white;
}}
QPushButton#primary:pressed {{
    background: {ACCENT_PRESSED};
    border: 1px solid {ACCENT_PRESSED};
}}
"""


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gemini Live Translate Settings")
        self.setMinimumWidth(520)
        self._settings = settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        # ---------- Header ----------
        title = QLabel("Gemini Live Translate Settings")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: #1F2330; padding: 0 0 4px 0;"
        )
        outer.addWidget(title)
        subtitle = QLabel("Real-time speech translation powered by Gemini Live")
        subtitle.setStyleSheet("color: #6A6F7D; font-size: 11px;")
        outer.addWidget(subtitle)

        # ---------- Connection card ----------
        outer.addWidget(self._section_title("CONNECTION"))
        conn_card, conn_form = self._make_card()
        self.api_key_edit = QLineEdit(settings.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your Google Gemini API key")
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key_btn = QPushButton("Show")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setFixedWidth(72)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_btn)
        conn_form.addRow("Gemini API Key", self._wrap(key_row))

        # Optional: override the API base URL for a self-hosted proxy or a
        # regional mirror. Empty falls back to the Google official endpoint.
        self.api_base_edit = QLineEdit(settings.api_base)
        self.api_base_edit.setPlaceholderText(DEFAULT_API_BASE)
        conn_form.addRow("API Base URL", self.api_base_edit)

        self.lang_combo = QComboBox()
        for code, name in LANGUAGES:
            self.lang_combo.addItem(name, code)
        idx = self.lang_combo.findData(settings.target_language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        conn_form.addRow("Translate to", self.lang_combo)
        outer.addWidget(conn_card)

        # ---------- Audio card ----------
        outer.addWidget(self._section_title("AUDIO"))
        audio_card, audio_form = self._make_card()
        self.source_combo = QComboBox()
        self.source_combo.addItem("System Audio (loopback)", "system")
        self.source_combo.addItem("Microphone", "mic")
        idx = self.source_combo.findData(settings.audio_source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        audio_form.addRow("Audio source", self.source_combo)

        self.volume_slider, self.volume_label = self._make_slider(
            int(settings.playback_volume * 100), lambda v: f"{v}%"
        )
        audio_form.addRow("Playback volume", self._wrap_slider(self.volume_slider, self.volume_label))

        self.echo_check = QCheckBox("Echo target-language audio (play translated speech)")
        self.echo_check.setChecked(settings.echo_target_language)
        audio_form.addRow("", self.echo_check)
        outer.addWidget(audio_card)

        # ---------- Appearance card ----------
        outer.addWidget(self._section_title("APPEARANCE"))
        app_card, app_form = self._make_card()
        self.font_slider, self.font_label = self._make_slider(
            settings.font_size, lambda v: f"{v} pt"
        )
        self.font_slider.setRange(14, 60)
        app_form.addRow("Caption font size", self._wrap_slider(self.font_slider, self.font_label))

        self.opacity_slider, self.opacity_label = self._make_slider(
            int(settings.bg_opacity * 100), lambda v: f"{v}%"
        )
        self.opacity_slider.setRange(20, 95)
        app_form.addRow("Background opacity", self._wrap_slider(self.opacity_slider, self.opacity_label))

        self.show_original_check = QCheckBox("Show original (source-language) transcript")
        self.show_original_check.setChecked(settings.show_original)
        app_form.addRow("", self.show_original_check)
        outer.addWidget(app_card)

        # ---------- Advanced card ----------
        outer.addWidget(self._section_title("ADVANCED"))
        adv_card, adv_form = self._make_card()
        self.model_edit = QLineEdit(settings.gemini_model)
        self.model_edit.setPlaceholderText(DEFAULT_GEMINI_MODEL)
        adv_form.addRow("Model ID", self.model_edit)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(settings.system_prompt)
        self.prompt_edit.setMaximumHeight(90)
        self.prompt_edit.setPlaceholderText(
            "Optional custom instructions sent to the model as systemInstruction."
        )
        adv_form.addRow("System prompt", self.prompt_edit)
        outer.addWidget(adv_card)

        outer.addStretch(1)

        # ---------- Buttons ----------
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setObjectName("primary")
        btns.button(QDialogButtonBox.Save).setText("Save")
        btns.button(QDialogButtonBox.Cancel).setText("Cancel")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

        self.setStyleSheet(DIALOG_QSS)

    # ---------- helpers ----------

    def _section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionTitle")
        return lbl

    def _make_card(self) -> tuple[QFrame, QFormLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        return card, form

    def _make_slider(self, value: int, fmt) -> tuple[QSlider, QLabel]:
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 100)
        s.setValue(value)
        lbl = QLabel(fmt(value))
        lbl.setMinimumWidth(48)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v: lbl.setText(fmt(v)))
        return s, lbl

    def _wrap(self, layout: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    def _wrap_slider(self, slider: QSlider, label: QLabel) -> QWidget:
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1)
        h.addWidget(label)
        w = QWidget()
        w.setLayout(h)
        return w

    def _toggle_key_visibility(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.show_key_btn.setText("Hide" if checked else "Show")

    def apply_to(self, settings: AppSettings) -> None:
        settings.api_key = self.api_key_edit.text().strip()
        # Empty api_base → use official Google endpoint (handled by client).
        settings.api_base = self.api_base_edit.text().strip() or DEFAULT_API_BASE
        settings.target_language = self.lang_combo.currentData() or "zh-CN"
        settings.audio_source = self.source_combo.currentData() or "system"
        settings.font_size = self.font_slider.value()
        settings.bg_opacity = self.opacity_slider.value() / 100.0
        settings.playback_volume = self.volume_slider.value() / 100.0
        settings.echo_target_language = self.echo_check.isChecked()
        settings.system_prompt = self.prompt_edit.toPlainText().strip()
        settings.show_original = self.show_original_check.isChecked()
        settings.gemini_model = self.model_edit.text().strip() or DEFAULT_GEMINI_MODEL
