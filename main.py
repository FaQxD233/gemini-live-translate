"""Gemini Live Translate entry point: PySide6 application + signal orchestration."""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
)

from audio import AudioCapture, AudioPlayer, terminate_pyaudio
from gemini_client import GeminiClient
from hud_window import HUDWindow
from settings import AppSettings
from settings_window import DIALOG_QSS, SettingsDialog

# Global QSS supplement: extends DIALOG_QSS with menu + QMessageBox styling so
# the system tray context menu and warning/error dialogs share the same theme.
GLOBAL_QSS = DIALOG_QSS + """
QMenu {
    background: white;
    border: 1px solid #D2D6DF;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    background: transparent;
    color: #1F2330;
    padding: 6px 18px;
    border-radius: 5px;
    margin: 1px 4px;
}
QMenu::item:selected {
    background: #7C5CFF;
    color: white;
}
QMenu::separator {
    height: 1px;
    background: #E3E6EC;
    margin: 4px 8px;
}
QToolTip {
    background: #1F2330;
    color: white;
    border: 1px solid #1F2330;
    padding: 4px 8px;
    border-radius: 4px;
}
QMessageBox {
    background: #F4F5F8;
}
QMessageBox QLabel {
    color: #1F2330;
    font-size: 13px;
}
"""


class LiveBuddyApp(QObject):
    restart_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings.load()
        self.client = GeminiClient()
        self.capture: Optional[AudioCapture] = None
        self.player: Optional[AudioPlayer] = None
        self.is_running = False
        self._active_client_session_id: Optional[int] = None

        self.hud = HUDWindow(self.settings)
        self.hud.toggle_requested.connect(self.toggle)
        self.hud.settings_requested.connect(self.open_settings)
        self.hud.clear_requested.connect(self.hud.clear)

        # wire client signals to HUD
        self.client.inputTranscript.connect(self._on_input_transcript)
        self.client.outputTranscript.connect(self._on_output_transcript)
        self.client.audioChunk.connect(self._on_audio_chunk)
        self.client.status.connect(self._on_client_status)
        self.client.connected.connect(self._on_connected)
        self.client.disconnected.connect(self._on_disconnected)

    def start(self) -> None:
        if self.is_running:
            return
        if not self.settings.api_key:
            QMessageBox.warning(
                None,
                "API key required",
                "Please set your Google Gemini API key in Settings.",
            )
            self.open_settings()
            return

        # audio player (24 kHz Float32)
        if self.settings.echo_target_language:
            try:
                self.player = AudioPlayer(
                    sample_rate=24000, volume=self.settings.playback_volume
                )
                self.player.start()
            except Exception as e:
                QMessageBox.warning(None, "Playback error", f"Could not open audio output:\n{e}")
                self.player = None

        # Audio capture is deferred to _on_connected() so we don't silently
        # drop audio during the WebSocket connection phase.

        self.client.configure(
            api_key=self.settings.api_key,
            target_lang=self.settings.target_language,
            system_prompt=self.settings.system_prompt,
            echo_target_language=self.settings.echo_target_language,
            api_base=self.settings.api_base,
            model=self.settings.gemini_model,
        )
        session_id = self.client.start()
        if session_id is None:
            self._stop_audio_player()
            self._active_client_session_id = None
            self.is_running = False
            self.hud.set_running_state(False)
            self.hud.set_status(
                self.client.last_start_error() or "Could not start Gemini session"
            )
            return
        self._active_client_session_id = session_id
        self.is_running = True
        self.hud.set_running_state(True)
        self.hud.set_status("Connecting...")

    def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        self._active_client_session_id = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self._stop_audio_player()
        self.client.stop()
        self.hud.set_running_state(False)
        self.hud.set_status("Stopped")

    def toggle(self) -> None:
        if self.is_running:
            self.stop()
        else:
            self.start()

    def open_settings(self) -> None:
        try:
            # Parent to the HUD so the modal dialog has a real owner window.
            # Without a parent, --noconsole PyInstaller builds can fail to
            # raise the dialog (no active window to lock the modal against).
            dlg = SettingsDialog(self.settings, parent=self.hud)
            if dlg.exec() == SettingsDialog.Accepted:
                prev_source = self.settings.audio_source
                prev_key = self.settings.api_key
                prev_base = self.settings.api_base
                prev_lang = self.settings.target_language
                prev_echo = self.settings.echo_target_language
                prev_prompt = self.settings.system_prompt
                prev_model = self.settings.gemini_model
                dlg.apply_to(self.settings)
                self.settings.save()
                self.hud.apply_style()
                if self.player is not None:
                    self.player.set_volume(self.settings.playback_volume)
                # restart if anything that affects the live session changed
                needs_restart = self.is_running and (
                    prev_source != self.settings.audio_source
                    or prev_key != self.settings.api_key
                    or prev_base != self.settings.api_base
                    or prev_lang != self.settings.target_language
                    or prev_echo != self.settings.echo_target_language
                    or prev_prompt != self.settings.system_prompt
                    or prev_model != self.settings.gemini_model
                )
                if needs_restart:
                    self.stop()
                    QTimer.singleShot(300, self.start)
        except Exception as e:
            QMessageBox.critical(
                self.hud,
                "Settings error",
                f"Failed to open settings dialog:\n{e}",
            )

    # ---------- internals ----------

    def _is_current_client_session(self, session_id: int) -> bool:
        return self._active_client_session_id == session_id

    def _on_input_transcript(self, session_id: int, text: str) -> None:
        if self._is_current_client_session(session_id):
            self.hud.set_input(text)

    def _on_output_transcript(self, session_id: int, text: str) -> None:
        if self._is_current_client_session(session_id):
            self.hud.set_output(text)

    def _on_client_status(self, session_id: int, status: str) -> None:
        if self._is_current_client_session(session_id):
            self.hud.set_status(status)

    def _on_audio_chunk(self, session_id: int, data: bytes) -> None:
        if not self._is_current_client_session(session_id):
            return
        if self.player is not None:
            self.player.enqueue_pcm16(data)

    def _on_connected(self, session_id: int) -> None:
        if not self._is_current_client_session(session_id):
            return
        self.hud.set_status("Connected")
        # Start audio capture now that the WebSocket is up, so no audio is
        # wasted during the connection phase. On reconnect, capture is already
        # running (it was never stopped — _on_disconnected is only called on
        # permanent session end, not during client-side reconnection backoff).
        if self.is_running and self.capture is None:
            try:
                self.capture = AudioCapture(
                    source=self.settings.audio_source,
                    on_pcm16_chunk=lambda chunk, sid=session_id: self.client.send_audio(
                        chunk, sid
                    ),
                )
                self.capture.start()
            except Exception as e:
                QMessageBox.critical(
                    self.hud,
                    "Capture error",
                    f"Could not start audio capture ({self.settings.audio_source}):\n{e}",
                )
                self._stop_audio_player()
                self.capture = None
                self.client.stop()
                self.is_running = False
                self._active_client_session_id = None
                self.hud.set_running_state(False)
                self.hud.set_status("Capture error")

    def _on_disconnected(self, session_id: int, reason: str) -> None:
        if not self._is_current_client_session(session_id):
            return
        if self.is_running:
            # tear down audio side; user can press Start again to retry
            if self.capture is not None:
                self.capture.stop()
                self.capture = None
            self._stop_audio_player()
            self.is_running = False
            self._active_client_session_id = None
            self.hud.set_running_state(False)
        self.hud.set_status(f"Disconnected: {reason}" if reason else "Disconnected")

    def _stop_audio_player(self) -> None:
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            self.player = None


