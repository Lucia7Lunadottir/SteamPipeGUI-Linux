"""Страницы: общая основа Page, вход в Steam и настройки. Сборка — в build_page.py."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QVBoxLayout, QWidget)

from ..config import AppConfig
from ..i18n import catalog, tr
from ..session import SteamSession
from ..theme import ThemeBackend
from .widgets import PathField, SpinBox, button

SDK_URL = "https://partner.steamgames.com/downloads/steamworks_sdk.zip"


class Page(QWidget):
    """Основа страницы: заголовок, форма с переводимыми подписями, реакция на язык и состояние сессии."""
    nav_key = ""     # ключ перевода для кнопки в боковой панели
    title_key = ""

    def __init__(self, session: SteamSession, config: AppConfig):
        super().__init__()
        self.session, self.config = session, config
        self._labels: list[tuple[QLabel, str]] = []
        self.title = QLabel(objectName="PageTitle")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 20, 24, 16)
        self.body.setSpacing(10)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(self.title)

    def finish_setup(self) -> None:
        """Вызывается наследником в конце своего __init__."""
        catalog().language_changed.connect(self.retranslate)
        self.session.state_changed.connect(self.on_state)
        self.retranslate()
        self.on_state()

    def label(self, key: str, object_name: str = "") -> QLabel:
        lbl = QLabel(objectName=object_name) if object_name else QLabel()
        lbl.setWordWrap(True)
        self._labels.append((lbl, key))
        return lbl

    def form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        return form

    def row(self, form: QFormLayout, key: str, field) -> QLabel:
        lbl = QLabel()
        self._labels.append((lbl, key))
        form.addRow(lbl, field)
        return lbl

    def retranslate(self) -> None:
        self.title.setText(tr(self.title_key))
        for lbl, key in self._labels:
            lbl.setText(tr(key))

    def on_state(self) -> None:
        pass


class LoginPage(Page):
    nav_key, title_key = "nav.login", "login.title"

    def __init__(self, session: SteamSession, config: AppConfig):
        super().__init__(session, config)
        self.hint = self.label("login.hint", "Hint")
        self.f_user = QLineEdit(config.username)
        self.f_pass = QLineEdit()
        self.f_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_guard = QLineEdit()
        self.f_guard.setMaximumWidth(160)
        form = self.form()
        self.row(form, "login.username", self.f_user)
        self.row(form, "login.password", self.f_pass)
        self.row(form, "login.guard", self.f_guard)

        self.state = QLabel()
        self.btn_login = button(role="primary")
        self.btn_check = button()
        self.btn_logout = button()
        buttons = QHBoxLayout()
        for b in (self.btn_login, self.btn_check, self.btn_logout):
            buttons.addWidget(b)
        buttons.addStretch()

        self.body.addWidget(self.hint)
        self.body.addLayout(form)
        self.body.addSpacing(6)
        self.body.addLayout(buttons)
        self.body.addWidget(self.state)

        self.btn_login.clicked.connect(self.do_login)
        self.f_pass.returnPressed.connect(self.do_login)
        self.f_guard.returnPressed.connect(self.do_login)
        self.btn_check.clicked.connect(lambda: self.session.check_saved_session(self.username()))
        self.btn_logout.clicked.connect(self.session.logout)
        self.f_user.textChanged.connect(lambda: self.on_state())
        self.finish_setup()

    def username(self) -> str:
        return self.f_user.text().strip()

    def do_login(self) -> None:
        if self.session.busy or self.session.logged_in:
            return
        if not self.username():
            self.f_user.setFocus()
            self.session.message.emit("login.need_username", {}, "error")
            return
        self.config.username = self.username()
        self.config.save()
        if self.session.login(self.username(), self.f_pass.text(), self.f_guard.text().strip()):
            self.f_guard.clear()   # одноразовый код повторно не пригодится

    def retranslate(self) -> None:
        super().retranslate()
        self.f_guard.setPlaceholderText(tr("login.guard_placeholder"))
        self.f_pass.setPlaceholderText(tr("login.password_placeholder"))
        self.btn_login.setText(tr("login.button"))
        self.btn_check.setText(tr("login.check"))
        self.btn_logout.setText(tr("login.logout"))
        self.btn_logout.setToolTip(tr("login.logout_tip"))
        self.on_state()

    def on_state(self) -> None:
        busy, logged = self.session.busy, self.session.logged_in
        if logged:
            self.f_pass.clear()   # пароль больше не нужен — не держим его в памяти окна
        for w in (self.f_user, self.f_pass, self.f_guard):
            w.setEnabled(not busy and not logged)
        self.btn_login.setVisible(not logged)
        self.btn_check.setVisible(not logged)
        self.btn_login.setEnabled(not busy and bool(self.username()))
        self.btn_check.setEnabled(not busy and bool(self.username()))
        self.btn_logout.setVisible(logged)
        self.btn_logout.setEnabled(not busy)
        if logged:
            self.state.setObjectName("OkText")
            self.state.setText(tr("login.state_in", user=self.session.user))
        else:
            self.state.setObjectName("Hint")
            self.state.setText(tr("login.state_out"))
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class SettingsPage(Page):
    nav_key, title_key = "nav.settings", "settings.title"

    def __init__(self, session: SteamSession, config: AppConfig, theme: ThemeBackend, log_lines_changed):
        super().__init__(session, config)
        self.theme = theme
        self._log_lines_changed = log_lines_changed

        self.f_sdk = PathField("dir")
        self.f_sdk.setText(config.sdk_folder)
        self.f_cmd = PathField("file")
        self.f_cmd.setText(config.steamcmd_path)
        self.btn_detect = button()
        self.cmd_state = QLabel()
        self.cmd_state.setWordWrap(True)
        self.cmd_state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sdk_link = QLabel()
        self.sdk_link.setOpenExternalLinks(True)
        self.sdk_link.setObjectName("Hint")

        self.cb_lang = QComboBox()
        self.cb_theme = QComboBox()
        self.chk_session = QCheckBox()
        self.chk_session.setChecked(config.check_session_on_start)
        self.sp_lines = SpinBox()
        self.sp_lines.setRange(100, 100000)
        self.sp_lines.setSingleStep(500)
        self.sp_lines.setValue(config.log_max_lines)

        # одна форма на обе секции — подписи выровнены по одной линии
        form = self.form()
        form.addRow(self.label("settings.section_steam", "LogHeader"))
        self.row(form, "settings.sdk", self.f_sdk)
        self.row(form, "settings.steamcmd", self.f_cmd)
        detect_row = QHBoxLayout()
        detect_row.addWidget(self.btn_detect)
        detect_row.addWidget(self.cmd_state, 1)
        form.addRow("", detect_row)
        form.addRow("", self.sdk_link)
        gap = QWidget()
        gap.setFixedHeight(10)
        form.addRow(gap)
        form.addRow(self.label("settings.section_app", "LogHeader"))
        self.row(form, "settings.language", self.cb_lang)
        theme_label = self.row(form, "settings.theme", self.cb_theme)
        form.addRow("", self.chk_session)
        self.row(form, "settings.log_lines", self.sp_lines)
        self.sp_lines.setMaximumWidth(140)
        has_themes = bool(theme.choices())   # выбор темы есть только с equestria-qt-kit
        theme_label.setVisible(has_themes)
        self.cb_theme.setVisible(has_themes)
        self.body.addLayout(form)

        self.f_sdk.changed.connect(self._on_sdk)
        self.f_cmd.changed.connect(self._on_cmd)
        self.btn_detect.clicked.connect(self._detect)
        self.cb_lang.activated.connect(self._on_language)
        self.cb_theme.activated.connect(lambda i: self.theme.choose(self.cb_theme.itemData(i)))
        self.chk_session.toggled.connect(self._on_session_check)
        self.sp_lines.editingFinished.connect(self._on_lines)
        self.finish_setup()

    # ----- изменения применяются сразу -----
    def _on_sdk(self, path: str) -> None:
        self.config.sdk_folder = path
        if path and self.session.locator.from_sdk(path) is None:
            self.session.message.emit("steamcmd.not_in_sdk", {"path": path}, "warn")
        self._apply_steamcmd()

    def _on_cmd(self, path: str) -> None:
        self.config.steamcmd_path = path
        self._apply_steamcmd()

    def _detect(self) -> None:
        found = self.session.locator.detect()
        if found is None:
            self.session.message.emit("steamcmd.not_found", {}, "error")
            return
        sdk = next((p for p in found.parents if self.session.locator.from_sdk(str(p)) == found), None)
        self.config.sdk_folder = str(sdk) if sdk else ""
        self.config.steamcmd_path = "" if sdk else str(found)
        self.f_sdk.setText(self.config.sdk_folder)
        self.f_cmd.setText(self.config.steamcmd_path)
        self._apply_steamcmd()

    def _apply_steamcmd(self) -> None:
        path = self.session.refresh_steamcmd()
        self.config.save()
        if path:
            self.session.message.emit("steamcmd.using", {"path": path}, "ok")
        self.on_state()

    def _on_language(self, index: int) -> None:
        self.config.language = self.cb_lang.itemData(index)
        self.config.save()
        catalog().set_language(self.config.language)

    def _on_session_check(self, on: bool) -> None:
        self.config.check_session_on_start = on
        self.config.save()

    def _on_lines(self) -> None:
        self.config.log_max_lines = self.sp_lines.value()
        self.config.save()
        self._log_lines_changed(self.config.log_max_lines)

    # ----- вид -----
    def retranslate(self) -> None:
        super().retranslate()
        self.btn_detect.setText(tr("settings.detect"))
        self.sdk_link.setText(tr("settings.sdk_link", url=SDK_URL))
        self.chk_session.setText(tr("settings.check_session"))
        self.f_sdk.edit.setPlaceholderText(tr("settings.sdk_placeholder"))
        self.f_cmd.edit.setPlaceholderText(tr("settings.steamcmd_placeholder"))

        self.cb_lang.clear()
        self.cb_lang.addItem(tr("settings.language_system"), "")
        for code in catalog().languages:
            self.cb_lang.addItem(catalog().language_name(code), code)
        self.cb_lang.setCurrentIndex(max(0, self.cb_lang.findData(self.config.language)))

        self.cb_theme.clear()
        for label, value in self.theme.choices():
            self.cb_theme.addItem(label or tr("settings.theme_system"), value)
        self.cb_theme.setCurrentIndex(max(0, self.cb_theme.findData(self.theme.current_choice())))
        self.on_state()

    def on_state(self) -> None:
        path = self.session.steamcmd
        self.cmd_state.setObjectName("OkText" if path else "ErrorText")
        self.cmd_state.setText(tr("settings.steamcmd_ok", path=path) if path else tr("steamcmd.not_found"))
        self.cmd_state.style().unpolish(self.cmd_state)
        self.cmd_state.style().polish(self.cmd_state)
        busy = self.session.busy
        for w in (self.f_sdk, self.f_cmd, self.btn_detect):
            w.setEnabled(not busy)
