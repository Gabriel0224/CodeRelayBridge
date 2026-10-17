"""Generate the native bundle icon from the existing app mark at build time."""
from pathlib import Path
import subprocess

def mac_icon(directory, role):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication(['crb-icon-builder', '-platform', 'offscreen'])
    directory = Path(directory)
    iconset = directory / 'CodeRelayBridge.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = size * scale
            image = QImage(pixels, pixels, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(pixels / 64, pixels / 64)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#2f7959' if role == 'client' else '#3b6aa0'))
            painter.drawRoundedRect(3, 3, 58, 58, 16, 16)
            painter.setPen(QColor('white'))
            painter.setFont(QFont('Helvetica', 28, QFont.Weight.Bold))
            from PySide6.QtCore import QRect
            painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, 'R')
            painter.end()
            suffix = '@2x' if scale == 2 else ''
            assert image.save(str(iconset / f'icon_{size}x{size}{suffix}.png'))
    output = directory / 'CodeRelayBridge.icns'
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', '-o', str(output), str(iconset)], check=True, capture_output=True, timeout=20)
    return output
