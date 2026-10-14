"""Native text bindings: retranslate in place, without restarting or polling.

Only labels/actions/tooltips/placeholders are bound. Editable values, logs,
process IDs, credentials, filenames, and user-provided table values are not.
"""
import weakref
from PySide6 import QtWidgets as W, QtGui as G
from PySide6.QtCore import QLocale, QSignalBlocker
from shiboken6 import isValid
from crb.i18n.ui_i18n import language, selected_language, set_language, tr
_bindings = weakref.WeakKeyDictionary()

def _bind(obj, key, value, setter):
    if isinstance(value, str):
        _bindings.setdefault(obj, {})[key] = (value, setter)
        setter(obj, tr(value))
    else:
        setter(obj, value)

def apply_language(value):
    set_language(value)
    for obj, properties in list(_bindings.items()):
        if isValid(obj):
            for source, setter in list(properties.values()):
                setter(obj, tr(source))

def initialize(preferences):
    system = QLocale.system()
    preferred = system.uiLanguages()
    value = selected_language(preferences.load().get('language'), preferred[0] if preferred else system.name())
    apply_language(value)
    return value

class _UiText:

    def setToolTip(self, value):
        _bind(self, 'tooltip', value, W.QWidget.setToolTip)

    def setAccessibleName(self, value):
        _bind(self, 'accessibleName', value, W.QWidget.setAccessibleName)

    def setWindowTitle(self, value):
        _bind(self, 'windowTitle', value, W.QWidget.setWindowTitle)

def _text_widget(name):
    base = getattr(W, name)

    def set_text(obj, value):
        base.setText(obj, value.replace('&', '&&') if name in ('QPushButton', 'QCheckBox', 'QToolButton') else value)
        if name == 'QPushButton':
            obj.setMinimumWidth(obj.fontMetrics().horizontalAdvance(value) + 28)

    class Localized(_UiText, base):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if args and isinstance(args[0], str):
                self.setText(args[0])

        def setText(self, value):
            _bind(self, 'text', value, set_text)
    Localized.__name__ = name
    return Localized
QLabel = _text_widget('QLabel')
QPushButton = _text_widget('QPushButton')
QCheckBox = _text_widget('QCheckBox')
QToolButton = _text_widget('QToolButton')

class QMainWindow(_UiText, W.QMainWindow):
    pass

class QDialog(_UiText, W.QDialog):
    pass

class QFormLayout(W.QFormLayout):

    def addRow(self, *args):
        if len(args) == 2 and isinstance(args[0], str):
            label = QLabel(args[0], self.parentWidget())
            if isinstance(args[1], W.QWidget):
                label.setBuddy(args[1])
            return super().addRow(label, args[1])
        return super().addRow(*args)

class QLineEdit(_UiText, W.QLineEdit):

    def setPlaceholderText(self, value):
        _bind(self, 'placeholder', value, W.QLineEdit.setPlaceholderText)

class QComboBox(_UiText, W.QComboBox):

    def addItem(self, *args, **kwargs):
        index = self.count()
        super().addItem(*args, **kwargs)
        label = args[0] if args and isinstance(args[0], str) else args[1] if len(args) > 1 else None
        if isinstance(label, str):
            _bind(self, ('item', index), label, lambda obj, value, i=index: W.QComboBox.setItemText(obj, i, value))

    def addItems(self, labels):
        for label in labels:
            self.addItem(label)

    def setItemText(self, index, value):
        _bind(self, ('item', index), value, lambda obj, text, i=index: W.QComboBox.setItemText(obj, i, text))

    def clear(self):
        _bindings.pop(self, None)
        super().clear()

class QAction(G.QAction):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        source = next((a for a in args[:2] if isinstance(a, str)), None)
        if source is not None:
            self.setText(source)

    def setText(self, value):
        _bind(self, 'text', value, lambda obj, text: G.QAction.setText(obj, text.replace('&', '&&')))

    def setToolTip(self, value):
        _bind(self, 'tooltip', value, G.QAction.setToolTip)

