from __future__ import annotations
import hashlib
import importlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget, QToolButton, QSizePolicy
from desktop_client import VERSION
from desktop_client.edition import edition_label
from desktop_client.backend import BridgeManager, ClientError, Preferences, StartupShortcut, redact
from desktop_client.connections import ConnectionStore
from desktop_client.platform_support import platform_name
from desktop_client.i18n import QAction, QLabel, QPushButton, QCheckBox, QComboBox, QLineEdit, QToolButton, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, initialize, language_picker, tr
from crb.i18n.ui_i18n import language
STYLE = "\nQWidget { color: #243442; font-family: 'Microsoft YaHei UI'; font-size: 13px; }\nQMainWindow, #main { background: #f4f6f8; }\n#sidebar { background: #fff; border-right: 1px solid #e2e7ec; }\nQLabel#brand { color: #186957; font-size: 20px; font-weight: 700; }\nQLabel#heading { font-size: 24px; font-weight: 700; }\nQLabel#muted { color: #667785; }\nQLabel#status { font-size: 13px; font-weight: 600; color: #186957; background: #e6f3ed; border-radius: 6px; padding: 5px 9px; }\nQFrame#card { background: white; border: 1px solid #e0e6ec; border-radius: 12px; }\nQLabel#cardTitle { font-size: 17px; font-weight: 700; }\nQPushButton { background: #fff; border: 1px solid #d4dde5; border-radius: 7px;\n  padding: 8px 12px; min-height: 20px; }\nQPushButton:hover { background: #f0f5f7; border-color: #7fa99d; }\nQPushButton:pressed { background: #e4eeea; }\nQPushButton:disabled { color: #8795a0; background: #f5f7f9; border-color: #e4e9ee; }\nQPushButton#primary { color: white; background: #207861; border: 1px solid #207861; }\nQPushButton#primary:hover { background: #18604d; }\nQPushButton#danger { color: #b63f3f; border-color: #e7bdbd; }\nQPushButton#danger:hover { background: #fff0f0; }\nQPushButton#nav { text-align: left; border: none; background: transparent; padding: 11px 14px; }\nQPushButton#nav:checked { background: #e5f2ec; color: #186957; font-weight: 700; }\nQLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #cbdcd2;\n border-radius: 7px; padding: 8px; selection-background-color: #c6e3d2; min-height: 20px; }\nQPlainTextEdit { font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; }\nQLineEdit:read-only { background: #f4f6f8; color: #536675; }\nQCheckBox { spacing: 10px; padding: 6px 0; }\nQMenu { background: white; border: 1px solid #cadbd0; padding: 6px; }\nQMenu::item { padding: 7px 20px; }\nQMenu::item:selected { background: #dcebe2; }\nQMenu::item:disabled { color: #8795a0; }\nQScrollArea { border: none; background: transparent; }\nQScrollArea > QWidget > QWidget { background: transparent; }\nQToolButton { color: #526c7a; border: none; padding: 7px 0; text-align: left; }\nQToolButton:hover { color: #186957; }\n"

def scroll_page(content):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content.layout().setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
    scroll.setWidget(content)
    return scroll

def foldout(layout, title):
    toggle = QToolButton()
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    toggle.setArrowType(Qt.ArrowType.RightArrow)
    content = QWidget()
    body = QVBoxLayout(content)
    body.setContentsMargins(0, 3, 0, 6)
    body.setSpacing(10)
    content.hide()

    def change(value):
        toggle.setArrowType(Qt.ArrowType.DownArrow if value else Qt.ArrowType.RightArrow)
        content.setVisible(value)
    toggle.toggled.connect(change)
    layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(content)
    return (toggle, content, body)

def note(text):
    label = QLabel(text)
    label.setObjectName('muted')
    label.setWordWrap(True)
    return label

def load_offscreen_fonts():
    if QApplication.platformName() == 'offscreen' and (not QFontDatabase.families()):
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf', 'consola.ttf'):
            path = Path('C:/Windows/Fonts') / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

def app_icon(color='#2f7959'):
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(3, 3, 58, 58, 16, 16)
    painter.setPen(QColor('white'))
    painter.setFont(QFont('Segoe UI', 28, QFont.Weight.Bold))
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, 'R')
    painter.end()
    return QIcon(pix)

class WorkSignals(QObject):
    done = Signal(str, object)
    failed = Signal(str, str)

class Work(QRunnable):

    def __init__(self, name, callback):
        super().__init__()
        self.name, self.callback = (name, callback)
        self.signals = WorkSignals()

    def run(self):
        try:
            self.signals.done.emit(self.name, self.callback())
        except Exception as exc:
            logging.warning('%s: %s', self.name, redact(str(exc)))
            self.signals.failed.emit(self.name, redact(str(exc)))

def bridge_status_text(item):
    running = item.get('instances', 0) > 0
    synced = running and item.get('relay_online', False)
    return '配对记录异常' if item.get('pairing_record_error') else '解除待完成' if item.get('unpair_cleanup_pending') else '已解除配对' if item.get('pairing_cancelled') else '尚未接入' if item.get('needs_setup') else '后台待修复' if item.get('needs_repair') else '状态异常' if item.get('error') else '重复实例' if item.get('instances', 0) > 1 else '未连接' if item.get('retry_exhausted') else '已连接' if synced and item.get('telemetry', {}).get('desktop_connected') else '等待桌面 App' if synced else '正在重连' if running and item.get('mode') in {'packaged', 'app-owned'} else '桥接运行中' if running else '桥接已暂停' if not item.get('enabled', True) else '桥接未运行'

def bridge_action_enabled(item, operation, busy=False):
    if busy or not item.get('controllable') or any((item.get(key) for key in ('needs_setup', 'needs_repair', 'pairing_record_error', 'unpair_cleanup_pending', 'pairing_cancelled', 'error'))):
        return False
    count = item.get('instances', 0)
    if operation == 'start':
        return count == 0
    if operation == 'pause':
        return count > 0
    return operation == 'restart' and count == 1

