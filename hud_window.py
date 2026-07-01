"""Floating caption HUD overlay (port of CaptionPanelController + CaptionView)."""
from __future__ import annotations

import base64

from PySide6.QtCore import QEvent, QByteArray, Qt, QPoint, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QMouseEvent, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

# Accent palette (Material You-inspired violet)
ACCENT = "#7C5CFF"
ACCENT_BRIGHT = "#9D7FFF"

# How many lines of translated / original text remain visible on screen.
# Older lines scroll out the top automatically.
OUTPUT_VISIBLE_LINES = 5
INPUT_VISIBLE_LINES = 3
CONTROL_BAR_HEIGHT = 40  # fixed; opacity toggles visibility, layout stays stable
RESIZE_GRIP_SIZE = 18  # corner grip hit area


class _TransparentSizeGrip(QSizeGrip):
    """A QSizeGrip that paints nothing: invisible, but still resizes the
    top-level window when dragged. We use four of these (one per corner)
    so the user can resize the HUD from any corner."""

    def paintEvent(self, event) -> None:  # noqa: D401
        return


class HUDWindow(QWidget):
    toggle_requested = Signal()
    settings_requested = Signal()
    clear_requested = Signal()
    exit_requested = Signal()

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings
        self._drag_offset: QPoint | None = None

        # accumulated finalized text (capped) + current in-progress draft
        self._out_committed = ""
        self._out_draft = ""
        self._in_committed = ""
        self._in_draft = ""
        self._status = ""
        self._status_kind = "idle"  # idle | connecting | connected | error

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Real drop shadow around the floating panel
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 8)
        self.setGraphicsEffect(shadow)

        # Root container: the visible "card". Child QFrame so we can round
        # its corners + paint a gradient background independently from the
        # (transparent) top-level widget. The card fills the HUD entirely;
        # the four corner grips are positioned absolutely on top of it.
        self._card = QFrame(self)
        self._card.setObjectName("card")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._card)

        # Four corner size grips. They are NOT in the layout — they sit on
        # top of the card via absolute geometry so their hit areas align
        # exactly with the visible card corners. _update_grip_geometry()
        # repositions them whenever the HUD is resized.
        self._grips: list[QSizeGrip] = []
        for cursor in (
            Qt.SizeFDiagCursor,  # top-left
            Qt.SizeBDiagCursor,  # top-right
            Qt.SizeBDiagCursor,  # bottom-left
            Qt.SizeFDiagCursor,  # bottom-right
        ):
            g = _TransparentSizeGrip(self)
            g.setFixedSize(RESIZE_GRIP_SIZE, RESIZE_GRIP_SIZE)
            g.setCursor(cursor)
            g.raise_()
            self._grips.append(g)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(20, 14, 20, 14)
        card_layout.setSpacing(6)

        # ---- Header row: status dot + status text + lang badge + drag hint ----
        header = QHBoxLayout()
        header.setSpacing(8)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setMinimumWidth(14)
        header.addWidget(self.status_dot)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusText")
        header.addWidget(self.status_label)
        header.addStretch(1)
        self.lang_badge = QLabel()
        self.lang_badge.setObjectName("badgeTranslated")
        header.addWidget(self.lang_badge)
        self.drag_hint = QLabel("⠿ drag")
        self.drag_hint.setObjectName("dragHint")
        header.addWidget(self.drag_hint)
        card_layout.addLayout(header)

        # ---- Translated output (primary, auto-scrolling) ----
        self.output_edit = self._make_caption_edit("outputText", 1500)
        card_layout.addWidget(self.output_edit, 1)

        # ---- Divider ----
        self.divider = QFrame()
        self.divider.setObjectName("divider")
        self.divider.setFixedHeight(1)
        card_layout.addWidget(self.divider)

        # ---- Original input (secondary) ----
        self.input_edit = self._make_caption_edit("inputText", 800)
        card_layout.addWidget(self.input_edit)

        # ---- Control bar (revealed on hover via opacity, layout never moves) ----
        self.control_bar = QWidget()
        self.control_bar.setObjectName("controlBar")
        self.control_bar.setFixedHeight(CONTROL_BAR_HEIGHT)
        ctrl = QHBoxLayout(self.control_bar)
        ctrl.setContentsMargins(0, 6, 0, 0)
        ctrl.setSpacing(8)
        self.toggle_btn = QPushButton("Pause")
        self.toggle_btn.setObjectName("primaryBtn")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_requested.emit)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_requested.emit)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconBtn")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setFixedWidth(36)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.setObjectName("iconBtn")
        self.exit_btn.setCursor(Qt.PointingHandCursor)
        self.exit_btn.setFixedWidth(48)
        self.exit_btn.setToolTip("Quit application")
        self.exit_btn.clicked.connect(self.exit_requested.emit)
        ctrl.addWidget(self.toggle_btn)
        ctrl.addWidget(self.clear_btn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.exit_btn)
        ctrl.addWidget(self.settings_btn)
        card_layout.addWidget(self.control_bar)
        # Buttons hidden until hover. control_bar itself stays visible + at
        # its fixed 40px height, so the caption rows above never reflow.
        # (We can't use QGraphicsOpacityEffect here because the parent
        # widget already has a QGraphicsDropShadowEffect and Qt refuses to
        # nest graphics effects reliably.)
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(False)

        # Note: size grips live in the outer 3x3 grid (one per corner),
        # not inside the card layout, so we don't add a grip row here.

        # Drag handling: install event filter on the card + the caption edits so
        # dragging anywhere on the panel (not just the empty margins) moves
        # the window. The buttons / size grip are excluded, so they keep working.
        self._card.installEventFilter(self)
        self.output_edit.installEventFilter(self)
        self.input_edit.installEventFilter(self)

        self.resize(720, 260)
        # Keep the panel wide enough that captions have room to wrap.
        # Prevents the user from shrinking via QSizeGrip to a width where
        # only ~6-7 chars fit per line.
        self.setMinimumWidth(560)
        self.apply_style()

    # ---------- private helpers ----------

    def _update_grip_geometry(self) -> None:
        """Reposition the four corner grips so their hit areas line up
        exactly with the visible card corners. Called from resizeEvent."""
        if not getattr(self, "_grips", None):
            return
        sz = RESIZE_GRIP_SIZE
        w = self.width()
        h = self.height()
        self._grips[0].setGeometry(0, 0, sz, sz)  # top-left
        self._grips[1].setGeometry(w - sz, 0, sz, sz)  # top-right
        self._grips[2].setGeometry(0, h - sz, sz, sz)  # bottom-left
        self._grips[3].setGeometry(w - sz, h - sz, sz, sz)  # bottom-right
        for g in self._grips:
            g.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep grips pinned to the new corners. Repolishing the card's
        # stylesheet here also forces the QPlainTextEdit viewports inside it
        # to repaint, killing the stale-artifact streaks seen during a drag.
        self._update_grip_geometry()
        # Repaint caption editors immediately so the new visible rows show
        # up without waiting for the next transcript tick.
        for edit in (self.output_edit, self.input_edit):
            vp = edit.viewport()
            if vp is not None:
                vp.update()

    def _make_caption_edit(self, object_name: str, max_blocks: int) -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setObjectName(object_name)
        edit.setReadOnly(True)
        edit.setMaximumBlockCount(max_blocks)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Explicit widget-width wrap so long CJK / mixed text wraps at the
        # viewport edge rather than being limited by a tiny sizeHint.
        edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        # Wrap inside CJK runs (no spaces) too, not just at word boundaries.
        edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        # Reserve a sane minimum so layout never shrinks the editor below
        # ~30 CJK chars per line at 16pt.
        edit.setMinimumWidth(420)
        # Let the surrounding eventFilter handle mouse interaction (drag).
        edit.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        edit.setMouseTracking(False)
        return edit

    # ---------- public API ----------

    def apply_style(self) -> None:
        s = self._settings
        bg_alpha = int(round(max(0.0, min(1.0, s.bg_opacity)) * 255))
        bg_alpha_dim = max(120, bg_alpha)  # ensure base readability

        # Output (translated) font
        out_f = QFont()
        out_f.setFamily("Segoe UI")
        out_f.setPointSize(max(12, s.font_size))
        out_f.setBold(True)
        self.output_edit.setFont(out_f)

        # Input (original) font
        in_f = QFont()
        in_f.setFamily("Segoe UI")
        in_f.setPointSize(max(10, int(s.font_size * 0.6)))
        in_f.setBold(False)
        self.input_edit.setFont(in_f)

        # Caption background / text colors are baked into the QSS so they
        # also apply to QPlainTextEdit viewports.
        self._card.setStyleSheet(
            f"""
            QFrame#card {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(18, 18, 28, {bg_alpha_dim}),
                    stop:1 rgba(28, 28, 40, {bg_alpha_dim}));
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 32);
            }}
            QLabel {{
                color: white;
                background: transparent;
            }}
            QLabel#statusDot {{ font-size: 12px; }}
            QLabel#statusText {{ color: rgba(255, 255, 255, 180); font-size: 11px; }}
            QLabel#dragHint   {{ color: rgba(255, 255, 255, 90); font-size: 10px; }}
            QLabel#badgeTranslated {{
                background: {ACCENT};
                color: white;
                border-radius: 9px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPlainTextEdit {{
                background: transparent;
                color: white;
                border: none;
                padding: 0;
            }}
            QPlainTextEdit#inputText {{
                color: rgba(255, 255, 255, 170);
                font-style: italic;
            }}
            QFrame#divider {{
                background: rgba(255, 255, 255, 28);
                border: none;
                max-height: 1px;
            }}
            QWidget#controlBar {{ background: transparent; }}
            QPushButton {{
                background: rgba(255, 255, 255, 26);
                color: white;
                border: 1px solid rgba(255, 255, 255, 50);
                border-radius: 8px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 56);
                border-color: rgba(255, 255, 255, 110);
            }}
            QPushButton:pressed {{ background: rgba(255, 255, 255, 18); }}
            QPushButton#primaryBtn {{
                background: {ACCENT};
                border: 1px solid {ACCENT};
                color: white;
                font-weight: 600;
            }}
            QPushButton#primaryBtn:hover {{
                background: {ACCENT_BRIGHT};
                border-color: {ACCENT_BRIGHT};
            }}
            QPushButton#iconBtn {{
                background: rgba(255, 255, 255, 20);
                border: 1px solid rgba(255, 255, 255, 50);
                padding: 0;
                font-size: 16px;
            }}
            QSizeGrip {{ background: transparent; }}
            QSizeGrip:hover {{
                background: rgba(255, 255, 255, 30);
                border-radius: 4px;
            }}
            """
        )

        # Pin caption heights to N visible lines so older text scrolls out.
        self._apply_caption_height(self.output_edit, OUTPUT_VISIBLE_LINES)
        self._apply_caption_height(self.input_edit, INPUT_VISIBLE_LINES)

        # Status text + drag hint + badge fonts
        st_f = QFont()
        st_f.setFamily("Segoe UI")
        st_f.setPointSize(9)
        self.status_label.setFont(st_f)
        self.drag_hint.setFont(st_f)
        self.lang_badge.setFont(st_f)
        self._update_lang_badge()

        # Toggle original-text visibility
        show_orig = bool(getattr(self._settings, "show_original", True))
        self.input_edit.setVisible(show_orig)
        self.divider.setVisible(show_orig)

        # Set the HUD minimum height so all rows fit without layout
        # compression, but never resize the HUD ourselves: this preserves
        # user-driven resizing (dragging any corner). If the current
        # height is below the new minimum, Qt grows the window automatically.
        self.setMinimumHeight(self._compute_min_height(show_orig))
        # Re-pin grips in case the geometry changed (also repaints viewports).
        self._update_grip_geometry()

        self._refresh()

    def _apply_caption_height(self, edit: QPlainTextEdit, lines: int) -> None:
        fm = QFontMetrics(edit.font())
        # QPlainTextEdit adds ~4px of frame padding top+bottom; ~3px per doc
        # margin. Empirically this lands on exactly N rendered lines.
        h = fm.lineSpacing() * lines + 14
        # Minimum (not fixed) height: when the user drags the HUD larger,
        # the caption area grows too, so more lines become visible. When
        # dragged smaller, the area never goes below N lines.
        edit.setMinimumHeight(h)
        edit.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX — no upper cap

    def _compute_min_height(self, show_original: bool) -> int:
        """Compute the minimum HUD height needed to fit every row without overlap."""
        out_fm = QFontMetrics(self.output_edit.font())
        in_fm = QFontMetrics(self.input_edit.font())
        out_h = out_fm.lineSpacing() * OUTPUT_VISIBLE_LINES + 14
        in_h = in_fm.lineSpacing() * INPUT_VISIBLE_LINES + 14
        divider_h = 1 if show_original else 0
        in_visible_h = in_h if show_original else 0
        header_h = 22
        card_pad_v = 14 * 2  # card_layout contentsMargins top+bottom
        # When original is hidden, divider+input_edit are removed from layout.
        # Items: header, output_edit, [divider, input_edit], control_bar
        # → 4 spacings when showing original, 2 when hidden.
        spacings = 4 if show_original else 2
        spacing_v = 6 * spacings
        return (
            card_pad_v
            + header_h
            + out_h
            + divider_h
            + in_visible_h
            + CONTROL_BAR_HEIGHT
            + spacing_v
        )

    def set_output(self, text: str) -> None:
        """Gemini sends cumulative text for the current utterance."""
        text = text or ""
        if self._out_draft and text.startswith(self._out_draft):
            self._out_draft = text
        else:
            # new utterance started
            if self._out_draft:
                self._out_committed = (self._out_committed + "\n" + self._out_draft).lstrip("\n")
                if len(self._out_committed) > 1500:
                    self._out_committed = self._out_committed[-1500:]
            self._out_draft = text
        self._refresh()

    def set_input(self, text: str) -> None:
        text = text or ""
        if self._in_draft and text.startswith(self._in_draft):
            self._in_draft = text
        else:
            if self._in_draft:
                self._in_committed = (self._in_committed + "\n" + self._in_draft).lstrip("\n")
                if len(self._in_committed) > 800:
                    self._in_committed = self._in_committed[-800:]
            self._in_draft = text
        self._refresh()

    def set_status(self, status: str) -> None:
        self._status = status or ""
        s = self._status.lower()
        if not s or "stopped" in s or s == "stop":
            self._status_kind = "idle"
        elif any(k in s for k in ("error", "fail", "disconnected", "closed", "timeout", "timed out")):
            self._status_kind = "error"
        elif any(k in s for k in ("connected", "ready", "live")):
            self._status_kind = "connected"
        elif any(k in s for k in ("connect", "starting", "loading", "init")):
            self._status_kind = "connecting"
        else:
            self._status_kind = "idle"
        self._refresh()

    def clear(self) -> None:
        self._out_committed = ""
        self._out_draft = ""
        self._in_committed = ""
        self._in_draft = ""
        self._refresh()

    def set_running_state(self, running: bool) -> None:
        self.toggle_btn.setText("Pause" if running else "Start")

    def restore_geometry(self, geometry_b64: str) -> None:
        """Restore window position/size from a base64-encoded QByteArray."""
        if not geometry_b64:
            return
        try:
            raw = base64.b64decode(geometry_b64)
            self.restoreGeometry(QByteArray(raw))
        except Exception:
            pass

    def save_geometry(self) -> str:
        """Return the current window geometry as a base64-encoded string."""
        try:
            ba = self.saveGeometry()
            return base64.b64encode(bytes(ba)).decode("ascii")
        except Exception:
            return ""

    # ---------- internals ----------

    def _update_lang_badge(self) -> None:
        code = self._settings.target_language
        try:
            from settings import LANGUAGES  # local import to avoid cycle
            name = next((n for c, n in LANGUAGES if c == code), code.upper())
        except Exception:
            name = code.upper()
        self.lang_badge.setText(f"→ {name}")

    def _refresh(self) -> None:
        out_text = self._out_committed
        if self._out_draft:
            out_text = (out_text + "\n" + self._out_draft).strip("\n")
        out_text = out_text if out_text else "—"
        # Only touch the editor when the text actually changed; setPlainText
        # resets the cursor and scrolls, which causes the visible "jitter" on
        # every transcript update tick even when nothing visually changed.
        if self.output_edit.toPlainText() != out_text:
            self.output_edit.setPlainText(out_text)
            self._scroll_to_end(self.output_edit)

        in_text = self._in_committed
        if self._in_draft:
            in_text = (in_text + "\n" + self._in_draft).strip("\n")
        if self.input_edit.toPlainText() != in_text:
            self.input_edit.setPlainText(in_text)
            self._scroll_to_end(self.input_edit)

        if self.status_label.text() != self._status:
            self.status_label.setText(self._status)

        color_map = {
            "idle": "rgba(180, 180, 180, 200)",
            "connecting": "#FFC53D",
            "connected": "#3DDC84",
            "error": "#FF5C5C",
        }
        color = color_map.get(self._status_kind, "rgba(180, 180, 180, 200)")
        self.status_dot.setStyleSheet(f"color: {color};")

    def _scroll_to_end(self, edit: QPlainTextEdit) -> None:
        cursor = edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        edit.setTextCursor(cursor)
        # Belt-and-suspenders: also force the scrollbar to maximum.
        sb = edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- Qt event overrides ----------

    def enterEvent(self, event) -> None:
        # Clear stale drag state if the user released the mouse outside
        # the window (no release event would have been delivered).
        if self._drag_offset is not None and not (
            QGuiApplication.mouseButtons() & Qt.LeftButton
        ):
            self._drag_offset = None
        # Reveal the control buttons on hover. control_bar itself stays at
        # its fixed 40px slot, so the captions above never reflow.
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(False)
        super().leaveEvent(event)

    def eventFilter(self, obj, event: QEvent) -> bool:
        et = event.type()
        # Drag the whole window when pressing anywhere on the card or the
        # caption edits. Buttons / size grip are NOT filtered, so they keep
        # their own click behavior.
        if obj in (self._card, self.output_edit, self.input_edit):
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                event.accept()
                return True
            if et == QEvent.MouseMove and self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                event.accept()
                return True
            if et == QEvent.MouseButtonRelease and self._drag_offset is not None:
                self._drag_offset = None
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Fallback for clicks on the outer transparent widget itself
        # (the 0px margin area). Rare, but keeps dragging predictable.
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        event.accept()
