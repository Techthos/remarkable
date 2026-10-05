import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GUdev from 'gi://GUdev';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {TabletMenu} from './tabletMenu.js';

const RESTART_DELAY_SECONDS = 3;

// The reMarkable shows up as a USB network interface (cdc_ether) with this ID on any port.
const isRemarkable = device =>
    device.get_property('ID_VENDOR_ID') === '04b3' && device.get_property('ID_MODEL_ID') === '4010';

export default class RemarkablePcExtension extends Extension {
    enable() {
        this._cancellable = new Gio.Cancellable();
        this._settings = this.getSettings();
        this._settingsId = this._settings.connect('changed', (_settings, key) => {
            if (key === 'enabled') {
                this._toggle.setToggleState(this._wanted());
                this._sync();
            } else {
                this._restartBridge();
            }
        });

        this._indicator = new PanelMenu.Button(0.0, this.metadata.name, false);
        this._indicator.add_child(new St.Icon({icon_name: 'input-tablet-symbolic', style_class: 'system-status-icon'}));
        this._toggle = new PopupMenu.PopupSwitchMenuItem('PC mode', this._wanted());
        this._toggle.connect('toggled', (_item, state) => this._settings.set_boolean('enabled', state));
        this._status = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._indicator.menu.addMenuItem(this._toggle);
        this._indicator.menu.addMenuItem(this._status);
        this._indicator.menu.addAction('Settings', () => this.openPreferences());
        Main.panel.addToStatusArea(this.uuid, this._indicator);

        this._udev = new GUdev.Client({subsystems: ['net']});
        this._udevId = this._udev.connect('uevent', (_client, action, device) => {
            if (isRemarkable(device) && (action === 'add' || action === 'remove'))
                this._sync();
        });
        this._sync();
    }

    disable() {
        this._cancelRestart();
        this._udev.disconnect(this._udevId);
        this._udev = null;
        this._cancellable.cancel();
        this._stopBridge();
        this._settings.disconnect(this._settingsId);
        this._settings = null;
        this._indicator.destroy();
        this._indicator = null;
        this._toggle = null;
        this._status = null;
    }

    _wanted() {
        return this._settings.get_boolean('enabled');
    }

    _config() {
        return Object.fromEntries(this._settings.settings_schema.list_keys()
            .map(key => [key, this._settings.get_value(key).recursiveUnpack()]));
    }

    _plugged() {
        return this._udev.query_by_subsystem('net').some(isRemarkable);
    }

    _sync() {
        const plugged = this._plugged();
        this._indicator.visible = plugged;
        if (plugged && this._wanted())
            this._startBridge();
        else
            this._stopBridge();
        this._status.label.text = !plugged ? 'Not connected'
            : this._process ? 'Running' : this._restartId ? 'Reconnecting' : 'Off';
    }

    _startBridge() {
        if (this._process || this._restartId)
            return;
        const {width, height} = Main.layoutManager.primaryMonitor;
        const process = Gio.Subprocess.new(
            ['python3', `${this.path}/rmpc.py`, String(width / height), JSON.stringify(this._config())],
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE);
        this._process = process;
        this._outbox = [];
        this._writing = false;
        this._readTablet(process, new Gio.DataInputStream({base_stream: process.get_stdout_pipe()}));
        process.wait_async(this._cancellable, () => {
            if (this._process !== process)
                return;
            this._endSession();
            if (this._plugged() && this._wanted())
                this._scheduleRestart();
            else
                this._sync();
        });
    }

    // The bridge reports "ready" once the tablet app runs, then relays the actions tapped on the tablet.
    // "close" turns PC mode off, so the tablet goes back to xochitl and stays there until switched on again.
    _readTablet(process, stream) {
        stream.read_line_async(GLib.PRIORITY_DEFAULT, this._cancellable, (_stream, result) => {
            let line;
            try {
                [line] = stream.read_line_finish_utf8(result);
            } catch {
                return;
            }
            if (line === null || this._process !== process)
                return;
            const message = JSON.parse(line);
            if (message.type === 'ready')
                this._menu = new TabletMenu(menuMessage => this._write(process, menuMessage));
            else if (message.type === 'close')
                this._settings.set_boolean('enabled', false);
            else
                this._menu?.handle(message);
            this._readTablet(process, stream);
        });
    }

    // Queued and asynchronous, so a slow tablet can never block the shell.
    _write(process, message) {
        this._outbox.push(`${JSON.stringify(message)}\n`);
        if (!this._writing)
            this._flush(process);
    }

    _flush(process) {
        const text = this._outbox.join('');
        this._outbox = [];
        this._writing = text !== '';
        if (!this._writing)
            return;
        const stream = process.get_stdin_pipe();
        stream.write_all_async(new TextEncoder().encode(text), GLib.PRIORITY_DEFAULT, this._cancellable, (_stream, result) => {
            try {
                stream.write_all_finish(result);
            } catch {
                return;
            }
            if (this._process === process)
                this._flush(process);
        });
    }

    _endSession() {
        this._process = null;
        this._menu?.destroy();
        this._menu = null;
    }

    _restartBridge() {
        if (!this._process && !this._restartId)
            return;
        this._stopBridge();
        this._scheduleRestart();
    }

    _scheduleRestart() {
        this._status.label.text = 'Reconnecting';
        this._restartId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RESTART_DELAY_SECONDS, () => {
            this._restartId = null;
            this._sync();
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelRestart() {
        if (this._restartId) {
            GLib.Source.remove(this._restartId);
            this._restartId = null;
        }
    }

    _stopBridge() {
        this._cancelRestart();
        const process = this._process;
        this._endSession();
        process?.send_signal(15);
    }
}
