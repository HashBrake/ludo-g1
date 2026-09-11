#!/usr/bin/env python3
"""PicoBridge discovery relay for the Unitree G1 onboard computer (Jetson Orin).

WHY THIS EXISTS
---------------
The PicoBridge headset app finds its PC server by listening for a UDP
broadcast on port 29888 (pico-bridge ``CMD.TCPIP`` = 0x7E, payload
``"<ip>|<tcp_port>"``). Teleopit's built-in broadcaster runs on the laptop,
but the headset sits on the robot-hosted Wi-Fi AP (``g1-teleop``,
10.42.0.0/24) and the laptop sits on the robot LAN (192.168.123.0/24).
Broadcasts do not cross the Orin's NAT, and pico-bridge's
``--bridge-advertise-ip`` option assumes the advertised address is on a /24
that the broadcaster can reach - which is not the case here.

This relay runs ON THE ORIN and broadcasts the laptop's address
(``192.168.123.2|63901``) into the headset subnet (``10.42.0.255``) every
2 s. The headset then connects over TCP to the laptop through the Orin's
NAT (NetworkManager "shared" hotspot mode masquerades 10.42.0.0/24).

INSTALL (once, on the Orin)
---------------------------
    scp lab/orin/discovery_relay.py unitree@192.168.123.164:~/discovery_relay.py
    ssh unitree@192.168.123.164
    chmod +x ~/discovery_relay.py
    (crontab -l 2>/dev/null; echo '@reboot /usr/bin/python3 /home/unitree/discovery_relay.py >> /home/unitree/discovery_relay.log 2>&1') | crontab -
    nohup python3 ~/discovery_relay.py >> ~/discovery_relay.log 2>&1 &

Wire format matches pico-bridge 0.2.1 ``protocol.pack``:
    <B head=0xCF (PC->VR)> <B cmd=0x7E> <i len> <payload> <q unix_ms> <B end=0xA5>

NOTE: the copy deployed on the Orin in August 2026 is the operational one; this
file reproduces its documented behaviour so a fresh Orin can be set up from the
repo. If the two ever differ, the Orin copy wins - diff them.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

HEAD_PC_TO_VR = 0xCF
CMD_TCPIP = 0x7E
END_BYTE = 0xA5
UDP_DISCOVERY_PORT = 29888
BROADCAST_INTERVAL_S = 2.0


def pack_discovery(advertise_ip: str, tcp_port: int) -> bytes:
    payload = f"{advertise_ip}|{tcp_port}".encode("utf-8")
    ts_ms = int(time.time() * 1000)
    return (
        struct.pack("<BBi", HEAD_PC_TO_VR, CMD_TCPIP, len(payload))
        + payload
        + struct.pack("<qB", ts_ms, END_BYTE)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--advertise-ip", default="192.168.123.2",
                    help="address the headset must connect to (laptop on the robot LAN)")
    ap.add_argument("--tcp-port", type=int, default=63901,
                    help="Teleopit/pico-bridge TCP port on the laptop")
    ap.add_argument("--broadcast", default="10.42.0.255",
                    help="broadcast address of the headset Wi-Fi subnet (g1-teleop AP)")
    ap.add_argument("--interval", type=float, default=BROADCAST_INTERVAL_S)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    print(f"relay: {args.advertise_ip}|{args.tcp_port} -> {args.broadcast}:{UDP_DISCOVERY_PORT} "
          f"every {args.interval:.1f}s", flush=True)
    while True:
        try:
            sock.sendto(pack_discovery(args.advertise_ip, args.tcp_port),
                        (args.broadcast, UDP_DISCOVERY_PORT))
        except OSError as exc:  # AP not up yet after boot, etc.
            print(f"relay: send failed ({exc}); retrying", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
