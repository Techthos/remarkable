"""reMarkable 2 as PC pen, touchpad, screen frame and Quick Settings menu.
Arguments: monitor aspect ratio, settings as JSON (keys of the extension schema).
Stdin and stdout carry JSON lines between the extension and the tablet app."""
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import libevdev
import paramiko
from libevdev import EV_ABS, EV_KEY, EV_SYN, INPUT_PROP_BUTTONPAD, INPUT_PROP_POINTER, InputAbsInfo, InputEvent

TABLET = '10.11.99.1'
SSH_KEY = Path.home() / '.ssh/remarkable'
PEN_DEVICE, TOUCH_DEVICE = '/dev/input/event1', '/dev/input/event2'
EVENT_FORMAT = '2IHHi'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
TABLET_BIN = '/home/root/bin'
BUNDLE = ('frame', 'privtmp', 'evgrab')

# Freezes xochitl, shows the frame until this connection or the USB link drops,
# then thaws xochitl. Stopping xochitl instead would make it ask for the
# passcode again on start. systemd cannot freeze units on this cgroup layout, so
# the kernel freezer is used directly and the watchdog is raised meanwhile, or
# systemd would kill the silent xochitl after a minute. The frame app runs with a
# private /tmp, because xochitl keeps the e-paper locks there. The memory mapped after
# xochitl's /dev/fb0 holds its screen image; the app draws that dump before it quits, so the
# thawed xochitl finds the screen it left and does not need to redraw. The fail handler,
# which reboots the tablet, stays masked as a safety net (in /run, gone after reboot).
# The lock serialises overlapping sessions. Output goes to /dev/null because
# cleanup runs after the SSH channel closed, and writing to it would kill the
# cleanup commands; only the app writes to the channel (fd 3).
PC_MODE = r"""
trap '' HUP
exec 3>&1 >/dev/null 2>&1 9>/run/pcmode.lock
flock 9
export NOTIFY_SOCKET=/run/systemd/notify
cgroup=/sys/fs/cgroup/unified/system.slice/xochitl.service
pid=$(systemctl show xochitl -p MainPID --value)
systemctl mask --runtime remarkable-fail.service
systemd-notify --pid=$pid WATCHDOG_USEC=86400000000
echo 1 > $cgroup/cgroup.freeze
range=$(awk '/\/dev\/fb0/ {{getline; print $1; exit}}' /proc/$pid/maps)
start=$((0x${{range%-*}})) end=$((0x${{range#*-}}))
dd if=/proc/$pid/mem of=/run/pcmode-screen bs=4096 skip=$((start / 4096)) count=$(((end - start) / 4096))
QT_QPA_PLATFORM=epaper QT_QUICK_BACKEND=epaper {bin}/privtmp {bin}/frame {aspect} {width} {height} {flipped} /run/pcmode-screen >&3 3>&-
exec 3>&-
rm /run/pcmode-screen
killall evgrab
echo 0 > $cgroup/cgroup.freeze
systemd-notify --pid=$pid WATCHDOG_USEC=60000000
systemctl unmask --runtime remarkable-fail.service
"""

# The tablet is held in landscape with the thick bezel on top, or at the bottom
# when flipped, which turns every axis around. The screen frame sits at the bottom,
# the strip above it holds the menu; pen and touch there go to the tablet app.
DISPLAY_W, DISPLAY_H = 1872, 1404
PEN_W, PEN_H = 20966, 15725
TOUCH_W, TOUCH_H, TOUCH_UNITS_PER_MM = 1871, 1403, 9

PEN_KEYS = (EV_KEY.BTN_TOOL_PEN, EV_KEY.BTN_TOOL_RUBBER, EV_KEY.BTN_TOUCH, EV_KEY.BTN_STYLUS, EV_KEY.BTN_STYLUS2)
PEN_AXES = ((EV_ABS.ABS_X, PEN_W, 100), (EV_ABS.ABS_Y, PEN_H, 100),
            (EV_ABS.ABS_PRESSURE, 4095, 0), (EV_ABS.ABS_DISTANCE, 255, 0))
FINGER_TOOLS = (EV_KEY.BTN_TOOL_FINGER, EV_KEY.BTN_TOOL_DOUBLETAP,
                EV_KEY.BTN_TOOL_TRIPLETAP, EV_KEY.BTN_TOOL_QUADTAP)

pen_near = threading.Event()


class Tablet:
    """Stdin of the tablet app: framed messages, b'I' for a GRAY8 image, b'M' for a JSON message."""

    def __init__(self, stdin):
        self.stdin, self.lock, self.pressed, self.position = stdin, threading.Lock(), False, (0, 0)

    def send(self, kind, payload):
        with self.lock:
            self.stdin.write(struct.pack('<cI', kind, len(payload)) + payload)
            self.stdin.flush()

    def point(self, pressed, x=0, y=0):
        """Press, drag and release in the menu strip, in landscape display pixels.
        A release repeats the last pressed position, so it lands on the pressed button."""
        if pressed:
            self.position = (x, y)
        if pressed or self.pressed:
            kind = 'move' if pressed and self.pressed else 'press' if pressed else 'release'
            x, y = self.position
            self.send(b'M', json.dumps({'pointer': kind, 'x': x, 'y': y}).encode())
        self.pressed = pressed


