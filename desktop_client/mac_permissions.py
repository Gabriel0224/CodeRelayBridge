"""Current-app macOS permission checks; never delegate TCC identity to Python."""
import ctypes
import sys
from functools import lru_cache
SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'

@lru_cache(maxsize=1)
def _frameworks():
    if sys.platform != 'darwin':
        raise RuntimeError('This permission is only available on macOS')
    pointer = ctypes.c_void_p
    ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    ax.AXIsProcessTrustedWithOptions.argtypes = [pointer]
    ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    cf.CFDictionaryCreate.argtypes = [pointer, pointer, pointer, ctypes.c_long, pointer, pointer]
    cf.CFDictionaryCreate.restype = pointer
    cf.CFRelease.argtypes = [pointer]
    return (ax, cf)

def accessibility_granted():
    """No prompts, process launch, UI control, or network access."""
    if sys.platform != 'darwin':
        return None
    ax, _ = _frameworks()
    return bool(ax.AXIsProcessTrusted())

def request_accessibility():
    """Only invoked by an explicit UI click, inside the installed App itself."""
    ax, cf = _frameworks()
    pointer = ctypes.c_void_p
    keys = (pointer * 1)(pointer.in_dll(ax, 'kAXTrustedCheckOptionPrompt').value)
    values = (pointer * 1)(pointer.in_dll(cf, 'kCFBooleanTrue').value)
    options = cf.CFDictionaryCreate(None, keys, values, 1, None, None)
    if not options:
        raise RuntimeError('Could not create macOS permission options')
    try:
        return bool(ax.AXIsProcessTrustedWithOptions(options))
    finally:
        cf.CFRelease(options)

def permission_widget(parent=None):
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout
    from desktop_client.i18n import QLabel, QPushButton

    class PermissionCard(QFrame):

        def __init__(self):
            super().__init__(parent)
            self.setObjectName('macPermissions')
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel('macOS 辅助功能'))
            self.status = QLabel('正在检测授权状态…')
            self.status.setObjectName('accessibilityStatus')
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            help_text = QLabel('授权对象是当前 CodeRelayBridge App，不是 Python。此权限用于本机界面联动，不是聊天、文件或终端接入的前提。')
            help_text.setWordWrap(True)
            layout.addWidget(help_text)
            row = QHBoxLayout()
            self.authorize = QPushButton('打开辅助功能授权')
            self.authorize.setObjectName('accessibilityAuthorize')
            self.authorize.clicked.connect(self.open_settings)
            row.addWidget(self.authorize)
            refresh = QPushButton('重新检测')
            refresh.setObjectName('accessibilityRefresh')
            refresh.clicked.connect(self.refresh)
            row.addWidget(refresh)
            row.addStretch()
            layout.addLayout(row)
            QApplication.instance().applicationStateChanged.connect(lambda state: self.refresh() if state == Qt.ApplicationState.ApplicationActive and self.isVisible() else None)

        def showEvent(self, event):
            super().showEvent(event)
            self.refresh()

        def refresh(self):
            try:
                granted = accessibility_granted()
                self.status.setText('辅助功能：已授权' if granted else '辅助功能：未授权')
            except (OSError, RuntimeError, AttributeError):
                self.status.setText('辅助功能：检测失败，请重新检测')

        def open_settings(self):
            try:
                request_accessibility()
                if not QDesktopServices.openUrl(QUrl(SETTINGS_URL)):
                    raise RuntimeError('Could not open System Settings')
                self.status.setText('请在系统设置中启用当前 CodeRelayBridge App；返回此窗口后会重新检测。')
            except (OSError, RuntimeError, AttributeError):
                self.status.setText('无法打开授权设置，请前往系统设置 → 隐私与安全性 → 辅助功能。')
    return PermissionCard()
