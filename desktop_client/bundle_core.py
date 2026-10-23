"""Stage deployed Windows bridge sources under a unique package name."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
MODULES = ('agent', 'ipc', 'question_ui', 'native_history', 'sidebar', 'live_view_cache', 'native_updates', 'codex_updates', 'read_progress', 'native_features', 'projects', 'native_sidebar', 'sidebar_ui', 'conversation', 'harness_agent', 'harness_client')
MODULE_SOURCES = {'agent': 'crb/codex/agent.py', 'ipc': 'crb/codex/ipc.py', 'question_ui': 'crb/codex/question_ui.py', 'native_history': 'crb/codex/native_history.py', 'sidebar': 'crb/codex/sidebar.py', 'projects': 'crb/codex/projects.py', 'native_sidebar': 'crb/codex/native_sidebar.py', 'sidebar_ui': 'crb/codex/sidebar_ui.py', 'conversation': 'crb/codex/conversation.py', 'native_features': 'crb/codex/native_features.py', 'read_progress': 'crb/codex/read_progress.py', 'live_view_cache': 'crb/common/live_view_cache.py', 'modern_desktop_client': 'crb/common/modern_desktop_client.py', 'harness_agent': 'crb/deepseek/harness_agent.py', 'harness_client': 'crb/deepseek/harness_client.py', 'software_updates': 'crb/optional provider/software_updates.py', 'native_updates': 'crb/updates/native_updates.py', 'codex_updates': 'crb/updates/codex_updates.py', 'mac_native_runtime': 'crb/platform/mac_native_runtime.py'}

def source_file(root, name):
    root = Path(root)
    candidate = root / MODULE_SOURCES.get(name, name + '.py')
    return candidate if candidate.is_file() else root / (name + '.py')

def function_hashes(text):
    return [hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() for node in ast.walk(ast.parse(text)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

def stage_core(source, stage, extras=None, edition='standard'):
    if edition not in ('standard',):
        raise ValueError('Unknown client edition')
    source, stage = (Path(source), Path(stage))
    package = stage / 'relay_bridge_bundle'
    package.mkdir(parents=True, exist_ok=True)
    (package / '__init__.py').write_text('"""Bundled Windows bridge core."""\n', encoding='utf-8')
    manifest = {'source': 'local-build', 'edition': edition, 'modules': {}, 'function_bodies_unchanged': True}
    names = list(MODULES)
    if sys.platform == 'darwin':
        names += ['mac_native_runtime']
    extras = {k: Path(v) for k, v in (extras or {}).items()}
    provenance_path = source / 'SOURCE_PROVENANCE.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8')) if provenance_path.is_file() else {}
    harness_source = extras.get('modern_desktop_client', extras.get('harness_agent', source))
    if source_file(harness_source, 'modern_desktop_client').is_file():
        names += ['modern_desktop_client']
        extras['modern_desktop_client'] = harness_source
    for name in names:
        original_path = source_file(extras.get(name, source), name)
        original = original_path.read_text(encoding='utf-8-sig')
        bundled = original
        for local in names:
            qualified = MODULE_SOURCES.get(local, local + '.py')[:-3].replace('/', '.')
            bundled = re.sub('(?m)^from ' + re.escape(qualified) + ' import ', 'from relay_bridge_bundle.' + local + ' import ', bundled)
            bundled = re.sub('(?m)^import ' + re.escape(qualified) + ' as ([A-Za-z_][A-Za-z_0-9]*)\\s*$', 'from relay_bridge_bundle import ' + local + ' as \\1', bundled)
            bundled = re.sub('(?m)^from ' + local + ' import ', 'from relay_bridge_bundle.' + local + ' import ', bundled)
            bundled = re.sub('(?m)^import ' + local + '\\s*$', 'from relay_bridge_bundle import ' + local, bundled)
            bundled = re.sub('(?m)^import ' + local + ' as ([A-Za-z_][A-Za-z_0-9]*)\\s*$', 'from relay_bridge_bundle import ' + local + ' as \\1', bundled)
        if function_hashes(original) != function_hashes(bundled):
            raise RuntimeError(f'{name}: bundle changed a function body')
        (package / (name + '.py')).write_text(bundled, encoding='utf-8')
        manifest['modules'][name] = {'source_sha256': hashlib.sha256(original.encode()).hexdigest(), 'bundle_sha256': hashlib.sha256(bundled.encode()).hexdigest(), 'functions': len(function_hashes(original))}
        origin_name = original_path.relative_to(source).as_posix() if original_path.is_relative_to(source) else name + '.py'
        origin = provenance.get('files', {}).get(origin_name, {})
        if origin:
            if origin['exportSha256'] == hashlib.sha256(original.encode()).hexdigest():
                manifest['modules'][name]['common_origin_sha256'] = origin['canonicalSha256']
            else:
                manifest['sourceModifiedAfterSelection'] = True
    assets = []
    if sys.platform == 'win32':
        assets.append('WebView2Loader.dll')
    for filename in assets:
        asset = source / filename
        if asset.is_file():
            shutil.copy2(asset, package / filename)
            manifest[filename + '_sha256'] = hashlib.sha256(asset.read_bytes()).hexdigest()
        elif sys.platform == 'win32':
            raise FileNotFoundError(asset)
    ui_source = source_file(extras.get('question_ui', source), 'question_ui').with_suffix('.js')
    shutil.copy2(ui_source, package / 'question_ui.js')
    manifest['question_ui.js_sha256'] = hashlib.sha256(ui_source.read_bytes()).hexdigest()
    sidebar_source = source_file(extras.get('sidebar_ui', source), 'sidebar_ui').with_suffix('.js')
    shutil.copy2(sidebar_source, package / 'sidebar_ui.js')
    manifest['sidebar_ui.js_sha256'] = hashlib.sha256(sidebar_source.read_bytes()).hexdigest()
    if sys.platform == 'darwin':
        native_bridge = source / 'crb/platform/mac_native_bridge.mjs'
        if not native_bridge.is_file():
            native_bridge = source / 'mac_native_bridge.mjs'
        shutil.copy2(native_bridge, package / 'mac_native_bridge.mjs')
    (package / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
