"""Small bounded WebSocket byte stream for authenticated CRB tunnels.

Browsers never receive device credentials. A tunnel carries binary mux frames;
control frames are handled here and do not enter the mux parser.
"""
import base64
import hashlib
import os
import socket
import struct
import threading
import time

class ServerWebSocket:
    MAX_MESSAGE = 256 * 1024

    def __init__(self, connection, reader, valid=lambda: True):
        self.connection, self.reader, self.valid = (connection, reader, valid)
        self.closed = threading.Event()
        self.output = threading.Lock()
        self.buffer = bytearray()
        self.fragments = bytearray()
        self.fragmented = False
        self.last_ping = time.monotonic()
        connection.settimeout(None)
        self.last_read = time.monotonic()

        def maintain():
            while not self.closed.wait(5):
                now = time.monotonic()
                if not self.valid() or now - self.last_read > 60:
                    self.close()
                    return

        def ping():
            while not self.closed.wait(20):
                try:
                    self.send_frame(9, b'crb')
                except OSError:
                    self.close()
                    return
        threading.Thread(target=maintain, daemon=True, name='local-preview-lease').start()
        threading.Thread(target=ping, daemon=True, name='local-preview-ping').start()

    @classmethod
    def upgrade(cls, handler, valid=lambda: True):
        key = handler.headers.get('Sec-WebSocket-Key', '')
        try:
            decoded = base64.b64decode(key, validate=True)
        except ValueError:
            raise ValueError('无效的网页通道握手') from None
        if len(decoded) != 16 or handler.headers.get('Sec-WebSocket-Version') != '13' or handler.headers.get('Upgrade', '').lower() != 'websocket' or ('upgrade' not in [value.strip().lower() for value in handler.headers.get('Connection', '').split(',')]):
            raise ValueError('无效的网页通道握手')
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        handler.send_response(101)
        handler.send_header('Upgrade', 'websocket')
        handler.send_header('Connection', 'Upgrade')
        handler.send_header('Sec-WebSocket-Accept', accept)
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        return cls(handler.connection, handler.rfile, valid)

    def exact(self, size):
        data = bytearray()
        while len(data) < size:
            if self.closed.is_set() or not self.valid():
                raise EOFError()
            block = self.reader.read1(size - len(data))
            if not block:
                raise EOFError()
            data.extend(block)
        return bytes(data)

    def frame(self):
        first, second = self.exact(2)
        final = bool(first & 128)
        opcode = first & 15
        masked = bool(second & 128)
        length = second & 127
        if first & 112 or not masked:
            raise ValueError('无效的网页通道帧')
        if length == 126:
            length = struct.unpack('!H', self.exact(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self.exact(8))[0]
        if length > self.MAX_MESSAGE or (opcode >= 8 and (not final or length > 125)):
            raise ValueError('网页通道帧超过上限')
        key = self.exact(4)
        payload = self.exact(length)
        payload = bytes((value ^ key[index % 4] for index, value in enumerate(payload)))
        self.last_read = time.monotonic()
        if opcode == 8:
            if len(payload) == 1:
                raise ValueError('无效的网页关闭帧')
            self.close()
            return None
        if opcode == 9:
            self.send_frame(10, payload)
            return b''
        if opcode == 10:
            return b''
        if opcode == 2 and (not self.fragmented):
            if final:
                return payload
            self.fragmented = True
            self.fragments.extend(payload)
            return b''
        if opcode == 0 and self.fragmented:
            self.fragments.extend(payload)
            if len(self.fragments) > self.MAX_MESSAGE:
                raise ValueError('网页通道消息超过上限')
            if final:
                result = bytes(self.fragments)
                self.fragments.clear()
                self.fragmented = False
                return result
            return b''
        raise ValueError('不支持的网页通道帧')

    def read(self, size):
        while len(self.buffer) < size and (not self.closed.is_set()):
            try:
                payload = self.frame()
            except (OSError, EOFError, ValueError):
                self.close()
                break
            if payload is None:
                break
            self.buffer.extend(payload)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def send_frame(self, opcode, payload):
        with self.output:
            size = len(payload)
            header = bytes([128 | opcode])
            header += bytes([size]) if size < 126 else b'~' + struct.pack('!H', size) if size <= 65535 else b'\x7f' + struct.pack('!Q', size)
            self.connection.sendall(header + payload)

    def write(self, data):
        if self.closed.is_set():
            raise OSError('网页通道已关闭')
        self.send_frame(2, bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        acquired = self.output.acquire(blocking=False)
        try:
            if acquired:
                self.connection.settimeout(0.2)
                self.connection.sendall(b'\x88\x02' + struct.pack('!H', 1000))
        except OSError:
            pass
        finally:
            if acquired:
                self.output.release()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

class ClientWebSocket:

    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()
        self.normal = False

    def read(self, size):
        import websocket
        while len(self.buffer) < size:
            try:
                opcode, payload = self.connection.recv_data(control_frame=True)
            except websocket.WebSocketException as error:
                raise OSError('网页通道连接已断开') from error
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                self.normal = len(payload) >= 2 and struct.unpack('!H', payload[:2])[0] == 1000
                break
            if opcode == websocket.ABNF.OPCODE_BINARY:
                self.buffer.extend(payload)
            elif opcode not in (websocket.ABNF.OPCODE_PING, websocket.ABNF.OPCODE_PONG):
                raise OSError('无效的网页通道消息')
            if len(self.buffer) > 512 * 1024:
                raise OSError('网页通道缓冲超过上限')
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def write(self, data):
        import websocket
        try:
            self.connection.send_binary(bytes(data))
        except websocket.WebSocketException as error:
            raise OSError('网页通道已断开') from error
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.connection.close()
