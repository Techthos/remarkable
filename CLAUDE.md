# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Tooling to administrate a reMarkable 2 and to build programs that run on it. The device is reached with `ssh remarkable.usb` (root, USB network at 10.11.99.1).

## Commands

Every program lives in `cmd/<name>/` and is a static Go binary cross compiled for the tablet (`GOARCH=arm GOARM=7`, `CGO_ENABLED=0`).

```
make build/<name>    # compile into build/<name>
make deploy-<name>   # copy to /home/root/bin on the device
make run-<name>      # deploy, then run it over ssh
make backup          # rsync device documents into backup/xochitl
HOST=other make ...  # target another ssh host
```

Screen apps are Qt Quick projects in `apps/<name>/` (CMake, executable named `<name>`; the QML module URI must differ from it). They need the official SDK installed at `~/.local/opt/remarkable-sdk/3.28.0.172` (override with `SDK=`):

```
make qt-build-<name>   # cross compile into build/apps/<name>
make qt-run-<name>     # deploy, stop xochitl, run with the epaper backend, start xochitl after exit
```

The build target unsets `LD_LIBRARY_PATH` because the host exports CUDA paths and the SDK refuses to run with it set. `qt-run` blocks until the app exits, so apps need their own exit path (hello quits on tap). `qt-run` masks `remarkable-fail.service` while xochitl is stopped (see PC mode below for why).

`make run-absinfo` is not useful as is; deploy it and run `/home/root/bin/absinfo /dev/input/eventN` to print axis ranges and whether another process holds an exclusive grab on the device.

There are no tests or linters yet; `gofmt -l cmd` is the formatting check.

Notebook pages are converted on the host with `rmc` (installed via `uv tool install rmc`, brings `rmscene`), for example `rmc -t svg -o out.svg page.rm`.

## Device facts (verified on the device)

- reMarkable 2, i.MX7 Dual armv7l, 1 GB RAM, OS 3.28.0.172 (Codex Linux, Yocto scarthgap), kernel 5.4.70, xochitl on Qt 6.10.3.
- BusyBox userland: no python3, opkg, curl, git or gcc. `wget` and `rsync` exist. BusyBox `head` has no `-N` shorthand; use `sed -n 1,Np`.
- Root partition (A/B, p2 and p3) is 96% full and replaced by every OS update. Install only under `/home/root`, which is on the encrypted persistent partition. Systemd units placed in `/etc` do not survive updates.
- Input: `event0` power key, `event1` Wacom pen, `event2` touch (`pt_mt`). The display is driven through `libepaper.so`, not a usable `/dev/fb0`.
- Documents: `/home/root/.local/share/remarkable/xochitl/<uuid>.{metadata,content}` plus `<uuid>/*.rm` pages. Pages are a mix of `.lines` v5 and v6; `rmc`/`rmscene` only read v6.
- `/home/root/.config/remarkable/xochitl.conf` contains the root password; do not print it.

## Ecosystem notes

- Toltec and remarkable-hacks do not support OS 3.28. Current community stack is Vellum, xovi and AppLoad (3.28 support merged September 2026). xovi needs `rebuild_hashtable` after every OS update or xochitl crash loops.
- Official Codex SDK for the exact OS version: https://developer.remarkable.com/links

## PC mode (GNOME extension)

`extension/remarkable-pc@techthos.net/` turns the plugged in tablet into a PC pen tablet, touchpad and screen frame. Host is GNOME 50 on Wayland.

```
make extension           # build the tablet app and pack build/remarkable-pc@techthos.net.shell-extension.zip
make extension-install   # unpack it into ~/.local/share/gnome-shell/extensions (new installs need a re-login)
```

