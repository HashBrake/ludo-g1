"""Mock drivers: the same interfaces as the real ones, no hardware (CLAUDE.md 5.1, 7).

Every test in this project runs against these by default. They are deterministic (a sample is a pure
function of its timestamp, and the clock is injectable), they run faster than real time (a test
advances a fake clock instead of sleeping), and the two that actuate -- :class:`MockArm` and
:class:`MockHand` -- build their guard with ``simulated=True`` and still call
:meth:`runtime.safety.Guard.admit` on every command, so the envelope of ``config/safety.yaml`` is
enforced here exactly as on hardware. ``simulated=True`` skips the human session gate only, which is
what CLAUDE.md R1 says about simulated robots; this package is the only place in ``drivers/`` that
sets it.

Usage and the injectable-clock pattern are documented in ``docs/drivers.md``.
"""

from .cameras import COUNTER_BITS, MockCamera, frame_index
from .dexh15 import MockHand
from .g1_arm import MockArm
from .pico import MockPose
from .pxcap import MockGlove
from .ticker import Ticker

__all__ = [
    "COUNTER_BITS",
    "MockArm",
    "MockCamera",
    "MockGlove",
    "MockHand",
    "MockPose",
    "Ticker",
    "frame_index",
]
