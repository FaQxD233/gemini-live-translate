"""Audio capture (mic / WASAPI loopback) and playback via PyAudioWPatch.

sounddevice 0.5.x removed the `loopback` kwarg from WasapiSettings; PyAudioWPatch
is a PyAudio fork with first-class WASAPI loopback support, so we use it for
all audio I/O.
"""
from __future__ import annotations

import threading
import traceback
from collections import deque
from queue import Empty, Full, Queue
from typing import Callable, Deque, Optional

import numpy as np
import pyaudiowpatch as pyaudio

from pcm_processor import PCM16Chunker, PCM16Downsampler

# Gemini Live API expects 16 kHz mono PCM16 input and returns 24 kHz Float32 PCM output.
GEMINI_INPUT_RATE = 16000
GEMINI_OUTPUT_RATE = 24000
CHUNK_SIZE = 3200  # bytes per WebSocket send (matches macOS impl)
FRAMES_PER_BUFFER = 1024
# Cap AudioPlayer buffer to ~5 seconds to prevent unbounded growth when the
# producer (Gemini audio chunks) outpaces the consumer (PyAudio output callback).
MAX_BUFFER_SAMPLES = GEMINI_OUTPUT_RATE * 5
# Keep capture latency bounded if CPU/network processing falls behind.
CAPTURE_QUEUE_SIZE = 12

# Process-wide PyAudio instance (PyAudio is expensive to init; multiple streams
# can share one instance).
_pyaudio: Optional[pyaudio.PyAudio] = None
_pyaudio_lock = threading.Lock()


def _get_pyaudio() -> pyaudio.PyAudio:
    global _pyaudio
    with _pyaudio_lock:
        if _pyaudio is None:
            _pyaudio = pyaudio.PyAudio()
        return _pyaudio


def terminate_pyaudio() -> None:
    """Terminate the process-wide PyAudio instance on app shutdown."""
    global _pyaudio
    with _pyaudio_lock:
        if _pyaudio is not None:
            try:
                _pyaudio.terminate()
            except Exception:
                traceback.print_exc()
            _pyaudio = None


class AudioCapture:
    """Capture audio from microphone or system output (WASAPI loopback)."""

    def __init__(
        self, source: str, on_pcm16_chunk: Callable[[bytes], None]
    ) -> None:
        self.source = source  # "mic" | "system"
        self.on_chunk = on_pcm16_chunk
        self.downsampler = PCM16Downsampler(target_rate=GEMINI_INPUT_RATE)
        self.chunker = PCM16Chunker(chunk_size=CHUNK_SIZE, on_chunk=on_pcm16_chunk)
        self._stream = None
        self._samplerate = 0
        self._channels = 1
        self._queue: Queue[tuple[bytes, int, int]] = Queue(maxsize=CAPTURE_QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._stream is not None:
            return
        pa = _get_pyaudio()

        if self.source == "system":
            # WASAPI loopback device (captures the default output endpoint's mix)
            try:
                dev = pa.get_default_wasapi_loopback()
            except OSError as e:
                raise RuntimeError(
                    "No WASAPI loopback device available. "
                    "Make sure a default audio output device is set in Windows."
                ) from e
            device_idx = dev["index"]
            samplerate = int(dev["defaultSampleRate"])
            # Loopback channel count must match the source output device
            # (typically 2 for stereo). The downsampler will downmix to mono.
            channels = max(1, int(dev["maxInputChannels"]))
        else:  # mic
            dev = pa.get_default_input_device_info()
            device_idx = dev["index"]
            samplerate = int(dev["defaultSampleRate"])
            # Request mono (mic capture usually mono anyway)
            channels = 1

        self._samplerate = samplerate
        self._channels = channels
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._worker.start()
        try:
            self._stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=device_idx,
                frames_per_buffer=FRAMES_PER_BUFFER,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
        except Exception:
            self._stop_worker()
            raise

    def _callback(self, in_data: bytes, frame_count: int, time_info, status) -> tuple:
        # Keep the realtime callback light; processing happens on a worker.
        try:
            item = (bytes(in_data), int(self._samplerate), int(self._channels))
            try:
                self._queue.put_nowait(item)
            except Full:
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass
                try:
                    self._queue.put_nowait(item)
                except Full:
                    pass
        except Exception:
            traceback.print_exc()
        return (None, pyaudio.paContinue)

    def _process_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                data, samplerate, channels = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                samples = np.frombuffer(data, dtype=np.float32)
                if channels > 1:
                    usable = samples.size - (samples.size % channels)
                    if usable <= 0:
                        continue
                    samples = samples[:usable].reshape(-1, channels)
                pcm16 = self.downsampler.convert(samples, samplerate)
                self.chunker.append(pcm16)
            except Exception:
                traceback.print_exc()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._stop_worker()
        self.chunker.reset()

    def _stop_worker(self) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        self._worker = None
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break


class AudioPlayer:
    """Play Float32 audio at 24 kHz mono (Gemini output format)."""

    def __init__(
        self, sample_rate: int = GEMINI_OUTPUT_RATE, volume: float = 0.8
    ) -> None:
        self.sample_rate = sample_rate
        self.volume = max(0.0, min(1.0, volume))
        self._lock = threading.Lock()
        self._buffers: Deque[np.ndarray] = deque()
        self._buffer_samples = 0
        self._stream = None

    def start(self) -> None:
        if self._stream is not None:
            return
        pa = _get_pyaudio()
        self._stream = pa.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=FRAMES_PER_BUFFER,
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def enqueue_pcm16(self, data: bytes) -> None:
        if not data:
            return
        # Drop odd trailing byte (defensive: Gemini shouldn't send odd-length PCM)
        if len(data) % 2 != 0:
            data = data[:-1]
        if not data:
            return
        arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        with self._lock:
            if arr.size >= MAX_BUFFER_SAMPLES:
                arr = arr[-MAX_BUFFER_SAMPLES:]
                self._buffers.clear()
                self._buffer_samples = 0
            self._buffers.append(arr)
            self._buffer_samples += arr.size
            # Keep the newest audio and drop stale samples so latency stays bounded.
            while self._buffer_samples > MAX_BUFFER_SAMPLES and self._buffers:
                overflow = self._buffer_samples - MAX_BUFFER_SAMPLES
                head = self._buffers[0]
                if head.size <= overflow:
                    self._buffers.popleft()
                    self._buffer_samples -= head.size
                else:
                    self._buffers[0] = head[overflow:]
                    self._buffer_samples -= overflow

    def _callback(self, in_data: bytes, frame_count: int, time_info, status) -> tuple:
        out = np.zeros(frame_count, dtype=np.float32)
        with self._lock:
            pos = 0
            remaining = frame_count
            volume = self.volume
            while remaining > 0 and self._buffers:
                head = self._buffers[0]
                take = min(remaining, head.size)
                out[pos : pos + take] = head[:take] * volume
                if take == head.size:
                    self._buffers.popleft()
                else:
                    self._buffers[0] = head[take:]
                self._buffer_samples -= take
                pos += take
                remaining -= take
        return (out.tobytes(), pyaudio.paContinue)

    def set_volume(self, v: float) -> None:
        with self._lock:
            self.volume = max(0.0, min(1.0, v))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            self._buffers.clear()
            self._buffer_samples = 0
