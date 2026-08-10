"""在已确认的六毛角色帧上绘制工作、音乐和成就配饰。

所有图层均以当前透明画布的比例绘制，不改变宠物窗口尺寸或原始人物轮廓。配饰采用简单原创
矢量造型，避免重新生成角色导致六根毛、身形或五官不一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap


@dataclass(frozen=True)
class Outfit:
    key: str
    name: str
    message: str


OUTFITS = (
    Outfit("paper_crown", "纸王冠", "小时候的秘密王国，今天重新开门。"),
    Outfit("big_coat", "大人外套", "偷偷试穿过的大人外套，现在也能认真穿好。"),
    Outfit("pilot", "小飞行员", "不是梦想职业，是允许自己飞远一点。"),
    Outfit("stage_star", "卧室歌手", "镜子前的小舞台，也曾装下整个未来。"),
    Outfit("blanket_cape", "毯子披风", "柔软也可以是一种勇敢。"),
    Outfit("explorer", "纸箱探险家", "卧室角落的远方，今天继续出发。"),
    Outfit("painter", "小画家", "把还没说清的心情先画下来。"),
    Outfit("astronaut", "月亮来客", "长大以后，也保留一点奔向星星的认真。"),
    Outfit("reader", "故事学者", "小小的大人，也在慢慢读懂世界。"),
    Outfit("dreamer", "可能性勋章", "不规定将来，只纪念你仍相信可能。"),
)


def unlocked_outfits(count: int) -> tuple[Outfit, ...]:
    """返回按累计工作时长解锁的前若干套配饰。"""

    return OUTFITS[: max(0, min(len(OUTFITS), int(count)))]


def draw_activity_overlay(
    source: QPixmap,
    activity: str = "none",
    outfit: str = "",
    phase: int = 0,
) -> QPixmap:
    """返回叠加活动物件和娃衣配饰后的新像素图。"""

    result = QPixmap(source)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    ratio = max(1.0, result.devicePixelRatio())
    w = result.width() / ratio
    h = result.height() / ratio
    painter.scale(ratio, ratio)
    line = max(1.5, w * 0.012)
    painter.setPen(QPen(QColor("#252d38"), line, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

    if outfit:
        _draw_outfit(painter, QRectF(0, 0, w, h), outfit)
    if activity == "computer":
        painter.setBrush(QColor("#546a7b"))
        painter.drawRoundedRect(QRectF(w * .25, h * .57, w * .52, h * .25), w * .025, w * .025)
        painter.setBrush(QColor("#bfe8ff"))
        painter.drawRoundedRect(QRectF(w * .30, h * .61, w * .42, h * .14), w * .014, w * .014)
        painter.setBrush(QColor("#fff4a8" if phase % 2 else "#8ce0ff"))
        painter.drawEllipse(QRectF(w * .49, h * .65, w * .05, w * .05))
        painter.setBrush(QColor("#384957"))
        painter.drawRoundedRect(QRectF(w * .20, h * .80, w * .62, h * .055), w * .02, w * .02)
    elif activity == "headphones":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#4a5166"), line * 2.2))
        painter.drawArc(QRectF(w * .24, h * .07, w * .52, h * .37), 15 * 16, 150 * 16)
        painter.setBrush(QColor("#6fc8ef"))
        painter.drawRoundedRect(QRectF(w * .19, h * .27, w * .12, h * .21), w * .025, w * .025)
        painter.drawRoundedRect(QRectF(w * .69, h * .27, w * .12, h * .21), w * .025, w * .025)
    elif activity == "guitar":
        painter.setBrush(QColor("#d59a51"))
        painter.drawEllipse(QRectF(w * .32, h * .64, w * .28, h * .22))
        painter.drawEllipse(QRectF(w * .46, h * .59, w * .24, h * .20))
        painter.setBrush(QColor("#604129"))
        painter.drawEllipse(QRectF(w * .47, h * .68, w * .07, w * .07))
        painter.drawRoundedRect(QRectF(w * .58, h * .46, w * .075, h * .28), w * .02, w * .02)
    elif activity == "drums":
        painter.setBrush(QColor("#74c7e9"))
        painter.drawEllipse(QRectF(w * .18, h * .73, w * .29, h * .14))
        painter.drawEllipse(QRectF(w * .53, h * .73, w * .29, h * .14))
        painter.drawLine(QPointF(w * .37, h * .56), QPointF(w * .55, h * .75))
        painter.drawLine(QPointF(w * .63, h * .56), QPointF(w * .45, h * .75))
    elif activity in {"reading", "writing"}:
        painter.setBrush(QColor("#fffaf0"))
        painter.drawRoundedRect(QRectF(w * .24, h * .61, w * .52, h * .24), w * .025, w * .025)
        painter.drawLine(QPointF(w * .50, h * .62), QPointF(w * .50, h * .83))
        if activity == "writing":
            painter.setPen(QPen(QColor("#5b6fa8"), line * 1.5))
            painter.drawLine(QPointF(w * .58, h * .56), QPointF(w * .69, h * .78))
    painter.end()
    return result


def _draw_outfit(painter: QPainter, rect: QRectF, outfit: str) -> None:
    """绘制一枚轻量配饰；每套都不遮住蓝色面部与六根毛。"""

    w, h = rect.width(), rect.height()
    if outfit == "paper_crown":
        painter.setBrush(QColor("#ffd95a"))
        points = [QPointF(w*.36,h*.14), QPointF(w*.42,h*.04), QPointF(w*.49,h*.13), QPointF(w*.57,h*.03), QPointF(w*.65,h*.14)]
        painter.drawPolygon(points)
    elif outfit == "big_coat":
        painter.setBrush(QColor(90, 110, 150, 185)); painter.drawRoundedRect(QRectF(w*.22,h*.63,w*.56,h*.28), w*.04,w*.04)
    elif outfit == "pilot":
        painter.setBrush(QColor("#f1c55b")); painter.drawEllipse(QRectF(w*.31,h*.10,w*.38,h*.12)); painter.drawLine(QPointF(w*.50,h*.72),QPointF(w*.80,h*.59))
    elif outfit == "stage_star":
        painter.setBrush(QColor("#d9b3ff")); painter.drawEllipse(QRectF(w*.74,h*.25,w*.12,w*.12)); painter.drawLine(QPointF(w*.76,h*.36),QPointF(w*.62,h*.65))
    elif outfit == "blanket_cape":
        painter.setBrush(QColor(112, 185, 220, 175)); painter.drawRoundedRect(QRectF(w*.17,h*.62,w*.66,h*.30),w*.07,w*.07)
    elif outfit == "explorer":
        painter.setBrush(QColor("#c69c62")); painter.drawEllipse(QRectF(w*.27,h*.10,w*.46,h*.12)); painter.drawRoundedRect(QRectF(w*.35,h*.05,w*.30,h*.09),w*.02,w*.02)
    elif outfit == "painter":
        painter.setBrush(QColor("#e8e4da")); painter.drawEllipse(QRectF(w*.30,h*.07,w*.40,h*.13)); painter.setBrush(QColor("#6a9bd2")); painter.drawEllipse(QRectF(w*.61,h*.09,w*.08,w*.05))
    elif outfit == "astronaut":
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(QColor("#d8e7ee"),w*.035)); painter.drawEllipse(QRectF(w*.16,h*.03,w*.68,h*.42))
    elif outfit == "reader":
        painter.setBrush(QColor("#59483f")); painter.drawEllipse(QRectF(w*.28,h*.31,w*.18,h*.12)); painter.drawEllipse(QRectF(w*.54,h*.31,w*.18,h*.12)); painter.drawLine(QPointF(w*.46,h*.36),QPointF(w*.54,h*.36))
    elif outfit == "dreamer":
        painter.setBrush(QColor("#ffde62")); painter.drawEllipse(QRectF(w*.70,h*.57,w*.16,w*.16)); painter.setBrush(QColor("#6fc8ef")); painter.drawEllipse(QRectF(w*.745,h*.615,w*.07,w*.07))
