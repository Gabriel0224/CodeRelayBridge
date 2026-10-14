"""One Codex updater for both editions; optional targets are build-gated."""
import importlib
import crb.updates.codex_updates as codex_updates

def check(package):
    if package == 'codex':
        return codex_updates.check()
    raise ValueError('不支持的更新目标')

def install(package, password):
    if package == 'codex':
        return codex_updates.install(password)
    raise ValueError('不支持的更新目标')
