"""统一矢量勾选绘制：复用 Qt Boolean 与列表模型，不依赖系统主题或字体字形。"""

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox as NativeCheckBox, QListWidget as NativeListWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem


def draw_check(painter, rect, checked, *, enabled=True, hover=False, partial=False):
    """逻辑尺寸绘制，Qt 自动映射 DPI；禁用选中仍有填充和白色勾。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRectF(rect).adjusted(1, 1, -1, -1)
    fill = "#087f91" if enabled else "#66858d"
    border = "#075968" if enabled else "#566b72"
    if not checked and not partial:
        fill = "#e0f1f3" if hover and enabled else "#ffffff" if enabled else "#edf1f2"
        border = "#0b8293" if hover and enabled else "#526971" if enabled else "#87979d"
    elif hover and enabled:
        fill = "#056777"
    painter.setPen(QPen(QColor(border), 1.4))
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(box, 3, 3)
    if checked or partial:
        pen = QPen(QColor("#ffffff"), max(1.8, box.width() * .13))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        if partial:
            path.moveTo(box.left()+box.width()*.25, box.center().y())
            path.lineTo(box.left()+box.width()*.75, box.center().y())
        else:
            path.moveTo(box.left()+box.width()*.22, box.top()+box.height()*.53)
            path.lineTo(box.left()+box.width()*.43, box.top()+box.height()*.73)
            path.lineTo(box.left()+box.width()*.79, box.top()+box.height()*.28)
        painter.drawPath(path)
    painter.restore()


class AppCheckBox(NativeCheckBox):
    """保留 clicked/toggled/checkState、可访问性和键盘操作，只替换绘制。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(20 + (8 + self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0), max(24, self.fontMetrics().height()+4))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        box = QRectF(self.width()-20 if rtl else 0, (self.height()-20)/2, 20, 20)
        draw_check(painter, box, self.isChecked(), enabled=self.isEnabled(), hover=self.underMouse() or self.isDown(), partial=self.checkState()==Qt.CheckState.PartiallyChecked)
        painter.setPen(QColor("#203847" if self.isEnabled() else "#596b74"))
        text_rect = self.rect().adjusted(0 if rtl else 28, 0, -28 if rtl else 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.TextFlag.TextShowMnemonic, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor("#087f91"), 1, Qt.PenStyle.DotLine)); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5), 3, 3)


class CheckItemDelegate(QStyledItemDelegate):
    """列表沿用原生文本/选中/点击区域，在实际 indicator 区域覆盖统一勾。"""
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is None:
            return
        current = QStyleOptionViewItem(option); self.initStyleOption(current, index)
        style = current.widget.style() if current.widget is not None else self.parent().style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, current, current.widget)
        draw_check(painter, rect, state == Qt.CheckState.Checked.value,
                   partial=state == Qt.CheckState.PartiallyChecked.value,
                   enabled=bool(current.state & QStyle.StateFlag.State_Enabled),
                   hover=bool(current.state & QStyle.StateFlag.State_MouseOver))


class CheckListWidget(NativeListWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setItemDelegate(CheckItemDelegate(self))
        self.setMouseTracking(True)
