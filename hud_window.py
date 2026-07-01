"""Floating caption HUD overlay (port of CaptionPanelController + CaptionView)."""
from __future__ import annotations

import base64

from PySide6.QtCore import (
    QEvent,
    QByteArray,
    QEasingCurve,
    Property,
    QPoint,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QMouseEvent,
    QTextCursor,
    QTextOption,
)
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

from i18n import tr
from settings import LANGUAGES  # C-8: top-level import; no actual cycle exists
from theme import (
    ACCENT,
    ACCENT_BRIGHT,
    ACCENT_PRESSED,
    ANIM_FAST,
    ANIM_PULSE,
    ANIM_SLOW,
    FONT_FAMILIES,
    FONT_FAMILY_QSS,
    HUD_BG_BOTTOM,
    HUD_BG_TOP,
    HUD_BORDER,
    HUD_BTN_BG,
    HUD_BTN_BG_HOVER,
    HUD_BTN_BG_PRESSED,
    HUD_BTN_BORDER,
    HUD_BTN_BORDER_HOVER,
    HUD_DIVIDER,
    HUD_GLASS_HIGHLIGHT,
    HUD_INPUT_TEXT,
    HUD_TEXT_SECONDARY,
    HUD_TEXT_TERTIARY,
    RADIUS_BADGE,
    RADIUS_BUTTON,
    RADIUS_CARD_HUD,
    STATUS_COLORS,
)

# How many lines of translated / original text remain visible on screen.
# Older lines scroll out the top automatically.
OUTPUT_VISIBLE_LINES = 5
INPUT_VISIBLE_LINES = 3
CONTROL_BAR_HEIGHT = 40  # expanded height; collapses to 0 when not hovering
RESIZE_GRIP_SIZE = 18  # corner grip hit area

# Status kind → dot color. Built from theme tokens so palette stays in sync.
KIND_COLORS = {k: QColor(*rgb) for k, rgb in STATUS_COLORS.items()}
KIND_COLORS["idle"].setAlpha(200)  # dimmer when idle

# Refresh coalescing interval (ms). Batches multiple transcript ticks that
# arrive within a single frame into one repaint (P-1).
REFRESH_THROTTLE_MS = 16


class _TransparentSizeGrip(QSizeGrip):
    """A QSizeGrip that paints nothing: invisible, but still resizes the
    top-level window when dragged. We use four of these (one per corner)
    so the user can resize the HUD from any corner."""

    def paintEvent(self, event) -> None:  # noqa: D401
        return


