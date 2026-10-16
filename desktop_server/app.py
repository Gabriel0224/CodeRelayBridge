"""Native server management UI; no terminal, browser automation or model login."""
import sys
from PySide6.QtCore import QThreadPool, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QCheckBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView
from desktop_client.app import STYLE, Work, SingleInstance, app_icon, note, scroll_page
from desktop_client.platform_support import platform_name
from desktop_server.backend import ServerManager, suggested_panel_url
from desktop_client import VERSION
from desktop_client.backend import Preferences, ClientError
from desktop_client.i18n import QAction, QFileDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QCheckBox, QSystemTrayIcon, QTableWidget, QFormLayout, initialize, language_picker, tr, status_table_item
from desktop_client.edition import edition_label

class ServerWindow(QMainWindow):

    def __init__(self, manager=None):
        super().__init__()
        self.manager = manager or ServerManager()
        self.preferences = Preferences(self.manager.root)
        initialize(self.preferences)
        self.setWindowTitle('Relay Server · 服务端管理')
        self.setWindowIcon(app_icon('#3b6aa0'))
        self.resize(980, 720)
        self.setMinimumSize(740, 540)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.busy = False
        self.current = {}
        self.system_dialog = None
        self.update_dialog = None
        self.pending_update = None
        self.tray = None
        main = QWidget()
        main.setObjectName('main')
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        outer.setContentsMargins(24, 24, 24, 18)
        header = QHBoxLayout()
        heading = QLabel('Relay Server')
        heading.setObjectName('heading')
        header.addWidget(heading)
        header.addStretch()
        header.addWidget(note(platform_name() + f' · {edition_label()} v{VERSION}'))
        outer.addLayout(header)
        nav = QHBoxLayout()
        self.pages = QStackedWidget()
        for i, title in enumerate(('服务概览', '连接与首次设置', '日志')):
            b = QPushButton(title)
            b.clicked.connect(lambda checked=False, n=i: self.pages.setCurrentIndex(n))
            nav.addWidget(b)
        nav.addStretch()
        self.update_button = QPushButton('检查 CRB 更新')
        self.update_button.clicked.connect(self.open_relay_update)
        nav.addWidget(self.update_button)
        outer.addLayout(nav)
        overview = QWidget()
        body = QVBoxLayout(overview)
        self.status_label = QLabel('正在读取本机服务状态…')
        self.status_label.setWordWrap(True)
        body.addWidget(self.status_label)
        operations = QHBoxLayout()
        self.controls = []
        for key, label in [('start', '启动服务'), ('pause', '停止服务'), ('restart', '重启服务')]:
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False, k=key: self.control(k))
            self.controls.append(b)
            operations.addWidget(b)
        browser = QPushButton('打开网页面板')
        browser.clicked.connect(self.open_panel)
        operations.addWidget(browser)
        body.addLayout(operations)
        body.addWidget(note('服务端只传递数据；模型账号和原 App 留在客户端电脑。停止或重启会暂时断开网页与终端，不会停止模型任务。'))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['电脑', '应用', '客户端连接', '原 App 连接'])
        self.table.horizontalHeader().setStretchLastSection(True)
        for column in range(3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        body.addWidget(self.table, 1)
        self.pages.addWidget(overview)
        self.pages.addWidget(scroll_page(self.setup_page()))
        logs = QWidget()
        layout = QVBoxLayout(logs)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        layout.addWidget(self.logs)
        log_refresh = QPushButton('刷新日志')
        log_refresh.clicked.connect(lambda: self.submit('log', self.manager.log_tail))
        layout.addWidget(log_refresh)
        self.pages.addWidget(logs)
        outer.addWidget(self.pages, 1)
        self.notice = note('已有服务会直接读取其配置，不覆盖账号和历史。')
        outer.addWidget(self.notice)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu()
            for label, callback in [('打开服务端', self.show_window), ('打开网页面板', self.open_panel), ('退出管理界面', QApplication.instance().quit)]:
                action = QAction(label, menu)
                action.triggered.connect(callback)
                menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        QTimer.singleShot(0, self.refresh)

    def setup_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(note('新机器：填写监听地址、访问地址和网页登录账号即可。默认仅本机监听；可自动配置本程序的系统端口规则，公网 HTTPS 反代仍需独立部署。'))
        form = QFormLayout()
        self.bind = QLineEdit('127.0.0.1')
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(8799)
        self.url = QLineEdit('http://127.0.0.1:8799')
        self.url_overridden = False
        self.bind.textChanged.connect(self.update_panel_url)
        self.port.valueChanged.connect(self.update_panel_url)
        self.url.textEdited.connect(self.panel_url_edited)
        self.username = QLineEdit('relay')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('至少 8 位；只存储密码摘要')
        if self.manager.config_path().exists():
            cfg = self.manager.config()
            self.bind.setText(str(cfg['bind']))
            self.port.setValue(int(cfg['port']))
            self.url.setText(cfg['public_url'])
            self.url_overridden = cfg['public_url'] != suggested_panel_url(cfg['bind'], cfg['port'])
            self.username.setText(cfg.get('username', 'relay'))
            self.password.setPlaceholderText('留空保持现有密码；填写新密码后保存生效')
        for label, field in [('监听 IP', self.bind), ('监听端口', self.port), ('网页访问地址', self.url), ('网页登录账号', self.username), ('网页登录密码', self.password)]:
            form.addRow(label, field)
        layout.addLayout(form)
        layout.addWidget(note('网页访问地址根据监听 IP 和端口自动生成；使用域名或反代时可自行修改。'))
        languages = QHBoxLayout()
        languages.addWidget(QLabel('语言'))
        self.language_select = language_picker(self, self.preferences, lambda value, error: self.notice.setText('保存失败：' + error) if error else None)
        languages.addWidget(self.language_select)
        languages.addStretch()
        layout.addLayout(languages)
        create = QPushButton('保存服务器配置' if self.manager.config_path().exists() else '创建服务器配置')
        self.save_config_button = create
        create.clicked.connect(self.create_config)
        layout.addWidget(create, alignment=Qt.AlignmentFlag.AlignLeft)
        import_button = QPushButton('选择已有服务器配置…')
        import_button.clicked.connect(self.import_config)
        layout.addWidget(import_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.system_auto = QCheckBox('启动时自动配置系统端口与依赖')
        self.system_auto.setChecked(True)
        layout.addWidget(self.system_auto)
        layout.addWidget(note('仅配置本服务端的限定来源规则；必要时请求本机授权，不修改路由器或云安全组。'))
        network = QPushButton('网络与依赖')
        network.clicked.connect(self.open_system_setup)
        layout.addWidget(network, alignment=Qt.AlignmentFlag.AlignLeft)
        migrate = QPushButton('接管旧服务为独立程序')
        migrate.clicked.connect(self.migrate_server)
        layout.addWidget(migrate, alignment=Qt.AlignmentFlag.AlignLeft)
        from desktop_server.install import ServerStartup
        self.startup = ServerStartup()
        self.autostart = QCheckBox('登录后启动服务端管理界面到托盘')
        self.autostart.setChecked(self.startup.enabled())
        self.autostart.clicked.connect(self.set_autostart)
        layout.addWidget(self.autostart)
        layout.addWidget(note('现有 服务器 服务器无需重新配对。选择原 relay.json 后，可管理原服务；不会复制客户端模型凭证。'))
        layout.addStretch()
        return page

    def update_panel_url(self, *_):
        if self.url_overridden:
            return
        try:
            self.url.setText(suggested_panel_url(self.bind.text(), self.port.value()))
        except (ValueError, ClientError):
            pass

    def panel_url_edited(self, value):
        self.url_overridden = bool(value.strip())
        if not self.url_overridden:
            self.update_panel_url()

    def set_autostart(self, enabled):
        from desktop_server.install import install

        def configure():
            executable = install(self.manager.root)
            self.startup.configure(enabled, executable)
            return '管理界面的登录启动已' + ('开启。' if enabled else '关闭。')
        self.submit('startup', configure)

    def migrate_server(self):
        if QMessageBox.question(self, '接管旧服务', '只替换已核验的旧服务启动入口，保留账号、配对、历史和端口。网页会短暂断开，失败自动恢复。继续？') != QMessageBox.StandardButton.Yes:
            return
        from desktop_server.install import install

        def migrate():
            install(self.manager.root)
            return self.manager.migrate()
        self.submit('migrate', migrate)

    def submit(self, key, callback):
        if self.busy:
            return
        self.busy = True
        for b in self.controls:
            b.setEnabled(False)
        job = Work(key, callback)
        job.signals.done.connect(self.done)
        job.signals.failed.connect(self.failed)
        self.pool.start(job)

    def done(self, key, value):
        self.busy = False
        for b in self.controls:
            b.setEnabled(True)
        if key == 'crb-update-stop':
            try:
                from desktop_client.update_apply import schedule
                schedule(self.pending_update)
                QApplication.instance().quit()
            except Exception as error:
                self.timer.start(5000)
                self.update_dialog.failed('install', str(error))
                if value:
                    self.submit('restore-after-update', lambda: self.manager.control('start'))
            return
        if key == 'status':
            self.current = value
            if not value['configured']:
                self.status_label.setText('尚未配置服务器，请进入“连接与首次设置”。')
            else:
                self.status_label.setText(('运行中' if value['running'] else '未运行 / 暂时不可达') + '\n' + value['url'] + '\n配置：' + value['config'])
            hosts = value.get('hosts', [])
            self.table.setRowCount(len(hosts))
            for row, host in enumerate(hosts):
                fields = [host.get('label') or host['id'], host.get('applicationId') or 'codex', '在线' if host.get('online') else '离线', '已连接' if host.get('desktopConnected') else '未连接']
                for col, text in enumerate(fields):
                    self.table.setItem(row, col, status_table_item(str(text)) if col >= 2 else QTableWidgetItem(str(text)))
        elif key == 'log':
            self.logs.setPlainText(str(value))
        else:
            if key == 'import':
                cfg = self.manager.config()
                self.bind.setText(str(cfg['bind']))
                self.port.setValue(int(cfg['port']))
                self.url.setText(cfg['public_url'])
                self.url_overridden = cfg['public_url'] != suggested_panel_url(cfg['bind'], cfg['port'])
                self.username.setText(cfg.get('username', 'relay'))
                self.password.clear()
                self.password.setPlaceholderText('留空保持现有密码；填写新密码后保存生效')
                self.save_config_button.setText('保存服务器配置')
            if key == 'create':
                self.password.clear()
                self.password.setPlaceholderText('留空保持现有密码；填写新密码后保存生效')
                self.save_config_button.setText('保存服务器配置')
            self.notice.setText(str(value))
            self.refresh()

    def failed(self, key, error):
        self.busy = False
        for b in self.controls:
            b.setEnabled(True)
        self.notice.setText(error)
        if key == 'crb-update-stop':
            self.timer.start(5000)
            self.update_dialog.failed('install', error)
        if key == 'startup':
            self.autostart.setChecked(self.startup.enabled())

    def refresh(self):
        self.submit('status', self.manager.status)

    def open_relay_update(self):
        from desktop_client.update_ui import UpdateDialog
        if not self.update_dialog:
            self.update_dialog = UpdateDialog(self, 'server')
            self.update_dialog.installRequested.connect(self.apply_relay_update)
        self.update_dialog.show()
        self.update_dialog.raise_()

    def apply_relay_update(self, plan_file):
        if self.busy:
            self.update_dialog.failed('install', '服务操作仍在进行，请稍后重新更新。')
            return
        self.timer.stop()
        self.pending_update = plan_file

        def stop():
            from pathlib import Path
            from desktop_client.connections import atomic_json
            import json
            running = self.manager.status().get('running', False)
            plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
            plan['resumeServer'] = bool(running)
            atomic_json(Path(plan_file), plan)
            if running:
                self.manager.control('pause')
            return running
        self.submit('crb-update-stop', stop)

    def control(self, key):
        if key in {'pause', 'restart'} and QMessageBox.question(self, '服务操作', '这会暂时断开面板连接与已打开的网页终端。原桌面 App 与任务不停止。继续？') != QMessageBox.StandardButton.Yes:
            return
        automatic = self.system_auto.isChecked()

        def operation():
            if automatic and key in {'start', 'restart'}:
                from desktop_client.system_setup import plan, repair
                cfg = self.manager.config()
                repair(plan('server', bind=cfg['bind'], port=cfg['port']))
            return self.manager.control(key)
        self.submit(key, operation)

    def open_system_setup(self):
        if self.busy:
            return
        from desktop_client.system_setup_ui import SystemSetupDialog

        def configuration():
            cfg = self.manager.config() if self.current.get('configured') else {'bind': self.bind.text(), 'port': self.port.value()}
            return {'bind': cfg['bind'], 'port': cfg['port']}
        self.system_dialog = SystemSetupDialog(self, 'server', configuration)
        self.system_dialog.show()

    def create_config(self):
        if self.busy:
            return
        if self.current.get('running') and QMessageBox.question(self, '保存服务器配置', '保存后会重启中继服务以应用配置，网页和终端将短暂断开。原桌面 App 与任务不停止。继续？') != QMessageBox.StandardButton.Yes:
            return
        args = (self.bind.text(), self.port.value(), self.url.text(), self.username.text(), self.password.text())
        self.submit('create', lambda: self.manager.save(*args))

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Relay 服务配置', '', 'JSON (*.json)')
        if path:
            self.submit('import', lambda: self.manager.use_existing(path))

    def open_panel(self):
        if self.current.get('url'):
            QDesktopServices.openUrl(QUrl(self.current['url']))

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.timer.setInterval(5000)

    def closeEvent(self, event):
        if self.tray:
            event.ignore()
            self.hide()
            self.timer.setInterval(30000)
        else:
            event.accept()
            QApplication.instance().quit()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Relay Server')
    app.setOrganizationName('CodexRelay')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    single = SingleInstance()
    single.name = single.name.replace('RelayClient-', 'RelayServer-')
    if not single.claim(show='--tray' not in sys.argv):
        return 0
    window = ServerWindow()
    single.window = window
    if '--tray' not in sys.argv or not window.tray:
        window.show_window()
    return app.exec()
