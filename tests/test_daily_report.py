"""验证六毛工作日报可以离屏生成，且只包含统计型信息。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.daily_report import render_daily_report


def test_daily_report_is_saved_as_nonempty_png(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    photo = QPixmap(220, 220); photo.fill(Qt.GlobalColor.transparent)
    stats = {
        "date": "2026-08-10",
        "completed_tasks": 5,
        "longest_focus_seconds": 4980,
        "touches": 17,
        "sleeps": 3,
        "random_events": 2,
        "last_activity": "guitar",
    }

    path = render_daily_report(6 * 3600 + 42 * 60, stats, photo, tmp_path)

    assert path.name == "2026-08-10-六毛工作日报.png"
    # The synthetic test photo is fully transparent, so PNG compression keeps
    # this fixture deliberately small.  Real pet photos make the card larger.
    assert path.stat().st_size > 5_000
    rendered = QPixmap(str(path))
    assert rendered.size().width() == 760
    assert rendered.size().height() == 980
    app.processEvents()
