"""Non-blocking, explicit GitHub checks and installations in native Qt UIs."""
from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout
from desktop_client.app import Work
from desktop_client.relay_updates import ReleaseUpdater, GitHubHTTP, TokenStore, UpdateError
from desktop_client.i18n import QCheckBox, QDialog, QLabel, QLineEdit, QMessageBox, QPushButton

class UpdateDialog(QDialog):
    installRequested = Signal(str)

    def __init__(self, parent, role='client', updater=None):
        super().__init__(parent)
        self.updater = updater or ReleaseUpdater(role)
        self.setWindowTitle('CRB · GitHub 更新')
        self.resize(520, 370)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.job = None
        self.release = None
        self.plan = None
        layout = QVBoxLayout(self)
        self.info = QLabel('当前版本 ' + self.updater.current + '\n仓库：' + self.updater.repo)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.status = QLabel('点击检查更新。不会自动停止模型 App 或执行下载脚本。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.check_button = QPushButton('检查更新')
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        self.install_button = QPushButton('下载并更新')
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        row.addWidget(self.install_button)
        layout.addLayout(row)
        dismiss = QPushButton('关闭')
        dismiss.clicked.connect(self.close)
        layout.addWidget(dismiss)
        layout.addWidget(QLabel('更新会短暂断开本机面板接入；配对、账号和历史保留。\n签名、哈希、系统和版本类型均校验通过后才替换。'))

    def submit(self, name, callback):
        if self.job:
            return
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.job = Work(name, callback)
        self.job.signals.done.connect(self.done)
        self.job.signals.failed.connect(self.failed)
        self.pool.start(self.job)

    def check(self):
        self.release = None
        entered = self.token.text().strip()
        self.status.setText('正在读取 GitHub 正式 Release…')

        def operation():
            return self.updater.check()
        self.submit('check', operation)

    def install(self):
        if not self.release or self.job:
            return
        if QMessageBox.question(self, '更新 CRB', '下载校验完成后将关闭并重启本机 Relay；网页连接和面板终端会短暂断开。\n原 Codex / DeepSeek App 与任务不停止。继续？') != QMessageBox.StandardButton.Yes:
            return
        self.status.setText('正在下载并校验完整安装包…')
        self.progress.setRange(0, 0)
        self.progress.show()

        def operation():
            from desktop_client.update_apply import build_plan
            return str(build_plan(self.updater.prepare(self.release)))
        self.submit('prepare', operation)

    def done(self, name, value):
        self.job = None
        self.enable()
        self.progress.hide()
        if name == 'check':
            self.release = value
            self.install_button.setEnabled(bool(value['available']))
            self.status.setText('发现新版本 ' + value['latest'] if value['available'] else '当前已是最新正式版本（' + value['current'] + '）。')
        elif name == 'forget':
            self.status.setText('已移除本机保存的 GitHub 更新授权。')
        elif name == 'prepare':
            self.plan = value
            self.status.setText('校验通过，正在请求本机 Relay 完全退出并更新…')
            self.installRequested.emit(value)

    def enable(self):
        self.check_button.setEnabled(True)

    def failed(self, name, message):
        self.job = None
        self.enable()
        self.progress.hide()
        self.status.setText(message)
        self.install_button.setEnabled(bool(self.release and self.release.get('available')))

    def closeEvent(self, event):
        if self.job:
            self.status.setText('操作仍在进行，请等待完成后关闭。')
            event.ignore()
        else:
            event.accept()
