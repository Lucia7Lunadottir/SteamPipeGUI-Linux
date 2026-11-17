"""Состояние подключения к Steam поверх SteamCmdRunner: кто вошёл, что сейчас выполняется.

Страницы интерфейса не трогают QProcess напрямую — только этот объект."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .config import AppConfig, BuildProfile
from .steamcmd import (BuildCommand, CommandResult, LoginCommand, Outcome, SessionCheckCommand, SteamCmdCommand,
                       SteamCmdLocator, SteamCmdRunner)
from .vdf import BuildScripts

CACHE_DIR = Path.home() / ".cache" / "steampipe-gui"


class SteamSession(QObject):
    state_changed = pyqtSignal()                 # вход/выход, занятость
    message = pyqtSignal(str, dict, str)         # ключ перевода, параметры, уровень: info/ok/warn/error
    build_finished = pyqtSignal(object, object)  # BuildProfile, CommandResult

    def __init__(self, config: AppConfig, app_dir: Path):
        super().__init__()
        self.config = config
        self.locator = SteamCmdLocator(app_dir)
        self.runner = SteamCmdRunner()
        self.user = ""                           # вошедший пользователь ("" — не вошли)
        self._build_profile: BuildProfile | None = None
        self.runner.started.connect(lambda _cmd: self.state_changed.emit())
        self.runner.finished.connect(self._on_finished)
        self.runner.failed_to_start.connect(self._on_failed_to_start)
        self.runner.login_confirmed.connect(self._on_login_confirmed)
        self.refresh_steamcmd()

    # ----- steamcmd -----
    def refresh_steamcmd(self) -> Path | None:
        self.runner.steamcmd = SteamCmdLocator.resolve(self.config.sdk_folder, self.config.steamcmd_path, self.locator)
        return self.runner.steamcmd

    @property
    def steamcmd(self) -> Path | None:
        return self.runner.steamcmd

    def build_output_dir(self) -> Path:
        """Логи и кэш сборок: в SDK (как у официального ContentBuilder), если steamcmd оттуда."""
        sdk = SteamCmdLocator.sdk_root(self.steamcmd)
        return sdk / "output" if sdk else CACHE_DIR / "output"

    def scripts(self) -> BuildScripts:
        return BuildScripts(CACHE_DIR / "scripts", self.build_output_dir())

    # ----- состояние -----
    @property
    def busy(self) -> bool:
        return self.runner.busy

    @property
    def logged_in(self) -> bool:
        return bool(self.user)

    # ----- действия -----
    def login(self, username: str, password: str, guard_code: str) -> bool:
        return self._run(LoginCommand(username, password, guard_code))

    def check_saved_session(self, username: str) -> bool:
        return bool(username) and self._run(SessionCheckCommand(username))

    def logout(self) -> None:
        """Забыть вход в этой программе. Сохранённая сессия steamcmd остаётся на диске."""
        self.user = ""
        self.state_changed.emit()

    def build(self, profile: BuildProfile) -> bool:
        if not self.logged_in:
            self.message.emit("build.need_login", {}, "error")
            return False
        try:
            app_vdf = self.scripts().write(profile)
        except OSError as error:
            self.message.emit("build.script_failed", {"error": error}, "error")
            return False
        self.message.emit("build.script_written", {"path": app_vdf}, "info")
        self._build_profile = profile
        return self._run(BuildCommand(self.user, app_vdf, len(profile.depots)))

    def cancel(self) -> None:
        self.runner.cancel()

    # ----- внутреннее -----
    def _run(self, command: SteamCmdCommand) -> bool:
        if self.runner.busy:
            self.message.emit("status.busy", {}, "warn")
            return False
        if self.refresh_steamcmd() is None:
            self.message.emit("steamcmd.not_found", {}, "error")
            return False
        return self.runner.run(command)

    def _on_login_confirmed(self, username: str) -> None:
        self.user = username
        self.state_changed.emit()

    def _on_failed_to_start(self, error: str) -> None:
        self.message.emit("steamcmd.failed_to_start", {"error": error}, "error")
        self.state_changed.emit()

    def _on_finished(self, command: SteamCmdCommand, result: CommandResult) -> None:
        if isinstance(command, (LoginCommand, SessionCheckCommand)):
            if result.ok:
                self.user = command.username
                self.message.emit("login.ok", {"user": command.username}, "ok")
            elif isinstance(command, LoginCommand):
                self.message.emit(result.message, result.args, "warn" if result.outcome is Outcome.CANCELLED else "error")
            else:
                self.message.emit("login.no_saved_session", {"user": command.username}, "info")
        elif isinstance(command, BuildCommand):
            profile, self._build_profile = self._build_profile, None
            if not command.logged_in and not result.ok:
                self.user = ""   # сохранённая сессия больше не действует
            level = "ok" if result.ok else ("warn" if result.outcome is Outcome.CANCELLED else "error")
            self.message.emit(result.message, result.args, level)
            if profile is not None:
                self.build_finished.emit(profile, result)
        self.state_changed.emit()
