"""渲染 Lili 活动与娃衣图层预览，供本地视觉验收；不修改原始素材。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.accessories import OUTFITS, draw_activity_overlay
from onepic_desktop_pet.resources import resource_path


def _idle_frame() -> QPixmap:
    manifest = resource_path("assets/pet/manifest.json")
    import json

    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    first = data["animations"]["idle"][0]
    return QPixmap(str(Path(manifest).parent / first))


def render(output: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = _idle_frame().scaled(210, 210, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    cards = [("经典六毛", "none", "")]
    cards += [(label, key, "") for label, key in (("敲电脑", "computer"), ("戴耳机", "headphones"), ("弹吉他", "guitar"), ("打鼓", "drums"), ("看书", "reading"), ("写字", "writing"))]
    cards += [(outfit.name, "none", outfit.key) for outfit in OUTFITS]
    columns = 4; card_w = 250; card_h = 250
    canvas = QPixmap(columns * card_w, ((len(cards) + columns - 1) // columns) * card_h)
    canvas.fill(QColor("#e7edf2"))
    painter = QPainter(canvas); painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont("Microsoft YaHei UI", 11); painter.setFont(font); painter.setPen(QColor("#27313d"))
    for index, (label, activity, outfit) in enumerate(cards):
        row, column = divmod(index, columns)
        pixmap = draw_activity_overlay(source, activity, outfit, index)
        x = column * card_w + (card_w - pixmap.width()) // 2
        y = row * card_h + 28
        painter.drawText(QRectF(column * card_w, row * card_h + 4, card_w, 24), Qt.AlignmentFlag.AlignCenter, label)
        painter.drawPixmap(x, y, pixmap)
    painter.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output), "PNG")
    app.processEvents()


if __name__ == "__main__":
    render(Path("build/visual/lili-0.9-accessories.png"))
