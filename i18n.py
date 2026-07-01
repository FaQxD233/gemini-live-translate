"""Lightweight i18n for UI strings.

Auto-detects UI language from system locale. Ships English (en) and
Simplified Chinese (zh-CN); falls back to English for anything else.
Adding a language = add an entry to TRANSLATIONS and a branch in
detect_language().
"""
from __future__ import annotations

import locale
from typing import Dict

# Language code → {key: translation}
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        # HUD
        "hud.drag_hint": "⠿ drag",
        "hud.start": "Start",
        "hud.pause": "Pause",
        "hud.clear": "Clear",
        "hud.settings": "Settings",
        "hud.exit": "Exit",
        "hud.empty": "Waiting for speech…",
        # Status kinds are driven by the `kind` argument; these are messages.
        "status.connecting": "Connecting...",
        "status.connected": "Connected",
        "status.disconnected": "Disconnected",
        "status.stopped": "Stopped",
        "status.start_failed": "Could not start Gemini session",
        "status.socket_opened": "Gemini socket opened",
        "status.session_ready": "Gemini session ready",
        "status.reconnecting": "Reconnecting in {secs}s...",
        "status.network_slow": "Network slow — audio being dropped",
        "status.setup_timeout": "Gemini setup timed out (attempt {n}/{total})",
        "status.setup_timeout_final": "Gemini setup timed out repeatedly. Check model name and API key.",
        "status.gemini_error": "Gemini error: {msg}",
        "status.gemini_network_error": "Gemini network error: {msg}",
        "status.send_failed": "Send failed: {msg}",
        "status.parse_failed": "Parse failed: {msg}",
        "status.capture_error": "Capture error",
        "status.playback_error": "Playback error",
        "status.disconnected_reason": "Disconnected: {reason}",
        # Main / dialogs
        "app.title": "Gemini Live Translate",
        "app.api_key_required": "API key required",
        "app.api_key_msg": "Please set your Google Gemini API key in Settings.",
        "app.capture_error": "Capture error",
        "app.capture_error_msg": "Could not start audio capture ({source}):\n{error}",
        "app.playback_error": "Playback error",
        "app.playback_error_msg": "Could not open audio output:\n{error}",
        "app.settings_error": "Settings error",
        "app.settings_error_msg": "Failed to open settings dialog:\n{error}",
        "app.already_running": "Gemini Live Translate is already running.",
        # Tray
        "tray.toggle": "Start / Stop",
        "tray.settings": "Settings...",
        "tray.show_hud": "Show HUD",
        "tray.quit": "Quit",
        # Settings dialog
        "settings.title": "Gemini Live Translate Settings",
        "settings.subtitle": "Real-time speech translation powered by Gemini Live",
        "settings.section.connection": "CONNECTION",
        "settings.section.audio": "AUDIO",
        "settings.section.appearance": "APPEARANCE",
        "settings.section.advanced": "ADVANCED",
        "settings.api_key": "Gemini API Key",
        "settings.api_key_placeholder": "Paste your Google Gemini API key",
        "settings.show": "Show",
        "settings.hide": "Hide",
        "settings.api_base": "API Base URL",
        "settings.translate_to": "Translate to",
        "settings.audio_source": "Audio source",
        "settings.source_system": "System Audio (loopback)",
        "settings.source_mic": "Microphone",
        "settings.playback_volume": "Playback volume",
        "settings.echo": "Echo target-language audio (play translated speech)",
        "settings.font_size": "Caption font size",
        "settings.bg_opacity": "Background opacity",
        "settings.show_original": "Show original (source-language) transcript",
        "settings.model_id": "Model ID",
        "settings.system_prompt": "System prompt",
        "settings.prompt_placeholder": "Optional custom instructions sent to the model as systemInstruction.",
        "settings.test_connection": "Test Connection",
        "settings.testing": "Testing...",
        "settings.test_success": "✓ Connection OK",
        "settings.test_failed": "✗ Failed: {error}",
        "settings.save": "Save",
        "settings.cancel": "Cancel",
        # Quality
        "quality.drops": "⚡ {n} dropped",
    },
    "zh-CN": {
        # HUD
        "hud.drag_hint": "⠿ 拖动",
        "hud.start": "开始",
        "hud.pause": "暂停",
        "hud.clear": "清空",
        "hud.settings": "设置",
        "hud.exit": "退出",
        "hud.empty": "等待语音…",
        # Status
        "status.connecting": "连接中…",
        "status.connected": "已连接",
        "status.disconnected": "已断开",
        "status.stopped": "已停止",
        "status.start_failed": "无法启动 Gemini 会话",
        "status.socket_opened": "Gemini 连接已建立",
        "status.session_ready": "Gemini 会话就绪",
        "status.reconnecting": "{secs} 秒后重连…",
        "status.network_slow": "网络较慢 — 正在丢弃音频",
        "status.setup_timeout": "Gemini 初始化超时（第 {n}/{total} 次）",
        "status.setup_timeout_final": "Gemini 初始化反复超时，请检查模型名与 API key。",
        "status.gemini_error": "Gemini 错误：{msg}",
        "status.gemini_network_error": "Gemini 网络错误：{msg}",
        "status.send_failed": "发送失败：{msg}",
        "status.parse_failed": "解析失败：{msg}",
        "status.capture_error": "采集错误",
        "status.playback_error": "播放错误",
        "status.disconnected_reason": "已断开：{reason}",
        # Main / dialogs
        "app.title": "Gemini 实时翻译",
        "app.api_key_required": "需要 API key",
        "app.api_key_msg": "请在设置中填入 Google Gemini API key。",
        "app.capture_error": "采集错误",
        "app.capture_error_msg": "无法启动音频采集（{source}）：\n{error}",
        "app.playback_error": "播放错误",
        "app.playback_error_msg": "无法打开音频输出：\n{error}",
        "app.settings_error": "设置错误",
        "app.settings_error_msg": "无法打开设置对话框：\n{error}",
        "app.already_running": "Gemini 实时翻译已在运行。",
        # Tray
        "tray.toggle": "开始 / 停止",
        "tray.settings": "设置…",
        "tray.show_hud": "显示字幕条",
        "tray.quit": "退出",
        # Settings dialog
        "settings.title": "Gemini 实时翻译设置",
        "settings.subtitle": "基于 Gemini Live 的实时语音翻译",
        "settings.section.connection": "连接",
        "settings.section.audio": "音频",
        "settings.section.appearance": "外观",
        "settings.section.advanced": "高级",
        "settings.api_key": "Gemini API Key",
        "settings.api_key_placeholder": "粘贴你的 Google Gemini API key",
        "settings.show": "显示",
        "settings.hide": "隐藏",
        "settings.api_base": "API Base URL",
        "settings.translate_to": "翻译为",
        "settings.audio_source": "音频来源",
        "settings.source_system": "系统音频（loopback）",
        "settings.source_mic": "麦克风",
        "settings.playback_volume": "回放音量",
        "settings.echo": "回放目标语言音频（朗读翻译结果）",
        "settings.font_size": "字幕字号",
        "settings.bg_opacity": "背景不透明度",
        "settings.show_original": "显示原文（源语言）字幕",
        "settings.model_id": "模型 ID",
        "settings.system_prompt": "系统提示词",
        "settings.prompt_placeholder": "可选，作为 systemInstruction 发送给模型的自定义指令。",
        "settings.test_connection": "测试连接",
        "settings.testing": "测试中…",
        "settings.test_success": "✓ 连接正常",
        "settings.test_failed": "✗ 失败：{error}",
        "settings.save": "保存",
        "settings.cancel": "取消",
        # Quality
        "quality.drops": "⚡ 丢弃 {n} 段",
    },
}

_current_lang = "en"


def detect_language() -> str:
    """Detect UI language from system locale. Returns a code in TRANSLATIONS."""
    try:
        loc = locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = ""
    if loc.lower().startswith("zh"):
        return "zh-CN"
    return "en"


def set_language(code: str) -> None:
    global _current_lang
    if code in TRANSLATIONS:
        _current_lang = code


def get_language() -> str:
    return _current_lang


def tr(key: str, **kwargs) -> str:
    """Translate a key with optional str.format arguments.

    Falls back to English, then to the key itself if missing.
    """
    table = TRANSLATIONS.get(_current_lang) or TRANSLATIONS["en"]
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


# Initialize on import.
set_language(detect_language())