def create_pen():
    device = libevdev.Device()
    device.name = 'reMarkable pen'
    device.id = {'bustype': 0x03, 'vendor': 0x056a, 'product': 0, 'version': 54}
    for key in PEN_KEYS:
        device.enable(key)
    for code, maximum, resolution in PEN_AXES:
        device.enable(code, InputAbsInfo(minimum=0, maximum=maximum, resolution=resolution))
    return device.create_uinput_device()


def create_touchpad():
    device = libevdev.Device()
    device.name = 'reMarkable touchpad'
    device.enable(INPUT_PROP_POINTER)
    device.enable(INPUT_PROP_BUTTONPAD)
    for key in (EV_KEY.BTN_LEFT, EV_KEY.BTN_TOUCH, *FINGER_TOOLS):
        device.enable(key)
    for code, maximum in ((EV_ABS.ABS_X, TOUCH_W), (EV_ABS.ABS_Y, TOUCH_H),
                          (EV_ABS.ABS_MT_POSITION_X, TOUCH_W), (EV_ABS.ABS_MT_POSITION_Y, TOUCH_H)):
        device.enable(code, InputAbsInfo(minimum=0, maximum=maximum, resolution=TOUCH_UNITS_PER_MM))
    device.enable(EV_ABS.ABS_MT_SLOT, InputAbsInfo(minimum=0, maximum=31))
    device.enable(EV_ABS.ABS_MT_TRACKING_ID, InputAbsInfo(minimum=0, maximum=65535))
    return device.create_uinput_device()


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(TABLET, username='root', key_filename=str(SSH_KEY) if SSH_KEY.exists() else None, timeout=10)
    return client


def run(client, command):
    return client.exec_command(command)[1].read().decode()


def install_bundle(client):
    run(client, f'mkdir -p {TABLET_BIN}')
    for name in BUNDLE:
        local, remote = Path(__file__).with_name(name), f'{TABLET_BIN}/{name}'
        if run(client, f'md5sum {remote} 2>/dev/null').split(' ')[0] == hashlib.md5(local.read_bytes()).hexdigest():
            continue
        with client.open_sftp() as sftp:
            sftp.put(str(local), remote + '.new')
            sftp.chmod(remote + '.new', 0o755)
            sftp.posix_rename(remote + '.new', remote)


def read_events(client, device):
    channel = client.exec_command(f'{TABLET_BIN}/evgrab {device}')[1].channel
    pending = b''
    while data := channel.recv(65536):
        pending += data
        whole = len(pending) - len(pending) % EVENT_SIZE
        for _, _, e_type, e_code, value in struct.iter_unpack(EVENT_FORMAT, pending[:whole]):
            yield libevdev.evbit(e_type, e_code), value
        pending = pending[whole:]


def forward_pen(client, pen, tablet, top, flipped):
    """Above `top` the pen operates the menu strip: the PC sees the pen leave, and gets
    its whole state back when it returns over the frame."""
    pen_codes = {*PEN_KEYS, *(axis[0] for axis in PEN_AXES)}
    flip = {EV_ABS.ABS_X: PEN_W, EV_ABS.ABS_Y: PEN_H} if flipped else {}
    frame, state, raw_x, raw_y, in_strip = [], {}, 0, PEN_H, False
    for code, value in read_events(client, PEN_DEVICE):
        if code in (EV_KEY.BTN_TOOL_PEN, EV_KEY.BTN_TOOL_RUBBER):
            pen_near.set() if value else pen_near.clear()
        if code in flip:
            value = flip[code] - value
        if code == EV_ABS.ABS_X:
            raw_x = value
        if code == EV_ABS.ABS_Y:
            raw_y = value
            value = max(0, round((value - top) * PEN_H / (PEN_H - top)))
        if code in pen_codes:
            state[code] = value
            frame.append(InputEvent(code, value))
        if code != EV_SYN.SYN_REPORT:
            continue

        was_in_strip, in_strip = in_strip, raw_y < top and pen_near.is_set()
        if in_strip:
            if not was_in_strip:
                pen.send_events([*(InputEvent(key, 0) for key in PEN_KEYS), InputEvent(EV_SYN.SYN_REPORT, 0)])
            tablet.point(bool(state.get(EV_KEY.BTN_TOUCH)), raw_x * DISPLAY_W / PEN_W, raw_y * DISPLAY_H / PEN_H)
        else:
            if was_in_strip:
                tablet.point(False)
                frame = [InputEvent(c, v) for c, v in state.items()]
            pen.send_events([*frame, InputEvent(EV_SYN.SYN_REPORT, 0)])
        frame = []


def finger_state(fingers):
    return [InputEvent(EV_KEY.BTN_TOUCH, int(fingers > 0)),
            *(InputEvent(tool, int(min(fingers, 4) == count)) for count, tool in enumerate(FINGER_TOOLS, 1))]


