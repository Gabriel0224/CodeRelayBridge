"""Shared native setup dialog, with local OS authorization when necessary."""
import json
import shutil
import sys
from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout
from desktop_client import system_setup
from desktop_client.i18n import QDialog, QInputDialog, QLineEdit, QPushButton, tr

class SystemSetupDialog(QDialog):

    def __init__(self, parent, role, configuration):
        super().__init__(parent)
        from desktop_client.app import Work, note
        self.Work = Work
        self.role = role
        self.configuration = configuration
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.current = None
        self.working = False
        self.setWindowTitle('网络与依赖')
        self.resize(700, 560)
        self.setMinimumSize(520, 420)
        body = QVBoxLayout(self)
        body.addWidget(note('只配置本程序需要的系统规则，不关闭防火墙，不开放路由器或阿里云安全组。系统授权由本机处理，密码不保存。'))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        body.addWidget(self.output, 1)
        row = QHBoxLayout()
        self.check = QPushButton('重新检测')
        self.check.clicked.connect(self.inspect)
        row.addWidget(self.check)
        self.repair = QPushButton('自动修复')
        self.repair.setEnabled(False)
        self.repair.clicked.connect(self.fix)
        row.addWidget(self.repair)
        row.addStretch()
        self.cancel = QPushButton('关闭')
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.cancel)
        body.addLayout(row)
        self.inspect()

    def inspect(self):
        if self.working:
            return
        try:
            configuration = self.configuration()
        except Exception as exc:
            self.failed('check', str(exc))
            return
        self.execute('check', lambda: system_setup.plan(self.role, **configuration))

    def execute(self, key, callback):
        self.working = True
        for b in (self.check, self.repair, self.cancel):
            b.setEnabled(False)
        self.output.setPlainText(tr('正在检测…' if key == 'check' else '正在处理本机系统授权与修复，请稍候…'))
        self.job = self.Work(key, callback)
        self.job.signals.done.connect(self.completed)
        self.job.signals.failed.connect(self.failed)
        self.pool.start(self.job)

    def completed(self, key, value):
        self.working = False
        self.check.setEnabled(True)
        self.cancel.setEnabled(True)
        if key == 'check':
            self.current = value
            endpoint = value.get('endpoint')
            lines = [f"程序：{('客户端' if self.role == 'client' else '服务端')}", '运行依赖：' + ('正常' if value['dependencies']['passed'] else '有缺项'), '防火墙：' + value['firewall']['provider']]
            if self.role == 'server':
                lines += [f"监听：{value['bind']}:{value['port']}", '允许来源：' + (', '.join(value['scopes']) or '仅本机')]
            if endpoint:
                lines += [f"目标：{endpoint['host']}:{endpoint['port']}", 'TCP：' + ('可连接' if endpoint['tcpReachable'] else '未连接'), 'HTTPS：' + ('证书验证通过' if endpoint.get('tlsVerified') else '未验证或验证失败' if endpoint.get('tlsVerified') is False else '未使用 HTTPS')]
            lines += value.get('notes', [])
            self.output.setPlainText(tr('\n'.join(lines) + '\n\n技术详情：\n') + json.dumps(value, ensure_ascii=False, indent=2))
            self.repair.setEnabled(value['needsFirewall'] or bool(value['packages']))
        else:
            self.output.setPlainText(tr(value['message'] + '\n' + '\n'.join(value.get('notes', [])) + '\n请重新检测，并从另一台电脑验证连接；规则不能覆盖公司策略或第三方防火墙。'))
            self.repair.setEnabled(False)

    def failed(self, key, message):
        self.working = False
        self.check.setEnabled(True)
        self.cancel.setEnabled(True)
        self.output.setPlainText(tr('未完成：' + message))
        self.repair.setEnabled(bool(self.current))

    def fix(self):
        if not self.current or self.working:
            return
        password = None
        if sys.platform.startswith('linux') and (not system_setup.is_admin()) and (not shutil.which('pkexec')):
            password, accepted = QInputDialog.getText(self, '系统授权', '请输入本机 sudo 密码（不保存）：', QLineEdit.EchoMode.Password)
            if not accepted:
                return
        value = self.current
        self.execute('repair', lambda: system_setup.repair(value, password))

    def reject(self):
        if not self.working:
            super().reject()
