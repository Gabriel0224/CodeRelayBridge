"""The installed Relay Client's scoped accessibility action for 'Open side chat'."""
import ctypes as C
from contextlib import ExitStack
import time

def open_side_chat(pid, title):
    ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr = C.c_void_p

    def func(lib, name, ret, *args):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = (ret, list(args))
        return fn
    trusted = func(ax, 'AXIsProcessTrusted', C.c_bool)
    if not trusted():
        raise PermissionError('请在 CRB 客户端设置中确认当前 Relay Client 的辅助功能授权')
    app_for = func(ax, 'AXUIElementCreateApplication', ptr, C.c_int)
    attr = func(ax, 'AXUIElementCopyAttributeValue', C.c_int, ptr, ptr, C.POINTER(ptr))
    set_timeout = func(ax, 'AXUIElementSetMessagingTimeout', C.c_int, ptr, C.c_float)
    release = func(cf, 'CFRelease', None, ptr)
    mkstr = func(cf, 'CFStringCreateWithCString', ptr, ptr, C.c_char_p, C.c_uint32)
    as_text = func(cf, 'CFStringGetCString', C.c_bool, ptr, C.c_char_p, C.c_long, C.c_uint32)
    type_of = func(cf, 'CFGetTypeID', C.c_ulong, ptr)
    string_type = func(cf, 'CFStringGetTypeID', C.c_ulong)()
    array_type = func(cf, 'CFArrayGetTypeID', C.c_ulong)()
    array_len = func(cf, 'CFArrayGetCount', C.c_long, ptr)
    array_item = func(cf, 'CFArrayGetValueAtIndex', ptr, ptr, C.c_long)
    boolean_type = func(cf, 'CFBooleanGetTypeID', C.c_ulong)()
    boolean_value = func(cf, 'CFBooleanGetValue', C.c_bool, ptr)
    with ExitStack() as stack:

        def owned(obj):
            if obj:
                stack.callback(release, obj)
            return obj
        keys = {s: owned(mkstr(None, s.encode(), 134217984)) for s in ('AXFocusedWindow', 'AXFrontmost', 'AXRole', 'AXTitle', 'AXChildren')}
        app = owned(app_for(pid))
        if not app:
            raise RuntimeError('原 App 窗口不可用')
        set_timeout(app, 0.5)

        def get(element, key):
            out = ptr()
            return owned(out.value) if element and attr(element, keys[key], C.byref(out)) == 0 else None

        def text(element, key):
            val = get(element, key)
            buf = C.create_string_buffer(4096)
            if val and type_of(val) == string_type and as_text(val, buf, len(buf), 134217984):
                return buf.value.decode('utf-8')
            return ''

        def front():
            value = get(app, 'AXFrontmost')
            return value and type_of(value) == boolean_type and boolean_value(value)
        win = get(app, 'AXFocusedWindow')
        if not win or not front():
            raise RuntimeError('请让原 Codex 窗口处于前台')
        pending = [win]
        seen = set()
        docs = []
        deadline = time.monotonic() + 5
        while pending and len(seen) < 256 and (time.monotonic() < deadline):
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            role = text(node, 'AXRole')
            if role == 'AXWebArea':
                docs.append(text(node, 'AXTitle'))
                continue
            kids = get(node, 'AXChildren')
            if kids and type_of(kids) == array_type:
                count = array_len(kids)
                if count > 256:
                    raise RuntimeError('无法唯一确认原窗口文档')
                pending.extend((array_item(kids, i) for i in range(count)))
        if pending or docs != [title] or (not front()):
            raise RuntimeError('原 Codex 当前文档与所选主聊不符')
        key_event = func(ax, 'CGEventCreateKeyboardEvent', ptr, ptr, C.c_ushort, C.c_bool)
        set_flags = func(ax, 'CGEventSetFlags', None, ptr, C.c_uint64)
        post = func(ax, 'CGEventPostToPid', None, C.c_int, ptr)
        for pressed in (True, False):
            event = owned(key_event(None, 1, pressed))
            if not event:
                raise RuntimeError('无法打开原生侧聊')
            set_flags(event, 1 << 20 | 1 << 19)
            post(pid, event)
