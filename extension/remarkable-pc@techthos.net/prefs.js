import Adw from 'gi://Adw';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk?version=4.0';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const SCREENCAST_TOKEN = GLib.build_filenamev([GLib.get_user_state_dir(), 'rmpc', 'screencast-token']);

const LAYOUTS = {
    landscape: 'Landscape',
    below: 'Controls below',
    minimal: 'Minimal',
    portrait: 'Portrait',
    sidebar: 'Sidebar',
    remote: 'Remote',
};

export default class RemarkablePcPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window._settings = settings;

        const bind = (key, row, property) => settings.bind(key, row, property, Gio.SettingsBindFlags.DEFAULT);
        const switchRow = (key, title, subtitle = '') => {
            const row = new Adw.SwitchRow({title, subtitle});
            bind(key, row, 'active');
            return row;
        };
        const spinRow = (key, title, subtitle, lower, upper, step, digits = 0) => {
            const row = new Adw.SpinRow({
                title, subtitle, digits,
                adjustment: new Gtk.Adjustment({lower, upper, step_increment: step}),
            });
            bind(key, row, 'value');
            return row;
        };

        const page = new Adw.PreferencesPage();
        window.add(page);

        const layout = new Adw.PreferencesGroup({title: 'Layout'});
        page.add(layout);
        const layoutIds = Object.keys(LAYOUTS);
        const layoutRow = new Adw.ComboRow({
            title: 'Layout',
            subtitle: 'How the pad and the menu share the tablet. Remote has no pad.',
            model: Gtk.StringList.new(Object.values(LAYOUTS)),
            selected: layoutIds.indexOf(settings.get_string('layout')),
        });
        layoutRow.connect('notify::selected', () => settings.set_string('layout', layoutIds[layoutRow.selected]));
        layout.add(layoutRow);
        layout.add(switchRow('flipped', 'Flip orientation', 'Thick bezel at the bottom, or on the right in portrait'));

        const mirror = new Adw.PreferencesGroup({
            title: 'Screen mirror',
            description: 'Changes restart the running session.',
        });
        page.add(mirror);
        mirror.add(switchRow('mirror', 'Show the screen on the tablet'));
        mirror.add(switchRow('show-cursor', 'Show the mouse cursor'));
        mirror.add(spinRow('image-width', 'Resolution', 'Image width in pixels, the tablet frame is 1860 wide', 156, 1860, 52));
        mirror.add(spinRow('frame-interval', 'Refresh interval', 'Minimum seconds between two images', 0.1, 10, 0.1, 1));
        mirror.add(spinRow('change-level', 'Pixel change threshold', 'Gray level difference that counts a pixel as changed', 1, 255, 1));
        mirror.add(spinRow('min-changed-pixels', 'Changed pixels', 'How many pixels must change before a new image is sent', 0, 100000, 10));

        const resetButton = new Gtk.Button({label: 'Reset', valign: Gtk.Align.CENTER});
        resetButton.connect('clicked', () => GLib.unlink(SCREENCAST_TOKEN));
        const resetRow = new Adw.ActionRow({
            title: 'Screen permission',
            subtitle: 'Forget the shared monitor and ask again on the next connect',
        });
        resetRow.add_suffix(resetButton);
        mirror.add(resetRow);

        const input = new Adw.PreferencesGroup({title: 'Input'});
        page.add(input);
        input.add(switchRow('touchpad', 'Touchpad'));
        input.add(switchRow('palm-rejection', 'Palm rejection', 'Ignore touches while the pen is near'));
        input.add(switchRow('ink', 'Ink', 'Draw the pen strokes on the tablet'));
        input.add(spinRow('ink-delay', 'Ink delay', 'Seconds after the pen moves out of range until the ink clears', 0.5, 30, 0.5, 1));
    }
}
