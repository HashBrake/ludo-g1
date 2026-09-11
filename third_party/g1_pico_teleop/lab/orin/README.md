# Orin (robot onboard computer) setup for laptop-side Teleopit

Everything here is done **once** on the G1's Jetson Orin
(`ssh unitree@192.168.123.164`, answer `1` to the ROS prompt). It makes the
headset able to reach the laptop without the laptop ever joining the robot
Wi-Fi.

```
PICO 4 headset ──Wi-Fi "g1-teleop" (10.42.0.x)──► Orin (10.42.0.1, NAT) ──Ethernet 192.168.123.x──► laptop 192.168.123.2
                                                       ▲
                                                       └── discovery_relay.py broadcasts "192.168.123.2|63901" to 10.42.0.255:29888
```

## 1. Robot-hosted Wi-Fi access point

```bash
sudo nmcli device wifi hotspot ifname wlan0 ssid g1-teleop password teleop12345
# NetworkManager names it "Hotspot-1"; make it come back after every boot:
sudo nmcli con modify Hotspot-1 connection.autoconnect yes connection.autoconnect-priority 10
```

NetworkManager's hotspot uses `ipv4.method shared`, which brings up DHCP on
10.42.0.0/24 and masquerades that subnet out of the Orin's other interfaces.
That is the NAT the headset traffic rides to the laptop; nothing else to
configure.

Check: `nmcli -t -f NAME con show --active | grep Hotspot-1`.

## 2. Discovery relay (headset auto-connect)

```bash
# from the laptop
scp lab/orin/discovery_relay.py unitree@192.168.123.164:~/discovery_relay.py
ssh unitree@192.168.123.164
chmod +x ~/discovery_relay.py
(crontab -l 2>/dev/null; echo '@reboot /usr/bin/python3 /home/unitree/discovery_relay.py >> /home/unitree/discovery_relay.log 2>&1') | crontab -
nohup python3 ~/discovery_relay.py >> ~/discovery_relay.log 2>&1 &
```

Check: `pgrep -af discovery_relay`. On the laptop you can watch the packets
arrive at the headset side only indirectly: the PicoBridge app status turns
"connected" ~5 s after `go.sh` prints READY.

## 3. What is also on the Orin (for the record)

| Item | Notes |
|---|---|
| `~/teleopit_rollback_2026-08-17.tgz` | Onboard Teleopit snapshot from before the waist unlock. |
| `miniforge3/envs/teleopit` | Onboard Teleopit env. **Never run an onboard session and a laptop session at the same time**; `go.sh` refuses to start if one is running. |
| HoloMotion Docker image `horizonrobotics/holomotion:v1.4.0-orin-jp5.1-arm64` | Parked previous stack, harmless. |

## Robot state on boot

The G1 boots into factory control. Teleopit can only command the robot after
the factory controller is released: on the Unitree remote press **L2+R2**
(debug/dev mode) with the robot hanging on the gantry. See the root README,
section "Real robot session".
