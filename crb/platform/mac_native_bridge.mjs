/** User-authorized, App-managed adapter for the bundled App Tools MCP.
 * Never launches Codex/LLM, alters the App bundle, or changes callerSource.
 * A separate MCP component; the App's original tools server is never replaced.
 * This component advertises no model tools or instructions. Panel operations
 * alone use the private endpoint and the unmodified bundled tools implementation.
 * The private local endpoint becomes available only after native tools/list succeeds.
 */
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';

export const ALLOWED_TOOLS = new Set([
  'list_projects', 'create_thread', 'set_thread_title', 'set_thread_archived',
  'set_thread_read_state', 'automation_update', 'open_in_codex',
]);
const MAX_INPUT = 256 * 1024;
const MAX_OUTPUT = 8 * 1024 * 1024;

function privateEntry(file, kind, uid) {
  const s = fs.lstatSync(file);
  if (s.isSymbolicLink() || s.uid !== uid || (s.mode & 0o077) ||
      !(kind === 'directory' ? s.isDirectory() : s.isFile())) {
    throw new Error('Bridge credentials must be private and owned by the current user');
  }
  return s;
}

export function startBridge({ input = process.stdin, output = process.stdout,
  directory = process.env.CODEX_RELAY_NATIVE_BRIDGE_DIR, backend,
  node = process.execPath, env = process.env, uid = process.getuid?.() } = {}) {
  if (uid == null || !directory || !path.isAbsolute(directory)) {
    throw new Error('A private local bridge directory is required');
  }
  privateEntry(directory, 'directory', uid);
  const credential = path.join(directory, 'token');
  privateEntry(credential, 'file', uid);
  const token = fs.readFileSync(credential, 'utf8').trim();
  if (!/^[a-f0-9]{64}$/.test(token)) throw new Error('Invalid bridge credential');
  if (!backend) {
    let resources = env.CODEX_ELECTRON_RESOURCES_PATH;
    if (!resources) {
      let p = path.dirname(node);
      while (path.dirname(p) !== p) {
        if (path.basename(p) === 'Resources' && path.basename(path.dirname(p)) === 'Contents') {
          resources = p; break;
        }
        p = path.dirname(p);
      }
    }
    if (!resources) throw new Error('The bundled App runtime is required');
    backend = path.join(resources, 'plugins/openai-bundled/plugins/codex-app-tools/server.mjs');
  }
  const child = spawn(node, [backend], { env, stdio: ['pipe', 'pipe', 'pipe'] });
  // Do not copy potentially sensitive native diagnostics into the panel logs.
  child.stderr.resume();
  const pending = new Map();
  const appIds = new Map();
  const sockets = new Set();
  let sequence = 0, closed = false, listener = null, endpoint = null;
  let available = new Set();
  const writeChild = value => child.stdin.write(JSON.stringify(value) + '\n');
  const writeApp = value => output.write(JSON.stringify(value) + '\n');
  const appInput = readline.createInterface({ input, crlfDelay: Infinity });
  const childInput = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });

  function nativeRequest(method, params, timeout) {
    if (closed) return Promise.reject(new Error('Native bridge closed'));
    const id = 'relay:' + randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        // No resend: a write might already have been accepted by the App.
        reject(new Error('Native tool result unknown after timeout; do not resend automatically'));
      }, timeout);
      pending.set(id, { resolve, reject, timer });
      writeChild({ jsonrpc: '2.0', id, method, params });
    });
  }

  function authorized(value) {
    if (typeof value !== 'string' || value.length !== token.length) return false;
    return timingSafeEqual(Buffer.from(value), Buffer.from(token));
  }

  let activeCalls = 0;
  async function localRequest(message) {
    const p = message?.params;
    if (!authorized(p?.token)) throw new Error('Local bridge authentication failed');
    if (message.method === 'status') {
      return { ready: true, version: 1, pid: process.pid, tools: [...available].filter(t => ALLOWED_TOOLS.has(t)) };
    }
    if (message.method !== 'tools/call' || !ALLOWED_TOOLS.has(p.tool) || !available.has(p.tool)) {
      throw new Error('This native operation is not enabled for the panel');
    }
    if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(p.threadId ?? '') ||
        !p.arguments || typeof p.arguments !== 'object' || Array.isArray(p.arguments)) {
      throw new Error('Invalid native caller context or arguments');
    }
    if (activeCalls >= 4) throw new Error('Native bridge busy; operation was not submitted');
    const timeout = Math.max(1000, Math.min(Number(p.timeoutMs) || 60000, 120000));
    activeCalls++;
    try {
      // Standard MCP metadata; the bundled server supplies its own call/turn IDs
      // and submits to the App, including its unchanged native eligibility checks.
      return await nativeRequest('tools/call', { name: p.tool, arguments: p.arguments,
        _meta: { 'openai/threadId': p.threadId } }, timeout);
    } finally { activeCalls--; }
  }

  function listen() {
    if (listener || closed) return;
    endpoint = path.join(directory, process.pid + '-' + randomBytes(6).toString('hex') + '.sock');
    if (Buffer.byteLength(endpoint) > 100) throw new Error('Bridge socket path is too long');
    listener = net.createServer(socket => {
      if (sockets.size >= 8) { socket.destroy(); return; }
      sockets.add(socket);
      socket.on('close', () => sockets.delete(socket));
      socket.on('error', () => {});
      socket.setTimeout(125000, () => socket.destroy());
      let buffer = Buffer.alloc(0), count = 0, chain = Promise.resolve();
      socket.on('data', data => {
        buffer = Buffer.concat([buffer, data]);
        if (buffer.length > MAX_INPUT + 4) { socket.destroy(); return; }
        while (buffer.length >= 4) {
          const length = buffer.readUInt32LE(0);
          if (!length || length > MAX_INPUT) { socket.destroy(); return; }
          if (buffer.length < length + 4) break;
          const frame = buffer.subarray(4, length + 4);
          buffer = buffer.subarray(length + 4);
          if (++count > 2) { socket.destroy(); return; }
          chain = chain.then(async () => {
            let message, result;
            try {
              message = JSON.parse(frame.toString('utf8'));
              if (message.jsonrpc !== '2.0' || typeof message.id !== 'string') throw new Error('Invalid request');
              result = { jsonrpc: '2.0', id: message.id, result: await localRequest(message) };
            } catch (error) {
              result = { jsonrpc: '2.0', id: message?.id ?? null,
                error: { code: -32603, message: error.message } };
            }
            const body = Buffer.from(JSON.stringify(result));
            if (body.length > MAX_OUTPUT || socket.destroyed) { socket.destroy(); return; }
            const header = Buffer.alloc(4); header.writeUInt32LE(body.length);
            socket.write(Buffer.concat([header, body]));
          }).catch(() => socket.destroy());
        }
      });
    });
    listener.on('error', () => close());
    listener.listen(endpoint, () => fs.chmodSync(endpoint, 0o600));
  }

  childInput.on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message.method && pending.has(message.id)) {
      const p = pending.get(message.id); pending.delete(message.id); clearTimeout(p.timer);
      if (p.kind === 'app') {
        appIds.delete(p.key);
        if (p.method === 'initialize' && message.result) {
          const { instructions: _instructions, ...result } = message.result;
          message = { ...message, result: { ...result,
            serverInfo: { name: 'codex-relay-native-bridge', version: '1.0' } } };
        }
        if (p.method === 'tools/list' && Array.isArray(message.result?.tools)) {
          available = new Set(message.result.tools.map(t => t.name));
          try { listen(); } catch { close(); return; }
          message = { ...message, result: { ...message.result, tools: [] } };
        }
        writeApp({ ...message, id: p.originalId });
      } else if (message.error) p.reject(new Error(message.error.message || 'Native tool failed'));
      else p.resolve(message.result);
      return;
    }
    if (!message.method && typeof message.id === 'string' && message.id.startsWith('relay:')) return;
    writeApp(message);
  });
  appInput.on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (message.method && Object.hasOwn(message, 'id')) {
      const id = 'app:' + (++sequence), key = JSON.stringify(message.id);
      pending.set(id, { kind: 'app', originalId: message.id, key, method: message.method });
      appIds.set(key, id);
      writeChild({ ...message, id });
    } else {
      if (message.method === 'notifications/cancelled' && message.params?.requestId != null) {
        const id = appIds.get(JSON.stringify(message.params.requestId));
        if (id) message = { ...message, params: { ...message.params, requestId: id } };
      }
      writeChild(message);
    }
  });

  function close() {
    if (closed) return;
    closed = true;
    appInput.close(); childInput.close();
    for (const p of pending.values()) {
      clearTimeout(p.timer);
      p.reject?.(new Error('Native bridge closed; operation result may be unknown'));
    }
    pending.clear();
    for (const socket of sockets) socket.destroy();
    listener?.close();
    if (endpoint) { try { fs.unlinkSync(endpoint); } catch {} }
    child.kill();
  }
  child.on('error', close); child.on('exit', close); appInput.on('close', close);
  return { close, child, get endpoint() { return endpoint; } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const bridge = startBridge();
    process.once('SIGTERM', () => bridge.close());
    process.once('SIGINT', () => bridge.close());
  } catch {
    process.stderr.write('Codex Relay native bridge could not initialize its private runtime.\n');
    process.exitCode = 1;
  }
}
