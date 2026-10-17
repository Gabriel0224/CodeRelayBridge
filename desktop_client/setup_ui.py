"""Beginner-facing enrollment dialog; credentials never enter preferences or logs."""
import socket
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from desktop_client.setup import EnrollmentClient
from desktop_client.backend import Preferences, read_public_config, native_app_running
from desktop_client.i18n import QCheckBox, QDialog, QLabel, QLineEdit, QPushButton

class SetupDialog(QDialog):

    def __init__(self, owner, service):
        super().__init__(owner)
        from desktop_client.app import note, scroll_page, foldout
        self.owner, self.service, self.working = (owner, service, False)
        self.setWindowTitle('配对 / 添加应用')
        self.setMinimumSize(520, 430)
        self.resize(640, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        heading = QLabel('接入这台电脑')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        layout.addWidget(note('填写网页登录信息，客户端自动建立配对和后台启动。'))
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(10)
        self.name = QLineEdit(socket.gethostname())
        self.name.setObjectName('setup_name')
        base = owner.settings['panel_url'].rstrip('/')
        for spec in owner.manager.specs.values():
            try:
                base = read_public_config(spec)['relay_url']
                break
            except (OSError, ValueError):
                pass
        self.server = QLineEdit(base)
        self.server.setObjectName('setup_server')
        self.server.setPlaceholderText('填写服务器地址；通常与网页面板地址相同')
        self.panel = QLineEdit(owner.settings['panel_url'])
        self.panel.setObjectName('setup_panel')
        self.panel.setPlaceholderText('填写你自己的面板地址，例如 https://relay.example.com')
        self.server_dirty = False
        self.server.textChanged.connect(lambda _: setattr(self, 'server_dirty', True))
        self.panel.textChanged.connect(self.sync_default_server)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setObjectName('setup_password')
        self.password.setPlaceholderText('与网页登录面板相同的密码')
        self.username = QLineEdit('relay')
        self.username.setObjectName('setup_username')
        for caption, field in (('电脑名称', self.name), ('网页面板地址', self.panel), ('网页登录账号', self.username), ('网页登录密码', self.password)):
            body.addWidget(QLabel(caption))
            body.addWidget(field)
        body.addWidget(note('不是 Windows / SSH 密码或 DeepSeek API Key。密码只在此次配对使用，不会保存。'))
        self.system_auto = QCheckBox('首次接入自动检查网络与依赖')
        self.system_auto.setChecked(True)
        body.addWidget(self.system_auto)
        body.addWidget(note('需要修复系统规则或图形依赖时，会请求本机授权；不会保存系统密码。'))
        body.addWidget(QLabel('选择接入的应用'))
        self.apps = {}
        missing = set(service.missing())
        processes = owner.manager.processes()
        for key, spec in owner.manager.specs.items():
            checkbox = QCheckBox(spec.title + ('（已接入，保持不变）' if key not in missing else ''))
            checkbox.setObjectName('setup_' + key)
            checkbox.setEnabled(key in missing)
            checkbox.setChecked(key in missing and (key == 'codex' or native_app_running(key, processes)))
            body.addWidget(checkbox)
            self.apps[key] = checkbox
        self.advanced_toggle, self.advanced_body, advanced = foldout(body, '高级：后台服务器地址')
        advanced.addWidget(QLabel('后台服务器地址'))
        advanced.addWidget(self.server)
        advanced.addWidget(note('通常与面板地址相同；只有已有部署使用独立后台地址时才修改。公网使用 HTTPS；100.x 地址需要连接同一 Tailscale。'))
        body.addWidget(note('只连接你信任的服务器。配对后，面板用户可访问本机文件、运行终端及管理所选应用；电脑需已安装对应桌面 App。'))
        self.result = QLabel('准备好后点击“配对并接入”。')
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.result)
        body.addStretch()
        self.scroll = scroll_page(content)
        layout.addWidget(self.scroll, 1)
        buttons = QHBoxLayout()
        self.check = QPushButton('检查服务器')
        self.check.setObjectName('setup_check')
        self.check.clicked.connect(self.check_server)
        buttons.addWidget(self.check)
        self.network = QPushButton('网络与依赖')
        self.network.clicked.connect(owner.open_system_setup)
        buttons.addWidget(self.network)
        buttons.addStretch()
        self.cancel = QPushButton('取消')
        self.cancel.setObjectName('setup_cancel')
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        self.connect_button = QPushButton('配对并接入')
        self.connect_button.setObjectName('primary')
        self.connect_button.clicked.connect(self.connect_client)
        self.connect_button.setEnabled(bool(missing))
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)

    def sync_default_server(self, value):
        if self.server_dirty:
            return
        blocked = self.server.blockSignals(True)
        try:
            self.server.setText(value)
        finally:
            self.server.blockSignals(blocked)

    def busy(self, value):
        self.working = value
        for field in (self.name, self.server, self.panel, self.username, self.password, self.check, self.network, self.cancel):
            field.setEnabled(not value)
        self.connect_button.setEnabled(not value and bool(self.service.missing()))
        for key, checkbox in self.apps.items():
            checkbox.setEnabled(not value and key in self.service.missing())

    def check_server(self):
        base = self.server.text()
        self.busy(True)
        self.result.setText('正在检查服务器配对接口…')
        self.owner.submit('setup-check', lambda: (EnrollmentClient(base).info(), '服务器可达，支持配对。请填写网页登录密码。')[1])

    def connect_client(self):
        if self.owner.busy_key or self.working:
            return
        try:
            panel = Preferences.validate_url(self.panel.text())
        except Exception as exc:
            self.result.setText(str(exc))
            return
        apps = [key for key, checkbox in self.apps.items() if checkbox.isEnabled() and checkbox.isChecked()]
        base, name, password, username = (self.server.text(), self.name.text(), self.password.text(), self.username.text())
        self.password.clear()
        self.busy(True)
        self.owner.busy_key = 'setup'
        self.owner.set_controls_busy(True)
        self.result.setText('正在授权配对、建立客户端接入并检查同步…\n请稍候，不要重复提交。')
        automatic = self.system_auto.isChecked()

        def run():
            if automatic and getattr(sys, 'frozen', False) and (self.owner.session is not None):
                from desktop_client.system_setup import plan, repair
                repair(plan('client', url=base))
            result = self.service.connect(base, name, apps, password, username)
            self.owner.preferences.save({'panel_url': panel})
            return result
        self.owner.submit('setup-connect', run)

    def completed(self, name, result, failed=False):
        self.busy(False)
        self.result.setText(('未完成：' if failed else '') + result)
        if name == 'setup-connect' and (not failed):
            self.password.clear()
            self.accept()
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.result))

    def reject(self):
        if not self.working:
            self.password.clear()
            super().reject()

