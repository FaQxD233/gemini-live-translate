"""Application settings + persistence (port of AppSettings.swift)."""
from __future__ import annotations

import json
import os
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
    ("zh", "Chinese"),
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


@dataclass
class AppSettings:
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE  # override for OpenAI-compatible proxies
    target_language: str = "zh"
    audio_source: str = "system"  # "mic" | "system"
    font_size: int = 16
    bg_opacity: float = 0.6  # 0..1
    echo_target_language: bool = False
    playback_volume: float = 0.8  # 0..1
    system_prompt: str = ""
    show_original: bool = False  # show source-language transcript

    @staticmethod
    def config_dir() -> Path:
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "gemini-live-translate"

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
                pass
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
                pass
        return cls()

    def save(self) -> None:
        d = self.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "settings.json"
        p.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
