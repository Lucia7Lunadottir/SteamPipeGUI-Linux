"""Небольшие общие виджеты."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QWidget

from ..i18n import catalog, tr


def button(text: str = "", role: str = "") -> QPushButton:
    """role: "primary" — главное действие страницы, "danger" — отмена/удаление."""
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    return b


def set_invalid(edit: QWidget, invalid: bool) -> None:
    if bool(edit.property("invalid")) != invalid:
        edit.setProperty("invalid", invalid)
        edit.style().unpolish(edit)
        edit.style().polish(edit)


class SpinBox(QSpinBox):
    """QSpinBox с отступом текста. Под QSS Qt ставит внутреннее поле ввода вплотную к левому краю
    и игнорирует горизонтальный padding — отступ задаётся полю напрямую."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.lineEdit().setTextMargins(6, 0, 0, 0)


class PathField(QWidget):
    """Поле пути + «Обзор…». mode: "dir" или "file"."""
    changed = pyqtSignal(str)   # после правки (editingFinished) или выбора в диалоге

    def __init__(self, mode: str = "dir", parent: QWidget | None = None):
        super().__init__(parent)
        self.mode = mode
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.browse = button()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.browse)
        self.browse.clicked.connect(self._pick)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.text()))
        catalog().language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.browse.setText(tr("common.browse"))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def _pick(self) -> None:
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, tr("common.pick_folder"), self.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, tr("common.pick_file"), self.text())
        if path:
            self.edit.setText(path)
            self.changed.emit(path)


class BusyIndicator(QWidget):
    """Полоса выполнения: бегущий отрезок, пока процент неизвестен, иначе — заполнение.
    Рисуется сама (не QProgressBar): глобальный QSS ломает анимацию стандартной полосы."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = -1
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._value = -1
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._value = -1
        self.update()

    def set_value(self, percent: int) -> None:
        self._value = percent
        if percent >= 0:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        groove = QColor(self.palette().color(self.palette().ColorRole.Base))
        accent = self.palette().color(self.palette().ColorRole.Highlight)
        p.setBrush(groove)
        p.drawRoundedRect(r, 3, 3)
        if self._value >= 0:
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(r.x(), r.y(), r.width() * self._value / 100, r.height()), 3, 3)
        elif self._timer.isActive():
            seg = r.width() * 0.25
            x = -seg + (r.width() + seg) * self._phase
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(x, r.y(), seg, r.height()).intersected(r), 3, 3)
