"""Главное окно: шапка, боковая навигация, страницы, полоса выполнения и журнал."""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                             QMessageBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from .. import VERSION
from ..config import AppConfig
from ..i18n import catalog, tr
from ..session import SteamSession
from ..steamcmd import Prompt, SteamCmdCommand
from ..theme import ThemeBackend
from .build_page import BuildPage
from .log_view import Level, LogView
from .pages import LoginPage, Page, SettingsPage
from .widgets import BusyIndicator, button

_LEVELS = {"info": Level.INFO, "ok": Level.OK, "warn": Level.WARN, "error": Level.ERROR}


class TaskBar(QFrame):
    """Что сейчас делает steamcmd: этап, полоса, «Отмена»."""

    def __init__(self, session: SteamSession):
        super().__init__(objectName="TaskBar")
        self.session = session
        self.stage = QLabel()
        self.bar = BusyIndicator()
        self.btn_cancel = button(role="danger")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.addWidget(self.stage)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.btn_cancel)
        self.btn_cancel.clicked.connect(session.cancel)
        session.runner.started.connect(self._on_started)
        session.runner.progress.connect(self._on_progress)
        session.runner.notice.connect(lambda key: self.stage.setText(tr(key)))
        session.state_changed.connect(self._on_state)
        catalog().language_changed.connect(lambda: self.btn_cancel.setText(tr("common.cancel")))
        self.btn_cancel.setText(tr("common.cancel"))
        self.setVisible(False)

    def _on_started(self, command: SteamCmdCommand) -> None:
        self.stage.setText(tr(command.status_key))
        self.bar.start()
        self.setVisible(True)

    def _on_progress(self, key: str, args: dict, percent: int) -> None:
        self.stage.setText(tr(key, **args) + (f" — {percent}%" if percent >= 0 else ""))
        self.bar.set_value(percent)

    def _on_state(self) -> None:
        if not self.session.busy:
            self.bar.stop()
            self.setVisible(False)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, session: SteamSession, theme: ThemeBackend):
        super().__init__()
        self.config, self.session, self.theme = config, session, theme
        self.resize(980, 720)
        if config.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(config.window_geometry.encode()))

        central = QWidget(objectName="Central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # шапка
        header = QFrame(objectName="Header")
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 10, 20, 10)
        title = QLabel("SteamPipe GUI", objectName="Title")
        self.status = QLabel(objectName="Status")
        h.addWidget(title)
        h.addStretch()
        h.addWidget(self.status)
        root.addWidget(header)

        # страницы
        self.log = LogView(theme, config.log_max_lines)
        self.pages: list[Page] = [
            LoginPage(session, config),
            BuildPage(session, config),
            SettingsPage(session, config, theme, self.log.set_max_lines),
        ]
        self.stack = QStackedWidget()
        sidebar = QFrame(objectName="Sidebar")
        sidebar.setFixedWidth(170)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 10, 8, 10)
        self.nav = QButtonGroup(self)
        self.nav_buttons = []
        for i, page in enumerate(self.pages):
            self.stack.addWidget(page)
            b = button()
            b.setCheckable(True)
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            side.addWidget(b)
        side.addStretch()
        side.addWidget(QLabel(f"v{VERSION}", objectName="Version"))
        self.nav.idClicked.connect(self.stack.setCurrentIndex)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.stack)
        split.addWidget(self.log)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(sidebar)
        content = QVBoxLayout()
        content.setSpacing(0)
        content.addWidget(split, 1)
        content.addWidget(TaskBar(session))
        body.addLayout(content, 1)
        root.addLayout(body, 1)

        session.message.connect(self._on_message)
        session.state_changed.connect(self._on_state)
        session.runner.output.connect(self.log.append)
        session.runner.notice.connect(lambda key: self.log.append(tr(key), Level.WARN))
        session.runner.prompt.connect(self._on_prompt)
        catalog().language_changed.connect(self.retranslate)

        self.retranslate()
        self._on_state()
        self.go_to(0 if not config.username else 1)
        self._greet()

    def go_to(self, index: int) -> None:
        self.nav_buttons[index].setChecked(True)
        self.stack.setCurrentIndex(index)

    def _greet(self) -> None:
        path = self.session.steamcmd
        if path:
            self.log.append(tr("steamcmd.using", path=path), Level.OK)
        else:
            self.log.append(tr("steamcmd.not_found"), Level.WARN)
            self.go_to(2)
            return
        if self.config.check_session_on_start and self.config.username:
            self.session.check_saved_session(self.config.username)

    # ----- реакция на сессию -----
    def _on_message(self, key: str, args: dict, level: str) -> None:
        self.log.append(tr(key, **args), _LEVELS.get(level, Level.INFO))
        if key == "build.fail_session":
            self.go_to(0)

    def _on_state(self) -> None:
        s = self.session
        if s.busy and s.runner.command is not None:
            text, state = tr(s.runner.command.status_key), ""
        elif s.logged_in:
            text, state = tr("status.logged_in", user=s.user), "ok"
        elif s.steamcmd is None:
            text, state = tr("steamcmd.not_found"), "error"
        else:
            text, state = tr("status.not_connected"), ""
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _on_prompt(self, kind: Prompt) -> None:
        """steamcmd ждёт код Steam Guard или пароль — спрашиваем и передаём ему на ввод."""
        if kind is Prompt.GUARD_CODE:
            value, ok = QInputDialog.getText(self, tr("prompt.guard_title"), tr("prompt.guard_text"))
        else:
            value, ok = QInputDialog.getText(self, tr("prompt.password_title"), tr("prompt.password_text"),
                                             QLineEdit.EchoMode.Password)
        if ok and value.strip():
            self.session.runner.send_input(value.strip())
        else:
            self.session.cancel()

    def retranslate(self) -> None:
        self.setWindowTitle("SteamPipe GUI")
        for b, page in zip(self.nav_buttons, self.pages):
            b.setText(tr(page.nav_key))
        self._on_state()

    def closeEvent(self, event) -> None:
        if self.session.busy:
            answer = QMessageBox.question(self, tr("quit.title"), tr("quit.busy"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.session.cancel()
            self.session.runner.wait(8000)
        self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.config.save()
        event.accept()
