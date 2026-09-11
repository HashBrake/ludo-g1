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

``backend="real"`` raises :class:`NotImplementedError` until those files exist. Only
``drivers/mock`` builds a guard with ``simulated=True``; the real drivers will build theirs with the
session gate live (R1).
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
    nanoseconds) and ``config_root=``. Raises ``ValueError`` for an unknown name or backend and
    ``NotImplementedError`` for ``backend="real"``.
    """
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; known backends: {', '.join(BACKENDS)}")
    if name not in DEVICES:
        raise ValueError(f"unknown driver {name!r}; known drivers: {', '.join(DEVICES)}")
    if name in _CAMERAS and name not in config.load("cameras", root=kwargs.get("config_root")):
        raise ValueError(f"config/cameras.yaml has no camera named {name!r}")
    if backend == "real":
        raise NotImplementedError(
            f"the real {name!r} driver does not exist yet (Phase 1, CLAUDE.md section 6); use backend='mock'"
        )

    from drivers import mock  # imported lazily so that `import drivers` costs nothing

    if name in _CAMERAS:
        return mock.MockCamera(name, **kwargs)
    builders = {"arm": mock.MockArm, "hand": mock.MockHand, "glove": mock.MockGlove, "pose": mock.MockPose}
    return builders[name](**kwargs)
