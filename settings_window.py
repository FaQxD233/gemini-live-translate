"""Settings dialog (port of SettingsView.swift, MVP subset)."""
from __future__ import annotations

import asyncio
import json
import threading

import websockets
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gemini_client import GEMINI_WS_PATH
from i18n import tr
from settings import DEFAULT_API_BASE, DEFAULT_GEMINI_MODEL, LANGUAGES, AppSettings
from theme import ACCENT, ACCENT_BRIGHT as ACCENT_HOVER, ACCENT_PRESSED, FONT_FAMILY_QSS

# Global QSS applied to the dialog so all child widgets share the same theme.
DIALOG_QSS = f"""
QDialog {{
    background-color: #F4F5F8;
    font-family: {FONT_FAMILY_QSS};
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
    padding: 8px 16px 6px 16px;
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
    padding: 8px 16px 6px 16px;
}}
QPushButton#testBtn {{
    background: white;
    border: 1px solid {ACCENT};
    color: {ACCENT};
    font-weight: 600;
}}
QPushButton#testBtn:hover {{
    background: {ACCENT};
    color: white;
}}
QPushButton#testBtn:disabled {{
    color: #9AA0AB;
    border-color: #D2D6DF;
}}
QLabel#testResultOk   {{ color: #1A8A4A; font-size: 12px; }}
QLabel#testResultFail {{ color: #C0392B; font-size: 12px; }}
"""


def _build_ws_url(api_base: str, api_key: str) -> str:
    """Mirror GeminiClient._build_ws_url for the connection test."""
    base = (api_base or DEFAULT_API_BASE).rstrip("/")
    if base.startswith("https://"):
        ws = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws = "ws://" + base[len("http://"):]
    else:
        ws = base
    return f"{ws}{GEMINI_WS_PATH}?key={api_key}"


async def _do_test_connection(api_key: str, api_base: str, model: str) -> tuple[bool, str]:
    """Open a one-shot Gemini Live setup handshake and report whether it
    succeeded. Runs inside the worker thread's event loop."""
    url = _build_ws_url(api_base, api_key)
    setup = {
        "model": model or DEFAULT_GEMINI_MODEL,
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "translationConfig": {
                "targetLanguageCode": "en",
                "echoTargetLanguage": False,
            },
        },
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "contextWindowCompression": {
            "triggerTokens": "0",
            "slidingWindow": {"targetTokens": "0"},
        },
    }
    async with websockets.connect(
        url,
        max_size=2 ** 22,
        open_timeout=8,
        close_timeout=2,
    ) as ws_conn:
        await ws_conn.send(json.dumps({"setup": setup}))
        try:
            raw = await asyncio.wait_for(ws_conn.recv(), timeout=8)
        except asyncio.TimeoutError:
            return False, "Setup timed out"
        try:
            root = json.loads(raw)
        except Exception:
            return False, "Invalid response"
        if isinstance(root, dict):
            err = root.get("error")
            if isinstance(err, dict):
                return False, err.get("message", "Unknown error")
            if "setupComplete" in root:
                return True, "OK"
        return True, "OK"


def _run_test(api_key: str, api_base: str, model: str) -> tuple[bool, str]:
    try:
        return asyncio.run(_do_test_connection(api_key, api_base, model))
    except websockets.InvalidStatusCode as e:
        return False, f"HTTP {e.status_code}"
    except Exception as e:
        return False, str(e)


