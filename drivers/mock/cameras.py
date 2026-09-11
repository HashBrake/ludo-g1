"""Mock cameras: deterministic synthetic frames on each stream's configured grid (T-006).

Same interface as the real ``drivers/cameras.py`` of Phase 1
(:class:`drivers.interfaces.CameraDriver`), no V4L2. One class serves all three streams of
``config/cameras.yaml``; the stream name selects the size and the rate.

A frame is a fixed gradient (x in the red channel, y in the green channel) plus the frame index: in
the blue channel as a flat value, and exactly, in the first :data:`COUNTER_BITS` pixels of row 0 as
bits. :func:`frame_index` reads that counter back, so a test can prove which frame it is holding --
which is what makes a dropped or repeated frame detectable in the Phase 2 recorder tests.

Frames are emitted at the stream's ``policy_resolution``, i.e. the size the policy sees (CLAUDE.md
5.3), not the capture resolution: the real driver captures larger and downscales, and everything
downstream of a driver only ever deals with the policy size.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from runtime import config
from runtime.clock import Stamped

from .ticker import Ticker

__all__ = ["COUNTER_BITS", "MockCamera", "frame_index"]

#: Bits of the frame counter written into row 0, one pixel per bit, brightest bit first.
COUNTER_BITS = 32
_BIT = np.arange(COUNTER_BITS, dtype=np.int64)


class MockCamera:
    """One synthetic camera stream, named as in ``config/cameras.yaml`` (``top``/``oblique``/``palm``)."""

    def __init__(
        self,
        name: str,
        *,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        cameras = config.load("cameras", root=config_root)
        spec = cameras.get(name)
        if not isinstance(spec, dict) or "policy_resolution" not in spec:
            known = sorted(k for k, v in cameras.items() if isinstance(v, dict) and "policy_resolution" in v)
            raise KeyError(f"config/cameras.yaml has no camera named {name!r}; known cameras: {', '.join(known)}")
        self.name = name
        width, height = (int(v) for v in spec["policy_resolution"])
        if width < COUNTER_BITS:
            raise config.ConfigError(f"config/cameras.yaml: {name}.policy_resolution is narrower than the counter")
        self.width, self.height = width, height
        self._ticker = Ticker(float(spec["fps"]), now_ns)

    def __repr__(self) -> str:
        return f"MockCamera(name={self.name!r}, {self.width}x{self.height}, fps={self._ticker.hz:g})"

    def grab(self) -> Stamped[np.ndarray]:
        """The frame the stream is showing now, stamped with its grid time."""
        index, ts_ns = self._ticker.sample()
        return Stamped(ts_ns, self.frame(index))

    def frame(self, index: int) -> np.ndarray:
        """The synthetic frame with counter ``index``: ``(h, w, 3)`` uint8, a pure function of it."""
        img = np.empty((self.height, self.width, 3), dtype=np.uint8)
        img[:, :, 0] = (np.arange(self.width) * 255 // max(self.width - 1, 1)).astype(np.uint8)[None, :]
        img[:, :, 1] = (np.arange(self.height) * 255 // max(self.height - 1, 1)).astype(np.uint8)[:, None]
        img[:, :, 2] = np.uint8(index % 256)
        bits = ((int(index) >> _BIT) & 1).astype(np.uint8) * 255
        img[0, :COUNTER_BITS, :] = bits[:, None]
        return img


def frame_index(frame: np.ndarray) -> int:
    """Read the frame counter back out of a frame produced by :class:`MockCamera`."""
    if frame.ndim != 3 or frame.shape[1] < COUNTER_BITS:
        raise ValueError(f"not a mock frame: shape {frame.shape}")
    bits = (frame[0, :COUNTER_BITS, 0] > 127).astype(np.int64)
    return int((bits << _BIT).sum())
