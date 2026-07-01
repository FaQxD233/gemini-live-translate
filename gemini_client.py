"""Gemini Live Translate WebSocket client (port of GeminiLiveTranslateClient.swift).

Runs an asyncio loop in a background thread. Audio capture callbacks feed PCM16
bytes via `send_audio()` which uses `asyncio.run_coroutine_threadsafe`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import traceback
from concurrent.futures import CancelledError
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
MAX_PENDING_SENDS = 20
# Max consecutive setup timeouts before giving up (avoids infinite reconnect
# loops on permanent errors like a wrong model name that doesn't return an
# explicit error response).
MAX_SETUP_FAILURES = 3
# Minimum interval between "audio dropped" status emissions (seconds).
DROP_WARN_INTERVAL_SEC = 5.0


class GeminiClient(QObject):
    # UI-facing signals (emitted from the asyncio thread; Qt cross-thread safe)
    inputTranscript = Signal(int, str)
    outputTranscript = Signal(int, str)
    audioChunk = Signal(int, bytes)
    status = Signal(int, str)
    connected = Signal(int)
    disconnected = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._thread: Optional[threading.Thread] = None
        self._api_key = ""
        self._api_base = DEFAULT_API_BASE
        self._target_lang = "es"
        self._system_prompt = ""
        self._echo = False
        self._model = GEMINI_MODEL
        self._running = False
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._session_id = 0
        self._send_generation = 0
        self._pending_sends = 0
        self._consecutive_setup_failures = 0
        self._last_drop_warn = 0.0
        self._last_start_error = ""

    def configure(
        self,
        api_key: str,
        target_lang: str,
        system_prompt: str,
        echo_target_language: bool,
        api_base: str = DEFAULT_API_BASE,
        model: str = GEMINI_MODEL,
    ) -> None:
        self._api_key = api_key.strip()
        self._api_base = (api_base or DEFAULT_API_BASE).strip()
        self._target_lang = target_lang
        self._system_prompt = system_prompt
        self._echo = echo_target_language
        self._model = (model or GEMINI_MODEL).strip()

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

    def start(self) -> Optional[int]:
        with self._state_lock:
            self._last_start_error = ""
            if self._running:
                self._last_start_error = "Gemini session is already running"
                return None
            if self._thread is not None and self._thread.is_alive():
                self._last_start_error = "Previous Gemini session is still stopping"
                return None
            self._session_id += 1
            session_id = self._session_id
            self._send_generation += 1
            self._pending_sends = 0
            self._consecutive_setup_failures = 0
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(session_id,),
                daemon=True,
            )
            thread = self._thread
        thread.start()
        return session_id

    def last_start_error(self) -> str:
        with self._state_lock:
            return self._last_start_error

    def stop(self) -> None:
        with self._state_lock:
            thread = self._thread
            if not self._running and not (thread is not None and thread.is_alive()):
                return
            self._running = False
            self._stop_event.set()
            loop = self._loop
            ws = self._ws
        if loop and ws:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(ws), loop).result(timeout=2.0)
            except Exception:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            with self._state_lock:
                if self._thread is thread and not thread.is_alive():
                    self._thread = None

    def send_audio(self, pcm16_bytes: bytes, session_id: Optional[int] = None) -> None:
        """Thread-safe: called from audio capture callback thread."""
        if not pcm16_bytes:
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
        drop_warn = False
        should_send = False
        with self._state_lock:
            loop = self._loop
            ws = self._ws
            current_session_id = self._session_id
            send_generation = self._send_generation
            if session_id is not None and session_id != current_session_id:
                return
            session_id = current_session_id
            if not self._running or not loop or not ws:
                return
            if self._pending_sends >= MAX_PENDING_SENDS:
                # Backpressure: too many in-flight sends. Drop this chunk
                # but warn the user (throttled) so they know audio is lagging.
                now = time.monotonic()
                if now - self._last_drop_warn >= DROP_WARN_INTERVAL_SEC:
                    self._last_drop_warn = now
                    drop_warn = True
            else:
                self._pending_sends += 1
                should_send = True

        if drop_warn:
            self.status.emit(session_id, "Network slow — audio being dropped")
        if not should_send:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(ws.send(text), loop)
            future.add_done_callback(
                lambda fut, sid=session_id, gen=send_generation: self._on_send_done(
                    fut, sid, gen
                )
            )
        except Exception as e:
            self._decrement_pending_send(send_generation)
            self.status.emit(session_id, f"Send failed: {e}")

    def _decrement_pending_send(self, send_generation: int) -> None:
        with self._state_lock:
            if send_generation == self._send_generation and self._pending_sends > 0:
                self._pending_sends -= 1

    def _on_send_done(self, future, session_id: int, send_generation: int) -> None:
        self._decrement_pending_send(send_generation)
        try:
            exc = future.exception()
        except CancelledError:
            return
        except Exception:
            return
        if exc is not None and self._is_active(session_id):
            self.status.emit(session_id, f"Send failed: {exc}")

    def _is_active(self, session_id: int) -> bool:
        with self._state_lock:
            return (
                self._running
                and self._session_id == session_id
                and not self._stop_event.is_set()
            )

    # ---------- internal ----------

    def _run_loop(self, session_id: int) -> None:
        loop = asyncio.new_event_loop()
        with self._state_lock:
            if self._session_id != session_id:
                loop.close()
                return
            self._loop = loop
        asyncio.set_event_loop(loop)
        reconnect_delay = 1.0
        try:
            while self._is_active(session_id):
                self._was_connected = False
                try:
                    loop.run_until_complete(self._main(session_id))
                except Exception as e:
                    self.status.emit(session_id, f"Gemini error: {e}")
                    self._last_error = str(e)
                    self._reconnect_ok = False

                if not self._is_active(session_id):
                    break  # user-initiated stop

                if not getattr(self, "_reconnect_ok", False):
                    break  # non-reconnectable error (e.g. bad API key)

                # Reset backoff after a successful session
                if getattr(self, "_was_connected", False):
                    reconnect_delay = 1.0

                self.status.emit(session_id, f"Reconnecting in {int(reconnect_delay)}s...")
                # Sleep in small increments so user stop is detected quickly
                slept = 0.0
                while self._is_active(session_id) and slept < reconnect_delay:
                    loop.run_until_complete(asyncio.sleep(0.5))
                    slept += 0.5
                reconnect_delay = min(reconnect_delay * 2, 30.0)

            # Emit disconnected only on error exit (not user stop)
            should_emit = False
            with self._state_lock:
                if self._session_id == session_id and self._running:
                    self._running = False
                    should_emit = True
            if should_emit:
                reason = getattr(self, "_last_error", "") or "Session ended"
                self.disconnected.emit(session_id, reason)
        finally:
            loop.close()
            with self._state_lock:
                if self._session_id == session_id:
                    self._loop = None
                    self._ws = None
                    self._send_generation += 1
                    self._pending_sends = 0
                    if self._thread is threading.current_thread():
                        self._thread = None

    async def _main(self, session_id: int) -> None:
        url = self._build_ws_url()
        self._last_error = ""
        self._reconnect_ok = True
        try:
            async with websockets.connect(
                url,
                max_size=2 ** 22,
                open_timeout=SETUP_TIMEOUT_SEC,
                close_timeout=2.0,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                if not self._is_active(session_id):
                    return
                with self._state_lock:
                    if self._session_id == session_id:
                        self._ws = ws
                        self._send_generation += 1
                        self._pending_sends = 0
                self.status.emit(session_id, "Gemini socket opened")
                await self._send_setup()
                try:
                    await asyncio.wait_for(
                        self._wait_for_setup_complete(session_id),
                        timeout=SETUP_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    self._consecutive_setup_failures += 1
                    if self._consecutive_setup_failures >= MAX_SETUP_FAILURES:
                        self.status.emit(
                            session_id,
                            "Gemini setup timed out repeatedly. "
                            "Check model name and API key."
                        )
                        self._last_error = "Setup timed out (repeated)"
                        self._reconnect_ok = False
                    else:
                        self.status.emit(
                            session_id,
                            f"Gemini setup timed out "
                            f"(attempt {self._consecutive_setup_failures}/{MAX_SETUP_FAILURES})"
                        )
                        self._last_error = "Setup timed out"
                        self._reconnect_ok = True
                    return
                # Reset the failure counter once setup succeeds.
                self._consecutive_setup_failures = 0
                if not self._is_active(session_id):
                    return
                self.status.emit(session_id, "Gemini session ready")
                self.connected.emit(session_id)
                self._was_connected = True

                async for raw in ws:
                    await self._handle_message(session_id, raw)
                    if not self._is_active(session_id):
                        break
        except websockets.ConnectionClosed:
            self._last_error = "Connection closed"
            self._reconnect_ok = True
        except OSError as e:
            # Network errors (DNS failure, connection refused, host unreachable,
            # etc.) are potentially transient — allow reconnection with backoff.
            self.status.emit(session_id, f"Gemini network error: {e}")
            self._last_error = str(e)
            self._reconnect_ok = True
        except Exception as e:
            self.status.emit(session_id, f"Gemini error: {e}")
            self._last_error = str(e)
            self._reconnect_ok = False
        finally:
            with self._state_lock:
                if self._session_id == session_id:
                    self._ws = None
                    self._send_generation += 1
                    self._pending_sends = 0

    async def _send_setup(self) -> None:
        setup: dict = {
            "model": self._model,
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

    async def _wait_for_setup_complete(self, session_id: int) -> None:
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
            await self._handle_root(session_id, root)

    async def _handle_message(self, session_id: int, raw) -> None:
        try:
            root = json.loads(raw)
        except Exception as e:
            self.status.emit(session_id, f"Parse failed: {e}")
            return
        await self._handle_root(session_id, root)

    async def _handle_root(self, session_id: int, root) -> None:
        if not isinstance(root, dict):
            return
        err = root.get("error")
        if isinstance(err, dict):
            message = err.get("message", "Unknown Gemini error")
            self.status.emit(session_id, f"Gemini error: {message}")
            raise RuntimeError(message)
        content = root.get("serverContent")
        if not isinstance(content, dict):
            return

        it = content.get("inputTranscription")
        if isinstance(it, dict) and it.get("text"):
            self.inputTranscript.emit(session_id, it["text"])

        ot = content.get("outputTranscription")
        if isinstance(ot, dict) and ot.get("text"):
            self.outputTranscript.emit(session_id, ot["text"])

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
                        self.audioChunk.emit(session_id, audio)
                    except Exception:
                        traceback.print_exc()
                if part.get("text"):
                    self.outputTranscript.emit(session_id, part["text"])

    async def _send_json(self, obj: dict) -> None:
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(obj))

    async def _shutdown(self, ws) -> None:
        try:
            await ws.close()
        except Exception:
            pass
