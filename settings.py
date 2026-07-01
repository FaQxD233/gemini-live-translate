"""Application settings + persistence (port of AppSettings.swift)."""
from __future__ import annotations

import json
import os
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# (code, human name) - subset of macOS TranslationLanguage.all
LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"),
    ("vi", "Vietnamese"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("hi", "Hindi"),
    ("ar", "Arabic"),
    ("th", "Thai"),
    ("id", "Indonesian"),
    ("tr", "Turkish"),
]


DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_GEMINI_MODEL = "models/gemini-3.5-live-translate-preview"


@dataclass
class AppSettings:
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE  # override for OpenAI-compatible proxies
    target_language: str = "zh-CN"
    audio_source: str = "system"  # "mic" | "system"
    font_size: int = 16
    bg_opacity: float = 0.6  # 0..1
    echo_target_language: bool = False
    playback_volume: float = 0.8  # 0..1
    system_prompt: str = ""
    show_original: bool = False  # show source-language transcript
    gemini_model: str = DEFAULT_GEMINI_MODEL  # model ID sent in setup
    hud_geometry: str = ""  # base64 of QByteArray from QWidget.saveGeometry()

    def __post_init__(self) -> None:
        self._normalize()

    @staticmethod
    def config_dir() -> Path:
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "gemini-live-translate"

    @staticmethod
    def _coerce_str(value, default: str = "") -> str:
        return value if isinstance(value, str) else default

    @staticmethod
    def _coerce_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
        return default

    @staticmethod
    def _coerce_int(value, default: int, minimum: int, maximum: int) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = default
        return max(minimum, min(maximum, v))

    @staticmethod
    def _coerce_float(value, default: float, minimum: float, maximum: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = default
        return max(minimum, min(maximum, v))

    def _normalize(self) -> None:
        valid_languages = {code for code, _ in LANGUAGES}
        self.api_key = self._coerce_str(self.api_key)
        self.api_base = self._coerce_str(self.api_base, DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
        self.target_language = self._coerce_str(self.target_language, "zh-CN")
        if self.target_language == "zh":
            self.target_language = "zh-CN"
        if self.target_language not in valid_languages:
            self.target_language = "zh-CN"
        self.audio_source = self._coerce_str(self.audio_source, "system")
        if self.audio_source not in {"mic", "system"}:
            self.audio_source = "system"
        self.font_size = self._coerce_int(self.font_size, 16, 14, 60)
        self.bg_opacity = self._coerce_float(self.bg_opacity, 0.6, 0.2, 0.95)
        self.echo_target_language = self._coerce_bool(self.echo_target_language, False)
        self.playback_volume = self._coerce_float(self.playback_volume, 0.8, 0.0, 1.0)
        self.system_prompt = self._coerce_str(self.system_prompt)
        self.show_original = self._coerce_bool(self.show_original, False)
        self.gemini_model = self._coerce_str(self.gemini_model, DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        self.hud_geometry = self._coerce_str(self.hud_geometry)

    @staticmethod
    def _legacy_config_path() -> Optional[Path]:
        """Old install path used before the rename. Used for one-time
        migration so users don't lose their API key."""
        for name in ("LiveBuddy",):
            p = Path(os.environ.get("APPDATA", str(Path.home()))) / name / "settings.json"
            if p.exists():
                return p
        return None

    @classmethod
    def load(cls) -> "AppSettings":
        p = cls.config_dir() / "settings.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                known = set(cls.__dataclass_fields__.keys())
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                traceback.print_exc()
        # First run after rename: silently migrate the legacy config so the
        # user's existing API key / language preference survives.
        legacy = cls._legacy_config_path()
        if legacy is not None:
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
                known = set(cls.__dataclass_fields__.keys())
                migrated = cls(**{k: v for k, v in data.items() if k in known})
                # Persist to the new location so we don't keep migrating.
                migrated.save()
                return migrated
            except Exception:
                traceback.print_exc()
        return cls()

    def save(self) -> None:
        d = self.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "settings.json"
        p.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
