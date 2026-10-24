"""Background discovery and explicit, cancellable updates for both native roles."""
from threading import Event
from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QProgressBar, QVBoxLayout
from desktop_client.app import Work
from desktop_client.update_channels import ChannelSettings, ChannelUpdater
from desktop_client.relay_updates import TokenStore, UpdateError, UpdateCancelled
from desktop_client.i18n import QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QMessageBox, QPushButton

class UpdateDialog(QDialog):
    installRequested = Signal(str)
    downloadProgress = Signal(object, object)
    stageChanged = Signal(str)
    INITIAL_CHECK_MS = 20000
    CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
    BUSY_RETRY_MS = 60000

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
        self.background_request = False
        self.background_timer = None
        self.monitor_stopped = False
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
        self.check_button = QPushButton('检查更新')
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        self.tokens = {}
        self.remember = {}
        self.credential_status = {}
        self.saved_credentials = {}
        for provider in ('github', 'gitee'):
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText('GitHub 令牌：私有仓库 Contents 只读权限' if provider == 'github' else 'Gitee 令牌：私有仓库读取权限')
            keep = QCheckBox('将令牌保存到系统密钥库（不写入配置或日志）')
            saved = QLabel('正在检测已保存的授权…')
            saved.setWordWrap(True)
            layout.addWidget(field)
            layout.addWidget(keep)
            layout.addWidget(saved)
            self.tokens[provider], self.remember[provider] = (field, keep)
            self.credential_status[provider] = saved
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
        self.skip_button = QPushButton('跳过此版本')
        self.skip_button.setToolTip('不再提醒此版本；以后有更新版本时仍会提醒。')
        self.skip_button.setEnabled(False)
        self.skip_button.clicked.connect(self.skip)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        self.cancel_button = QPushButton('取消')
        self.cancel_button.setToolTip('本次关闭，下次检查到此版本时仍会提醒。')
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        self.install_button = QPushButton('升级')
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        row.addWidget(self.install_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.forget_button = QPushButton('移除所选通道授权')
        self.forget_button.setVisible(self.updater.requires_auth)
        self.forget_button.clicked.connect(self.forget)
        row.addWidget(self.forget_button)
        layout.addLayout(row)
        note = QLabel('更新会短暂断开本机面板接入；配对、账号和历史保留。\n签名、哈希、系统和版本类型均校验通过后才替换。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.downloadProgress.connect(self.show_progress)
        self.stageChanged.connect(self.show_stage)
        self.auth_visibility()
        self.load_credentials()

    def start_background_checks(self):
        if self.background_timer is not None:
            return
        self.background_timer = QTimer(self)
        self.background_timer.setSingleShot(True)
        self.background_timer.timeout.connect(self.background_check)
        QApplication.instance().aboutToQuit.connect(self.stop_background_checks)
        self.background_timer.start(self.INITIAL_CHECK_MS)

    def stop_background_checks(self):
        self.monitor_stopped = True
        if self.background_timer:
            self.background_timer.stop()
        if self.job:
            self.cancelled.set()

    def background_check(self):
        parent = self.parentWidget()
        if self.monitor_stopped or (parent and (getattr(parent, 'exiting', False) or getattr(parent, 'shutdown_mode', None))):
            return
        if self.job or self.isVisible() or (parent and (getattr(parent, 'busy', False) or getattr(parent, 'busy_key', None))):
            self.background_timer.start(self.BUSY_RETRY_MS)
            return
        self.check(automatic=True)

    def show_manual(self):
        self.background_request = False
        self.closing = False
        self.show()
        self.raise_()
        self.activateWindow()

    def clear_authorization(self):
        self.updater.tokens.clear()
        for source in self.updater.checked.values():
            if hasattr(source.http, '_token'):
                source.http._token = ''
        self.updater.checked.clear()

    def load_credentials(self):
        """Only return presence, never place a stored secret into UI state."""
        if not self.updater.requires_auth or self.job:
            return
        requested = self.requested_channels()

        def operation():
            result = {}
            for provider in requested:
                try:
                    result[provider] = bool(TokenStore(provider).get(self.updater.repo))
                except UpdateError:
                    result[provider] = None
            return result
        self.submit('credentials', operation)

    def show_credentials(self, states):
        for provider, saved in states.items():
            self.saved_credentials[provider] = saved
            self.remember[provider].setChecked(saved is True)
            self.credential_status[provider].setText('已保存授权，留空即可自动使用；填写新令牌可替换。' if saved is True else '没有已保存的授权；填写令牌并勾选保存后检查更新。' if saved is False else '无法读取系统密钥库；可填写令牌仅供本次使用。')

    def requested_channels(self):
        value = self.channel.currentData()
        return ('github', 'gitee') if value == 'auto' else (value,)

    def auth_visibility(self):
        for provider, field in self.tokens.items():
            visible = self.updater.requires_auth and provider in self.requested_channels()
            field.setVisible(visible)
            self.remember[provider].setVisible(visible)
            self.credential_status[provider].setVisible(visible)

    def channel_changed(self):
        self.release = None
        self.install_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.version_notice.setText('')
        self.auth_visibility()
        try:
            self.settings.save(self.channel.currentData())
            self.status.setText('更新通道已保存，请检查更新。')
        except (UpdateError, OSError):
            self.status.setText('无法保存通道选择；本次选择仍有效。')
        if any((provider not in self.saved_credentials for provider in self.requested_channels())):
            self.load_credentials()

    def submit(self, name, callback):
        if self.job:
            return
        self.cancelled.clear()
        self.job = Work(name, callback)
        self.job.signals.done.connect(self.done)
        self.job.signals.failed.connect(self.failed)
        self.enable(False)
        self.pool.start(self.job)

    def check(self, checked=False, *, automatic=False):
        if self.job:
            return
        self.background_request = automatic
        self.closing = False
        if self.background_timer:
            self.background_timer.stop()
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
            states = {}
            warnings = []
            if self.updater.requires_auth:
                for provider in requested:
                    token = entered.get(provider)
                    if not token:
                        try:
                            token = TokenStore(provider).get(self.updater.repo)
                            states[provider] = bool(token)
                        except UpdateError as error:
                            token = ''
                            states[provider] = None
                            warnings.append(str(error))
                    if token:
                        self.updater.tokens[provider] = token
            result = self.updater.check(selected, cancelled=self.cancelled.is_set, probe=not automatic)
            for provider in requested if self.updater.requires_auth else ():
                if entered.get(provider) and remember[provider] and (provider in self.updater.checked):
                    if self.cancelled.is_set():
                        raise UpdateCancelled('更新已取消，原程序未改变。')
                    try:
                        TokenStore(provider).save(self.updater.repo, entered[provider])
                        states[provider] = True
                    except UpdateError as error:
                        warnings.append(str(error))
            result['credentialWarnings'] = warnings
            result['savedCredentials'] = states
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
            self.closing = True
            self.cancelled.set()
            self.cancel_button.setEnabled(False)
            self.status.setText('正在取消，请等待当前网络或校验步骤结束…')
        else:
            self.release = None
            self.install_button.setEnabled(False)
            self.status.setText('本次更新已取消；可稍后重新检查。')
            self.close()

    def skip(self):
        if self.job or not self.release or (not self.release.get('available')):
            return
        try:
            self.settings.skip_version(self.updater.repo, self.release['latest'])
        except (OSError, UpdateError):
            self.status.setText('无法保存跳过版本，请重试。')
            return
        self.release = None
        self.close()

    def reject(self):
        self.cancel()

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
        automatic = self.background_request and name == 'check'
        if name == 'check' and self.background_timer and (not self.monitor_stopped):
            self.background_timer.start(self.CHECK_INTERVAL_MS)
        self.background_request = False
        self.enable(True)
        self.progress.hide()
        if self.cancelled.is_set():
            self.status.setText('更新已取消，原程序未改变。')
        elif name == 'credentials':
            self.show_credentials(value)
        elif name == 'check':
            self.show_credentials(value.get('savedCredentials', {}))
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
            if automatic:
                if value['available'] and (not self.settings.is_skipped(self.updater.repo, value['latest'])):
                    self.show()
                    self.raise_()
                    self.activateWindow()
                else:
                    self.release = None
                    self.clear_authorization()
        elif name == 'forget':
            self.show_credentials({provider: False for provider in self.requested_channels()})
            self.status.setText('已移除所选通道的本机更新授权。')
        elif name == 'prepare':
            self.plan = value
            self.status.setText('校验通过，正在请求本机 Relay 完全退出并更新…')
            self.installRequested.emit(value)
        self.install_button.setEnabled(bool(self.release and self.release.get('available')))
        self.skip_button.setEnabled(self.install_button.isEnabled())
        if self.closing:
            self.close()

    def enable(self, enabled):
        for field in [self.check_button, self.forget_button, self.channel, *self.tokens.values(), *self.remember.values()]:
            field.setEnabled(enabled)
        self.cancel_button.setEnabled(True)
        self.install_button.setEnabled(enabled and bool(self.release and self.release.get('available')))
        self.skip_button.setEnabled(self.install_button.isEnabled())

    def failed(self, name, message):
        self.job = None
        self.enable(True)
        self.progress.hide()
        self.status.setText('更新已取消，原程序未改变。' if self.cancelled.is_set() else message)
        if name == 'check':
            self.background_request = False
            if self.background_timer and (not self.monitor_stopped):
                self.background_timer.start(self.CHECK_INTERVAL_MS)
            self.clear_authorization()
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.job:
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            self.clear_authorization()
            self.release = None
            self.install_button.setEnabled(False)
            self.skip_button.setEnabled(False)
            for field in self.tokens.values():
                field.clear()
            event.accept()
