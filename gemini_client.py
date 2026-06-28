"""Gemini Live Translate WebSocket client (port of GeminiLiveTranslateClient.swift).

Runs an asyncio loop in a background thread. Audio capture callbacks feed PCM16
bytes via `send_audio()` which uses `asyncio.run_coroutine_threadsafe`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
from typing import Optional

import websockets
from PySide6.QtCore import QObject, Signal

# Default Google Gemini official endpoint. The user can override this with
# a self-hosted proxy or regional mirror in Settings.
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
# WebSocket path appended to the (scheme-swapped) API base.
GEMINI_WS_PATH = (
    "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
GEMINI_MODEL = "models/gemini-3.5-live-translate-preview"
SETUP_TIMEOUT_SEC = 8.0


class GeminiClient(QObject):
    # UI-facing signals (emitted from the asyncio thread; Qt cross-thread safe)
    inputTranscript = Signal(str)
    outputTranscript = Signal(str)
    audioChunk = Signal(bytes)
    status = Signal(str)
    connected = Signal()
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._thread: Optional[threading.Thread] = None
        self._api_key = ""
        self._api_base = DEFAULT_API_BASE
        self._target_lang = "es"
        self._system_prompt = ""
        self._echo = True
        self._running = False

    def configure(
        self,
        api_key: str,
        target_lang: str,
        system_prompt: str,
        echo_target_language: bool,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        self._api_key = api_key.strip()
        self._api_base = (api_base or DEFAULT_API_BASE).strip()
        self._target_lang = target_lang
        self._system_prompt = system_prompt
        self._echo = echo_target_language

    def _build_ws_url(self) -> str:
        """Build the WebSocket URL from the configured API base.
        https://... → wss://...
        http://...  → ws://...
        anything else is passed through as-is (let user specify wss://).
        """
        base = (self._api_base or DEFAULT_API_BASE).rstrip("/")
        if base.startswith("https://"):
            ws = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            ws = "ws://" + base[len("http://"):]
        else:
            ws = base
        return f"{ws}{GEMINI_WS_PATH}?key={self._api_key}"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        loop = self._loop
        ws = self._ws
        if loop and ws:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(ws), loop).result(timeout=2.0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def send_audio(self, pcm16_bytes: bytes) -> None:
        """Thread-safe: called from audio capture callback thread."""
        loop = self._loop
        ws = self._ws
        if not loop or not ws or not pcm16_bytes:
            return
        b64 = base64.b64encode(pcm16_bytes).decode("ascii")
        msg = {
            "realtimeInput": {
                "audio": {
                    "data": b64,
                    "mimeType": "audio/pcm;rate=16000",
                }
            }
        }
        text = json.dumps(msg)
        try:
            asyncio.run_coroutine_threadsafe(ws.send(text), loop)
        except Exception as e:
            self.status.emit(f"Send failed: {e}")

    # ---------- internal ----------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.status.emit(f"Gemini error: {e}")
        finally:
            self._loop.close()
            self._loop = None
            self.disconnected.emit("Session ended")

    async def _main(self) -> None:
        url = self._build_ws_url()
        try:
            async with websockets.connect(url, max_size=2 ** 22) as ws:
                self._ws = ws
                self.status.emit("Gemini socket opened")
                await self._send_setup()
                try:
                    await asyncio.wait_for(
                        self._wait_for_setup_complete(), timeout=SETUP_TIMEOUT_SEC
                    )
                except asyncio.TimeoutError:
                    self.status.emit("Gemini setup timed out")
                    return
                self.status.emit("Gemini session ready")
                self.connected.emit()

                async for raw in ws:
                    await self._handle_message(raw)
                    if not self._running:
                        break
        except websockets.ConnectionClosed as e:
            self.disconnected.emit(f"Closed: {e}")
        except Exception as e:
            self.status.emit(f"Gemini error: {e}")
            self.disconnected.emit(f"Error: {e}")
        finally:
            self._ws = None

    async def _send_setup(self) -> None:
        setup: dict = {
            "model": GEMINI_MODEL,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": self._target_lang,
                    "echoTargetLanguage": self._echo,
                },
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "contextWindowCompression": {
                "triggerTokens": "0",
                "slidingWindow": {"targetTokens": "0"},
            },
        }
        instruction = self._system_prompt.strip()
        if instruction:
            setup["systemInstruction"] = {"parts": [{"text": instruction}]}
        await self._send_json({"setup": setup})

    async def _wait_for_setup_complete(self) -> None:
        while True:
            raw = await self._ws.recv()
            try:
                root = json.loads(raw)
            except Exception:
                continue
            if not isinstance(root, dict):
                continue
            err = root.get("error")
            if isinstance(err, dict):
                raise RuntimeError(f"Gemini error: {err.get('message', 'Unknown')}")
            if "setupComplete" in root:
                return
            await self._handle_root(root)

    async def _handle_message(self, raw) -> None:
        try:
            root = json.loads(raw)
        except Exception as e:
            self.status.emit(f"Parse failed: {e}")
            return
        await self._handle_root(root)

    async def _handle_root(self, root) -> None:
        if not isinstance(root, dict):
            return
        err = root.get("error")
        if isinstance(err, dict):
            self.status.emit(f"Gemini error: {err.get('message', '')}")
            return
        content = root.get("serverContent")
        if not isinstance(content, dict):
            return

        it = content.get("inputTranscription")
        if isinstance(it, dict) and it.get("text"):
            self.inputTranscript.emit(it["text"])

        ot = content.get("outputTranscription")
        if isinstance(ot, dict) and ot.get("text"):
            self.outputTranscript.emit(ot["text"])

        model_turn = content.get("modelTurn")
        if isinstance(model_turn, dict):
            parts = model_turn.get("parts") or []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData")
                if isinstance(inline, dict) and inline.get("data"):
                    try:
                        audio = base64.b64decode(inline["data"])
                        self.audioChunk.emit(audio)
                    except Exception:
                        pass
                if part.get("text"):
                    self.outputTranscript.emit(part["text"])

    async def _send_json(self, obj: dict) -> None:
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(obj))

    async def _shutdown(self, ws) -> None:
        try:
            await ws.close()
        except Exception:
            pass
