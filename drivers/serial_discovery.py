"""Finding a USB serial device by its ``vendor:product`` id (T-042, D-013).

Split unchanged out of :mod:`drivers.dexh15`, which is where it was written for the DexH15's Modbus
adapter and where :mod:`drivers.pxcap` was importing it from: the PxCap Pro hangs off the same kind
of ``/dev/ttyUSB*`` node and resolves its port the same way, so the search belongs to neither
driver. Both still expose their own ``resolve_port``, because the config key and the exception each
raises name their own device.

Everything here reads sysfs and ``/dev``; nothing opens a port, and nothing commands anything (R1).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SERIAL_GLOBS", "find_port"]

#: Serial node patterns the DexH15 Modbus adapter can appear as (docs/sdks.md 4.2, H-003).
SERIAL_GLOBS: tuple[str, ...] = ("ttyUSB*", "ttyACM*")


# ------------------------------------------------------------------------------------------------
# device discovery: the same three-step order as drivers/cameras.py, on serial nodes
# ------------------------------------------------------------------------------------------------


def _usb_id_for(node: Path) -> str | None:
    """``vendor:product`` of the USB device a ``/dev/tty*`` node hangs off, from sysfs. Read-only."""
    try:
        sysfs: Path | None = (Path("/sys/class/tty") / node.name / "device").resolve()
    except OSError:
        return None
    for _ in range(10):
        if sysfs is None or str(sysfs) == sysfs.anchor:
            return None
        try:
            vendor = (sysfs / "idVendor").read_text().strip()
            return f"{vendor.lower()}:{(sysfs / 'idProduct').read_text().strip().lower()}"
        except OSError:
            sysfs = sysfs.parent
    return None


def find_port(usb_id: str) -> str | None:
    """The lowest-numbered serial node with this USB ``vendor:product`` id, by-id link preferred."""
    wanted = usb_id.strip().lower()
    nodes: list[Path] = []
    for pattern in SERIAL_GLOBS:
        nodes.extend(Path("/dev").glob(pattern))
    by_id: dict[str, str] = {}
    links = Path("/dev/serial/by-id")
    if links.is_dir():
        for link in sorted(links.iterdir()):
            try:
                by_id.setdefault(str(link.resolve()), str(link))
            except OSError:
                continue
    for node in sorted(nodes, key=lambda p: (len(p.name), p.name)):
        if _usb_id_for(node) == wanted:
            return by_id.get(str(node), str(node))
    return None
