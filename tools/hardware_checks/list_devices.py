#!/usr/bin/env python3
"""Read-only inventory of the devices LUDO-G1 cares about.

Lists V4L2 video nodes (Brio, Orbbec UVC, DexH15 palm camera), USB serial nodes
(DexH15 Modbus on /dev/ttyUSB*, PxCap Pro glove on /dev/ttyACM*), every USB
vendor:product id, and the network interfaces, flagging the ones holding a
192.168.123.x address (the G1 DDS LAN).

This script only reads: it opens V4L2 nodes with O_RDONLY|O_NONBLOCK for a
VIDIOC_QUERYCAP ioctl, never starts streaming, never opens a serial port and
never sends a motion command. It is safe to run with nothing plugged in and
exits 0 in that case (R1: no motion command is possible from here).

Usage:
    .venv/bin/python tools/hardware_checks/list_devices.py
    .venv/bin/python tools/hardware_checks/list_devices.py --json
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Any

# linux/videodev2.h: VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability)
VIDIOC_QUERYCAP = 0x80685600
# linux/sockios.h
SIOCGIFADDR = 0x8915

G1_LAN_PREFIX = "192.168.123."

V4L2_CAP_FLAGS: tuple[tuple[int, str], ...] = (
    (0x00000001, "VIDEO_CAPTURE"),
    (0x00000002, "VIDEO_OUTPUT"),
    (0x00000004, "VIDEO_OVERLAY"),
    (0x00001000, "VIDEO_CAPTURE_MPLANE"),
    (0x00200000, "META_CAPTURE"),
    (0x01000000, "READWRITE"),
    (0x04000000, "STREAMING"),
    (0x80000000, "DEVICE_CAPS"),
)


class V4l2Capability(ctypes.Structure):
    """struct v4l2_capability (linux/videodev2.h), 104 bytes."""

    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _decode_caps(mask: int) -> list[str]:
    return [name for bit, name in V4L2_CAP_FLAGS if mask & bit]


def _usb_ids_for_sysfs_device(start: Path) -> dict[str, str]:
    """Walk up a sysfs device path until a USB node with idVendor is found."""
    node: Path | None = start
    for _ in range(10):
        if node is None or str(node) == node.anchor:
            break
        vendor = _read_text(node / "idVendor")
        product = _read_text(node / "idProduct")
        if vendor and product:
            return {
                "usb_vid": vendor,
                "usb_pid": product,
                "usb_manufacturer": _read_text(node / "manufacturer") or "",
                "usb_product": _read_text(node / "product") or "",
                "usb_serial": _read_text(node / "serial") or "",
            }
        node = node.parent
    return {}


def list_v4l2_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    nodes = sorted(Path("/dev").glob("video*"), key=lambda p: (len(p.name), p.name))
    for node in nodes:
        entry: dict[str, Any] = {"path": str(node)}
        sysfs = Path("/sys/class/video4linux") / node.name
        entry["sysfs_name"] = _read_text(sysfs / "name") or ""
        entry["index"] = _read_text(sysfs / "index") or ""
        try:
            entry.update(_usb_ids_for_sysfs_device((sysfs / "device").resolve()))
        except OSError:
            pass

        cap = V4l2Capability()
        try:
            fd = os.open(str(node), os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            entry["error"] = f"open failed: {exc.strerror}"
            devices.append(entry)
            continue
        try:
            fcntl.ioctl(fd, VIDIOC_QUERYCAP, cap)
            caps = cap.device_caps if cap.capabilities & 0x80000000 else cap.capabilities
            entry["driver"] = cap.driver.decode(errors="replace")
            entry["card"] = cap.card.decode(errors="replace")
            entry["bus_info"] = cap.bus_info.decode(errors="replace")
            entry["capabilities"] = _decode_caps(caps)
            entry["is_capture"] = bool(caps & 0x00000001)
        except OSError as exc:
            entry["error"] = f"VIDIOC_QUERYCAP failed: {exc.strerror}"
        finally:
            os.close(fd)
        devices.append(entry)
    return devices


def list_serial_devices() -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    patterns = ("ttyUSB*", "ttyACM*")
    nodes: list[Path] = []
    for pattern in patterns:
        nodes.extend(Path("/dev").glob(pattern))
    for node in sorted(nodes, key=lambda p: (len(p.name), p.name)):
        entry: dict[str, Any] = {"path": str(node)}
        sysfs = Path("/sys/class/tty") / node.name / "device"
        try:
            entry.update(_usb_ids_for_sysfs_device(sysfs.resolve()))
        except OSError:
            pass
        try:
            st = node.stat()
            entry["mode"] = oct(st.st_mode & 0o777)
            entry["writable_by_us"] = os.access(str(node), os.R_OK | os.W_OK)
        except OSError as exc:
            entry["error"] = str(exc)
        by_id = []
        by_id_dir = Path("/dev/serial/by-id")
        if by_id_dir.is_dir():
            for link in sorted(by_id_dir.iterdir()):
                try:
                    if link.resolve() == node.resolve():
                        by_id.append(str(link))
                except OSError:
                    continue
        entry["by_id"] = by_id
        ports.append(entry)
    return ports


def list_usb_devices() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = Path("/sys/bus/usb/devices")
    if not root.is_dir():
        return out
    for node in sorted(root.iterdir()):
        vendor = _read_text(node / "idVendor")
        product = _read_text(node / "idProduct")
        if not vendor or not product:
            continue
        out.append(
            {
                "sysfs": node.name,
                "id": f"{vendor}:{product}",
                "manufacturer": _read_text(node / "manufacturer") or "",
                "product": _read_text(node / "product") or "",
                "serial": _read_text(node / "serial") or "",
            }
        )
    return out


def _ipv4_of(ifname: str) -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            packed = fcntl.ioctl(
                sock.fileno(),
                SIOCGIFADDR,
                struct.pack("256s", ifname.encode("utf-8")[:15]),
            )
        except OSError:
            return None
    return socket.inet_ntoa(packed[20:24])


def list_network_interfaces() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = Path("/sys/class/net")
    if not root.is_dir():
        return out
    for node in sorted(root.iterdir()):
        name = node.name
        ipv4 = _ipv4_of(name)
        out.append(
            {
                "name": name,
                "ipv4": ipv4 or "",
                "operstate": _read_text(node / "operstate") or "",
                "mac": _read_text(node / "address") or "",
                "is_g1_lan": bool(ipv4 and ipv4.startswith(G1_LAN_PREFIX)),
            }
        )
    return out


def collect() -> dict[str, Any]:
    return {
        "v4l2": list_v4l2_devices(),
        "serial": list_serial_devices(),
        "usb": list_usb_devices(),
        "net": list_network_interfaces(),
    }


def _print_human(report: dict[str, Any]) -> None:
    print("== V4L2 video nodes ==")
    if not report["v4l2"]:
        print("  (none)")
    for dev in report["v4l2"]:
        ids = f" usb={dev['usb_vid']}:{dev['usb_pid']}" if dev.get("usb_vid") else ""
        card = dev.get("card") or dev.get("sysfs_name") or "?"
        extra = dev.get("error") or ",".join(dev.get("capabilities", []))
        print(f"  {dev['path']:<16} {card!r}{ids}  [{extra}]")

    print("== USB serial nodes (/dev/ttyUSB*, /dev/ttyACM*) ==")
    if not report["serial"]:
        print("  (none)")
    for dev in report["serial"]:
        ids = f" usb={dev['usb_vid']}:{dev['usb_pid']}" if dev.get("usb_vid") else ""
        prod = dev.get("usb_product", "")
        rw = "rw" if dev.get("writable_by_us") else "no-access"
        print(f"  {dev['path']:<16} {prod!r}{ids} mode={dev.get('mode', '?')} {rw}")

    print("== USB devices ==")
    if not report["usb"]:
        print("  (none)")
    for dev in report["usb"]:
        print(f"  {dev['id']}  {dev['manufacturer']} {dev['product']}".rstrip())

    print("== Network interfaces ==")
    for dev in report["net"]:
        flag = "  <-- G1 LAN" if dev["is_g1_lan"] else ""
        print(f"  {dev['name']:<18} {dev['ipv4'] or '-':<16} {dev['operstate']}{flag}")
    if not any(dev["is_g1_lan"] for dev in report["net"]):
        print(f"  (no interface holds a {G1_LAN_PREFIX}x address)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = collect()
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
