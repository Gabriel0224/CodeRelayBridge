"""Offscreen native-widget acceptance; never activate user windows."""
import json
import os
from pathlib import Path
import tempfile

def displayed_button_text(text):
    """Qt doubles literal ampersands; single ampersands mark mnemonics."""
    return text.replace('&&', '\x00').replace('&', '').replace('\x00', '&')

def check(role, report):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop_client.app import MainWindow, load_offscreen_fonts, STYLE
    from desktop_client.backend import BridgeManager, Preferences
    from desktop_client.connections import atomic_json
    from desktop_client.i18n import apply_language
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    load_offscreen_fonts()
    with tempfile.TemporaryDirectory() as directory:
        if role == 'client':
            manager = BridgeManager(processes=lambda: [])
            manager.status = lambda: [{'key': s.key, 'title': s.title, 'needs_setup': True, 'pids': [], 'instances': 0, 'enabled': False, 'error': ''} for s in manager.specs.values()]

            class Startup:

                def enabled(self):
                    return False
            window = MainWindow(manager=manager, preferences=Preferences(directory), startup=Startup(), tray_enabled=False)
            window.setup_prompted = True
        else:
            from desktop_server.app import ServerWindow
            from desktop_server.backend import ServerManager
            window = ServerWindow(ServerManager(Path(directory) / 'server', directory))
        window.timer.stop()
        window.show()
        app.processEvents()
        errors = []
        pages = window.pages
        language_checks = []
        for language in ('zh', 'en'):
            window.language_select.setCurrentIndex(0 if language == 'zh' else 1)
            window.language_select.activated.emit(0 if language == 'zh' else 1)
            apply_language(language)
            for width, height in [(760, 560), (1040, 760), (1400, 950)]:
                window.resize(width, height)
                for index in range(pages.count()):
                    pages.setCurrentIndex(index)
                    app.processEvents()
                    for button in window.findChildren(QPushButton):
                        if not button.isVisibleTo(window):
                            continue
                        needed = button.fontMetrics().horizontalAdvance(displayed_button_text(button.text())) + 20
                        if button.width() + 2 < needed:
                            errors.append({'language': language, 'size': [width, height], 'page': index, 'button': button.text(), 'actual': button.width(), 'needed': needed})
            language_checks.append({'language': language, 'persisted': window.preferences.load().get('language') == language, 'selected': window.language_select.currentData() == language})
            if not language_checks[-1]['persisted'] or not language_checks[-1]['selected']:
                errors.append({'language': language, 'error': 'Language selection was not persisted'})
            pages.setCurrentIndex(0)
            window.resize(1040, 760)
            app.processEvents()
            window.grab().save(str(Path(report).with_name(Path(report).stem + '-' + language + '.png')))
        pages.setCurrentIndex(0)
        window.resize(1040, 760)
        app.processEvents()
        screenshot = Path(report).with_suffix('.png')
        window.grab().save(str(screenshot))
        window.pool.waitForDone(8000) if hasattr(window, 'pool') else window.thread_pool.waitForDone(8000) if hasattr(window, 'thread_pool') else None
        atomic_json(Path(report), {'role': role, 'platform': os.sys.platform, 'passed': not errors, 'errors': errors, 'screenshot': str(screenshot), 'offscreen': True, 'languages': language_checks})
        window.hide()
        window.deleteLater()
        app.processEvents()
    return 0 if not errors else 1
