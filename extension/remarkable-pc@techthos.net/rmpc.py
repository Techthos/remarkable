"""reMarkable 2 as PC pen, touchpad, screen frame and Quick Settings menu.
Arguments: monitor aspect ratio, settings as JSON (keys of the extension schema).
Stdin and stdout carry JSON lines between the extension and the tablet app."""
import hashlib
import json
import os
import shlex
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
PEN_COPY = '/run/pcmode-pen'
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
QT_QPA_PLATFORM=epaper QT_QUICK_BACKEND=epaper {bin}/privtmp {bin}/frame {setup} {width} {height} /run/pcmode-screen {pen} >&3 3>&-
exec 3>&-
rm -f /run/pcmode-screen {pen}
killall evgrab
echo 0 > $cgroup/cgroup.freeze
systemd-notify --pid=$pid WATCHDOG_USEC=60000000
systemctl unmask --runtime remarkable-fail.service
"""

# The canvas is the screen as the tablet is held: landscape with the thick bezel on top,
# portrait with it on the left, both turned around when flipped. The pad is the part of it
# that belongs to the PC and shows its screen; pen and touch elsewhere go to the menu.
DISPLAY_W, DISPLAY_H = 1872, 1404
PEN_W, PEN_H = 20966, 15725
TOUCH_W, TOUCH_H, TOUCH_UNITS_PER_MM = 1871, 1403, 9

PEN_KEYS = (EV_KEY.BTN_TOOL_PEN, EV_KEY.BTN_TOOL_RUBBER, EV_KEY.BTN_TOUCH, EV_KEY.BTN_STYLUS, EV_KEY.BTN_STYLUS2)
PEN_AXES = ((EV_ABS.ABS_X, PEN_W, 100), (EV_ABS.ABS_Y, PEN_H, 100),
            (EV_ABS.ABS_PRESSURE, 4095, 0), (EV_ABS.ABS_DISTANCE, 255, 0))
FINGER_TOOLS = (EV_KEY.BTN_TOOL_FINGER, EV_KEY.BTN_TOOL_DOUBLETAP,
                EV_KEY.BTN_TOOL_TRIPLETAP, EV_KEY.BTN_TOOL_QUADTAP)

# Portrait canvas, pad width as a share of the canvas width, pad docked at the top or bottom right.
LAYOUTS = {
    'landscape': (False, 1, 'bottom'),
    'below': (False, 1, 'top'),
    'minimal': (False, 1, 'bottom'),
    'portrait': (True, 1, 'bottom'),
    'sidebar': (False, 2 / 3, 'bottom'),
    'remote': (False, 0, 'bottom'),
}

pen_near = threading.Event()


class Canvas:
    def __init__(self, layout, flipped, aspect):
        self.portrait, share, dock = LAYOUTS[layout]
        self.flipped = flipped
        self.w, self.h = (DISPLAY_H, DISPLAY_W) if self.portrait else (DISPLAY_W, DISPLAY_H)
        pad_w = round(self.w * share)
        pad_h = round(pad_w / aspect)
        self.pad = (self.w - pad_w, 0 if dock == 'top' else self.h - pad_h, pad_w, pad_h)

    def point(self, x, y):
        """Landscape display pixels with the thick bezel on top to canvas pixels."""
        if self.portrait:
            x, y = y, DISPLAY_W - x
        return (self.w - x, self.h - y) if self.flipped else (x, y)

    def on_pad(self, x, y):
        pad_x, pad_y, pad_w, pad_h = self.pad
        return pad_x <= x < pad_x + pad_w and pad_y <= y < pad_y + pad_h


class Tablet:
    """Stdin of the tablet app: framed messages, b'I' for a GRAY8 image, b'M' for a JSON message."""

    def __init__(self, stdin):
        self.stdin, self.lock, self.pressed = stdin, threading.Lock(), None

    def send(self, kind, payload):
        with self.lock:
            self.stdin.write(struct.pack('<cI', kind, len(payload)) + payload)
            self.stdin.flush()

    def point(self, position):
        """Press, drag and release in the menu at a canvas position, None releases.
        A release repeats the last pressed position, so it lands on the pressed button."""
        if position or self.pressed:
            kind = 'move' if position and self.pressed else 'press' if position else 'release'
            x, y = position or self.pressed
            self.send(b'M', json.dumps({'pointer': kind, 'x': x, 'y': y}).encode())
        self.pressed = position


def create_pen():
    device = libevdev.Device()
    device.name = 'reMarkable pen'
    device.id = {'bustype': 0x03, 'vendor': 0x056a, 'product': 0, 'version': 54}
    for key in PEN_KEYS:
        device.enable(key)
    for code, maximum, resolution in PEN_AXES:
        device.enable(code, InputAbsInfo(minimum=0, maximum=maximum, resolution=resolution))
    return device.create_uinput_device()


def create_touchpad(width, height):
    device = libevdev.Device()
    device.name = 'reMarkable touchpad'
    device.enable(INPUT_PROP_POINTER)
    device.enable(INPUT_PROP_BUTTONPAD)
    for key in (EV_KEY.BTN_LEFT, EV_KEY.BTN_TOUCH, *FINGER_TOOLS):
        device.enable(key)
    for code, maximum in ((EV_ABS.ABS_X, width), (EV_ABS.ABS_Y, height),
                          (EV_ABS.ABS_MT_POSITION_X, width), (EV_ABS.ABS_MT_POSITION_Y, height)):
        device.enable(code, InputAbsInfo(minimum=0, maximum=maximum, resolution=TOUCH_UNITS_PER_MM))
    device.enable(EV_ABS.ABS_MT_SLOT, InputAbsInfo(minimum=0, maximum=31))
    device.enable(EV_ABS.ABS_MT_TRACKING_ID, InputAbsInfo(minimum=0, maximum=65535))
    return device.create_uinput_device()


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # The ssh agent is skipped: the gnome-keyring one in the shell's environment offers every key in
    # ~/.ssh and signs RSA with SHA-1, which dropbear rejects, and those failures make paramiko fall
    # through to a passphrase protected key and give up instead of trying the key that would work.
    try:
        client.connect(TABLET, username='root', allow_agent=False, timeout=10,
                       key_filename=str(SSH_KEY) if SSH_KEY.exists() else None)
    except (paramiko.SSHException, OSError) as error:
        sys.exit(f'ssh to {TABLET} failed: {error}. Put a key the tablet accepts in {SSH_KEY}.')
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


def read_events(client, *evgrab_args):
    channel = client.exec_command(' '.join((f'{TABLET_BIN}/evgrab', *evgrab_args)))[1].channel
    pending = b''
    while data := channel.recv(65536):
        pending += data
        whole = len(pending) - len(pending) % EVENT_SIZE
        for _, _, e_type, e_code, value in struct.iter_unpack(EVENT_FORMAT, pending[:whole]):
            yield libevdev.evbit(e_type, e_code), value
        pending = pending[whole:]


def forward_pen(client, pen, tablet, canvas, ink):
    """On the pad the pen drives the PC, elsewhere the menu: the PC sees the pen leave, and gets
    its whole state back when it returns to the pad. With ink, evgrab also copies the pen events to the
    tablet app, which draws them on the pad without the round trip through the PC."""
    pad_x, pad_y, pad_w, pad_h = canvas.pad
    pen_codes = {*PEN_KEYS, *(axis[0] for axis in PEN_AXES)}
    frame, state, raw, on_pad = [], {}, [0, 0], True
    for code, value in read_events(client, PEN_DEVICE, *([PEN_COPY] if ink else [])):
        if code in (EV_KEY.BTN_TOOL_PEN, EV_KEY.BTN_TOOL_RUBBER):
            pen_near.set() if value else pen_near.clear()
        if code == EV_ABS.ABS_X:
            raw[0] = value * DISPLAY_W / PEN_W
        elif code == EV_ABS.ABS_Y:
            raw[1] = value * DISPLAY_H / PEN_H
        elif code in pen_codes:
            state[code] = value
            frame.append(InputEvent(code, value))
        if code != EV_SYN.SYN_REPORT:
            continue

        x, y = canvas.point(*raw)
        was_on_pad, on_pad = on_pad, canvas.on_pad(x, y)
        if on_pad:
            if not was_on_pad:
                tablet.point(None)
                frame = [InputEvent(c, v) for c, v in state.items()]
            pen.send_events([*frame, InputEvent(EV_ABS.ABS_X, round((x - pad_x) * PEN_W / pad_w)),
                             InputEvent(EV_ABS.ABS_Y, round((y - pad_y) * PEN_H / pad_h)),
                             InputEvent(EV_SYN.SYN_REPORT, 0)])
        else:
            if was_on_pad:
                pen.send_events([*(InputEvent(key, 0) for key in PEN_KEYS), InputEvent(EV_SYN.SYN_REPORT, 0)])
            tablet.point((round(x), round(y)) if state.get(EV_KEY.BTN_TOUCH) else None)
        frame = []


def finger_state(fingers):
    return [InputEvent(EV_KEY.BTN_TOUCH, int(fingers > 0)),
            *(InputEvent(tool, int(min(fingers, 4) == count)) for count, tool in enumerate(FINGER_TOOLS, 1))]


def forward_touches(client, touchpad, tablet, canvas, palm_rejection):
    """The touch panel's portrait axes run along landscape Y and X. Touches that begin outside the pad
    go to the menu until every finger has lifted. With palm rejection, touches are dropped while the pen
    is near and until every finger has lifted, so a resting palm stays inert."""
    slot, touches, sent, touching, in_menu, blocked = 0, {}, set(), False, False, False
    for code, value in read_events(client, TOUCH_DEVICE):
        if code == EV_ABS.ABS_MT_SLOT:
            slot = value
        elif code == EV_ABS.ABS_MT_TRACKING_ID:
            if value == -1:
                touches.pop(slot, None)
            else:
                touches[slot] = [value, 0, 0]
        elif code == EV_ABS.ABS_MT_POSITION_Y and slot in touches:
            touches[slot][1] = value * DISPLAY_W / TOUCH_W
        elif code == EV_ABS.ABS_MT_POSITION_X and slot in touches:
            touches[slot][2] = value * DISPLAY_H / TOUCH_H
        if code != EV_SYN.SYN_REPORT:
            continue

        fingers = {s: (tracking_id, *canvas.point(x, y)) for s, (tracking_id, x, y) in touches.items()}
        first = fingers[min(fingers)][1:] if fingers else None
        if first and not touching:
            in_menu = not canvas.on_pad(*first)
        touching = bool(fingers)
        if in_menu:
            tablet.point((round(first[0]), round(first[1])) if first else None)
            in_menu = touching
            continue

        blocked = palm_rejection and (pen_near.is_set() or (blocked and touching))
        if blocked:
            fingers = {}
        events = [e for s in sent - fingers.keys() for e in (InputEvent(EV_ABS.ABS_MT_SLOT, s),
                                                             InputEvent(EV_ABS.ABS_MT_TRACKING_ID, -1))]
        for s, (tracking_id, x, y) in fingers.items():
            events += [InputEvent(EV_ABS.ABS_MT_SLOT, s), InputEvent(EV_ABS.ABS_MT_TRACKING_ID, tracking_id),
                       InputEvent(EV_ABS.ABS_MT_POSITION_X, round(x)), InputEvent(EV_ABS.ABS_MT_POSITION_Y, round(y))]
        if fingers:
            events += [InputEvent(EV_ABS.ABS_X, round(first[0])), InputEvent(EV_ABS.ABS_Y, round(first[1]))]
        touchpad.send_events([*events, *finger_state(len(fingers)), InputEvent(EV_SYN.SYN_REPORT, 0)])
        sent = set(fingers)


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
    canvas = Canvas(config['layout'], config['flipped'], aspect)
    client = connect()
    install_bundle(client)
    image_w = config['image-width']
    image_h = round(image_w / aspect)
    setup = {'layout': config['layout'], 'portrait': canvas.portrait, 'flipped': canvas.flipped,
             'pad': canvas.pad, 'inkDelay': config['ink-delay']}

    # Keeping stdin open keeps the frame up; it closes when this process exits.
    tablet_stdin, tablet_stdout, _ = client.exec_command(PC_MODE.format(
        bin=TABLET_BIN, setup=shlex.quote(json.dumps(setup)), width=image_w, height=image_h, pen=PEN_COPY))
    tablet = Tablet(tablet_stdin)

    threading.Thread(target=forward_host, args=(tablet,), daemon=True).start()
    threading.Thread(target=exit_when_done, args=(forward_tablet, tablet_stdout), daemon=True).start()
    if config['mirror'] and canvas.pad[2]:
        threading.Thread(target=forward_screen, args=(tablet, config, image_h), daemon=True).start()
    if config['touchpad']:
        threading.Thread(target=exit_when_done, daemon=True, args=(
            forward_touches, client, create_touchpad(canvas.w, canvas.h), tablet, canvas,
            config['palm-rejection'])).start()
    exit_when_done(forward_pen, client, create_pen(), tablet, canvas, config['ink'])


if __name__ == '__main__':
    main()
