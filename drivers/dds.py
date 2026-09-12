"""The Unitree DDS factory binding, shared by every ``unitree_sdk2py`` driver (T-042, D-013).

Split out of :mod:`drivers.g1_arm` unchanged: ``ChannelFactoryInitialize`` binds the whole process
to one domain and one interface, so the binding is process state and not one driver's state, and
the write path of T-021 subscribes and publishes through the same factory.

Nothing here is a motion command and nothing here creates a writer: :func:`default_subscriber`
builds a ``ChannelSubscriber`` and nothing else (R1, R2). The SDK is imported inside the function,
never at module import, so importing this module costs nothing and creates no DDS participant.
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["ArmUnavailable", "dds_binding", "default_subscriber"]


class ArmUnavailable(RuntimeError):
    """There is no arm state to read: not configured, not arriving, gone silent, or closed."""


# ------------------------------------------------------------------------------------------------
# the DDS factory: one domain and one interface per process, bound lazily
# ------------------------------------------------------------------------------------------------

_DDS_LOCK = threading.Lock()
_DDS_BINDING: tuple[int, str] | None = None


def dds_binding() -> tuple[int, str] | None:
    """``(domain_id, interface)`` this process bound the DDS factory to, or None if never."""
    return _DDS_BINDING


def default_subscriber(topic: str, domain_id: int, interface: str) -> Any:
    """Build the real ``LowState_`` subscriber, initialising the DDS factory on first use.

    The SDK imports happen here, not at module import, so that ``import drivers.g1_arm`` costs
    nothing and no DDS participant is created by importing anything.
    """
    global _DDS_BINDING
    with _DDS_LOCK:
        if _DDS_BINDING is None:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize

            ChannelFactoryInitialize(domain_id, interface)
            _DDS_BINDING = (domain_id, interface)
        elif _DDS_BINDING != (domain_id, interface):
            bound_domain, bound_interface = _DDS_BINDING
            raise ArmUnavailable(
                f"this process already bound the DDS factory to domain {bound_domain} interface "
                f"{bound_interface!r}; it cannot also serve domain {domain_id} interface {interface!r}. "
                f"Use one interface per process (config/robot.yaml network.dds_interface)."
            )
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

    return ChannelSubscriber(topic, LowState_)