class _ConnectionTester(QObject):
    """Runs the connection test off the UI thread and emits the result.

    Lives as a child of the dialog so its signal is delivered on the GUI
    thread via Qt's queued connections.
    """
    result = Signal(bool, str)

    def run(self, api_key: str, api_base: str, model: str) -> None:
        threading.Thread(
            target=self._worker,
            args=(api_key, api_base, model),
            daemon=True,
        ).start()

    def _worker(self, api_key: str, api_base: str, model: str) -> None:
        ok, msg = _run_test(api_key, api_base, model)
        self.result.emit(ok, msg)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(520)
        self._settings = settings

        # UI-7: connection tester (child of dialog → signal delivered on GUI thread)
        self._tester = _ConnectionTester(self)
        self._tester.result.connect(self._on_test_result)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        # ---------- Header ----------
        title = QLabel(tr("settings.title"))
        title.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #1F2330; padding: 0 0 4px 0;"
        )
        outer.addWidget(title)
        subtitle = QLabel(tr("settings.subtitle"))
        subtitle.setStyleSheet("color: #6A6F7D; font-size: 11px;")
        outer.addWidget(subtitle)

        # ---------- Connection card ----------
        outer.addWidget(self._section_title(tr("settings.section.connection"), "🔗"))
        conn_card, conn_form = self._make_card()
        self.api_key_edit = QLineEdit(settings.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(tr("settings.api_key_placeholder"))
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key_btn = QPushButton(tr("settings.show"))
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setFixedWidth(72)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_btn)
        conn_form.addRow(tr("settings.api_key"), self._wrap(key_row))

        # Optional: override the API base URL for a self-hosted proxy or a
        # regional mirror. Empty falls back to the Google official endpoint.
        self.api_base_edit = QLineEdit(settings.api_base)
        self.api_base_edit.setPlaceholderText(DEFAULT_API_BASE)
        conn_form.addRow(tr("settings.api_base"), self.api_base_edit)

        self.lang_combo = QComboBox()
        for code, name in LANGUAGES:
            self.lang_combo.addItem(name, code)
        idx = self.lang_combo.findData(settings.target_language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        conn_form.addRow(tr("settings.translate_to"), self.lang_combo)

        # UI-7: Test Connection button + result label.
        self.test_btn = QPushButton(tr("settings.test_connection"))
        self.test_btn.setObjectName("testBtn")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._on_test_connection)
        self.test_result_label = QLabel()
        self.test_result_label.setObjectName("testResultOk")
        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result_label, 1)
        conn_form.addRow("", self._wrap(test_row))
        outer.addWidget(conn_card)

        # ---------- Audio card ----------
        outer.addWidget(self._section_title(tr("settings.section.audio")))
        audio_card, audio_form = self._make_card()
        self.source_combo = QComboBox()
        self.source_combo.addItem(tr("settings.source_system"), "system")
        self.source_combo.addItem(tr("settings.source_mic"), "mic")
        idx = self.source_combo.findData(settings.audio_source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        audio_form.addRow(tr("settings.audio_source"), self.source_combo)

        self.volume_slider, self.volume_label = self._make_slider(
            int(settings.playback_volume * 100), lambda v: f"{v}%"
        )
        audio_form.addRow(tr("settings.playback_volume"), self._wrap_slider(self.volume_slider, self.volume_label))

        self.echo_check = QCheckBox(tr("settings.echo"))
        self.echo_check.setChecked(settings.echo_target_language)
        audio_form.addRow("", self.echo_check)
        outer.addWidget(audio_card)

        # ---------- Appearance card ----------
        outer.addWidget(self._section_title(tr("settings.section.appearance"), "🎨"))
        app_card, app_form = self._make_card()
        self.font_slider, self.font_label = self._make_slider(
            settings.font_size, lambda v: f"{v} pt"
        )
        self.font_slider.setRange(14, 60)
        app_form.addRow(tr("settings.font_size"), self._wrap_slider(self.font_slider, self.font_label))

        self.opacity_slider, self.opacity_label = self._make_slider(
            int(settings.bg_opacity * 100), lambda v: f"{v}%"
        )
        self.opacity_slider.setRange(20, 95)
        app_form.addRow(tr("settings.bg_opacity"), self._wrap_slider(self.opacity_slider, self.opacity_label))

        self.show_original_check = QCheckBox(tr("settings.show_original"))
        self.show_original_check.setChecked(settings.show_original)
        app_form.addRow("", self.show_original_check)
        outer.addWidget(app_card)

        # ---------- Advanced card ----------
        outer.addWidget(self._section_title(tr("settings.section.advanced")))
        adv_card, adv_form = self._make_card()
        self.model_edit = QLineEdit(settings.gemini_model)
        self.model_edit.setPlaceholderText(DEFAULT_GEMINI_MODEL)
        adv_form.addRow(tr("settings.model_id"), self.model_edit)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(settings.system_prompt)
        self.prompt_edit.setMaximumHeight(90)
        self.prompt_edit.setPlaceholderText(tr("settings.prompt_placeholder"))
        adv_form.addRow(tr("settings.system_prompt"), self.prompt_edit)
        outer.addWidget(adv_card)

        outer.addStretch(1)

        # ---------- Buttons ----------
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setObjectName("primary")
        btns.button(QDialogButtonBox.Save).setText(tr("settings.save"))
        btns.button(QDialogButtonBox.Cancel).setText(tr("settings.cancel"))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

        self.setStyleSheet(DIALOG_QSS)

    # ---------- helpers ----------

    def _section_title(self, text: str, icon: str = "") -> QLabel:
        lbl = QLabel(f"{icon}  {text}" if icon else text)
        lbl.setObjectName("sectionTitle")
        return lbl

    def _make_card(self) -> tuple[QFrame, QFormLayout]:
        card = QFrame()
        card.setObjectName("card")
        # Design polish: subtle elevation on each card.
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 28))
        shadow.setOffset(0, 2)
        card.setGraphicsEffect(shadow)
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
        self.show_key_btn.setText(tr("settings.hide") if checked else tr("settings.show"))

    # ---------- UI-7: connection test ----------

    def _on_test_connection(self) -> None:
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._show_test_result(False, "API key is empty")
            return
        api_base = self.api_base_edit.text().strip() or DEFAULT_API_BASE
        model = self.model_edit.text().strip() or DEFAULT_GEMINI_MODEL
        self.test_btn.setEnabled(False)
        self.test_btn.setText(tr("settings.testing"))
        self.test_result_label.setText("")
        self.test_result_label.setObjectName("testResultOk")
        self._tester.run(api_key, api_base, model)

    def _on_test_result(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("settings.test_connection"))
        self._show_test_result(ok, message)

    def _show_test_result(self, ok: bool, message: str) -> None:
        if ok:
            self.test_result_label.setObjectName("testResultOk")
            self.test_result_label.setText(tr("settings.test_success"))
        else:
            self.test_result_label.setObjectName("testResultFail")
            self.test_result_label.setText(tr("settings.test_failed", error=message))
        # Re-apply QSS for the objectName-based color to take effect.
        self.test_result_label.style().unpolish(self.test_result_label)
        self.test_result_label.style().polish(self.test_result_label)

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