- `extension.js` watches udev (net subsystem, USB ID 04b3:4010, any port) and runs `python3 rmpc.py <monitor aspect> <settings json>` while the tablet is plugged and the panel switch is on, restarting it after 3 s if it exits. Bridge output lands in the gnome-shell journal.
- Settings live in `schemas/` (GSettings, edited through `prefs.js`, opened from the panel menu): panel switch state, mirror on/off, cursor, image width, refresh interval, change thresholds, touchpad, palm rejection, flipped orientation. The whole schema is passed to `rmpc.py` as JSON; any change other than the switch restarts a running bridge after 3 s.
- `rmpc.py` (Fedora python3 with python3-paramiko, python3-libevdev and python3-numpy) connects over ssh (`~/.ssh/remarkable` if present), uploads the bundled `frame`, `privtmp` and `evgrab` when their md5 differs, runs the session script, and forwards pen (`event1`) and touch (`event2`) via `evgrab` streams (exclusive grab, 4096 byte reads, so one SSH packet per input frame instead of per event; dropbear already disables Nagle) into two uinput devices. Pen: absolute tablet, Y offset so the frame at the bottom of the landscape tablet maps to the monitor. Touch: portrait axes swapped, exposed as a clickpad so libinput gives tap, scroll and acceleration; dropped while the pen is in proximity until all fingers lift (palm rejection).
- `screencast.py` gets the monitor through the ScreenCast portal (permission remembered via `~/.local/state/rmpc/screencast-token`) and writes GRAY8 frames of the configured width to stdout only on visible change, at most once per refresh interval. GStreamer drops frames to the refresh rate before scaling, and scales before converting; converting every full resolution frame cost about 3x the CPU. It exits when stdout closes, so it does not outlive the bridge. The tablet app hands each image to QML through an image provider.
- Quick Settings mirror: `tabletMenu.js` polls the shell's Quick Settings grid every 0.5 s (toggles, sliders, system buttons, one open submenu, clock) and sends the state as JSON when it changed; icons go once per session as base64 SVG/PNG under short keys. The extension talks to `rmpc.py` over its stdin/stdout as JSON lines; `rmpc.py` frames them to the tablet app's stdin (type byte `I` image or `M` JSON, uint32 LE length, payload) and relays the app's stdout JSON lines back (the e-paper library also prints to stdout, so only lines starting with `{` are forwarded). The app sends `{"type":"ready"}` on start, which creates the mirror.
- Pen and touch over the strip above the frame go to the menu, not the PC: `rmpc.py` sends them as `pointer` press/move/release messages in landscape pixels and the app injects them as mouse events; the app drops its own real panel input. Touch sequences are routed by where the first finger lands. Submenus are shown inside the strip because input below it belongs to the PC.
- The Close tile in the tablet strip sends `{"type":"close"}`; `extension.js` turns the PC mode switch off, so the bridge stops and xochitl returns while the cable stays plugged.
- The session freezes xochitl instead of stopping it: every xochitl start runs its cold boot lock and asks for the passcode again, even though `/home` stays unlocked. `systemctl freeze` fails ("does not support freezing", hybrid cgroup layout), so the script writes `/sys/fs/cgroup/unified/system.slice/xochitl.service/cgroup.freeze` and raises the 1 min watchdog first with `NOTIFY_SOCKET=/run/systemd/notify systemd-notify --pid=<xochitl> WATCHDOG_USEC=...`, restoring it after the thaw; without that the watchdog kills the frozen xochitl. xochitl does not redraw after the thaw, so the e-paper would keep showing the frame: the session dumps the memory mapped right after xochitl's `/dev/fb0` mapping (`/run/pcmode-screen`), which holds xochitl's screen as 1404x1872 RGBA starting at the first opaque pixel (offset 2629640 on 3.28), and the frame app draws it full screen before quitting.
- xochitl holds flocks on `/tmp/epframebuffer.lock` and `/tmp/epd.lock`, so a second e-paper app fails with "Failed to lock epframebuffer"; `cmd/privtmp` runs the frame app in a mount namespace with a private `/tmp` (BusyBox has no `unshare`). While frozen, xochitl's own input fds still queue events and replay them as taps after the thaw, so `cmd/evgrab` holds `EVIOCGRAB`; the session kills it before thawing.
- The tablet app (`apps/frame`) quits when stdin closes or `/sys/class/udc/ci_hdrc.0/state` stops being `configured`, then the session script thaws xochitl.
- xochitl's `OnFailure=remarkable-fail.service` reboots the tablet (it used to fire on segfaults during stop, and would on a watchdog kill). A drop-in cannot clear `OnFailure`, so the session runs `systemctl mask --runtime remarkable-fail.service` and unmasks afterwards; the script's output goes to /dev/null because writing to the closed ssh channel killed the unmask. A flock serialises overlapping sessions.
- Connections made right after replug can arrive from the PC's LAN address (192.168.1.194) instead of 10.11.99.2; that is this PC, not a foreign client.
- `rmpc.py` connects with `allow_agent=False`. gnome-shell exports `SSH_AUTH_SOCK=/run/user/1000/gcr/ssh`, and that gnome-keyring agent offers every key in `~/.ssh` and signs RSA with SHA-1, which dropbear 2025.88 no longer lists in `server-sig-algs`; after those failures paramiko fell through to a passphrase protected `id_ed25519` and raised `PasswordRequiredException`, so the bridge crash looped every 3 s while a plain `ssh remarkable.usb` worked fine.
- The bridge's stderr is read by `extension.js` and its last line is shown as a GNOME notification once per distinct failure, so a reconnect loop is visible instead of only landing in the journal.
- Hard won quirks from the earlier remarkable-mouse setup: declaring `ABS_MT_POSITION_X/Y` on a pen device makes libinput read pen X/Y as 0; a non blocking paramiko read loop burns 100% CPU and lags the cursor.
- `config/libwacom/remarkable.tablet` (symlinked into `~/.config/libwacom/`) marks the pen as an external tablet; whether GNOME needs it is unverified.

## Layout

- `backup/` is a local copy of device data, not source.
- `build/` holds compiled binaries.
- `extension/remarkable-pc@techthos.net/{frame,privtmp,evgrab}` are ARM builds copied in by `make extension`; edit `apps/frame` and `cmd/`, not these files.
