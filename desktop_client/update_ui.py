"""Explicit, cancellable signed updates shared by the two native roles."""
from threading import Event
from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout
from desktop_client.app import Work
from desktop_client.update_channels import ChannelSettings, ChannelUpdater
from desktop_client.relay_updates import TokenStore, UpdateError, UpdateCancelled
from desktop_client.i18n import QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QMessageBox, QPushButton

class UpdateDialog(QDialog):
    installRequested = Signal(str)
    downloadProgress = Signal(object, object)
    stageChanged = Signal(str)

    def __init__(self, parent, role='client', updater=None, settings=None):
        super().__init__(parent)
        self.updater = updater or ChannelUpdater(role)
        self.settings = settings or ChannelSettings(role)
        self.setWindowTitle('CRB · 程序更新')
        self.resize(580, 440)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.job = self.release = self.plan = None
        self.cancelled = Event()
        self.closing = False
        layout = QVBoxLayout(self)
        self.info = QLabel('当前版本 ' + self.updater.current + '\n仓库：' + self.updater.repo)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        row = QHBoxLayout()
        row.addWidget(QLabel('更新通道'))
        self.channel = QComboBox()
        for label, value in [('自动选择（限量测速）', 'auto'), ('GitHub', 'github'), ('Gitee', 'gitee')]:
            self.channel.addItem(label, value)
        self.channel.setCurrentIndex(max(0, self.channel.findData(self.settings.load())))
        self.channel.currentIndexChanged.connect(self.channel_changed)
        row.addWidget(self.channel, 1)
        layout.addLayout(row)
        self.tokens = {}
        self.remember = {}
        for provider in ('github', 'gitee'):
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText('GitHub 令牌：私有仓库 Contents 只读权限' if provider == 'github' else 'Gitee 令牌：私有仓库读取权限')
            keep = QCheckBox('将令牌保存到系统密钥库（不写入配置或日志）')
            layout.addWidget(field)
            layout.addWidget(keep)
            self.tokens[provider], self.remember[provider] = (field, keep)
        self.version_notice = QLabel('')
        self.version_notice.setWordWrap(True)
        layout.addWidget(self.version_notice)
        self.status = QLabel('点击检查更新。发现新版本后才会限量测速；确认前不会安装。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.route = QLabel('')
        layout.addWidget(self.route)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.transfer = QLabel('')
        layout.addWidget(self.transfer)
        row = QHBoxLayout()
        self.check_button = QPushButton('检查更新')
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        self.install_button = QPushButton('确认更新')
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        row.addWidget(self.install_button)
        self.cancel_button = QPushButton('取消')
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.forget_button = QPushButton('移除所选通道授权')
        self.forget_button.setVisible(self.updater.requires_auth)
        self.forget_button.clicked.connect(self.forget)
        row.addWidget(self.forget_button)
        dismiss = QPushButton('关闭')
        dismiss.clicked.connect(self.close)
        row.addWidget(dismiss)
        layout.addLayout(row)
        note = QLabel('更新会短暂断开本机面板接入；配对、账号和历史保留。\n签名、哈希、系统和版本类型均校验通过后才替换。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.downloadProgress.connect(self.show_progress)
        self.stageChanged.connect(self.show_stage)
        self.auth_visibility()

    def requested_channels(self):
        value = self.channel.currentData()
        return ('github', 'gitee') if value == 'auto' else (value,)

    def auth_visibility(self):
        for provider, field in self.tokens.items():
            visible = self.updater.requires_auth and provider in self.requested_channels()
            field.setVisible(visible)
            self.remember[provider].setVisible(visible)

    def channel_changed(self):
        self.release = None
        self.install_button.setEnabled(False)
        self.version_notice.setText('')
        self.auth_visibility()
        try:
            self.settings.save(self.channel.currentData())
            self.status.setText('更新通道已保存，请检查更新。')
        except (UpdateError, OSError):
            self.status.setText('无法保存通道选择；本次选择仍有效。')

    def submit(self, name, callback):
        if self.job:
            return
        self.cancelled.clear()
        self.job = Work(name, callback)
        self.job.signals.done.connect(self.done)
        self.job.signals.failed.connect(self.failed)
        self.enable(False)
        self.pool.start(self.job)

    def check(self):
        if self.job:
            return
        self.release = None
        self.version_notice.setText('')
        self.transfer.setText('')
        self.route.setText('')
        selected, requested = (self.channel.currentData(), self.requested_channels())
        entered = {key: field.text().strip() for key, field in self.tokens.items()}
        remember = {key: field.isChecked() for key, field in self.remember.items()}
        for field in self.tokens.values():
            field.clear()
        self.status.setText('正在检查正式版本和可用通道…')

        def operation():
            self.updater.tokens.clear()
            if self.updater.requires_auth:
                for provider in requested:
                    token = entered.get(provider)
                    if not token:
                        try:
                            token = TokenStore(provider).get(self.updater.repo)
                        except UpdateError:
                            token = ''
                    if token:
                        self.updater.tokens[provider] = token
            result = self.updater.check(selected, cancelled=self.cancelled.is_set)
            warnings = []
            for provider in requested if self.updater.requires_auth else ():
                if entered.get(provider) and remember[provider] and (provider in self.updater.checked):
                    if self.cancelled.is_set():
                        raise UpdateCancelled('更新已取消，原程序未改变。')
                    try:
                        TokenStore(provider).save(self.updater.repo, entered[provider])
                    except UpdateError as error:
                        warnings.append(str(error))
            result['credentialWarnings'] = warnings
            return result
        self.submit('check', operation)

    def forget(self):
        if self.job or not self.updater.requires_auth:
            return
        requested = self.requested_channels()

        def operation():
            for provider in requested:
                TokenStore(provider).save(self.updater.repo, '')
            self.updater.tokens.clear()
        for field in self.tokens.values():
            field.clear()
        self.release = None
        self.submit('forget', operation)

    def install(self):
        if not self.release or self.job:
            return
        if QMessageBox.question(self, '更新 CRB', '下载校验完成后将关闭并重启本机 Relay；网页连接和面板终端会短暂断开。\n原 Codex / DeepSeek App 与任务不停止。继续？') != QMessageBox.StandardButton.Yes:
            self.status.setText('本次更新已取消；可稍后重新确认。')
            return
        release = self.release
        self.status.setText('正在下载完整安装包…')
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()

        def operation():
            from desktop_client.update_apply import build_plan
            prepared = self.updater.prepare(release, self.downloadProgress.emit, cancelled=self.cancelled.is_set, status=self.stageChanged.emit)
            if self.cancelled.is_set():
                raise UpdateCancelled('更新已取消，原程序未改变。')
            return str(build_plan(prepared))
        self.submit('prepare', operation)

    def cancel(self):
        if self.job:
            self.cancelled.set()
            self.cancel_button.setEnabled(False)
            self.status.setText('正在取消，请等待当前网络或校验步骤结束…')
        else:
            self.release = None
            self.install_button.setEnabled(False)
            self.status.setText('本次更新已取消；可稍后重新检查。')

    def show_progress(self, received, total):
        if not self.cancelled.is_set():
            self.progress.setValue(min(100, received * 100 // max(1, total)))
            self.transfer.setText('已下载 ' + f'{received / 1048576:.1f} / {total / 1048576:.1f} MiB')

    def show_stage(self, stage):
        if self.cancelled.is_set():
            return
        if stage.startswith('channel:'):
            self.progress.setValue(0)
            self.route.setText('下载通道：' + stage.split(':', 1)[1].replace('github', 'GitHub').replace('gitee', 'Gitee'))
        elif stage in ('download', 'verify', 'extract', 'ready'):
            self.status.setText({'download': '正在下载完整安装包…', 'verify': '正在校验安装包签名与哈希…', 'extract': '正在准备已校验的原生程序…', 'ready': '安装包校验通过。'}[stage])

    def done(self, name, value):
        self.job = None
        self.enable(True)
        self.progress.hide()
        if self.cancelled.is_set():
            self.status.setText('更新已取消，原程序未改变。')
        elif name == 'check':
            self.release = value
            if value['available']:
                self.version_notice.setText('已有新版本 ' + value['latest'])
                selected = value['channel'].replace('github', 'GitHub').replace('gitee', 'Gitee')
                self.status.setText('更新通道：' + selected + '；确认后下载并更新。')
                measurements = value.get('measurements', {})
                self.transfer.setText(' · '.join((key.replace('github', 'GitHub').replace('gitee', 'Gitee') + f" {item['bytesPerSecond'] / 1024:.0f} KiB/s" for key, item in sorted(measurements.items()))))
            else:
                self.version_notice.setText('已是最新版（' + value['current'] + '）。')
                self.status.setText('检查完成，没有可安装的新版本。')
            warnings = list(value.get('credentialWarnings', []))
            if value.get('channelErrors'):
                warnings.append('部分通道不可用，已使用可用通道。')
            if warnings:
                self.status.setText(self.status.text() + '\n' + '\n'.join(warnings))
        elif name == 'forget':
            self.status.setText('已移除所选通道的本机更新授权。')
        elif name == 'prepare':
            self.plan = value
            self.status.setText('校验通过，正在请求本机 Relay 完全退出并更新…')
            self.installRequested.emit(value)
        self.install_button.setEnabled(bool(self.release and self.release.get('available')))
        if self.closing:
            self.close()

    def enable(self, enabled):
        for field in [self.check_button, self.forget_button, self.channel, *self.tokens.values(), *self.remember.values()]:
            field.setEnabled(enabled)
        self.cancel_button.setEnabled(True)
        self.install_button.setEnabled(enabled and bool(self.release and self.release.get('available')))

    def failed(self, name, message):
        self.job = None
        self.enable(True)
        self.progress.hide()
        self.status.setText('更新已取消，原程序未改变。' if self.cancelled.is_set() else message)
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.job:
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            self.updater.tokens.clear()
            for source in self.updater.checked.values():
                if hasattr(source.http, '_token'):
                    source.http._token = ''
            self.updater.checked.clear()
            for field in self.tokens.values():
                field.clear()
            event.accept()