class _StatusDot(QWidget):
    """Self-painting status indicator that pulses while connecting.

    Replaces the old QLabel("●") whose color was driven by string-matching
    the status text. The kind is now passed explicitly (C-3), so the dot
    color and pulse animation are derived directly from it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._color = KIND_COLORS["idle"]
        self._pulse = 0.0
        self._kind = "idle"
        self.setFixedSize(16, 16)
        self._anim = QPropertyAnimation(self, b"pulse")
        self._anim.setDuration(ANIM_PULSE)
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.0)
        self._anim.setLoopCount(-1)

    def set_kind(self, kind: str) -> None:
        if kind == self._kind and self._color == KIND_COLORS.get(kind):
            return
        self._kind = kind
        self._color = KIND_COLORS.get(kind, KIND_COLORS["idle"])
        if kind == "connecting":
            if self._anim.state() != QPropertyAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()
            self._pulse = 0.0
        self.update()

    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    # Qt meta-property so QPropertyAnimation can bind to it.
    pulse = Property(float, _get_pulse, _set_pulse)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        r = 4.5 + 1.8 * self._pulse
        # Soft glow ring while pulsing (connecting state).
        if self._pulse > 0.01:
            glow = QColor(c)
            glow.setAlpha(int(90 * self._pulse))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawEllipse(self.rect().center(), r + 3.0, r + 3.0)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect().center(), r, r)


class _FadingLabel(QLabel):
    """A QLabel whose text alpha can be animated via a `alpha` property.

    Used for the drag hint so it can fade out after the first drag without
    relying on QGraphicsOpacityEffect (which nests unreliably under the
    HUD's existing QGraphicsDropShadowEffect)."""

    def __init__(self, *args, base_alpha: int = 90, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._alpha = 1.0
        self._base_alpha = max(0, min(255, base_alpha))
        self._fade = QPropertyAnimation(self, b"alpha")
        self._fade.setDuration(ANIM_SLOW)
        self._fade.finished.connect(self._on_finished)
        self._apply_alpha()

    def fade_out(self) -> None:
        if self._alpha <= 0.01:
            return
        self._fade.stop()
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_finished(self) -> None:
        if self._alpha <= 0.01:
            self.setVisible(False)

    def _get_alpha(self) -> float:
        return self._alpha

    def _set_alpha(self, a: float) -> None:
        self._alpha = max(0.0, min(1.0, a))
        self._apply_alpha()

    # Qt meta-property so QPropertyAnimation can bind to it.
    alpha = Property(float, _get_alpha, _set_alpha)

    def _apply_alpha(self) -> None:
        a255 = int(self._alpha * self._base_alpha)
        self.setStyleSheet(f"color: rgba(255, 255, 255, {a255});")


class HUDWindow(QWidget):
    toggle_requested = Signal()
    settings_requested = Signal()
    clear_requested = Signal()
    exit_requested = Signal()

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._dragged_once = False
        self._shown_once = False  # entrance animation plays only on first show

        # accumulated finalized text (capped) + current in-progress draft
        self._out_committed = ""
        self._out_draft = ""
        self._in_committed = ""
        self._in_draft = ""
        self._status = ""
        self._status_kind = "idle"
        self._drops = 0

        # P-2: track last applied background alpha so we only rebuild the
        # (large) card QSS when something it depends on actually changed.
        self._last_applied_alpha = -1

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

        # ---- Header row: status dot + status text + drops + lang badge + drag hint ----
        header = QHBoxLayout()
        header.setSpacing(8)
        self.status_dot = _StatusDot()
        header.addWidget(self.status_dot)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusText")
        header.addWidget(self.status_label)
        header.addStretch(1)
        # P-6: dropped-chunks indicator (hidden unless drops > 0).
        self.drops_label = QLabel()
        self.drops_label.setObjectName("dropsLabel")
        self.drops_label.setVisible(False)
        header.addWidget(self.drops_label)
        self.lang_badge = QLabel()
        self.lang_badge.setObjectName("badgeTranslated")
        header.addWidget(self.lang_badge)
        self.drag_hint = _FadingLabel(tr("hud.drag_hint"), base_alpha=90)
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

        # ---- Control bar (UI-6: collapses to 0 when not hovering) ----
        self.control_bar = QWidget()
        self.control_bar.setObjectName("controlBar")
        # Animate maximumHeight between 0 (idle) and CONTROL_BAR_HEIGHT (hover).
        # Using maximumHeight (not fixedHeight) lets the layout reallocate the
        # freed vertical space to the captions above.
        self.control_bar.setMaximumHeight(0)
        self.control_bar.setMinimumHeight(0)
        ctrl = QHBoxLayout(self.control_bar)
        ctrl.setContentsMargins(0, 6, 0, 0)
        ctrl.setSpacing(8)
        self.toggle_btn = QPushButton(tr("hud.pause"))
        self.toggle_btn.setObjectName("primaryBtn")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_requested.emit)
        self.clear_btn = QPushButton(tr("hud.clear"))
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_requested.emit)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconBtn")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setFixedWidth(36)
        self.settings_btn.setToolTip(tr("hud.settings"))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.exit_btn = QPushButton(tr("hud.exit"))
        self.exit_btn.setObjectName("iconBtn")
        self.exit_btn.setCursor(Qt.PointingHandCursor)
        self.exit_btn.setFixedWidth(48)
        self.exit_btn.setToolTip(tr("hud.exit"))
        self.exit_btn.clicked.connect(self.exit_requested.emit)
        ctrl.addWidget(self.toggle_btn)
        ctrl.addWidget(self.clear_btn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.exit_btn)
        ctrl.addWidget(self.settings_btn)
        card_layout.addWidget(self.control_bar)
        # Buttons hidden until hover. control_bar collapses to 0 height so
        # the caption rows above reclaim the space when not interacting.
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(False)

        self._ctrl_anim = QPropertyAnimation(self.control_bar, b"maximumHeight")
        self._ctrl_anim.setDuration(ANIM_FAST)

        # Drag handling: install event filter on the card + the caption edits so
        # dragging anywhere on the panel (not just the empty margins) moves
        # the window. The buttons / size grip are excluded, so they keep working.
        self._card.installEventFilter(self)
        self.output_edit.installEventFilter(self)
        self.input_edit.installEventFilter(self)

        # P-1: coalesce rapid transcript updates into one repaint per frame.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_THROTTLE_MS)
        self._refresh_timer.timeout.connect(self._do_refresh)

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

        # P-2: only rebuild + setStyleSheet for the card when the dynamic
        # part (bg_alpha_dim) actually changed. Fonts / layout below are
        # cheap and always reapplied.
        if bg_alpha_dim != self._last_applied_alpha:
            self._card.setStyleSheet(self._build_card_qss(bg_alpha_dim))
            self._last_applied_alpha = bg_alpha_dim

        # Output (translated) font
        out_f = QFont()
        out_f.setFamilies(FONT_FAMILIES)
        out_f.setPointSize(max(12, s.font_size))
        out_f.setBold(True)
        self.output_edit.setFont(out_f)

        # Input (original) font
        in_f = QFont()
        in_f.setFamily("Segoe UI")
        in_f.setPointSize(max(10, int(s.font_size * 0.6)))
        in_f.setBold(False)
        self.input_edit.setFont(in_f)

        # Pin caption heights to N visible lines so older text scrolls out.
        self._apply_caption_height(self.output_edit, OUTPUT_VISIBLE_LINES)
        self._apply_caption_height(self.input_edit, INPUT_VISIBLE_LINES)

        # Status text + drag hint + badge fonts
        st_f = QFont()
        st_f.setFamilies(FONT_FAMILIES)
        st_f.setPointSize(9)
        self.status_label.setFont(st_f)
        self.drag_hint.setFont(st_f)
        self.lang_badge.setFont(st_f)
        self.drops_label.setFont(st_f)
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

        self._schedule_refresh()

    def _build_card_qss(self, bg_alpha_dim: int) -> str:
        r, g, b = HUD_BG_TOP
        hr, hg, hb = HUD_GLASS_HIGHLIGHT
        br, bg2, bb = HUD_BG_BOTTOM
        return f"""
        QFrame#card {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba({hr}, {hg}, {hb}, {bg_alpha_dim}),
                stop:0.04 rgba({r}, {g}, {b}, {bg_alpha_dim}),
                stop:1 rgba({br}, {bg2}, {bb}, {bg_alpha_dim}));
            border-radius: {RADIUS_CARD_HUD}px;
            border: 1px solid {HUD_BORDER};
            font-family: {FONT_FAMILY_QSS};
        }}
        QLabel {{
            color: white;
            background: transparent;
        }}
        QLabel#statusText {{ color: {HUD_TEXT_SECONDARY}; font-size: 11px; }}
        QLabel#dragHint   {{ color: {HUD_TEXT_TERTIARY}; font-size: 10px; }}
        QLabel#dropsLabel {{
            color: #FF9F43;
            background: rgba(255, 159, 67, 40);
            border-radius: {RADIUS_BUTTON}px;
            padding: 2px 8px;
            font-size: 10px;
            font-weight: 600;
        }}
        QLabel#badgeTranslated {{
            background: {ACCENT};
            color: white;
            border-radius: {RADIUS_BADGE}px;
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
            color: {HUD_INPUT_TEXT};
            font-style: italic;
        }}
        QFrame#divider {{
            background: {HUD_DIVIDER};
            border: none;
            max-height: 1px;
        }}
        QWidget#controlBar {{ background: transparent; }}
        QPushButton {{
            background: {HUD_BTN_BG};
            color: white;
            border: 1px solid {HUD_BTN_BORDER};
            border-radius: {RADIUS_BUTTON}px;
            padding: 6px 16px;
            font-size: 12px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {HUD_BTN_BG_HOVER};
            border-color: {HUD_BTN_BORDER_HOVER};
        }}
        QPushButton:pressed {{
            background: {HUD_BTN_BG_PRESSED};
            padding: 7px 16px 5px 16px;
        }}
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
        QPushButton#primaryBtn:pressed {{
            background: {ACCENT_PRESSED};
            border-color: {ACCENT_PRESSED};
            padding: 7px 16px 5px 16px;
        }}
        QPushButton#iconBtn {{
            background: rgba(255, 255, 255, 20);
            border: 1px solid {HUD_BTN_BORDER};
            padding: 0;
            font-size: 16px;
        }}
        QPushButton#iconBtn:pressed {{
            padding: 1px 0 0 0;
        }}
        QSizeGrip {{ background: transparent; }}
        QSizeGrip:hover {{
            background: rgba(255, 255, 255, 30);
            border-radius: 4px;
        }}
        """

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
        self._schedule_refresh()

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
        self._schedule_refresh()

    def set_status(self, text: str, kind: str = "info") -> None:
        self._status = text or ""
        self._status_kind = kind
        self._schedule_refresh()

    def set_drops(self, count: int) -> None:
        """P-6: show the cumulative dropped-chunk count when > 0."""
        self._drops = max(0, int(count))
        self._schedule_refresh()

    def clear(self) -> None:
        self._out_committed = ""
        self._out_draft = ""
        self._in_committed = ""
        self._in_draft = ""
        self._schedule_refresh()

    def set_running_state(self, running: bool) -> None:
        self.toggle_btn.setText(tr("hud.pause") if running else tr("hud.start"))

    def show(self) -> None:
        super().show()
        if not self._shown_once:
            self._shown_once = True
            self._play_entrance()

    def _play_entrance(self) -> None:
        """Fade the HUD in on first show for a polished entrance."""
        self.setWindowOpacity(0.0)
        self._entrance_anim = QPropertyAnimation(self, b"windowOpacity")
        self._entrance_anim.setDuration(ANIM_SLOW)
        self._entrance_anim.setStartValue(0.0)
        self._entrance_anim.setEndValue(1.0)
        self._entrance_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._entrance_anim.start()

    def restore_geometry(self, geometry_b64: str) -> None:
        """Restore window position/size from a base64-encoded QByteArray."""
        if not geometry_b64:
            return
        try:
            raw = base64.b64decode(geometry_b64)
            self.restoreGeometry(QByteArray(raw))
        except Exception:
            pass
        # UI-4: if the restored geometry landed off-screen (e.g. a monitor
        # was disconnected since last run), pull the HUD back onto a visible
        # screen so it never becomes unreachable.
        self._ensure_on_screen()

    def _ensure_on_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        geo = self.frameGeometry()
        for s in screens:
            if s.geometry().intersects(geo):
                return  # at least partially visible
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            sg = primary.availableGeometry()
            self.move(sg.center() - self.rect().center())

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
        name = next((n for c, n in LANGUAGES if c == code), code.upper())
        self.lang_badge.setText(f"→ {name}")

    def _schedule_refresh(self) -> None:
        # P-1: collapse multiple state mutations arriving within one frame
        # into a single repaint.
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _do_refresh(self) -> None:
        out_text = self._out_committed
        if self._out_draft:
            out_text = (out_text + "\n" + self._out_draft).strip("\n")
        out_text = out_text if out_text else tr("hud.empty")
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
        # Drive the dot directly from the kind (no string matching).
        self.status_dot.set_kind(self._status_kind)

        # P-6: drops indicator.
        if self._drops > 0:
            self.drops_label.setText(tr("quality.drops", n=self._drops))
            if not self.drops_label.isVisible():
                self.drops_label.setVisible(True)
        else:
            if self.drops_label.isVisible():
                self.drops_label.setVisible(False)

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
        # UI-6: expand the control bar + reveal buttons on hover.
        self._animate_control_bar(CONTROL_BAR_HEIGHT)
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # UI-6: collapse the control bar so captions reclaim the space.
        self._animate_control_bar(0)
        for btn in (self.toggle_btn, self.clear_btn, self.settings_btn, self.exit_btn):
            btn.setVisible(False)
        super().leaveEvent(event)

    def _animate_control_bar(self, target: int) -> None:
        self._ctrl_anim.stop()
        self._ctrl_anim.setStartValue(self.control_bar.maximumHeight())
        self._ctrl_anim.setEndValue(target)
        self._ctrl_anim.start()

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
                # UI-5: fade the drag hint out after the first real drag —
                # the user has learned they can drag, the hint is now noise.
                if not self._dragged_once:
                    self._dragged_once = True
                    self.drag_hint.fade_out()
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
        if self._drag_offset is not None:
            self._drag_offset = None
            if not self._dragged_once:
                self._dragged_once = True
                self.drag_hint.fade_out()
        event.accept()