class BridgeCard(QFrame):
    control = Signal(str, str)
    launch_app = Signal(str)
    close_app = Signal(str)

    def __init__(self, key, title):
        super().__init__()
        self.key = key
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 16)
        layout.setSpacing(12)
        row = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName('cardTitle')
        row.addWidget(heading)
        row.addStretch()
        self.status_label = QLabel('正在读取…')
        self.status_label.setObjectName('status')
        row.addWidget(self.status_label)
        layout.addLayout(row)
        self.summary = note('正在读取本机连接。')
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        self.app_button = QPushButton('启动 App')
        self.app_button.setObjectName(f'{key}_launch_app')
        self.app_button.setToolTip(f'启动本机 {title} 桌面 App 并恢复已配对桥接；不重新配对，不重启正在运行的 App。')
        self.app_button.clicked.connect(lambda _=False: self.launch_app.emit(self.key))
        buttons.addWidget(self.app_button)
        self.close_app_button = QPushButton('关闭 App')
        self.close_app_button.setObjectName(f'{key}_close_app')
        self.close_app_button.setEnabled(False)
        self.close_app_button.setToolTip(f'关闭本机 {title} 桌面 App，可能中断其中的任务；保留配对和桥接开关。')
        self.close_app_button.clicked.connect(lambda _=False: self.close_app.emit(self.key))
        buttons.addWidget(self.close_app_button)
        self.question_link_button = None
        if key == 'codex':
            self.question_link_button = QPushButton('联动启动')
            self.question_link_button.setObjectName('codex_question_link')
            self.question_link_button.setToolTip('启动 Codex 并启用仅本机的提问窗口联动。已运行时不重启；请先结束任务并关闭 App。')
            buttons.addWidget(self.question_link_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        buttons = QHBoxLayout()
        self.buttons = {}
        for operation, caption in [('start', '启动桥接'), ('pause', '暂停桥接'), ('restart', '重新连接')]:
            button = QPushButton(caption)
            button.setObjectName(f'{key}_{operation}')
            button.setEnabled(False)
            button.clicked.connect(lambda _=False, op=operation: self.control.emit(self.key, op))
            self.buttons[operation] = button
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.details_toggle, self.details_body, body = foldout(layout, '连接详情')
        self.detail = note('读取已有桥接配置，不重复配对。')
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.detail)
        self.last = None

    def update_status(self, item, busy=False):
        self.last = item
        app_running = bool(item.get('app_running'))
        self.app_button.setText('App 已运行' if app_running else '启动 App')
        self.app_button.setEnabled(not busy and (not app_running))
        self.close_app_button.setEnabled(not busy and app_running)
        if self.question_link_button:
            self.question_link_button.setEnabled(not busy and (not app_running))
        running = item.get('instances', 0) > 0
        state = item.get('telemetry', {})
        synced = running and item.get('relay_online', False)
        self.status_label.setText(bridge_status_text(item))
        cfg = item.get('config', {})
        self.summary.setText('解除记录无法读取；不会恢复旧授权。' if item.get('pairing_record_error') else ('远端授权已撤销；请继续完成本地清理。' if item.get('remote_revoked') else '解除结果待确认；请继续解除，不会恢复旧授权。') if item.get('unpair_cleanup_pending') else '本机已断开，远端授权待撤销。' if item.get('unpair_pending') else '重新配对后即可恢复连接。' if item.get('pairing_cancelled') else '尚未配对，请在配对管理中接入。' if item.get('needs_setup') else '连续 5 次连接未成功，桥接已断开；启动 App 或点击启动桥接可重新连接。' if item.get('retry_exhausted') else f"{cfg.get('label') or cfg.get('host_id') or '本机'} · " + ('网页与桌面已连接' if synced and state.get('desktop_connected') else '后台已同步，请打开桌面 App' if synced else '连接详情可展开查看'))
        if item.get('pairing_record_error'):
            self.detail.setText(item['error'])
        elif item.get('unpair_cleanup_pending'):
            self.detail.setText('点击配对管理中的“继续解除”完成原操作；不会启动旧授权，不影响其他应用。')
        elif item.get('needs_setup'):
            self.detail.setText('这台电脑尚未接入该应用。点击“配对 / 添加应用”即可配对，由客户端统一连接。')
        elif item.get('needs_repair'):
            self.detail.setText('配对配置已保存，但客户端接入尚未建立。点击“接管 / 修复内置桥接”，不用重新配对。')
        elif item.get('error'):
            self.detail.setText(item['error'])
        else:
            cfg = item.get('config', {})
            ids = ', '.join((str(x) for x in item.get('pids', []))) or '—'
            text = f"设备：{cfg.get('host_id', '—')}  ·  桌面 App：{('运行中' if item.get('app_running') else '未检测到')}\n桥接实例：{item.get('instances', 0)}  ·  进程：{ids}  ·  随客户端接入：{('开启' if item.get('enabled') else '暂停')}"
            if not cfg.get('paired'):
                text += '\n本机配置缺少配对凭证。'
            if item.get('mode'):
                text += '\n运行方式：' + ('由客户端统一管理（退出即断开）' if item['mode'] == 'app-owned' else '内置 EXE 桥接' if item['mode'] == 'packaged' else '旧 Python 桥接（可接管）')
            if state:
                sent, received = (state.get('sent_bytes', 0), state.get('received_bytes', 0))
                text += f"\n对话索引：{state.get('catalog_count', 0)}  ·  模型：{state.get('models_count', 0)}  ·  同步延迟：{state.get('latency_ms', '—')} ms"
                text += f'\n累计同步正文：↑ {sent / 1048576:.2f} MiB  ↓ {received / 1048576:.2f} MiB（不含 HTTP / TLS）'
                if state.get('last_error'):
                    text += '\n最近连接错误：' + state['last_error']
            self.detail.setText(text)
        for op, button in self.buttons.items():
            button.setEnabled(bridge_action_enabled(item, op, busy))

