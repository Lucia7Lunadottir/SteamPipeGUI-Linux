"""Страница сборки: профили игр, депо, ветка, запуск загрузки."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRegularExpression, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QRegularExpressionValidator
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..config import AppConfig, BuildProfile, DepotSpec
from ..i18n import catalog, tr
from ..session import SteamSession
from ..steamcmd import CommandResult, Outcome, SteamCmdLocator
from ..vdf import ProfileValidator, VdfImporter
from .pages import Page
from .widgets import button

DOCS_UPLOAD_URL = "https://partner.steamgames.com/doc/sdk/uploading"
DOCS_DEPOTS_URL = "https://partner.steamgames.com/doc/store/application/depots"
BUILDS_URL = "https://partner.steamgames.com/apps/builds/{app_id}"


class DepotsTable(QWidget):
    """Таблица депо профиля: ID, папка с содержимым, исключения через запятую."""
    changed = pyqtSignal()
    COL_ID, COL_ROOT, COL_EXCL = range(3)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.table = QTableWidget(0, 3)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ID, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_ROOT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_EXCL, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_EXCL, 180)
        self.table.setMinimumHeight(120)

        self.btn_add, self.btn_remove, self.btn_browse = button(), button(), button()
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        for b in (self.btn_add, self.btn_browse, self.btn_remove):
            bar.addWidget(b)
        bar.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(bar)

        self.app_id_provider = lambda: ""
        self.btn_add.clicked.connect(self.add_row)
        self.btn_remove.clicked.connect(self.remove_row)
        self.btn_browse.clicked.connect(lambda: self.browse(self.table.currentRow()))
        self.table.cellDoubleClicked.connect(lambda r, c: self.browse(r) if c == self.COL_ROOT else None)
        self.table.itemChanged.connect(lambda _item: self.changed.emit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        catalog().language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.table.setHorizontalHeaderLabels([tr("depots.id"), tr("depots.folder"), tr("depots.exclusions")])
        self.table.horizontalHeaderItem(self.COL_ID).setToolTip(tr("depots.id_tip"))
        self.table.horizontalHeaderItem(self.COL_ROOT).setToolTip(tr("depots.folder_tip"))
        self.table.horizontalHeaderItem(self.COL_EXCL).setToolTip(tr("depots.exclusions_tip"))
        self.btn_add.setText(tr("depots.add"))
        self.btn_remove.setText(tr("depots.remove"))
        self.btn_browse.setText(tr("depots.browse"))
        self._update_buttons()

    # ----- данные -----
    def set_depots(self, depots: list[DepotSpec]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for depot in depots:
            self._append(depot)
        self.table.blockSignals(False)
        self._update_buttons()

    def depots(self) -> list[DepotSpec]:
        result = []
        for r in range(self.table.rowCount()):
            excl = [e.strip() for e in self._text(r, self.COL_EXCL).replace(";", ",").split(",") if e.strip()]
            result.append(DepotSpec(self._text(r, self.COL_ID), self._text(r, self.COL_ROOT), excl))
        return result

    def _text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def _append(self, depot: DepotSpec) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, self.COL_ID, QTableWidgetItem(depot.depot_id))
        self.table.setItem(r, self.COL_ROOT, QTableWidgetItem(depot.content_root))
        self.table.setItem(r, self.COL_EXCL, QTableWidgetItem(", ".join(depot.exclusions)))

    # ----- действия -----
    def add_row(self) -> None:
        """Новое депо: ID = App ID + номер, как их выдаёт Steamworks по умолчанию."""
        app_id = self.app_id_provider()
        used = {d.depot_id for d in self.depots()}
        depot_id = ""
        if app_id.isdigit():
            n = int(app_id) + 1
            while str(n) in used:
                n += 1
            depot_id = str(n)
        self.table.blockSignals(True)
        self._append(DepotSpec(depot_id))
        self.table.blockSignals(False)
        self.table.selectRow(self.table.rowCount() - 1)
        self.changed.emit()
        self.browse(self.table.rowCount() - 1)

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def browse(self, row: int) -> None:
        if row < 0:
            return
        folder = QFileDialog.getExistingDirectory(self, tr("common.pick_folder"), self._text(row, self.COL_ROOT))
        if folder:
            self.table.item(row, self.COL_ROOT).setText(folder)   # itemChanged → changed

    def _update_buttons(self) -> None:
        has = self.table.currentRow() >= 0
        self.btn_remove.setEnabled(has)
        self.btn_browse.setEnabled(has)


class ScriptPreviewDialog(QDialog):
    def __init__(self, text: str, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(tr("build.scripts_title"))
        self.resize(720, 520)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(QFont("monospace"))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        layout.addWidget(box)


class BuildPage(Page):
    nav_key, title_key = "nav.build", "build.title"

    def __init__(self, session: SteamSession, config: AppConfig):
        super().__init__(session, config)
        self._loading = False
        self.validator = ProfileValidator()

        # профили
        self.cb_profile = QComboBox()
        self.cb_profile.setMinimumWidth(200)
        self.btn_new, self.btn_copy, self.btn_rename, self.btn_delete, self.btn_import = (button() for _ in range(5))
        self.btn_delete.setProperty("role", "danger")
        prof_row = QHBoxLayout()
        prof_row.addWidget(self.cb_profile, 1)
        for b in (self.btn_new, self.btn_copy, self.btn_rename, self.btn_delete, self.btn_import):
            prof_row.addWidget(b)

        # поля
        self.f_app = QLineEdit()
        self.f_app.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,10}")))
        self.f_app.setMaximumWidth(160)
        self.f_desc = QLineEdit()
        self.depots = DepotsTable()
        self.depots.app_id_provider = lambda: self.f_app.text().strip()

        self.chk_preview = QCheckBox()
        self.chk_live = QCheckBox()
        self.cb_branch = QComboBox()
        self.cb_branch.setEditable(True)
        self.cb_branch.setMinimumWidth(180)
        live_row = QHBoxLayout()
        live_row.addWidget(self.chk_live)
        live_row.addWidget(self.cb_branch)
        live_row.addStretch()
        self.live_hint = self.label("build.live_hint", "Hint")
        self.branch_warning = self.label("build.branch_default_warning", "Warning")

        self.howto = self.label("build.howto", "Hint")
        self.docs_links = QLabel()
        self.docs_links.setOpenExternalLinks(True)

        form = self.form()
        self.row(form, "build.profile", prof_row)
        self.lbl_app = self.row(form, "build.app_id", self.f_app)
        self.lbl_desc = self.row(form, "build.description", self.f_desc)
        self.lbl_depots = self.row(form, "build.depots", self.depots)
        form.addRow("", self.chk_preview)
        form.addRow("", live_row)
        form.addRow("", self.branch_warning)
        form.addRow("", self.live_hint)

        # действия
        self.btn_start = button(role="primary")
        self.btn_scripts, self.btn_output = button(), button()
        self.builds_link = QLabel()
        self.builds_link.setOpenExternalLinks(True)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_start)
        actions.addWidget(self.btn_scripts)
        actions.addWidget(self.btn_output)
        actions.addStretch()
        actions.addWidget(self.builds_link)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.body.addWidget(self.howto)
        self.body.addWidget(self.docs_links)
        self.body.addLayout(form)
        self.body.addSpacing(4)
        self.body.addLayout(actions)
        self.body.addWidget(self.result)

        self.cb_profile.activated.connect(self._switch_profile)
        self.btn_new.clicked.connect(self._new_profile)
        self.btn_copy.clicked.connect(self._copy_profile)
        self.btn_rename.clicked.connect(self._rename_profile)
        self.btn_delete.clicked.connect(self._delete_profile)
        self.btn_import.clicked.connect(self._import_profile)
        for signal in (self.f_app.textChanged, self.f_desc.textChanged, self.depots.changed,
                       self.chk_preview.toggled, self.chk_live.toggled, self.cb_branch.currentTextChanged):
            signal.connect(self._store)
        self.btn_start.clicked.connect(self.start_build)
        self.btn_scripts.clicked.connect(self._show_scripts)
        self.btn_output.clicked.connect(self._open_output)
        session.build_finished.connect(self._on_build_finished)

        self._load_profile()
        self.finish_setup()

    # ----- профиль <-> виджеты -----
    @property
    def profile(self) -> BuildProfile:
        return self.config.profile()

    def _load_profile(self) -> None:
        self._loading = True
        p = self.profile
        self.cb_profile.clear()
        self.cb_profile.addItems(self.config.profile_names())
        self.cb_profile.setCurrentText(p.name)
        self.f_app.setText(p.app_id)
        self.f_desc.setText(p.description)
        self.depots.set_depots(p.depots)
        self.chk_preview.setChecked(p.preview)
        self.chk_live.setChecked(p.set_live)
        self.cb_branch.clear()
        self.cb_branch.addItems(self.config.known_branches)
        self.cb_branch.setCurrentText(p.branch)
        self._loading = False
        self._refresh_view()

    def _store(self) -> None:
        if self._loading:
            return
        p = self.profile
        p.app_id = self.f_app.text().strip()
        p.description = self.f_desc.text()
        p.depots = self.depots.depots()
        p.preview = self.chk_preview.isChecked()
        p.set_live = self.chk_live.isChecked()
        p.branch = self.cb_branch.currentText().strip()
        self._refresh_view()

    def _refresh_view(self) -> None:
        p = self.profile
        is_default = p.branch.lower() == "default"
        self.cb_branch.setEnabled(p.set_live)
        self.branch_warning.setVisible(p.set_live and is_default)
        if p.app_id.isdigit():
            url = BUILDS_URL.format(app_id=p.app_id)
            self.builds_link.setText(f'<a href="{url}">{tr("build.builds_link")}</a>')
        else:
            self.builds_link.clear()
        if p.last_build_id and not self.session.busy:
            self._show_result(tr("build.last_build", build_id=p.last_build_id), "Hint")
        elif not self.session.busy:
            self.result.clear()

    # ----- профили -----
    def _switch_profile(self, index: int) -> None:
        self.config.current_profile = self.cb_profile.itemText(index)
        self.config.save()
        self._load_profile()

    def _ask_name(self, title_key: str, current: str = "") -> str:
        name, ok = QInputDialog.getText(self, tr(title_key), tr("profile.name"), text=current)
        return name.strip() if ok else ""

    def _new_profile(self) -> None:
        if name := self._ask_name("profile.new"):
            self.config.add_profile(BuildProfile(name=name))
            self.config.save()
            self._load_profile()

    def _copy_profile(self) -> None:
        if name := self._ask_name("profile.copy", tr("profile.copy_name", name=self.profile.name)):
            self.config.add_profile(self.profile.copy(name))
            self.config.save()
            self._load_profile()

    def _rename_profile(self) -> None:
        name = self._ask_name("profile.rename", self.profile.name)
        if name and name != self.profile.name:
            self.profile.name = self.config.unique_name(name)
            self.config.current_profile = self.profile.name
            self.config.save()
            self._load_profile()

    def _delete_profile(self) -> None:
        answer = QMessageBox.question(self, tr("profile.delete"), tr("profile.delete_confirm", name=self.profile.name))
        if answer == QMessageBox.StandardButton.Yes:
            self.config.remove_current_profile()
            self.config.save()
            self._load_profile()

    def _import_profile(self) -> None:
        sdk = SteamCmdLocator.sdk_root(self.session.steamcmd)   # там лежат скрипты официального ContentBuilder
        start = str(sdk / "scripts") if sdk and (sdk / "scripts").is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("profile.import"), start, "SteamPipe app build (*.vdf)")
        if not path:
            return
        try:
            profile = VdfImporter().load(Path(path))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr("profile.import"), tr("profile.import_failed", error=error))
            return
        self.config.add_profile(profile)
        self.config.remember_branch(profile.branch)
        self.config.save()
        self._load_profile()
        self.session.message.emit("profile.imported", {"name": profile.name, "depots": len(profile.depots)}, "ok")

    # ----- сборка -----
    def start_build(self) -> None:
        self._store()
        p = self.profile
        issues = self.validator.validate(p)
        if issues:
            text = "\n".join("• " + tr(i.key, **i.args) for i in issues)
            for i in issues:
                self.session.message.emit(i.key, i.args, "error")
            QMessageBox.warning(self, tr("build.check_failed"), text)
            return
        if not self.session.logged_in:
            QMessageBox.information(self, tr("build.title"), tr("build.need_login"))
            return
        if not self._confirm(p):
            return
        self.config.remember_branch(p.branch)
        self.config.save()
        self._show_result(tr("status.uploading"), "Hint")
        self.session.build(p)

    def _confirm(self, p: BuildProfile) -> bool:
        lines = [tr("build.confirm_app", app_id=p.app_id)]
        lines += [tr("build.confirm_depot", id=d.depot_id, path=d.content_root) for d in p.depots]
        if p.preview:
            lines.append(tr("build.confirm_preview"))
        elif p.set_live:
            lines.append(tr("build.confirm_live", branch=p.branch))
        else:
            lines.append(tr("build.confirm_no_live"))
        box = QMessageBox(QMessageBox.Icon.Question, tr("build.confirm_title"), "\n".join(lines),
                          QMessageBox.StandardButton.Cancel, self)
        go = box.addButton(tr("build.start"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(go)
        box.exec()
        return box.clickedButton() is go

    def _on_build_finished(self, profile: BuildProfile, result: CommandResult) -> None:
        if result.ok and result.args.get("build_id"):
            profile.last_build_id = result.args["build_id"]
            self.config.save()
        if profile is self.profile:
            style = "OkText" if result.ok else ("Hint" if result.outcome is Outcome.CANCELLED else "ErrorText")
            self._show_result(tr(result.message, **result.args), style)

    def _show_result(self, text: str, style: str) -> None:
        self.result.setObjectName(style)
        self.result.setText(text)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def _show_scripts(self) -> None:
        self._store()
        ScriptPreviewDialog(self.session.scripts().preview(self.profile), self).exec()

    def _open_output(self) -> None:
        folder = self.session.build_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ----- вид -----
    def retranslate(self) -> None:
        super().retranslate()
        self.btn_new.setText(tr("profile.new"))
        self.btn_copy.setText(tr("profile.copy"))
        self.btn_rename.setText(tr("profile.rename"))
        self.btn_delete.setText(tr("profile.delete"))
        self.btn_import.setText(tr("profile.import"))
        self.btn_import.setToolTip(tr("profile.import_tip"))
        self.docs_links.setText(
            f'{tr("build.docs")}: <a href="{DOCS_UPLOAD_URL}">{tr("build.docs_upload")} ↗</a> · '
            f'<a href="{DOCS_DEPOTS_URL}">{tr("build.docs_depots")} ↗</a>')
        self.f_app.setPlaceholderText("480")
        self.f_desc.setPlaceholderText(tr("build.description_placeholder"))
        for w in (self.lbl_app, self.f_app):
            w.setToolTip(tr("build.app_id_tip"))
        for w in (self.lbl_desc, self.f_desc):
            w.setToolTip(tr("build.description_tip"))
        self.lbl_depots.setToolTip(tr("build.depots_tip"))
        self.chk_live.setToolTip(tr("build.set_live_tip"))
        self.cb_branch.setToolTip(tr("build.set_live_tip"))
        self.chk_preview.setText(tr("build.preview"))
        self.chk_preview.setToolTip(tr("build.preview_tip"))
        self.chk_live.setText(tr("build.set_live"))
        self.btn_start.setText(tr("build.start"))
        self.btn_scripts.setText(tr("build.show_scripts"))
        self.btn_output.setText(tr("build.open_output"))
        self.btn_output.setToolTip(tr("build.open_output_tip"))
        self._refresh_view()

    def on_state(self) -> None:
        busy = self.session.busy
        self.btn_start.setEnabled(not busy)
        self.btn_start.setToolTip("" if self.session.logged_in else tr("build.need_login"))
        for w in (self.cb_profile, self.btn_new, self.btn_copy, self.btn_rename, self.btn_delete, self.btn_import):
            w.setEnabled(not busy)
        self.btn_delete.setEnabled(not busy and len(self.config.profiles) > 1)
