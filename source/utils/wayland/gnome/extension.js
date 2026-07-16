import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const DBUS_XML = `
<node>
  <interface name="org.cgrinder.Mutter">
    <method name="Ping">
      <arg type="s" direction="out" name="result"/>
    </method>
    <method name="Snapshot">
      <arg type="s" direction="out" name="json"/>
    </method>
  </interface>
</node>`;

function rectToObject(rect) {
    return {
        x: Math.round(rect.x || 0),
        y: Math.round(rect.y || 0),
        width: Math.round(rect.width || 0),
        height: Math.round(rect.height || 0),
    };
}

function safeString(fn) {
    try {
        return String(fn() || '');
    } catch (e) {
        return '';
    }
}

function shouldInclude(win) {
    if (!win)
        return false;
    try {
        if (win.is_skip_taskbar && win.is_skip_taskbar())
            return false;
        if (win.minimized)
            return false;
        const rect = win.get_client_content_rect();
        return rect.width > 0 && rect.height > 0;
    } catch (e) {
        return false;
    }
}

function windowObject(win) {
    const rect = rectToObject(win.get_client_content_rect());
    let frame = rect;
    try {
        frame = rectToObject(win.get_frame_rect());
    } catch (e) {
    }
    return {
        title: safeString(() => win.get_title()),
        left: rect.x,
        top: rect.y,
        width: rect.width,
        height: rect.height,
        frame_left: frame.x,
        frame_top: frame.y,
        frame_width: frame.width,
        frame_height: frame.height,
        app_id: safeString(() => win.get_gtk_application_id()),
        wm_class: safeString(() => win.get_wm_class() || win.get_wm_class_instance()),
    };
}

export default class CGrinderMutterExtension extends Extension {
    enable() {
        this._revision = 1;
        this._signals = [];
        this._windowSignals = new Map();
        this._impl = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
        this._impl.export(Gio.DBus.session, '/org/cgrinder/Mutter');
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            'org.cgrinder.Mutter',
            Gio.BusNameOwnerFlags.REPLACE,
            null,
            null
        );

        this._connect(global.display, 'notify::focus-window', () => this._bumpRevision());
        this._connect(global.display, 'window-created', (_display, win) => {
            this._watchWindow(win);
            this._bumpRevision();
        });
        this._connect(global.display, 'window-entered-monitor', () => this._bumpRevision());
        this._connect(global.display, 'window-left-monitor', () => this._bumpRevision());

        if (global.workspace_manager) {
            this._connect(global.workspace_manager, 'workspace-switched', () => this._bumpRevision());
            this._connect(global.workspace_manager, 'notify::n-workspaces', () => this._bumpRevision());
        }
    }

    disable() {
        if (this._impl) {
            this._impl.unexport();
            this._impl = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }

        if (this._signals) {
            for (const [obj, id] of this._signals) {
                try {
                    obj.disconnect(id);
                } catch (e) {
                }
            }
            this._signals = [];
        }

        if (this._windowSignals) {
            for (const [win, ids] of this._windowSignals) {
                for (const id of ids) {
                    try {
                        win.disconnect(id);
                    } catch (e) {
                    }
                }
            }
            this._windowSignals.clear();
        }
    }

    _bumpRevision() {
        this._revision = (this._revision || 0) + 1;
    }

    _connect(obj, signal, callback) {
        try {
            const id = obj.connect(signal, callback);
            this._signals.push([obj, id]);
        } catch (e) {
        }
    }

    _watchWindow(win) {
        if (!win || this._windowSignals.has(win))
            return;

        const ids = [];
        const bump = () => this._bumpRevision();
        for (const signal of [
            'position-changed',
            'size-changed',
            'workspace-changed',
            'unmanaged',
            'notify::title',
            'notify::minimized',
            'notify::gtk-application-id',
            'notify::wm-class',
        ]) {
            try {
                ids.push(win.connect(signal, bump));
            } catch (e) {
            }
        }
        this._windowSignals.set(win, ids);
    }

    _pruneWindowSignals(liveWindows) {
        for (const [win, ids] of this._windowSignals) {
            if (liveWindows.has(win))
                continue;
            for (const id of ids) {
                try {
                    win.disconnect(id);
                } catch (e) {
                }
            }
            this._windowSignals.delete(win);
        }
    }

    Ping() {
        return 'ok';
    }

    Snapshot() {
        const windows = [];
        const liveWindows = new Set();
        try {
            for (const actor of global.get_window_actors()) {
                const win = actor.get_meta_window();
                if (!win)
                    continue;
                liveWindows.add(win);
                this._watchWindow(win);
                if (shouldInclude(win))
                    windows.push(windowObject(win));
            }
        } catch (e) {
        }
        this._pruneWindowSignals(liveWindows);

        let activeTitle = '';
        try {
            activeTitle = safeString(() => global.display.get_focus_window().get_title());
        } catch (e) {
        }

        const outputs = [];
        try {
            const n = global.display.get_n_monitors();
            for (let i = 0; i < n; i++) {
                const g = rectToObject(global.display.get_monitor_geometry(i));
                if (g.width > 0 && g.height > 0)
                    outputs.push(g);
            }
        } catch (e) {
        }

        return JSON.stringify({
            backend: 'mutter',
            bridgeVersion: 4,
            revision: this._revision || 0,
            activeTitle,
            windows,
            outputs,
            keyboardLayout: {source: 'gnome-shell-extension'},
        });
    }
}