"""Shared close choice for the two native management windows."""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout
from desktop_client.i18n import QCheckBox, QLabel, QPushButton, tr

class CloseChoiceDialog(QDialog):

    def __init__(self, parent, role, tray_available, to_tray=False):
        super().__init__(parent)
        self.setWindowTitle(tr('关闭窗口'))
        self.setMinimumWidth(420)
        self.choice = None
        body = QVBoxLayout(self)
        message = QLabel('直接关闭将退出客户端并断开接入，配对与原 App 保留。' if role == 'client' else '直接关闭只退出管理界面，服务端继续运行。')
        message.setWordWrap(True)
        body.addWidget(message)
        self.remember = QCheckBox('保留这个状态，不再询问')
        body.addWidget(self.remember)
        row = QHBoxLayout()
        self.close_button = QPushButton('直接关闭')
        self.tray_button = QPushButton('最小化到托盘')
        self.tray_button.setEnabled(tray_available)
        if not tray_available:
            self.tray_button.setToolTip(tr('当前系统没有可用托盘'))
            unavailable = QLabel('当前系统没有可用托盘')
            body.addWidget(unavailable)
        self.close_button.clicked.connect(lambda: self.choose('close'))
        self.tray_button.clicked.connect(lambda: self.choose('tray'))
        row.addWidget(self.close_button)
        row.addWidget(self.tray_button)
        body.addLayout(row)
        (self.tray_button if to_tray and tray_available else self.close_button).setDefault(True)

    def choose(self, action):
        if action == 'tray' and (not self.tray_button.isEnabled()):
            return
        self.choice = action
        self.accept()

def choose_window_close(parent, preferences, role, tray_available, settings=None):
    """Return a decision or None on Escape/X; persist only an explicit remember."""
    saved = dict(settings if settings is not None else preferences.load())
    to_tray = saved.get('close_to_tray') is True
    if saved.get('ask_on_close', True) is False and (not to_tray or tray_available):
        return 'tray' if to_tray else 'close'
    dialog = CloseChoiceDialog(parent, role, tray_available, to_tray)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.choice not in ('close', 'tray'):
            return None
        if dialog.remember.isChecked():
            data = preferences.load()
            data.update(ask_on_close=False, close_to_tray=dialog.choice == 'tray')
            preferences.save(data)
        return dialog.choice
    finally:
        dialog.deleteLater()
