#!/usr/bin/env bash
# Software emergency stop for LAPTOP-side Teleopit sessions.
#
# THE UNITREE REMOTE IS THE PRIMARY E-STOP: L1+R1 = DAMPING in the Teleopit
# state machine. Use this script only when the remote is not in reach or the
# runtime stopped reacting to it.
#
# Mechanism (proven 2026-08-14): killing the process that streams LowCmd makes
# the G1 firmware release the motors within ~2 s. The robot WILL go limp -
# it must be on the gantry/tether or a fall is expected.
#
# SDK-side damping (LocoClient.Damp) does NOT work while a bridge holds the
# robot (three "DAMP UNCONFIRMED" events on record) - it is not attempted here.
for p in $(pgrep -f "miniconda3/envs/teleopi[t]/bin/python"); do
  kill -9 "$p" 2>/dev/null
done
# also stop an onboard session if the Orin is reachable (best effort, 3 s)
ssh -o BatchMode=yes -o ConnectTimeout=3 unitree@192.168.123.164 \
  'pkill -9 -f "miniforge3/envs/teleopi[t]/bin/python"' 2>/dev/null
echo "[teleopit_estop] command stream killed -> firmware damp expected in ~2 s"
echo "[teleopit_estop] if the robot is still stiff: Unitree remote L1+R1"
