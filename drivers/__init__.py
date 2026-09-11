"""Thin, tested wrappers around each vendor SDK (CLAUDE.md 5.1), and the factory that picks one.

``drivers.interfaces`` is the contract; ``drivers.mock`` implements it without hardware; the real
implementations arrive in Phase 1 as ``drivers/g1_arm.py``, ``drivers/dexh15.py``,
``drivers/pxcap.py``, ``drivers/pico.py`` and ``drivers/cameras.py``. Nothing above this package
imports a concrete driver: it calls :func:`make`.

```python
from drivers import make
arm = make("arm")                 # backend="mock" is the default
cam = make("top", backend="mock")
```

``backend="real"`` gives a :class:`drivers.cameras.V4L2Camera` for ``top`` and ``oblique`` (T-010), a
:class:`drivers.g1_arm.G1Arm` for ``arm`` (T-018), a :class:`drivers.dexh15.DexH15` for ``hand`` and
a :class:`drivers.dexh15.PalmCamera` for ``palm`` (T-019, the palm camera belongs to the hand's SDK),
all read-only and needing no session, and raises :class:`NotImplementedError` for the devices whose
drivers do not exist yet. Only ``drivers/mock`` builds a guard with ``simulated=True``; the real
drivers will build theirs with the session gate live (R1) -- neither ``G1Arm`` (until T-021) nor
``DexH15`` (until T-022) has a writer at all, so they build none.
"""

from __future__ import annotations

from typing import Any

from runtime import config

__all__ = ["BACKENDS", "DEVICES", "make"]

#: Devices :func:`make` knows. ``arm``, ``hand``, ``glove`` and ``pose`` are the four devices of
#: CLAUDE.md 3.1; the rest are the camera streams of ``config/cameras.yaml`` and are checked against
#: that file, not against this tuple.
DEVICES: tuple[str, ...] = ("arm", "hand", "glove", "pose", "top", "oblique", "palm")

#: Backends :func:`make` accepts. "real" is declared here and refused below, deliberately: the name
#: exists so that callers can be written against it before Phase 1 delivers it.
BACKENDS: tuple[str, ...] = ("mock", "real")

_CAMERAS: tuple[str, ...] = ("top", "oblique", "palm")


def make(name: str, backend: str = "mock", **kwargs: Any) -> Any:
    """Build one driver.

    ``name`` is one of :data:`DEVICES`; a camera name must also exist in ``config/cameras.yaml``.
    ``kwargs`` go to the constructor -- the mocks take ``now_ns=`` (an injectable clock returning
    nanoseconds) and ``config_root=``; :class:`drivers.cameras.V4L2Camera` takes those plus
    ``device=``; :class:`drivers.g1_arm.G1Arm` takes those plus ``subscriber_factory=`` and
    ``timeout_s=``; :class:`drivers.dexh15.DexH15` takes those plus ``control_factory=``,
    ``camera_factory=`` and ``port=``. Raises ``ValueError`` for an unknown name or backend,
    ``NotImplementedError`` for ``backend="real"`` on a device whose driver does not exist yet,
    :class:`drivers.cameras.CameraUnavailable` for a real camera that is absent,
    :class:`drivers.g1_arm.ArmUnavailable` for a real arm whose state stream is not there and
    :class:`drivers.dexh15.HandUnavailable` for a real hand that is not on the bus.
    """
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; known backends: {', '.join(BACKENDS)}")
    if name not in DEVICES:
        raise ValueError(f"unknown driver {name!r}; known drivers: {', '.join(DEVICES)}")
    if name in _CAMERAS and name not in config.load("cameras", root=kwargs.get("config_root")):
        raise ValueError(f"config/cameras.yaml has no camera named {name!r}")
    if backend == "real":
        if name == "palm":
            # The palm camera is built into the hand and is opened through the Paxini SDK (T-019).
            from drivers.dexh15 import PalmCamera

            return PalmCamera(**kwargs)
        if name in _CAMERAS:
            from drivers.cameras import V4L2Camera  # imported lazily: it pulls in cv2

            return V4L2Camera(name, **kwargs)
        if name == "arm":
            # Read-only: the real arm driver reads rt/lowstate and has no writer until T-021 (D-007).
            from drivers.g1_arm import G1Arm

            return G1Arm(**kwargs)
        if name == "hand":
            # Read-only: the real hand driver queries the Modbus bus and has no writer until T-022.
            from drivers.dexh15 import DexH15

            return DexH15(**kwargs)
        raise NotImplementedError(
            f"the real {name!r} driver does not exist yet (Phase 1, CLAUDE.md section 6); use backend='mock'"
        )

    from drivers import mock  # imported lazily so that `import drivers` costs nothing

    if name in _CAMERAS:
        return mock.MockCamera(name, **kwargs)
    builders = {"arm": mock.MockArm, "hand": mock.MockHand, "glove": mock.MockGlove, "pose": mock.MockPose}
    return builders[name](**kwargs)
