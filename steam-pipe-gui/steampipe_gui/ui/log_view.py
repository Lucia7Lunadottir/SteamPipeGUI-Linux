"""Журнал: вывод steamcmd и сообщения программы, с подсветкой и ограничением длины."""
from __future__ import annotations

import re
from datetime import datetime
from enum import IntEnum

from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ..i18n import catalog, tr
from ..theme import ThemeBackend
from .widgets import button


class Level(IntEnum):
    RAW = 0
    INFO = 1
    OK = 2
    WARN = 3
    ERROR = 4

    @staticmethod
    def classify(line: str) -> "Level":
        """Уровень строки steamcmd по её тексту."""
        if re.search(r"\bERROR\b|\bFAILED\b|\bfailed\b", line):
            return Level.ERROR
        if re.search(r"\bWarning\b|\bWARNING\b", line):
            return Level.WARN
        if re.search(r"Successfully|\.\.\.\s*OK\b|Logged in OK", line):
            return Level.OK
        return Level.RAW


class _Highlighter(QSyntaxHighlighter):
    """Цвет строки по её уровню (хранится в userState блока)."""
    ROLES = {Level.RAW: "subtext", Level.INFO: "accent2", Level.OK: "green", Level.WARN: "yellow", Level.ERROR: "red"}

    def __init__(self, document, theme: ThemeBackend):
        super().__init__(document)
        self.theme = theme
        self.formats: dict[int, QTextCharFormat] = {}
        self.refresh()

    def refresh(self) -> None:
        self.formats = {}
        for level, role in self.ROLES.items():
            fmt = QTextCharFormat()
            fmt.setForeground(self.theme.color(role))
            self.formats[level] = fmt
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        fmt = self.formats.get(self.currentBlockState())
        if fmt is not None:
            self.setFormat(0, len(text), fmt)


class LogView(QWidget):
    def __init__(self, theme: ThemeBackend, max_lines: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = QLabel(objectName="LogHeader")
        self.btn_copy, self.btn_save, self.btn_clear = button(), button(), button()
        self.text = QPlainTextEdit(objectName="LogArea")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setMaximumBlockCount(max_lines)   # Qt сам отбрасывает старые строки — без пересчёта
        self.highlighter = _Highlighter(self.text.document(), theme)
        theme.changed.connect(self.highlighter.refresh)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.title)
        bar.addStretch()
        for b in (self.btn_copy, self.btn_save, self.btn_clear):
            bar.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 10)
        layout.setSpacing(4)
        layout.addLayout(bar)
        layout.addWidget(self.text, 1)

        self.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        self.btn_save.clicked.connect(self._save)
        self.btn_clear.clicked.connect(self.text.clear)
        catalog().language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.title.setText(tr("log.title").upper())
        self.btn_copy.setText(tr("log.copy"))
        self.btn_save.setText(tr("log.save"))
        self.btn_clear.setText(tr("log.clear"))

    def set_max_lines(self, n: int) -> None:
        self.text.setMaximumBlockCount(max(100, n))

    def append(self, line: str, level: Level | None = None) -> None:
        level = Level.classify(line) if level is None else level
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.text.appendPlainText(f"{datetime.now():%H:%M:%S}  {line}")
        block = self.text.document().lastBlock()
        block.setUserState(int(level))
        self.highlighter.rehighlightBlock(block)
        if at_bottom:   # не утаскивать вниз, если пользователь читает начало
            bar.setValue(bar.maximum())

    def _save(self) -> None:
        name = f"steampipe-{datetime.now():%Y%m%d-%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(self, tr("log.save"), name, "Log (*.log *.txt)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.text.toPlainText())
            except OSError as error:
                self.append(tr("log.save_failed", error=error), Level.ERROR)