class UnpairDialog(QDialog):
    """Explicit per-application revoke, with honest offline-only fallback."""

    def __init__(self, owner, service, key):
        super().__init__(owner)
        from desktop_client.app import note, scroll_page
        self.owner, self.service, self.key = (owner, service, key)
        self.working = False
        self.completed_success = False
        title = owner.manager.specs[key].title
        self.setWindowTitle('解除 ' + title + ' 配对')
        self.resize(570, 470)
        self.setMinimumSize(520, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        heading = QLabel('解除 ' + title + ' 配对？')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(14)
        body.addWidget(note('只处理这台电脑的 ' + title + ' 桥接，不影响其他应用或其他电脑。'))
        body.addWidget(note('将停止本应用的后台同步、关闭其网页终端、移除启动任务，并向服务器撤销连接授权。再次使用需重新配对。'))
        body.addWidget(note('不会退出或重启桌面 App，不删除聊天、项目和文件。解除配对使用已保存的连接凭证，不需要再输入网页登录密码。'))
        self.local_only = QCheckBox('仅断开本机，不撤销服务器授权')
        body.addWidget(self.local_only)
        body.addWidget(note('仅在服务器不可达时使用。此方式不等于完全解除配对，后续可在配对管理中补撤销授权。'))
        self.result = note('确认后才会执行；取消不会改变连接。')
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.result)
        body.addStretch()
        self.scroll = scroll_page(content)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel = QPushButton('取消')
        self.cancel.setDefault(True)
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.cancel)
        self.confirm = QPushButton('解除配对')
        self.confirm.setObjectName('danger')
        self.confirm.setAutoDefault(False)
        self.confirm.clicked.connect(self.disconnect)
        row.addWidget(self.confirm)
        layout.addLayout(row)

    def disconnect(self):
        if self.working or self.completed_success or self.owner.busy_key:
            return
        local_only = self.local_only.isChecked()
        self.working = True
        for control in (self.cancel, self.confirm, self.local_only):
            control.setEnabled(False)
        self.result.setText('正在断开本机并' + ('保留远端授权…' if local_only else '撤销服务器授权…'))
        self.owner.busy_key = self.key
        self.owner.set_controls_busy(True)
        self.owner.submit('unpair', lambda: self.service.disconnect(self.key, local_only))

    def completed(self, result, failed=False):
        self.working = False
        self.completed_success = not failed
        self.result.setText(('未完成：' if failed else '') + result)
        self.cancel.setText('关闭' if self.completed_success else '取消')
        self.cancel.setEnabled(True)
        self.confirm.setEnabled(failed)
        self.local_only.setEnabled(failed)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.result))

    def reject(self):
        if not self.working:
            super().reject()
