"""PCM16 downsampler + chunker (port of PCM16AudioProcessor.swift)."""
from __future__ import annotations

import threading
from math import gcd
from typing import Callable, Optional

import numpy as np
from scipy.signal import resample_poly


class PCM16Downsampler:
    """Convert multi-channel Float32 PCM at arbitrary sample rate to mono 16kHz PCM16 bytes."""

    def __init__(self, target_rate: int = 16000) -> None:
        self.target_rate = target_rate

    def convert(self, samples: np.ndarray, src_rate: int) -> bytes:
        """samples: (frames, channels) or (frames,) float32."""
        if samples.size == 0 or src_rate <= 0:
            return b""

        if samples.ndim == 2:
            mono = samples.mean(axis=1)
        else:
            mono = samples

        if src_rate == self.target_rate:
            resampled = mono
        else:
            # Use scipy's polyphase resampler for proper anti-aliased
            # downsampling (linear interpolation introduces aliasing noise
            # that degrades speech recognition accuracy).
            g = gcd(src_rate, self.target_rate)
            up = self.target_rate // g
            down = src_rate // g
            resampled = resample_poly(mono, up, down)

        clipped = np.clip(resampled, -1.0, 1.0)
        int16 = (clipped * 32767.0).astype("<i2")  # little-endian int16
        return int16.tobytes()


class PCM16Chunker:
    """Accumulate PCM16 bytes and emit fixed-size chunks (thread-safe)."""

    def __init__(
        self, chunk_size: int = 3200, on_chunk: Optional[Callable[[bytes], None]] = None
    ) -> None:
        self.chunk_size = chunk_size
        self.on_chunk = on_chunk
        self._lock = threading.Lock()
        self._pending = bytearray()

    def append(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._pending.extend(data)
            while len(self._pending) >= self.chunk_size:
                chunk = bytes(self._pending[: self.chunk_size])
                del self._pending[: self.chunk_size]
                cb = self.on_chunk
                if cb is not None:
                    cb(chunk)

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
