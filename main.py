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

from audio import AudioCapture, AudioPlayer
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

        self.hud = HUDWindow(self.settings)
        self.hud.toggle_requested.connect(self.toggle)
        self.hud.settings_requested.connect(self.open_settings)
        self.hud.clear_requested.connect(self.hud.clear)
        self.hud.exit_requested.connect(self.stop)

        # wire client signals to HUD
        self.client.inputTranscript.connect(self.hud.set_input)
        self.client.outputTranscript.connect(self.hud.set_output)
        self.client.audioChunk.connect(self._on_audio_chunk)
        self.client.status.connect(self.hud.set_status)
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

        # audio capture (mic or WASAPI loopback)
        try:
            self.capture = AudioCapture(
                source=self.settings.audio_source,
                on_pcm16_chunk=self.client.send_audio,
            )
            self.capture.start()
        except Exception as e:
            QMessageBox.critical(
                None,
                "Capture error",
                f"Could not start audio capture ({self.settings.audio_source}):\n{e}",
            )
            self._stop_audio_player()
            self.capture = None
            return

        self.client.configure(
            api_key=self.settings.api_key,
            target_lang=self.settings.target_language,
            system_prompt=self.settings.system_prompt,
            echo_target_language=self.settings.echo_target_language,
            api_base=self.settings.api_base,
        )
        self.client.start()
        self.is_running = True
        self.hud.set_running_state(True)
        self.hud.set_status("Connecting...")

    def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
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
                prev_lang = self.settings.target_language
                prev_echo = self.settings.echo_target_language
                dlg.apply_to(self.settings)
                self.settings.save()
                self.hud.apply_style()
                if self.player is not None:
                    self.player.set_volume(self.settings.playback_volume)
                # restart if anything that affects the live session changed
                needs_restart = self.is_running and (
                    prev_source != self.settings.audio_source
                    or prev_key != self.settings.api_key
                    or prev_lang != self.settings.target_language
                    or prev_echo != self.settings.echo_target_language
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

    def _on_audio_chunk(self, data: bytes) -> None:
        if self.player is not None:
            self.player.enqueue_pcm16(data)

    def _on_connected(self) -> None:
        self.hud.set_status("Connected")

    def _on_disconnected(self, reason: str) -> None:
        if self.is_running:
            # tear down audio side; user can press Start again to retry
            if self.capture is not None:
                self.capture.stop()
                self.capture = None
            self._stop_audio_player()
            self.is_running = False
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

    menu = QMenu()
    act_toggle = QAction("Start / Stop", menu)
    act_toggle.triggered.connect(controller.toggle)
    act_settings = QAction("Settings...", menu)
    act_settings.triggered.connect(controller.open_settings)
    act_show = QAction("Show HUD", menu)
    act_show.triggered.connect(controller.hud.show)
    act_quit = QAction("Quit", menu)
    act_quit.triggered.connect(controller.stop)
    act_quit.triggered.connect(app.quit)
    # HUD Exit button: stop session (if running) then quit the app.
    # The stop() connection is already wired in LiveBuddyApp.__init__;
    # here we add the app-level quit on top.
    controller.hud.exit_requested.connect(app.quit)
    menu.addAction(act_toggle)
    menu.addAction(act_settings)
    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()
    tray.activated.connect(
        lambda reason: controller.hud.show()
        if reason == QSystemTrayIcon.Trigger
        else None
    )

    # show HUD on launch
    controller.hud.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