class MainWindow(QMainWindow):

    def __init__(self, manager=None, preferences=None, startup=None, tray_enabled=True, setup_service=None, session=None, app_launcher=None):
        super().__init__()
        load_offscreen_fonts()
        self.manager = manager or BridgeManager()
        from desktop_client.native_apps import DesktopAppLauncher
        self.app_launcher = app_launcher or DesktopAppLauncher(processes=self.manager.processes)
        self.session = session
        self.shutdown_mode = None
        self.preferences = preferences or Preferences()
        initialize(self.preferences)
        self.settings = self.preferences.load()
        self.startup = startup or StartupShortcut()
        from desktop_client.setup import SetupService
        self.setup_service = setup_service or SetupService(self.manager)
        self.setup_dialog = None
        self.system_dialog = None
        self.update_dialog = None
        self.unpair_dialog = None
        self.setup_prompted = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs = {}
        self.busy_key = None
        self.exiting = False
        self.foreground_requested = False
        self.setWindowTitle('Relay Client · 本机客户端')
        self.setWindowIcon(app_icon())
        self.resize(1040, 760)
        self.setMinimumSize(720, 520)
        root = QWidget()
        root.setObjectName('main')
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(208 if language() == 'en' else 176)
        self.sidebar = sidebar
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(14, 24, 14, 18)
        brand = QLabel('Relay Client')
        brand.setObjectName('brand')
        nav.addWidget(brand)
        subtitle = QLabel(f'{edition_label()}\n{platform_name()} · v{VERSION}')
        subtitle.setObjectName('muted')
        nav.addWidget(subtitle)
        nav.addSpacing(25)
        self.nav_buttons = []
        for index, title in enumerate(['连接概览', '配对管理', '诊断日志', '客户端设置']):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setObjectName('nav')
            button.clicked.connect(lambda _=False, i=index: self.navigate(i))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        nav.addStretch()
        languages = QHBoxLayout()
        languages.addWidget(QLabel('语言'))
        self.language_select = language_picker(self, self.preferences, self.language_changed)
        languages.addWidget(self.language_select)
        nav.addLayout(languages)
        self.update_button = QPushButton('检查 CRB 更新')
        self.update_button.clicked.connect(self.open_relay_update)
        nav.addWidget(self.update_button)
        self.tray_hint = QLabel()
        self.tray_hint.setWordWrap(True)
        self.tray_hint.setObjectName('muted')
        nav.addWidget(self.tray_hint)
        self.restart_button = QPushButton('重启客户端')
        self.restart_button.setObjectName('restart_client')
        self.restart_button.clicked.connect(self.restart_client)
        nav.addWidget(self.restart_button)
        quit_button = QPushButton('关闭并断开')
        quit_button.setObjectName('close_client')
        quit_button.setToolTip('停止本机所有接入并退出客户端，保留配对 · Ctrl+Q')
        quit_button.clicked.connect(self.exit_client)
        nav.addWidget(quit_button)
        quit_action = QAction('退出客户端', self)
        quit_action.setShortcut('Ctrl+Q')
        quit_action.triggered.connect(self.exit_client)
        self.addAction(quit_action)
        for index in range(4):
            shortcut = QAction(self)
            shortcut.setShortcut(f'Ctrl+{index + 1}')
            shortcut.triggered.connect(lambda _=False, i=index: self.navigate(i))
            self.addAction(shortcut)
        layout.addWidget(sidebar)
        right = QVBoxLayout()
        right.setContentsMargins(24, 24, 20, 14)
        self.pages = QStackedWidget()
        self.pages.addWidget(scroll_page(self.overview_page()))
        self.pages.addWidget(scroll_page(self.settings_page()))
        self.pages.addWidget(self.logs_page())
        self.pages.addWidget(scroll_page(self.about_page()))
        right.addWidget(self.pages)
        self.notice = QLabel('正在检查本机桥接…')
        self.notice.setObjectName('muted')
        self.notice.setWordWrap(True)
        self.notice.setMinimumHeight(36)
        self.notice.setMaximumHeight(78)
        self.notice.setToolTip('状态与操作结果；完整连接信息可在配对管理和诊断日志查看。')
        right.addWidget(self.notice)
        layout.addLayout(right, 1)
        self.tray = None
        if tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.create_tray()
        self.update_close_behavior()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.navigate(0)
        QTimer.singleShot(0, self.refresh)

    @staticmethod
    def page(title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(17)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        sub = QLabel(subtitle)
        sub.setObjectName('muted')
        sub.setWordWrap(True)
        layout.addWidget(sub)
        return (page, layout)

    def overview_page(self):
        page, layout = self.page('连接概览', '这台电脑负责桥接，聊天仍使用网页面板。')
        layout.addWidget(note('Claude Code：待接入，等待可验证的原桌面会话接口；不会替换成独立 CLI 会话。'))
        actions = QHBoxLayout()
        open_panel = QPushButton('打开网页面板  ↗')
        open_panel.setObjectName('primary')
        open_panel.clicked.connect(self.open_panel)
        actions.addWidget(open_panel)
        refresh = QPushButton('刷新状态')
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        actions.addStretch()
        layout.addLayout(actions)
        self.cards = {}
        for spec in self.manager.specs.values():
            card = BridgeCard(spec.key, spec.title)
            card.control.connect(self.control)
            card.launch_app.connect(self.launch_native_app)
            if card.question_link_button:
                card.question_link_button.clicked.connect(self.launch_question_link)
            card.close_app.connect(self.close_native_app)
            self.cards[spec.key] = card
            layout.addWidget(card)
        self.setup_button = QPushButton('配对 / 添加应用')
        self.setup_button.clicked.connect(self.open_setup)
        layout.addWidget(self.setup_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(note('客户端运行时才接入面板；“关闭并断开”彻底退出。可在客户端设置中选择关闭窗口时留在托盘。'))
        layout.addStretch()
        return page

    def settings_page(self):
        page, layout = self.page('配对管理', '按应用独立管理；解除一个应用的配对不会影响另一个。')
        intro = QFrame()
        intro.setObjectName('card')
        body = QVBoxLayout(intro)
        body.setContentsMargins(20, 18, 20, 18)
        body.addWidget(QLabel('配对用什么密码？'))
        body.addWidget(note('使用网页登录面板时的账号和密码，不是 Windows、SSH 密码，也不是 API Key。密码仅用于当次配对，不保存；成功后客户端自动保存独立连接凭证。'))
        first = QPushButton('配对 / 添加应用')
        first.setObjectName('primary')
        first.clicked.connect(self.open_setup)
        body.addWidget(first, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(intro)
        self.config_labels, self.pairing_labels, self.unpair_buttons = ({}, {}, {})
        for spec in self.manager.specs.values():
            card = QFrame()
            card.setObjectName('card')
            group = QVBoxLayout(card)
            group.setContentsMargins(20, 17, 20, 17)
            group.setSpacing(10)
            row = QHBoxLayout()
            heading = QLabel(spec.title)
            heading.setObjectName('cardTitle')
            row.addWidget(heading)
            row.addStretch()
            unpair = QPushButton('解除配对')
            unpair.setObjectName('danger')
            unpair.setEnabled(False)
            unpair.clicked.connect(lambda _=False, k=spec.key: self.open_unpair(k))
            row.addWidget(unpair)
            self.unpair_buttons[spec.key] = unpair
            group.addLayout(row)
            description = note('正在读取连接…')
            group.addWidget(description)
            self.pairing_labels[spec.key] = description
            server = QLineEdit()
            server.setReadOnly(True)
            server.setPlaceholderText('尚未接入')
            group.addWidget(server)
            self.config_labels[spec.key] = server
            layout.addWidget(card)
        self.advanced_toggle, self.advanced_body, advanced = foldout(layout, '高级连接与修复')
        advanced.addWidget(note('一般无需修改。桥接凭证不是网页登录密码；更换服务器会把凭证发送给新地址。'))
        self.connection_key = QComboBox()
        for spec in self.manager.specs.values():
            self.connection_key.addItem(spec.title, spec.key)
        self.connection_key.currentIndexChanged.connect(self.reload_connection)
        advanced.addWidget(self.connection_key)
        self.connection_label = QLineEdit()
        self.connection_label.setPlaceholderText('电脑名称')
        self.connection_url = QLineEdit()
        self.connection_url.setPlaceholderText('桥接服务器地址')
        self.connection_token = QLineEdit()
        self.connection_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.connection_token.setPlaceholderText('新连接凭证（留空保留原凭证）')
        for caption, field in (('电脑名称', self.connection_label), ('服务器地址', self.connection_url), ('连接凭证（不是网页登录密码）', self.connection_token)):
            advanced.addWidget(QLabel(caption))
            advanced.addWidget(field)
        row = QHBoxLayout()
        apply = QPushButton('保存并重新连接')
        apply.clicked.connect(self.save_connection)
        row.addWidget(apply)
        revert = QPushButton('取消修改')
        revert.clicked.connect(self.reload_connection)
        row.addWidget(revert)
        row.addStretch()
        advanced.addLayout(row)
        install = QPushButton('接管 / 修复内置桥接')
        install.clicked.connect(self.install_bridges)
        advanced.addWidget(install, alignment=Qt.AlignmentFlag.AlignLeft)
        advanced.addWidget(note('修复保留现有配对和数据；已解除配对的应用必须重新配对，不能恢复旧凭证。'))
        network = QPushButton('网络与依赖')
        network.clicked.connect(self.open_system_setup)
        layout.addWidget(network, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return page

    def logs_page(self):
        page, layout = self.page('诊断日志', '只读取本机日志尾部，常见密钥与认证字段会隐藏。')
        row = QHBoxLayout()
        self.log_source = QComboBox()
        for spec in self.manager.specs.values():
            self.log_source.addItem(spec.title, spec.key)
        self.log_source.currentIndexChanged.connect(self.refresh_logs)
        row.addWidget(self.log_source, 1)
        refresh = QPushButton('刷新日志')
        refresh.clicked.connect(self.refresh_logs)
        row.addWidget(refresh)
        layout.addLayout(row)
        row = QHBoxLayout()
        test = QPushButton('检查网络')
        test.clicked.connect(lambda: self.submit('network', self.manager.test_connection))
        row.addWidget(test)
        folder = QPushButton('日志目录')
        folder.clicked.connect(self.open_log_folder)
        row.addWidget(folder)
        export = QPushButton('导出诊断包')
        export.clicked.connect(lambda: self.submit('diagnostics', self.manager.export_diagnostics))
        row.addWidget(export)
        row.addStretch()
        layout.addLayout(row)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(700)
        layout.addWidget(self.logs, 1)
        return page

    def about_page(self):
        page, layout = self.page('客户端设置', f'Relay Client  v{VERSION} · {edition_label()} · {platform_name()}')
        layout.addWidget(QLabel('网页面板地址'))
        self.url = QLineEdit(self.settings['panel_url'])
        self.url.setObjectName('panel_url')
        self.url.setPlaceholderText('填写你自己的面板地址，例如 https://relay.example.com')
        layout.addWidget(self.url)
        self.autostart = QCheckBox('登录后启动客户端并接入面板')
        self.autostart.setChecked(self.startup.enabled())
        self.autostart.setEnabled(bool(getattr(sys, 'frozen', False)))
        layout.addWidget(self.autostart)
        self.close_to_tray = QCheckBox('关闭窗口时最小化到托盘（保持接入）')
        self.close_to_tray.setObjectName('close_to_tray')
        self.close_to_tray.setChecked(self.settings.get('close_to_tray', False))
        layout.addWidget(self.close_to_tray)
        self.close_behavior_note = note('')
        layout.addWidget(self.close_behavior_note)
        layout.addWidget(note('未勾选时，关闭窗口即断开。无可用托盘时仍关闭并断开，避免客户端隐藏后无法打开。\n“关闭并断开”和 Ctrl+Q 始终彻底退出，不保留独立桥接后台。'))
        self.codex_question_link = QCheckBox('启动 Codex 时启用提问框同步（仅本机）')
        self.codex_question_link.setObjectName('codex_question_link')
        self.codex_question_link.setChecked(self.settings.get('codex_question_link', False))
        layout.addWidget(self.codex_question_link)
        layout.addWidget(note('仅客户端启动 Codex 时启用 127.0.0.1 控制通道，用于网页代答后收起原 App 提问框。\n该通道具有 App 控制权限，不暴露到公网；保存设置不会重启已运行的 App。'))
        if sys.platform == 'darwin':
            from desktop_client.mac_permissions import permission_widget
            layout.addWidget(permission_widget(page))
        row = QHBoxLayout()
        save = QPushButton('保存设置')
        save.setObjectName('primary')
        save.clicked.connect(self.save_settings)
        row.addWidget(save)
        cancel = QPushButton('取消修改')
        cancel.clicked.connect(self.reload_preferences)
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _, _, help_body = foldout(layout, '关闭、暂停和解除配对有什么区别？')
        help_body.addWidget(note('启动 App：打开对应的本机桌面应用并恢复已配对桥接；已运行时不重复启动，不改变配对。\n关闭 App：确认后请求对应桌面应用退出，可能中断其任务；不强制杀进程，不改变配对和桥接开关。\n关闭窗口：默认断开；启用关闭到托盘且托盘可用时，只隐藏窗口并保持接入。\n关闭并断开 / Ctrl+Q：断开本机所有接入，不留下独立后台，配对保留。\n重启客户端：先关闭全部接入，再重新启动客户端并连接。\n最小化：客户端仍运行，接入保持。\n暂停桥接：只暂停所选应用，可点击启动桥接恢复。\n重新连接：只重新连接所选应用，不重启桌面 App。\n解除配对：断开所选应用并撤销服务器授权，再次接入需重新配对。\n除了明确的“关闭 App”，这些操作不停止原桌面 App，不删除聊天、项目或文件。'))
        layout.addWidget(note('状态刷新只查询本机，不增加网络轮询。网络检查仅在点击时运行。\n后台自动恢复与网络重连沿用原桥接协议；配对凭证不展示、不打包、不导出。'))
        layout.addStretch()
        return page

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 2:
            self.refresh_logs()
        if index == 1:
            self.reload_connection()

    def language_changed(self, value, error):
        if error:
            self.notice.setText('保存失败：' + error)
        else:
            self.settings = self.preferences.load()
            self.sidebar.setFixedWidth(208 if value == 'en' else 176)

    def create_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip('Relay Client · 正在检查桥接')
        menu = QMenu()
        self.tray_menu = menu
        menu.aboutToShow.connect(self.refresh)
        show = menu.addAction('显示客户端')
        show.triggered.connect(self.show_window)
        menu.addAction('打开网页面板', self.open_panel)
        menu.addSeparator()
        self.tray_actions = []
        self.tray_app_actions = {}
        self.tray_close_app_actions = {}
        self.tray_bridge_actions = {}
        self.tray_status_actions = {}
        self.tray_submenus = {}
        for spec in self.manager.specs.values():
            sub = menu.addMenu(spec.title)
            self.tray_submenus[spec.key] = sub
            sub.aboutToShow.connect(self.refresh)
            app_status = sub.addAction('App：正在读取…')
            bridge_status = sub.addAction('桥接：正在读取…')
            app_status.setEnabled(False)
            bridge_status.setEnabled(False)
            self.tray_status_actions[spec.key] = {'app': app_status, 'bridge': bridge_status}
            sub.addSeparator()
            launch = sub.addAction('启动 ' + spec.title + ' App')
            launch.setEnabled(False)
            launch.triggered.connect(lambda _=False, k=spec.key: self.launch_native_app(k))
            self.tray_app_actions[spec.key] = launch
            close = sub.addAction('关闭 ' + spec.title + ' App')
            close.setEnabled(False)
            close.triggered.connect(lambda _=False, k=spec.key: self.close_native_app(k))
            self.tray_close_app_actions[spec.key] = close
            sub.addSeparator()
            self.tray_bridge_actions[spec.key] = {}
            for op, label in [('start', '启动桥接'), ('pause', '暂停桥接'), ('restart', '重启桥接')]:
                action = sub.addAction(label)
                action.setEnabled(False)
                action.triggered.connect(lambda _=False, k=spec.key, o=op: self.control(k, o))
                self.tray_actions.append(action)
                self.tray_bridge_actions[spec.key][op] = action
            self.update_tray_item(spec.key, self.cards[spec.key].last or {}, bool(self.busy_key))
        menu.addSeparator()
        menu.addAction('重启客户端', self.restart_client)
        menu.addAction('关闭并断开', self.exit_client)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def update_tray_item(self, key, item, busy=False):
        title = self.manager.specs[key].title
        known_app = 'app_running' in item
        app_running = bool(item.get('app_running'))
        status = getattr(self, 'tray_status_actions', {}).get(key, {})
        if status:
            status['app'].setText('App：' + ('已启动' if app_running else '未启动') if known_app else 'App：正在读取…')
            status['bridge'].setText('桥接：' + bridge_status_text(item).removeprefix('桥接') if item else '桥接：正在读取…')
            status['app'].setEnabled(False)
            status['bridge'].setEnabled(False)
        launch = getattr(self, 'tray_app_actions', {}).get(key)
        if launch:
            launch.setText(title + ' App 已启动' if known_app and app_running else '启动 ' + title + ' App')
            launch.setEnabled(not busy and known_app and (not app_running))
        close = getattr(self, 'tray_close_app_actions', {}).get(key)
        if close:
            close.setEnabled(not busy and known_app and app_running)
        count = item.get('instances', 0)
        paused = count == 0 and item.get('enabled') is False and item.get('controllable')
        captions = {'start': '桥接已启动' if count > 0 else '启动桥接', 'pause': '桥接已暂停' if paused else '暂停桥接', 'restart': '重启桥接'}
        for operation, action in getattr(self, 'tray_bridge_actions', {}).get(key, {}).items():
            action.setText(captions[operation])
            action.setEnabled(bridge_action_enabled(item, operation, busy))

    def on_tray_activated(self, reason):
        if sys.platform != 'darwin' and reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_window()

    def show_window(self):
        if not self.foreground_requested:
            self.showNormal()
            self.hide()
            self.foreground_requested = True
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.timer.setInterval(5000)
        self.refresh()

    def closeEvent(self, event):
        if self.exiting:
            event.accept()
        else:
            event.ignore()
            if not self.shutdown_mode and self.settings.get('close_to_tray') is True and self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
                self.hide()
                self.timer.setInterval(5000)
            else:
                self.exit_client()

    def update_close_behavior(self):
        available = bool(self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable())
        to_tray = self.settings.get('close_to_tray') is True and available
        self.tray_hint.setText('关闭窗口留在托盘\n关闭并断开才退出' if to_tray else '关闭窗口即断开接入\n保留配对与桌面 App')
        self.close_behavior_note.setText('托盘可用，保存后生效。' if available else '当前系统没有可用托盘：该选项可保存，关闭窗口时仍会断开并退出。')

    def exit_client(self):
        self.begin_shutdown('close')

    def open_relay_update(self):
        if self.shutdown_mode:
            return
        from desktop_client.update_ui import UpdateDialog
        if not self.update_dialog:
            self.update_dialog = UpdateDialog(self, 'client')
            self.update_dialog.installRequested.connect(self.apply_relay_update)
        self.update_dialog.show()
        self.update_dialog.raise_()

    def apply_relay_update(self, plan_file):
        try:
            if self.shutdown_mode or self.busy_key:
                raise ClientError('本机连接操作仍在进行，请稍后重新更新。')
            from desktop_client.update_apply import schedule
            schedule(plan_file)
            self.begin_shutdown('close')
        except Exception as error:
            self.update_dialog.failed('install', redact(str(error)))

    def restart_client(self):
        self.begin_shutdown('restart')

    def begin_shutdown(self, mode):
        if self.exiting or self.shutdown_mode:
            return
        if mode == 'restart' and (not getattr(sys, 'frozen', False)):
            self.notice.setText('请在独立客户端 App 中使用重启功能。')
            return
        self.shutdown_mode = mode
        self.shutdown_stopped = False
        self.restart_hidden = not self.isVisible()
        self.timer.stop()
        self.restart_button.setEnabled(False)
        self.set_controls_busy(True)
        self.notice.setText('正在断开本机接入' + ('并重启客户端…' if mode == 'restart' else '并关闭客户端…'))
        self.busy_key = 'client-shutdown'
        if self.session:
            self.session.closing = True

        def stop_all():
            if self.session:
                return self.session.shutdown()
            for row in self.manager.status():
                if row.get('controllable'):
                    self.manager.control(row['key'], 'pause')
            return '本机接入已断开。'
        self.submit('client-shutdown', stop_all)

    def complete_shutdown(self):
        if self.shutdown_mode == 'restart':
            from desktop_client.lifecycle import launch_replacement
            try:
                launch_replacement(self.restart_hidden)
            except Exception as exc:
                self.notice.setText('接入已断开，但重启失败：' + redact(str(exc)) + '；可重试或关闭后手动打开。')
                self.shutdown_mode = None
                self.busy_key = None
                self.restart_button.setEnabled(True)
                self.set_controls_busy(False)
                if self.session:
                    self.session.closing = False
                return
        self.exiting = True
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def submit(self, name, callback):
        if name in self.jobs:
            return
        work = Work(name, callback)
        self.jobs[name] = work
        work.signals.done.connect(self.work_done)
        work.signals.failed.connect(self.work_failed)
        self.pool.start(work)

    def refresh(self):
        if not self.busy_key:

            def state():
                if self.session:
                    self.session.supervise()
                return self.manager.status()
            self.submit('status', state)
            if self.session:
                self.submit('control-presence', self.session.control_link.sync)

    def refresh_logs(self):
        if not hasattr(self, 'logs'):
            return
        key = self.log_source.currentData()
        self.submit(f'logs:{key}', lambda: self.manager.log_tail(key))

    def control(self, key, operation):
        if self.busy_key:
            return
        title = self.manager.specs[key].title
        if operation in {'pause', 'restart'}:
            verb = '暂停' if operation == 'pause' else '重启'
            if QMessageBox.question(self, f'{verb} {title} 桥接', f'{verb}本机 {title} 的同步桥接？\n网页同步会短暂中断，该桥接的网页终端会关闭；桌面 App 和正在运行的任务不会被停止。') != QMessageBox.StandardButton.Yes:
                return
        self.busy_key = key
        self.set_controls_busy(True)
        self.notice.setText(f'正在处理 {title} 桥接…')
        self.submit('control', lambda: self.manager.control(key, operation))

    def launch_native_app(self, key):
        if self.busy_key or key not in self.manager.specs:
            return
        self.busy_key = 'app:' + key
        self.set_controls_busy(True)
        self.notice.setText('正在启动 ' + self.manager.specs[key].title + ' 桌面 App…')
        linked = key == 'codex' and self.settings.get('codex_question_link', False)

        def launch_and_connect():
            result = self.app_launcher.launch(key, question_link=True) if linked else self.app_launcher.launch(key)
            if self.session and self.session.store.available(key):
                self.session.start(self.session.specs[key], remember=True)
            return result
        self.submit('launch-app', launch_and_connect)

    def close_native_app(self, key):
        if self.busy_key or key not in self.manager.specs:
            return
        title = self.manager.specs[key].title
        if QMessageBox.question(self, '关闭 ' + title + ' App', f'关闭本机 {title} 桌面 App？\n可能中断其中正在运行的任务，请先保存需要的内容。\n不会删除聊天、改变配对或关闭其他 App；桥接开关保持不变。', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.busy_key = 'app:' + key
        self.set_controls_busy(True)
        self.notice.setText('正在关闭 ' + title + ' 桌面 App…')
        self.submit('close-app', lambda: self.app_launcher.close(key))

    def launch_question_link(self):
        if self.busy_key:
            return
        if QMessageBox.question(self, '启用原 App 提问窗口联动', '启动 Codex 并启用仅监听 127.0.0.1 的本机控制通道？\n用于网页代答后收起原 App 的对应提问框。该通道具有 App 控制权限，不开放到局域网或公网。\n不会重启已运行的 App，不改变安装文件或配对。确认后保存此选择，之后客户端“启动 App”也启用联动。', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.preferences.save(dict(self.settings, codex_question_link=True))
            self.settings = self.preferences.load()
            self.codex_question_link.setChecked(True)
        except Exception as exc:
            self.notice.setText('无法保存联动设置，未启动 App：' + redact(str(exc)))
            return
        self.busy_key = 'app:codex'
        self.set_controls_busy(True)
        self.notice.setText('正在以提问窗口联动模式启动 Codex…')

        def launch_and_connect():
            result = self.app_launcher.launch('codex', question_link=True)
            if self.session and self.session.store.available('codex'):
                self.session.start(self.session.specs['codex'], remember=True)
            return result
        self.submit('launch-app', launch_and_connect)

    def set_controls_busy(self, busy):
        for card in self.cards.values():
            if card.last:
                card.update_status(card.last, busy)
        for key, card in self.cards.items():
            self.update_tray_item(key, card.last or {}, busy)
        self.setup_button.setEnabled(not busy and bool(self.setup_service.missing()))
        for key, button in self.unpair_buttons.items():
            last = self.cards[key].last or {}
            button.setEnabled(not busy and (not last.get('needs_setup', True) or last.get('unpair_pending', False)))

    def work_done(self, name, result):
        self.jobs.pop(name, None)
        if name == 'client-shutdown':
            self.shutdown_stopped = True
            self.finish_shutdown_when_idle()
            return
        if self.shutdown_mode:
            self.finish_shutdown_when_idle()
            return
        if name == 'control-presence':
            if self.session and self.session.remote_restart_ready:
                self.session.remote_restart_ready = False
                self.begin_shutdown('restart')
            return
        if name == 'client-start':
            self.busy_key = None
            self.notice.setText(result)
            self.set_controls_busy(False)
            self.refresh()
            return
        if name == 'status':
            states = []
            for item in result:
                self.cards[item['key']].update_status(item, bool(self.busy_key))
                self.update_tray_item(item['key'], item, bool(self.busy_key))
                cfg = item.get('config', {})
                key = item['key']
                self.config_labels[key].setText(cfg.get('relay_url', ''))
                self.config_labels[key].setToolTip(cfg.get('relay_url', '尚未接入'))
                self.pairing_labels[key].setText('解除配对记录异常 · 不会恢复旧授权' if item.get('pairing_record_error') else ('授权已撤销 · 本地清理待完成' if item.get('remote_revoked') else '解除结果待确认 · 不会恢复旧授权') if item.get('unpair_cleanup_pending') else '本机已断开 · 远端授权待撤销' if item.get('unpair_pending') else '已解除配对 · 可重新接入' if item.get('pairing_cancelled') else '尚未配对' if item.get('needs_setup') else f"{cfg.get('label') or '本机'} · 设备 {cfg.get('host_id', '—')} · 独立连接凭证已保存")
                self.unpair_buttons[key].setText('继续解除' if item.get('unpair_cleanup_pending') else '补撤销授权' if item.get('unpair_pending') else '解除配对')
                self.unpair_buttons[key].setEnabled(not self.busy_key and (not item.get('needs_setup') or item.get('unpair_pending', False)))
                state = '记录异常' if item.get('error') or item.get('pairing_record_error') else '解除待完成' if item.get('unpair_cleanup_pending') else '已解除配对' if item.get('pairing_cancelled') else '未接入' if item.get('needs_setup') else '待修复' if item.get('needs_repair') else '已暂停' if not item.get('enabled', True) else '未连接' if item.get('retry_exhausted') else '已连接' if item.get('relay_online') and item.get('telemetry', {}).get('desktop_connected') else '等待桌面 App' if not item.get('app_running', True) else '连接中'
                states.append(f"{item['title']} {state}")
            if self.tray:
                self.tray.setToolTip('Relay Client\n' + ' · '.join(states))
                healthy = all((not i.get('error') and (not i.get('pairing_record_error')) and (not i.get('needs_repair')) and (i.get('pairing_cancelled') or i.get('needs_setup') or (not i.get('enabled', True) and (not i.get('needs_repair'))) or (i.get('telemetry', {}).get('desktop_connected') and i.get('relay_online'))) and (not i.get('unpair_pending')) for i in result))
                self.tray.setIcon(app_icon('#2f7959' if healthy else '#bd8431'))
            needs_setup = any((i.get('needs_setup') for i in result))
            self.setup_button.setEnabled(needs_setup and (not self.busy_key))
            if self.notice.text() == '正在检查本机桥接…':
                self.notice.setText('尚未配置，请点击“首次接入 / 添加应用”。' if needs_setup else '本机已接入；关闭行为可在客户端设置中选择，配对保留。')
            if self.isVisible() and result and all((i.get('needs_setup') and (not i.get('pairing_cancelled')) for i in result)) and (not self.setup_prompted):
                self.setup_prompted = True
                QTimer.singleShot(0, self.open_setup)
        elif name == 'unpair':
            self.busy_key = None
            if self.unpair_dialog:
                self.unpair_dialog.completed(result)
            self.notice.setText(result)
            self.notice.setToolTip(result)
            self.set_controls_busy(False)
            self.reload_connection()
            self.refresh()
        elif name.startswith('setup-'):
            if self.setup_dialog:
                self.setup_dialog.completed(name, result)
            if name == 'setup-connect':
                self.busy_key = None
                self.notice.setText(str(result))
                self.notice.setToolTip(str(result))
                self.settings = self.preferences.load()
                self.url.setText(self.settings['panel_url'])
                self.set_controls_busy(False)
                self.refresh()
        elif name.startswith('logs:'):
            if name.split(':', 1)[1] == self.log_source.currentData():
                self.logs.setPlainText(result)
                self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
        else:
            self.notice.setText(result)
            if name in {'control', 'install', 'connection', 'launch-app', 'close-app'}:
                self.busy_key = None
                self.set_controls_busy(False)
                self.refresh()

    def work_failed(self, name, message):
        self.jobs.pop(name, None)
        if self.shutdown_mode:
            if name == 'client-shutdown':
                self.shutdown_mode = None
                self.busy_key = None
                self.notice.setText('关闭未完成：' + message)
                self.restart_button.setEnabled(True)
                self.set_controls_busy(False)
                return
            self.finish_shutdown_when_idle()
            return
        if name == 'client-start':
            self.busy_key = None
            self.notice.setText('接入未完成：' + message)
            self.set_controls_busy(False)
            return
        self.notice.setText('操作失败：' + message)
        self.notice.setToolTip('操作失败：' + message)
        if name == 'unpair':
            self.busy_key = None
            if self.unpair_dialog:
                self.unpair_dialog.completed(message, failed=True)
            self.set_controls_busy(False)
            self.refresh()
        if name.startswith('setup-') and self.setup_dialog:
            self.setup_dialog.completed(name, message, failed=True)
            if name == 'setup-connect':
                self.busy_key = None
                self.set_controls_busy(False)
                self.refresh()
        if name in {'control', 'install', 'connection', 'launch-app', 'close-app'}:
            self.busy_key = None
            self.set_controls_busy(False)
            self.refresh()

    def save_settings(self):
        if self.busy_key:
            self.notice.setText('连接操作进行中，请完成后再保存客户端设置。')
            return
        try:
            data = {'panel_url': self.preferences.validate_url(self.url.text(), allow_empty=True), 'close_to_tray': self.close_to_tray.isChecked(), 'codex_question_link': self.codex_question_link.isChecked(), **({'language': self.settings['language']} if 'language' in self.settings else {})}
            previous = self.preferences.snapshot()
            self.preferences.save(data)
            if bool(getattr(sys, 'frozen', False)):
                from desktop_client.setup import persistent_client_executable
                try:
                    executable = persistent_client_executable(install=self.autostart.isChecked())
                    self.startup.configure(self.autostart.isChecked(), executable, previous_executable=Path(sys.executable))
                except Exception:
                    try:
                        self.preferences.restore(previous)
                    except Exception:
                        self.reload_preferences()
                        raise ClientError('开机启动设置失败，旧面板地址也未能恢复；界面已显示实际保存值，请检查后重试。') from None
                    raise
            self.settings = data
            self.update_close_behavior()
            self.notice.setText('客户端设置已保存；原有桥接账号配置未修改。')
        except Exception as exc:
            self.notice.setText('保存失败：' + redact(str(exc)))

    def finish_shutdown_when_idle(self):
        if self.shutdown_mode and self.shutdown_stopped and (not self.jobs):
            self.complete_shutdown()

    def reload_preferences(self):
        self.settings = self.preferences.load()
        self.url.setText(self.settings['panel_url'])
        self.autostart.setChecked(self.startup.enabled())
        self.close_to_tray.setChecked(self.settings.get('close_to_tray', False))
        self.codex_question_link.setChecked(self.settings.get('codex_question_link', False))
        self.update_close_behavior()

    def reload_connection(self):
        if not hasattr(self, 'connection_url'):
            return
        key = self.connection_key.currentData()
        from desktop_client.backend import read_public_config
        try:
            config = read_public_config(self.manager.specs[key])
            self.connection_url.setText(config['relay_url'])
            self.connection_label.setText(config['label'])
            self.connection_token.clear()
        except Exception:
            self.connection_url.clear()
            self.connection_label.clear()

    def save_connection(self):
        if self.busy_key:
            return
        key = self.connection_key.currentData()
        if QMessageBox.question(self, '修改桥接连接', '保存本机桥接连接配置？\n修改地址后，配对凭证将发送到填写的服务器，请确认它属于你。\n运行中的桥接会重新连接，网页终端会关闭，桌面任务不会停止。') != QMessageBox.StandardButton.Yes:
            return
        url, label, token = (self.connection_url.text(), self.connection_label.text(), self.connection_token.text())
        self.connection_token.clear()
        self.busy_key = key
        self.set_controls_busy(True)
        self.submit('connection', lambda: self.manager.save_connection(key, url, label, token))

    def install_bridges(self):
        if self.busy_key:
            return
        if not getattr(sys, 'frozen', False):
            self.notice.setText('请在打包后的客户端中接管桥接。')
            return
        if QMessageBox.question(self, '修复客户端接入', '修复所选应用的客户端接入？\n保留配对与数据，不建立独立后台服务。\n同步会暂时断开，网页终端会关闭，桌面任务不会停止。') != QMessageBox.StandardButton.Yes:
            return
        self.busy_key = 'all'
        self.set_controls_busy(True)
        self.notice.setText('正在逐一接管桥接…')
        self.submit('install', lambda: '\n'.join((self.manager.install(k) for k in self.manager.specs)))

    def open_setup(self):
        if self.busy_key:
            return
        if not self.setup_service.missing():
            QMessageBox.information(self, '已有连接', '本机应用均已有配置。请在连接设置中修改；不会重复配对或覆盖现有设备。')
            return
        if self.setup_dialog and self.setup_dialog.isVisible():
            self.setup_dialog.raise_()
            self.setup_dialog.activateWindow()
            return
        from desktop_client.setup_ui import SetupDialog
        self.setup_dialog = SetupDialog(self, self.setup_service)
        self.setup_dialog.show()

    def open_system_setup(self):
        if self.busy_key:
            return
        from desktop_client.system_setup_ui import SystemSetupDialog
        self.system_dialog = SystemSetupDialog(self, 'client', lambda: {'url': self.settings['panel_url']})
        self.system_dialog.show()

    def open_unpair(self, key):
        if self.busy_key:
            return
        if self.unpair_dialog and self.unpair_dialog.isVisible():
            return
        from desktop_client.setup_ui import UnpairDialog
        self.unpair_dialog = UnpairDialog(self, self.setup_service, key)
        self.unpair_dialog.show()

    def open_panel(self):
        try:
            url = self.preferences.validate_url(self.settings['panel_url'])
            if not QDesktopServices.openUrl(QUrl(url)):
                raise RuntimeError('系统未能打开浏览器。')
        except Exception as exc:
            self.notice.setText(redact(str(exc)))

    def open_log_folder(self):
        folder = self.manager.log_path(self.log_source.currentData()).parent
        if folder.is_dir():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                self.notice.setText('系统未能打开日志目录：' + str(folder))
        else:
            self.notice.setText('日志目录尚未建立：' + str(folder))

class SingleInstance(QObject):

    def __init__(self, window=None):
        super().__init__(window or QApplication.instance())
        self.window = window
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        from desktop_client.connections import client_root
        self.name = 'RelayClient-' + hashlib.sha256((str(Path.home()) + str(client_root())).encode()).hexdigest()[:16]
        self.server.newConnection.connect(self.connection)

    def claim(self, show=True):
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if socket.waitForConnected(800):
            socket.write(b'show' if show else b'noop')
            socket.waitForBytesWritten(800)
            socket.disconnectFromServer()
            return False
        if not self.server.listen(self.name):
            raise RuntimeError('已有客户端正在启动，请稍后再次打开。')
        return True

    def connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self.read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self.read(socket)

    def read(self, socket):
        command = bytes(socket.readAll())
        if command == b'show':
            if self.window:
                self.window.show_window()
        elif self.window and command in {b'close-client', b'restart-client'}:
            self.window.begin_shutdown('restart' if command == b'restart-client' else 'close')
            socket.write(b'accepted')
            socket.flush()
        socket.disconnectFromServer()

def main():
    from desktop_client.startup_diagnostics import boot_mark
    from desktop_client.lifecycle import ClientSession, client_manager
    preferences = Preferences()
    preferences.directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(preferences.directory / 'client.log', maxBytes=128 * 1024, backupCount=2, encoding='utf-8')
    logging.basicConfig(handlers=[handler], level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    boot_mark('gui.qapplication.begin')
    app = QApplication(sys.argv)
    boot_mark('gui.qapplication.end')
    app.setApplicationName('Relay Client')
    app.setOrganizationName('CodexRelay')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    single = SingleInstance()
    try:
        if not single.claim(show='--tray' not in sys.argv):
            return 0
    except Exception as exc:
        QMessageBox.information(None, 'Relay Client', str(exc))
        return 1
    session = ClientSession()
    boot_mark('gui.window.begin')
    window = MainWindow(manager=client_manager(session), preferences=preferences, session=session)
    boot_mark('gui.window.end')
    single.window = window
    window.busy_key = 'client-start'
    window.set_controls_busy(True)
    window.submit('client-start', session.adopt)
    if '--tray' not in sys.argv:
        window.show_window()
    elif window.tray:
        window.timer.setInterval(5000)
    else:
        window.show_window()

    def cleanup():
        session.closing = True
        window.pool.waitForDone(15000)
        try:
            session.shutdown()
        except Exception as exc:
            logging.error('客户端退出清理：%s', redact(str(exc)))
    app.aboutToQuit.connect(cleanup)
    try:
        return app.exec()
    finally:
        session.shutdown()
        single.server.close()
