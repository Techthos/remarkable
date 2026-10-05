# remarkable

Tools for the reMarkable 2 on OS 3.28: small Go programs and Qt Quick apps that run on the tablet, plus a GNOME Shell extension that turns the plugged in tablet into a pen tablet, touchpad and e-paper second screen for your PC.

## PC mode

Plug the tablet in over USB and switch PC mode on from the GNOME panel.

- **Pen** moves the PC cursor as an absolute drawing tablet, with pressure, hover and eraser.
- **Touch** works as a clickpad with tap, scroll and acceleration, and is ignored while the pen is near the screen.
- **Screen mirror** shows the monitor in grayscale on the e-paper, only redrawn when the picture actually changes.
- **Quick Settings** from GNOME appear in a strip on the tablet and can be used with pen or finger.

xochitl is frozen during the session instead of stopped, so the tablet does not ask for the passcode again, and comes back exactly where you left it when you unplug or tap Close.

Settings (layout, mirror, cursor, image width, refresh rate, touchpad, palm rejection, ink, orientation) are in the extension preferences; screen sharing can also be switched off from the panel menu.

## Install

You need GNOME 50 on Wayland, the tablet reachable as root over ssh, and these Python packages (Fedora names):

```
sudo dnf install python3-paramiko python3-libevdev python3-numpy python3-gobject pipewire-gstreamer
```

1. Download `remarkable-pc@techthos.net.shell-extension.zip` from the [latest release](https://github.com/Techthos/remarkable/releases/latest).
2. Install it:
   ```
   gnome-extensions install --force remarkable-pc@techthos.net.shell-extension.zip
   ```
3. Log out and back in, then enable it:
   ```
   gnome-extensions enable remarkable-pc@techthos.net
   ```
4. Set up ssh access to the tablet. The root password is shown in the tablet settings at the bottom of Copyrights and licenses. A key in `~/.ssh/remarkable` avoids typing it:
   ```
   ssh-keygen -t ed25519 -f ~/.ssh/remarkable
   ssh-copy-id -i ~/.ssh/remarkable root@10.11.99.1
   ```
5. Plug in the tablet and switch PC mode on in the panel menu. The first start asks which screen to share.

The tablet programs are uploaded on every connect when they changed, nothing has to be installed on the tablet by hand.

## Releases

Pushing a tag starting with `v` runs `.github/workflows/release.yml`, which downloads the official SDK, builds the extension and attaches the zip to a GitHub release:

```
git tag v1.0.0 && git push origin v1.0.0
```

## Requirements for building

- reMarkable 2 on OS 3.28.0.172, reachable as `remarkable.usb` over ssh (root at 10.11.99.1)
- Go, for the tablet programs
- the official reMarkable SDK 3.28.0.172 in `~/.local/opt/remarkable-sdk/3.28.0.172` (download from https://developer.remarkable.com/links), for the Qt apps
- GNOME 50 on Wayland with `python3-paramiko`, `python3-libevdev`, `python3-numpy` and GStreamer, for PC mode

An ssh key at `~/.ssh/remarkable` is used if present.

## Build

```
make extension-install   # build the tablet app and install the GNOME extension
make build/<name>        # cross compile cmd/<name> for the tablet
make run-<name>          # deploy to /home/root/bin and run over ssh
make qt-run-<name>       # build, deploy and run apps/<name> on the e-paper
make backup              # copy the tablet's documents into backup/xochitl
```

Set `HOST=` to target another ssh host and `SDK=` for another SDK location. A new extension install needs a log out and back in.

## Layout

| Path | Contents |
| --- | --- |
| `cmd/` | Go programs for the tablet: input grabber, private `/tmp` runner, device info tools |
| `apps/` | Qt Quick apps for the e-paper: `frame` (PC mode screen), `hello` (minimal example) |
| `extension/` | GNOME Shell extension and its Python bridge |
| `config/` | libwacom description of the pen |

Everything installed on the tablet goes under `/home/root`, because OS updates replace the root partition.

## Disclaimer

Not affiliated with reMarkable AS. PC mode freezes system services on the tablet; use at your own risk.