def forward_touches(client, touchpad, tablet, strip_h, palm_rejection, flipped):
    """Touch panel portrait axes are swapped into landscape. Touches that begin in the menu strip
    go to the tablet app until every finger has lifted. With palm rejection, touches are dropped
    while the pen is near and until every finger has lifted, so a resting palm stays inert."""
    swapped = {EV_ABS.ABS_MT_POSITION_X: EV_ABS.ABS_MT_POSITION_Y,
               EV_ABS.ABS_MT_POSITION_Y: EV_ABS.ABS_MT_POSITION_X}
    flip = {EV_ABS.ABS_MT_POSITION_X: TOUCH_W, EV_ABS.ABS_MT_POSITION_Y: TOUCH_H} if flipped else {}
    slot, positions, sent_slots, frame, blocked = 0, {}, set(), [], False
    touching = in_strip = False
    for code, value in read_events(client, TOUCH_DEVICE):
        if code == EV_ABS.ABS_MT_SLOT:
            slot = value
        elif code == EV_ABS.ABS_MT_TRACKING_ID:
            if value == -1:
                positions.pop(slot, None)
            else:
                positions[slot] = [0, 0]
        elif code in swapped:
            code = swapped[code]
            if code in flip:
                value = flip[code] - value
            positions.setdefault(slot, [0, 0])[code == EV_ABS.ABS_MT_POSITION_Y] = value
        elif code != EV_SYN.SYN_REPORT:
            continue

        if code != EV_SYN.SYN_REPORT:
            frame.append(InputEvent(code, value))
            continue

        if positions and not touching:
            in_strip = positions[min(positions)][1] * DISPLAY_H / TOUCH_H < strip_h
        touching = bool(positions)
        if in_strip:
            x, y = positions[min(positions)] if touching else (0, 0)
            tablet.point(touching, x * DISPLAY_W / TOUCH_W, y * DISPLAY_H / TOUCH_H)
            in_strip, frame = touching, []
            continue

        blocked = palm_rejection and (pen_near.is_set() or (blocked and bool(positions)))
        if blocked:
            if sent_slots:
                releases = [e for s in sent_slots for e in (InputEvent(EV_ABS.ABS_MT_SLOT, s),
                                                           InputEvent(EV_ABS.ABS_MT_TRACKING_ID, -1))]
                touchpad.send_events([*releases, InputEvent(EV_ABS.ABS_MT_SLOT, slot),
                                      *finger_state(0), InputEvent(EV_SYN.SYN_REPORT, 0)])
                sent_slots.clear()
            frame = []
            continue

        sent_slots = set(positions)
        if positions:
            x, y = positions[min(positions)]
            frame += [InputEvent(EV_ABS.ABS_X, x), InputEvent(EV_ABS.ABS_Y, y)]
        touchpad.send_events([*frame, *finger_state(len(positions)), InputEvent(EV_SYN.SYN_REPORT, 0)])
        frame = []


def forward_screen(tablet, config, height):
    cast = subprocess.Popen([sys.executable, Path(__file__).with_name('screencast.py'), json.dumps(config), str(height)],
                            stdout=subprocess.PIPE)
    while image := cast.stdout.read(config['image-width'] * height):
        tablet.send(b'I', image)


def forward_host(tablet):
    for line in sys.stdin.buffer:
        tablet.send(b'M', line)


def forward_tablet(tablet_stdout):
    """Only JSON lines are the app's; the e-paper library prints its own lines to stdout too."""
    for line in tablet_stdout:
        if line.startswith('{'):
            sys.stdout.write(line)
            sys.stdout.flush()


def exit_when_done(target, *args):
    try:
        target(*args)
    finally:
        os._exit(1)


def main():
    aspect, config = float(sys.argv[1]), json.loads(sys.argv[2])
    flipped = config['flipped']
    client = connect()
    install_bundle(client)
    image_w = config['image-width']
    image_h = round(image_w / aspect)
    strip_h = DISPLAY_H - DISPLAY_W / aspect
    pen_top = round(strip_h / DISPLAY_H * PEN_H)

    # Keeping stdin open keeps the frame up; it closes when this process exits.
    tablet_stdin, tablet_stdout, _ = client.exec_command(PC_MODE.format(
        bin=TABLET_BIN, aspect=aspect, width=image_w, height=image_h, flipped=int(flipped)))
    tablet = Tablet(tablet_stdin)

    threading.Thread(target=forward_host, args=(tablet,), daemon=True).start()
    threading.Thread(target=exit_when_done, args=(forward_tablet, tablet_stdout), daemon=True).start()
    if config['mirror']:
        threading.Thread(target=forward_screen, args=(tablet, config, image_h), daemon=True).start()
    if config['touchpad']:
        threading.Thread(target=exit_when_done, daemon=True, args=(
            forward_touches, client, create_touchpad(), tablet, strip_h, config['palm-rejection'], flipped)).start()
    exit_when_done(forward_pen, client, create_pen(), tablet, pen_top, flipped)


if __name__ == '__main__':
    main()
