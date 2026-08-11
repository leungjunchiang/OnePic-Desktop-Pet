"""在已确认的六毛角色帧上绘制配饰，并加载用户确认的完整动作/娃衣素材。

所有图层均以当前透明画布的比例绘制，不改变宠物窗口尺寸或原始人物轮廓。配饰采用简单原创
矢量造型，避免重新生成角色导致六根毛、身形或五官不一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from .growth import ACTION_SPRITES
from .resources import resource_path


@dataclass(frozen=True)
class Outfit:
    key: str
    name: str
    message: str


OUTFITS = (
    Outfit("hour-01", "兔兔搭子", "一小时达成。六毛换上兔兔装，陪你轻轻向前。"),
    Outfit("hour-02", "小熊搭子", "两小时达成。认真工作的小熊也要记得抱抱自己。"),
    Outfit("hour-03", "鲨鱼搭子", "三小时达成。摸鱼一会儿，也不影响继续游向目标。"),
    Outfit("hour-04", "蜜蜂搭子", "四小时达成。半日的小蜜蜂辛苦啦。"),
    Outfit("hour-05", "草莓搭子", "五小时达成。给犯困的下午添一点甜。"),
    Outfit("hour-06", "熊猫搭子", "六小时达成。六毛满血复活，继续陪你。"),
    Outfit("hour-07", "恐龙搭子", "七小时达成。快下班了，再稳稳走一小段。"),
    Outfit("hour-08", "太空搭子", "八小时今日毕业。六毛陪你飞到今天的终点。"),
    Outfit("hour-09", "舞台歌手", "九小时达成。把今天的坚持唱给自己听。"),
    Outfit("hour-10", "驯鹿搭子", "十小时达成。很晚了，六毛来接你回家。"),
    Outfit("hour-11", "荒野勇士", "十一小时达成。勇敢不是硬撑，也包括好好休息。"),
    Outfit("hour-12", "荒野国王", "十二小时达成。为小小大人加冠：你是自己荒野里的国王。"),
)


SPECIAL_ACTIVITY_SPRITES = {
    key: f"assets/pet/daily-actions/{filename}"
    for key, filename in ACTION_SPRITES.items()
}

SPECIAL_OUTFIT_SPRITES = {
    f"hour-{hour:02d}": f"assets/pet/hourly-outfits/{hour:02d}-hour.png"
    for hour in range(1, 13)
}


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

    if activity in SPECIAL_ACTIVITY_SPRITES:
        return _full_sprite(source, SPECIAL_ACTIVITY_SPRITES[activity])
    if outfit in SPECIAL_OUTFIT_SPRITES:
        source = _full_sprite(source, SPECIAL_OUTFIT_SPRITES[outfit])
        outfit = ""

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
        painter.setPen(QPen(QColor("#26344f"), line * 3.2))
        painter.drawArc(QRectF(w * .16, h * .02, w * .68, h * .45), 12 * 16, 156 * 16)
        painter.setBrush(QColor("#42c9f5"))
        painter.drawRoundedRect(QRectF(w * .12, h * .25, w * .17, h * .25), w * .035, w * .035)
        painter.drawRoundedRect(QRectF(w * .71, h * .25, w * .17, h * .25), w * .035, w * .035)
        painter.setBrush(QColor("#ffe36e"))
        painter.drawEllipse(QRectF(w * .075, h * .33, w * .055, w * .055))
        painter.drawEllipse(QRectF(w * .87, h * .33, w * .055, w * .055))
    elif activity == "guitar":
        painter.setBrush(QColor("#e2a84f"))
        painter.drawEllipse(QRectF(w * .20, h * .58, w * .38, h * .31))
        painter.drawEllipse(QRectF(w * .41, h * .53, w * .33, h * .28))
        painter.setBrush(QColor("#493725"))
        painter.drawEllipse(QRectF(w * .42, h * .67, w * .09, w * .09))
        painter.setBrush(QColor("#6e4b2d"))
        painter.drawRoundedRect(QRectF(w * .61, h * .31, w * .105, h * .40), w * .02, w * .02)
        painter.setPen(QPen(QColor("#fff3c7"), line * .8))
        painter.drawLine(QPointF(w * .66, h * .32), QPointF(w * .47, h * .79))
    elif activity == "drums":
        painter.setBrush(QColor("#36bde8"))
        painter.drawEllipse(QRectF(w * .08, h * .68, w * .38, h * .20))
        painter.drawEllipse(QRectF(w * .54, h * .68, w * .38, h * .20))
        painter.setBrush(QColor("#f5cf54"))
        painter.drawEllipse(QRectF(w * .35, h * .75, w * .30, h * .17))
        painter.setPen(QPen(QColor("#5d3f29"), line * 1.7))
        painter.drawLine(QPointF(w * .28, h * .47), QPointF(w * .56, h * .73))
        painter.drawLine(QPointF(w * .72, h * .47), QPointF(w * .44, h * .73))
    elif activity in {"reading", "writing"}:
        painter.setBrush(QColor("#fffaf0"))
        painter.drawRoundedRect(QRectF(w * .24, h * .61, w * .52, h * .24), w * .025, w * .025)
        painter.drawLine(QPointF(w * .50, h * .62), QPointF(w * .50, h * .83))
        if activity == "writing":
            painter.setPen(QPen(QColor("#5b6fa8"), line * 1.5))
            painter.drawLine(QPointF(w * .58, h * .56), QPointF(w * .69, h * .78))
    painter.end()
    return result


def _full_sprite(source: QPixmap, relative_path: str) -> QPixmap:
    """把完整透明动作素材按源画布等比居中，保持桌宠窗口大小恒定。"""

    sprite = QPixmap(str(resource_path(relative_path)))
    if sprite.isNull():
        return QPixmap(source)
    result = QPixmap(source.size())
    result.fill(Qt.GlobalColor.transparent)
    result.setDevicePixelRatio(source.devicePixelRatio())
    scaled = sprite.scaled(
        source.size(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # scaled() drops DPR. Restoring it prevents half-sprite clipping on 200%
    # displays and keeps the imported high-resolution artwork sharp.
    scaled.setDevicePixelRatio(source.devicePixelRatio())
    painter = QPainter(result)
    painter.drawPixmap(
        (result.width() - scaled.width()) // 2,
        (result.height() - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return result


def _draw_outfit(painter: QPainter, rect: QRectF, outfit: str) -> None:
    """绘制一枚轻量配饰；每套都不遮住蓝色面部与六根毛。"""

    w, h = rect.width(), rect.height()
    if outfit == "paper_hat":
        painter.setBrush(QColor("#f4e2b8"))
        points = [QPointF(w*.33,h*.14), QPointF(w*.50,h*.025), QPointF(w*.68,h*.14)]
        painter.drawPolygon(points)
        painter.drawLine(QPointF(w*.50,h*.025), QPointF(w*.50,h*.14))
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
    elif outfit == "wild_king":
        painter.setBrush(QColor(109, 52, 122, 190)); painter.drawRoundedRect(QRectF(w*.18,h*.58,w*.64,h*.34),w*.06,w*.06)
        painter.setBrush(QColor("#ffd43b"))
        points = [QPointF(w*.30,h*.15), QPointF(w*.36,h*.025), QPointF(w*.45,h*.12), QPointF(w*.52,h*.015), QPointF(w*.61,h*.12), QPointF(w*.69,h*.025), QPointF(w*.74,h*.15)]
        painter.drawPolygon(points)
        painter.setBrush(QColor("#ef5b5b")); painter.drawEllipse(QRectF(w*.49,h*.08,w*.055,w*.055))