def main() -> int:
    QApplication.setQuitOnLastWindowClosed(False)
    app = QApplication(sys.argv)
    app.setApplicationName("gemini-live-translate")
    app.setStyleSheet(GLOBAL_QSS)

    controller = LiveBuddyApp()

    # system tray
    tray = QSystemTrayIcon()
    style = app.style()
    try:
        icon = style.standardIcon(QStyle.SP_MediaVolume) if style else QIcon()
    except Exception:
        icon = QIcon()
    tray.setIcon(icon if not icon.isNull() else QIcon())
    tray.setToolTip("Gemini Live Translate")

    def _show_hud():
        controller.hud.show()
        controller.hud.raise_()
        controller.hud.activateWindow()

    menu = QMenu()
    act_toggle = QAction("Start / Stop", menu)
    act_toggle.triggered.connect(controller.toggle)
    act_settings = QAction("Settings...", menu)
    act_settings.triggered.connect(controller.open_settings)
    act_show = QAction("Show HUD", menu)
    act_show.triggered.connect(_show_hud)
    act_quit = QAction("Quit", menu)
    act_quit.triggered.connect(app.quit)
    # HUD Exit button and tray Quit both just call app.quit(); the actual
    # session cleanup happens in _on_about_to_quit (single place, no
    # redundant stop() calls).
    controller.hud.exit_requested.connect(app.quit)
    menu.addAction(act_toggle)
    menu.addAction(act_settings)
    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()
    tray.activated.connect(
        lambda reason: _show_hud()
        if reason == QSystemTrayIcon.Trigger
        else None
    )

    # Restore saved HUD position/size, then show.
    controller.hud.restore_geometry(controller.settings.hud_geometry)
    controller.hud.show()

    # Orderly cleanup on quit: persist HUD geometry, stop session, terminate
    # PyAudio.
    def _on_about_to_quit():
        controller.settings.hud_geometry = controller.hud.save_geometry()
        controller.settings.save()
        controller.stop()
        terminate_pyaudio()

    app.aboutToQuit.connect(_on_about_to_quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
