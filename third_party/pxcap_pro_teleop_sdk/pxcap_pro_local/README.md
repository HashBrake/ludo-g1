# PxCapPro Local Release Package

This is a portable Linux x86_64 release package. It includes:

- A bundled CPython runtime and the required numerical Python libraries.
- The PxCapPro Python SDK, extension modules, and SDK shared libraries.
- Direct DH13 and DH15 serial-control support through the bundled bridge.
- The configuration files, URDF files, and retargeting algorithms.

The customer does not need to install Conda, Python, or PxHandSDK, and does not
need to start a UDP control server.

## 1. Copy the complete package

Copy the entire `pxcap_pro_local` directory to the target Linux machine. Run
the commands below from inside that directory:

```bash
cd /path/to/pxcap_pro_local
```

Do not copy only the outer executable. The `_internal` directory contains the
runtime, native libraries, SDK bridge, and other files required by the program.

This package targets Linux x86_64. USB kernel drivers, device firmware, and
`/dev/ttyUSB*` or `/dev/ttyACM*` device nodes must already be available on the
target machine.

## 2. Configure USB serial permissions

First identify the actual device nodes. The numbers assigned by Linux can
change after reconnecting a USB device, so do not assume that DH13 is always
`/dev/ttyUSB0`:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

The preferred persistent setup is to add the current user to the `dialout`
group:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and log in again, or start a new shell with the refreshed group
membership. Verify it with:

```bash
id -nG
```

For a quick test on a currently connected device, grant read/write access to
the exact device node. For example, if DH13 is currently `/dev/ttyUSB0`:

```bash
sudo chmod a+rw /dev/ttyUSB0
ls -l /dev/ttyUSB0
```

If the device is actually `/dev/ttyUSB1` or `/dev/ttyACM0`, replace the path in
the command accordingly. This `chmod` change is temporary: unplugging the
device or rebooting may restore the original permissions. Apply it separately
to every serial device that the program must open, including the PxCapPro
glove and DH15 when they use different nodes. The `dialout` group or a proper
udev rule is the recommended long-term solution. Do not use `chmod 777` as a
deployment fix.

## 3. Run a read-only diagnostic first

Without `--send13` or `--send15`, the program only reads the PxCapPro glove and
computes target values. It does not initialize, enable, or send motion commands
to DH13 or DH15:

```bash
./pxcap_pro_local --once --diagnose
```

If automatic glove-port selection is ambiguous, specify the glove device
explicitly. For example:

```bash
./pxcap_pro_local \
  --once \
  --diagnose \
  --pxcap-port /dev/ttyACM0
```

The diagnostic prints one frame, including raw encoder values, source joint
values, and computed target values. It does not write configuration files.

## 4. Control DH13 and DH15 directly

The release package calls the bundled DexHandSDK serial bridge directly. It
does not use the project's UDP servers:

```bash
# DH13 only
./pxcap_pro_local --send13

# DH15 only
./pxcap_pro_local --send15

# DH13 and DH15 together
./pxcap_pro_local --send13 --send15
```

When a send option is enabled, the program opens the corresponding serial
port, switches the hand to position control, and enables the motor output.
When the program exits, it disables the output and closes the connection.

For the first physical test, keep the hand clear of people and obstacles, use
only one hand at a time, and be ready to remove motor power. Confirm the
read-only output and the configured zero positions before adding a send option.

## 5. Select serial ports explicitly

The canonical DH13 and DH15 options are `--dh13-port` and `--dh15-port`:

```bash
./pxcap_pro_local --send13 --pxcap-port /dev/ttyACM0 --dh13-port /dev/ttyUSB0
./pxcap_pro_local --send15 --pxcap-port /dev/ttyACM0 --dh15-port /dev/ttyUSB1
./pxcap_pro_local --send13 --send15 \
  --pxcap-port /dev/ttyACM0 \
  --dh13-port /dev/ttyUSB0 \
  --dh15-port /dev/ttyUSB1
```

The aliases `--dh13-serial-port` and `--dh15-serial-port` are also accepted.
The PxCapPro glove input is selected with `--pxcap-port`; if it is omitted,
the program uses the port configured in the package or automatically selects a
unique matching USB device.

You can also set the default hand ports in the package configuration files:

- `config/pxcap_pro_left_dexh13.yaml`: `dexh13.serial_port`
- `config/pxcap_pro_left_dexh15.yaml`: `dexh15.serial_port`

Use an explicit option when more than one device has the same USB VID:PID. The
path must be the actual node present on the target machine; `/dev/ttyUSB0` is
only an example.

## 6. Common options

```text
--once                 Read and process one frame, then exit.
--max-frames N         Process N frames, then exit; 0 means continuous mode.
--diagnose             Print raw source values and additional diagnostics.
--frequency N          Override the input frequency; the default is 50 Hz.
--mode-dh13 joint|keyvector
                       Select the DH13 retargeting mode.
--mode-dh15 cartesian|angle
                       Select the DH15 retargeting mode.
--pxcap-port PATH      Explicitly select the PxCapPro glove serial port.
--dh13-port PATH       Explicitly select the DH13 serial port.
--dh15-port PATH       Explicitly select the DH15 serial port.
--send13               Enable direct DH13 output.
--send15               Enable direct DH15 output.
```

This release package does not accept or require `--sdk-python`; it uses the
SDK Python bridge bundled inside `_internal`.

## 7. Troubleshooting

- **No PxCapPro serial port found:** Check that the glove is connected, inspect
  the actual `/dev/ttyUSB*` or `/dev/ttyACM*` node, and verify `dialout`
  membership or apply the temporary `chmod` command above.
- **More than one matching serial port:** Pass the relevant explicit option,
  such as `--dh13-port /dev/ttyUSB0` or `--dh15-port /dev/ttyUSB1`.
- **Permission denied when opening a port:** Confirm the permission with
  `ls -l /dev/ttyUSB0`, use the actual node name, and check that no other
  process is holding the port.
- **SDK bridge exits immediately:** Confirm that the complete package was
  copied, especially `_internal/sdk_bridge` and the bundled native libraries.
- **DH15 is not detected:** Check the selected DH15 port, the 4 Mbps serial
  link, and the DH15 USB connection. Test DH15 by itself first.
- **The program starts but the hand does not move:** Confirm that the matching
  `--send13` or `--send15` option was supplied. Without a send option the
  program intentionally performs computation only.
- **A UDP server appears to be missing:** This package is intentionally a
  direct-serial release and does not require the project's UDP services.

## 8. Distribution boundary

The package contains the Paxini SDK binaries and Linux dependencies from the
build machine. It is intended for compatible Linux x86_64 systems. A different
operating system, CPU architecture, SDK version, or hand firmware may require a
new build and validation. Confirm the redistribution terms for the Paxini SDK
shared libraries before giving the package to a customer. USB permissions and
device-specific udev rules remain the responsibility of the target machine's
administrator.