class QMenu(_UiText, W.QMenu):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if args and isinstance(args[0], str):
            self.setTitle(args[0])

    def setTitle(self, value):
        _bind(self, 'title', value, W.QMenu.setTitle)

    def addAction(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            action = QAction(args[0], self)
            if len(args) > 1 and callable(args[1]):
                action.triggered.connect(args[1])
            super().addAction(action)
            return action
        action = super().addAction(*args, **kwargs)
        source = next((a for a in args[:2] if isinstance(a, str)), None)
        if source is not None:
            _bind(action, 'text', source, G.QAction.setText)
        return action

    def addMenu(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            menu = QMenu(args[0], self)
            super().addMenu(menu)
            return menu
        menu = super().addMenu(*args, **kwargs)
        source = next((a for a in args[:2] if isinstance(a, str)), None)
        if source is not None:
            _bind(menu, 'title', source, W.QMenu.setTitle)
        return menu

class QSystemTrayIcon(W.QSystemTrayIcon):

    def setToolTip(self, value):
        _bind(self, 'tooltip', value, W.QSystemTrayIcon.setToolTip)

class QTableWidget(_UiText, W.QTableWidget):

    def setHorizontalHeaderLabels(self, values):
        source = list(values)
        for i, value in enumerate(source):
            _bind(self, ('header', i), value, lambda obj, text, index=i, original=value: obj.horizontalHeaderItem(index).setText('Application' if original == '应用' and language() == 'en' else text) if obj.horizontalHeaderItem(index) else None)
        super().setHorizontalHeaderLabels(['Application' if v == '应用' and language() == 'en' else tr(v) for v in source])

def status_table_item(source):
    """Bind explicit status cells, not computer/application names."""
    item = W.QTableWidgetItem()
    _bind(item, 'status', source, W.QTableWidgetItem.setText)
    return item

class QMessageBox(W.QMessageBox):

    @staticmethod
    def _show(icon, parent, title, text, buttons, default):
        dialog = W.QMessageBox(icon, tr(title), tr(text), buttons, parent)
        dialog.setDefaultButton(default)
        labels = {'Yes': ('是', 'Yes'), 'No': ('否', 'No'), 'Ok': ('确定', 'OK'), 'Cancel': ('取消', 'Cancel'), 'Close': ('关闭', 'Close'), 'Retry': ('重试', 'Retry')}
        for name, captions in labels.items():
            button = dialog.button(getattr(W.QMessageBox.StandardButton, name))
            if button:
                button.setText(captions[0 if language() == 'zh' else 1])
        return W.QMessageBox.StandardButton(dialog.exec())

    @staticmethod
    def question(parent, title, text, buttons=W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.No, defaultButton=W.QMessageBox.StandardButton.NoButton):
        return QMessageBox._show(W.QMessageBox.Icon.Question, parent, title, text, buttons, defaultButton)

    @staticmethod
    def warning(parent, title, text, buttons=W.QMessageBox.StandardButton.Ok, defaultButton=W.QMessageBox.StandardButton.NoButton):
        return QMessageBox._show(W.QMessageBox.Icon.Warning, parent, title, text, buttons, defaultButton)

    @staticmethod
    def information(parent, title, text, buttons=W.QMessageBox.StandardButton.Ok, defaultButton=W.QMessageBox.StandardButton.NoButton):
        return QMessageBox._show(W.QMessageBox.Icon.Information, parent, title, text, buttons, defaultButton)

class QFileDialog(W.QFileDialog):

    @staticmethod
    def getOpenFileName(parent=None, caption='', *args, **kwargs):
        return W.QFileDialog.getOpenFileName(parent, tr(caption), *args, **kwargs)

class QInputDialog(W.QInputDialog):

    @staticmethod
    def getText(parent, title, label, *args, **kwargs):
        return W.QInputDialog.getText(parent, tr(title), tr(label), *args, **kwargs)

def language_picker(parent, preferences, on_change=None):
    """Save immediately without committing any unsaved connection fields."""
    picker = QComboBox(parent)
    picker.setObjectName('languageSelect')
    picker.setAccessibleName('语言')
    picker.addItem('中文', 'zh')
    picker.addItem('English', 'en')
    picker.setCurrentIndex(0 if language() == 'zh' else 1)

    def change(_index):
        value = picker.currentData()
        previous = language()
        try:
            if preferences.load().get('language') != value:
                preferences.save_language(value)
        except (OSError, ValueError) as exc:
            with QSignalBlocker(picker):
                picker.setCurrentIndex(0 if previous == 'zh' else 1)
            if on_change:
                on_change(None, str(exc))
            return
        apply_language(value)
        if on_change:
            on_change(value, None)
    picker.currentIndexChanged.connect(change)
    picker.activated.connect(change)
    return picker
