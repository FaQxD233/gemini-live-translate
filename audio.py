"""Audio capture (mic / WASAPI loopback) and playback via PyAudioWPatch.

sounddevice 0.5.x removed the `loopback` kwarg from WasapiSettings; PyAudioWPatch
is a PyAudio fork with first-class WASAPI loopback support, so we use it for
all audio I/O.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np
import pyaudiowpatch as pyaudio

from pcm_processor import PCM16Chunker, PCM16Downsampler

# Gemini Live API expects 16 kHz mono PCM16 input and returns 24 kHz Float32 PCM output.
GEMINI_INPUT_RATE = 16000
GEMINI_OUTPUT_RATE = 24000
CHUNK_SIZE = 3200  # bytes per WebSocket send (matches macOS impl)
FRAMES_PER_BUFFER = 1024

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

    def _callback(self, in_data: bytes, frame_count: int, time_info, status) -> tuple:
        # in_data is raw bytes; reshape to (frames, channels) float32 for the downsampler
        try:
            samples = np.frombuffer(in_data, dtype=np.float32)
            if self._channels > 1:
                samples = samples.reshape(-1, self._channels)
            pcm16 = self.downsampler.convert(samples, int(self._samplerate))
            self.chunker.append(pcm16)
        except Exception:
            pass
        return (None, pyaudio.paContinue)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.chunker.reset()


class AudioPlayer:
    """Play Float32 audio at 24 kHz mono (Gemini output format)."""

    def __init__(
        self, sample_rate: int = GEMINI_OUTPUT_RATE, volume: float = 0.8
    ) -> None:
        self.sample_rate = sample_rate
        self.volume = max(0.0, min(1.0, volume))
        self._lock = threading.Lock()
        self._buffer = np.zeros(0, dtype=np.float32)
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
            self._buffer = (
                np.concatenate([self._buffer, arr]) if self._buffer.size else arr
            )

    def _callback(self, in_data: bytes, frame_count: int, time_info, status) -> tuple:
        out = np.zeros(frame_count, dtype=np.float32)
        with self._lock:
            take = min(frame_count, self._buffer.size)
            if take > 0:
                out[:take] = self._buffer[:take] * self.volume
                self._buffer = self._buffer[take:]
        return (out.tobytes(), pyaudio.paContinue)

    def set_volume(self, v: float) -> None:
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
            self._buffer = np.zeros(0, dtype=np.float32)
