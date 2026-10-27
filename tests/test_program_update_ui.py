"""验证更新确认窗口会告知第三方下载与官方校验。"""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from onepic_desktop_pet.app import DesktopPetApplication
from onepic_desktop_pet.program_updates import ProgramRelease, ProgramUpdateCheckResult


def test_update_confirmation_discloses_fallback_and_sha256(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.show_speech = lambda *_args: None
    controller = DesktopPetApplication.__new__(DesktopPetApplication)
    QObject.__init__(controller)
    controller.window = window
    controller._program_update_manual = True
    captured = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: captured.append(args[2]) or QMessageBox.StandardButton.No)
    release = ProgramRelease('0.23.999', 'v0.23.999', 'https://github.com/a/b',
        'Lili-Windows-x64-Setup.exe', 'https://github.com/a/b', 100, None, '0'*64)
    try:
        controller._program_update_checked(ProgramUpdateCheckResult('0.23.352', '0.23.999', release))
        assert len(captured) == 1
        assert '第三方' in captured[0] and 'IP' in captured[0] and 'SHA-256' in captured[0]
    finally:
        window.close(); window.deleteLater(); controller.deleteLater(); app.processEvents()
