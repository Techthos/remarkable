import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GnomeDesktop from 'gi://GnomeDesktop';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, QuickSettingsItem, QuickSlider, QuickToggle} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const SYNC_INTERVAL_MS = 500;
const ICON_SIZE = 64;

const labels = actor => actor instanceof St.Label ? [actor.text]
    : actor.get_children().filter(child => child.visible).flatMap(labels);

const icons = actor => actor instanceof St.Icon ? [actor]
    : actor.get_children().filter(child => child.visible).flatMap(icons);

const menuItems = menu => menu._getMenuItems().flatMap(item =>
    item instanceof PopupMenu.PopupMenuSection
        ? item.actor.visible ? menuItems(item) : []
        : item.visible && item.reactive ? [item] : []);

// Mirrors the top level of the Quick Settings menu, the clock and one open submenu
// to the tablet, and applies the actions tapped there.
export class TabletMenu {
    constructor(send) {
        this._send = send;
        this._actors = [];
        this._iconKeys = new Map();
        this._iconTheme = new St.IconTheme();
        this._iconTheme.add_resource_path('/org/gnome/shell/icons');
        this._clock = new GnomeDesktop.WallClock({time_only: true});
        this._menuSource = null;
        this._lastState = '';
        this._syncId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SYNC_INTERVAL_MS, () => {
            this._sync();
            return GLib.SOURCE_CONTINUE;
        });
        this._sync();
    }

    destroy() {
        GLib.Source.remove(this._syncId);
        this._clock.run_dispose();
        this._actors = [];
    }

    handle(action) {
        const actor = this._actors[action.id];
        // A plain button that has a menu (Power Off) only opens it, so it opens on the tablet instead.
        const opensMenu = actor?.menu && !(actor instanceof QuickToggle || actor instanceof QuickMenuToggle);
        if (action.type === 'closeMenu') {
            this._menuSource = null;
        } else if (!actor) {
            return;
        } else if (action.type === 'click' && !opensMenu) {
            // A real click on a toggle mode button flips checked before emitting clicked.
            if (actor.toggleMode)
                actor.checked = !actor.checked;
            actor.emit('clicked', 1);
        } else if (action.type === 'click' || action.type === 'openMenu') {
            this._menuSource = actor;
        } else if (action.type === 'value') {
            actor.slider.value = action.value;
        } else if (action.type === 'iconClick') {
            actor.emit('icon-clicked');
        } else if (action.type === 'menuItem') {
            this._menuSource = null;
            actor.activate(null);
        }
        this._sync();
    }

    _id(actor) {
        const index = this._actors.indexOf(actor);
        return index >= 0 ? index : this._actors.push(actor) - 1;
    }

    _icon(gicon) {
        if (!gicon)
            return '';
        const name = gicon.to_string();
        if (!this._iconKeys.has(name)) {
            this._iconKeys.set(name, String(this._iconKeys.size));
            const data = this._iconData(gicon);
            if (data)
                this._send({icons: {[this._iconKeys.get(name)]: GLib.base64_encode(data)}});
        }
        return this._iconKeys.get(name);
    }

    _iconData(gicon) {
        const filename = gicon instanceof Gio.FileIcon ? gicon.get_file().get_path()
            : this._iconTheme.lookup_by_gicon(gicon, ICON_SIZE, St.IconLookupFlags.FORCE_SVG)?.get_filename();
        if (!filename)
            return null;
        const file = GLib.file_test(filename, GLib.FileTest.EXISTS)
            ? Gio.File.new_for_path(filename) : Gio.File.new_for_uri(`resource://${filename}`);
        try {
            return file.load_contents(null)[1];
        } catch {
            return null;
        }
    }

    _items(actor) {
        if (!actor.visible)
            return [];
        if (actor instanceof QuickSlider) {
            return [{
                kind: 'slider', id: this._id(actor), value: actor.slider.value, icon: this._icon(actor.gicon),
                iconReactive: actor.iconReactive, menu: actor.menuEnabled,
            }];
        }
        if (actor instanceof QuickToggle || actor instanceof QuickMenuToggle) {
            return [{
                kind: 'toggle', id: this._id(actor), title: actor.title ?? '', subtitle: actor.subtitle ?? '',
                checked: actor.checked, icon: this._icon(actor.gicon), menu: !!actor.menuEnabled,
            }];
        }
        if (actor instanceof QuickSettingsItem && actor.child instanceof St.Icon) {
            return [{
                kind: 'button', id: this._id(actor), label: actor.accessible_name ?? '',
                icon: this._icon(actor.child.gicon), menu: !!actor.menu,
            }];
        }
        return actor.get_children().flatMap(child => this._items(child));
    }

    _menu() {
        const source = this._menuSource;
        if (!source?.menu || !source.visible)
            return null;
        return {
            title: source.title ?? source.menu._headerTitle?.text ?? source.accessible_name ?? '',
            items: menuItems(source.menu).map(item => {
                const icon = icons(item).find(i => i !== item._ornamentIcon);
                return {
                    id: this._id(item),
                    label: labels(item).join('  '),
                    icon: this._icon(icon?.gicon),
                    checked: item.state ?? [PopupMenu.Ornament.CHECK, PopupMenu.Ornament.DOT].includes(item._ornament),
                    sensitive: item.sensitive,
                };
            }),
        };
    }

    _sync() {
        const now = GLib.DateTime.new_now_local();
        const state = {
            clock: {time: this._clock.clock, date: now.format('%A %-d %B')},
            items: this._items(Main.panel.statusArea.quickSettings.menu._grid),
            menu: this._menu(),
        };
        const text = JSON.stringify(state);
        if (text !== this._lastState) {
            this._lastState = text;
            this._send({state});
        }
    }
}
