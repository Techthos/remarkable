"""Writes the primary monitor to stdout as raw GRAY8 frames of the configured width
and the HEIGHT argument, only when it visibly changed and at most once per frame interval.
Arguments: settings as JSON (keys of the extension schema), HEIGHT.

Uses the ScreenCast portal; the permission is remembered through a restore token.
"""
import json
import os
import sys
import time

import gi
import numpy

gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst

PORTAL, DESKTOP = 'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop'
SCREENCAST = 'org.freedesktop.portal.ScreenCast'
TOKEN_FILE = os.path.expanduser('~/.local/state/rmpc/screencast-token')

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
sender = bus.get_unique_name()[1:].replace('.', '_')
loop = GLib.MainLoop()


def portal_request(method, signature, *args, options):
    token = f'rmpc_{method.lower()}'
    response = {}

    def on_response(*signal):
        response['code'], response['results'] = signal[-1].unpack()
        loop.quit()

    subscription = bus.signal_subscribe(
        PORTAL, 'org.freedesktop.portal.Request', 'Response', f'{DESKTOP}/request/{sender}/{token}',
        None, Gio.DBusSignalFlags.NONE, on_response)
    options = {'handle_token': GLib.Variant('s', token), **options}
    bus.call_sync(PORTAL, DESKTOP, SCREENCAST, method, GLib.Variant(signature, (*args, options)),
                  None, Gio.DBusCallFlags.NONE, -1, None)
    loop.run()
    bus.signal_unsubscribe(subscription)
    if response['code'] != 0:
        sys.exit(f'screencast {method} refused')
    return response['results']


def start_screencast(show_cursor):
    session = portal_request('CreateSession', '(a{sv})', options={
        'session_handle_token': GLib.Variant('s', 'rmpc')})['session_handle']
    restore = {}
    if os.path.exists(TOKEN_FILE):
        restore['restore_token'] = GLib.Variant('s', open(TOKEN_FILE).read())
    portal_request('SelectSources', '(oa{sv})', session, options={
        'types': GLib.Variant('u', 1),
        'cursor_mode': GLib.Variant('u', 2 if show_cursor else 1),
        'persist_mode': GLib.Variant('u', 2),
        **restore})
    started = portal_request('Start', '(osa{sv})', session, '', options={})
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        f.write(started['restore_token'])

    result, fds = bus.call_with_unix_fd_list_sync(
        PORTAL, DESKTOP, SCREENCAST, 'OpenPipeWireRemote', GLib.Variant('(oa{sv})', (session, {})),
        GLib.VariantType('(h)'), Gio.DBusCallFlags.NONE, -1, None, None)
    return fds.get(result.unpack()[0]), started['streams'][0][0]


def main():
    config, height = json.loads(sys.argv[1]), int(sys.argv[2])
    width = config['image-width']
    fd, node = start_screencast(config['show-cursor'])
    Gst.init(None)
    pipeline = Gst.parse_launch(
        f'pipewiresrc fd={fd} path={node} always-copy=true keepalive-time=1000'
        f' ! videorate drop-only=true max-rate={max(1, round(1 / config["frame-interval"]))} ! videoscale ! videoconvert'
        f' ! video/x-raw,format=GRAY8,width={width},height={height}'
        ' ! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false')
    last = {'image': numpy.zeros(width * height, numpy.int16), 'time': 0.0}

    def on_sample(sink):
        buffer = sink.emit('pull-sample').get_buffer()
        if time.monotonic() - last['time'] < config['frame-interval']:
            return Gst.FlowReturn.OK
        last['time'] = time.monotonic()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        image = bytes(mapped.data)
        buffer.unmap(mapped)
        pixels = numpy.frombuffer(image, numpy.uint8).astype(numpy.int16)
        if numpy.count_nonzero(abs(pixels - last['image']) > config['change-level']) > config['min-changed-pixels']:
            last['image'] = pixels
            sys.stdout.buffer.write(image)
            sys.stdout.buffer.flush()
        return Gst.FlowReturn.OK

    pipeline.get_by_name('sink').connect('new-sample', on_sample)
    pipeline.get_bus().add_signal_watch()
    pipeline.get_bus().connect('message::error', lambda *_: sys.exit('screencast pipeline failed'))
    pipeline.get_bus().connect('message::eos', lambda *_: loop.quit())
    GLib.io_add_watch(sys.stdout.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_ERR, lambda *_: loop.quit())
    pipeline.set_state(Gst.State.PLAYING)
    loop.run()


if __name__ == '__main__':
    main()
