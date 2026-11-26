"""Ask the foreground X11 Codex window to open a side chat for a verified parent."""
import os
import select
import time
from uuid import UUID

def open_side_chat(process, parent):
    from Xlib import X, XK, display, error
    from Xlib.ext import res, xfixes
    from Xlib.protocol import event
    expected = ('codex://threads/' + str(UUID(parent))).encode('ascii')
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland' or not os.environ.get('DISPLAY'):
        raise RuntimeError('当前 Linux 显示会话无法核对原 Codex 窗口')
    try:
        connection = display.Display()
    except (error.DisplayError, OSError) as exc:
        raise RuntimeError('无法连接本机桌面以核对当前对话') from exc
    try:
        connection.res_query_version()
        connection.xfixes_query_version()
        root = connection.screen().root
        window = connection.get_input_focus().focus

        def official(resource):
            if not hasattr(resource, 'id') or not process.is_running():
                return False
            ids = connection.res_query_client_ids([{'client': resource.id, 'mask': res.LocalClientPIDMask}]).ids
            return [pid for item in ids for pid in item.value] == [process.pid]

        def focused():
            return connection.get_input_focus().focus == window and official(window)

        def codex_window(w):
            return official(w) and (w.get_wm_class() or ('',))[-1].lower() == 'chatgpt'
        if not codex_window(window):
            clients = root.get_full_property(connection.intern_atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
            windows = [] if clients is None else [connection.create_resource_object('window', wid) for wid in clients.value]
            matches = [w for w in windows if codex_window(w) and w.get_attributes().map_state == X.IsViewable]
            if len(matches) != 1:
                raise RuntimeError('无法唯一确定可见的原 Codex 窗口')
            window = matches[0]
            active = connection.intern_atom('_NET_ACTIVE_WINDOW')
            root.send_event(event.ClientMessage(window=window, client_type=active, data=(32, [1, X.CurrentTime, 0, 0, 0])), event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            connection.flush()
            until = time.monotonic() + 2
            while not focused() and time.monotonic() < until:
                time.sleep(0.05)
        if not focused() or not codex_window(window):
            raise RuntimeError('请先让目标主聊显示在原 Codex 窗口')

        def shortcut(key, modifiers=X.ControlMask | X.Mod1Mask):
            keycode = connection.keysym_to_keycode(XK.string_to_keysym(key))
            if not keycode or not focused():
                raise RuntimeError('原窗口焦点已变化，操作已取消')
            for event_type in (event.KeyPress, event.KeyRelease):
                window.send_event(event_type(time=X.CurrentTime, root=root, window=window, child=X.NONE, root_x=0, root_y=0, event_x=0, event_y=0, state=modifiers, detail=keycode, same_screen=1), propagate=False, event_mask=0)
            connection.sync()
        notify = connection.query_extension('XFIXES').first_event + xfixes.XFixesSelectionNotify
        clipboard = connection.intern_atom('CLIPBOARD')
        utf8 = connection.intern_atom('UTF8_STRING')
        prop = connection.intern_atom('CRB_CURRENT_CHAT_LINK')

        def verify_link():
            receiver = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent, X.InputOnly, X.CopyFromParent)
            connection.xfixes_select_selection_input(receiver, clipboard, xfixes.XFixesSetSelectionOwnerNotifyMask)
            connection.sync()
            shortcut('l')
            deadline = time.monotonic() + 2
            owner = None
            while time.monotonic() < deadline:
                if not connection.pending_events():
                    select.select([connection.fileno()], [], [], max(0, deadline - time.monotonic()))
                    continue
                ev = connection.next_event()
                if getattr(ev, 'selection', None) != clipboard:
                    continue
                if ev.type == notify and ev.sub_code == xfixes.XFixesSetSelectionOwnerNotify and (not ev.send_event) and (ev.window == receiver):
                    if owner is not None or not official(ev.owner) or ev.owner != connection.get_selection_owner(clipboard):
                        break
                    owner = ev.owner
                    receiver.convert_selection(clipboard, utf8, prop, ev.selection_timestamp)
                    connection.flush()
                elif owner is not None and ev.type == X.SelectionNotify and (ev.requestor == receiver) and (ev.property == prop) and (ev.target == utf8) and (connection.get_selection_owner(clipboard) == owner):
                    value = receiver.get_property(prop, utf8, 0, 20)
                    if value and value.format == 8 and (not value.bytes_after) and (bytes(value.value) == expected) and focused():
                        return
                    break
            raise RuntimeError('原 Codex 未确认当前主聊的链接，侧聊未创建')
        verify_link()
        shortcut('t', X.ControlMask)
        time.sleep(0.45)
        verify_link()
        shortcut('s')
    except (error.XError, error.DisplayError, AttributeError) as exc:
        raise RuntimeError('当前桌面未提供可核对的 Codex 窗口') from exc
    finally:
        connection.close()
