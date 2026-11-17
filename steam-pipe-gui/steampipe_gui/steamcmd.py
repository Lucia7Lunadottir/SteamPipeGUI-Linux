"""Работа с steamcmd.

  • SteamCmdLocator — где лежит steamcmd (SDK, системный пакет, ~/.steam/steamcmd …);
  • SteamCmdCommand — одна операция: что передать steamcmd и как понимать его вывод.
    Наследники: LoginCommand, SessionCheckCommand, BuildCommand;
  • SteamCmdRunner  — запуск через QProcess, построчный разбор, запросы кода/пароля,
    отмена, итог операции.

Пароль не попадает в аргументы процесса (их видно в `ps` любому пользователю):
вход с паролем идёт через +runscript — файл с правами 0600 в $XDG_RUNTIME_DIR,
который удаляется сразу по завершении steamcmd. Сборка и проверка сессии используют
сохранённый вход steamcmd и пароль не получают вовсе."""
from __future__ import annotations

import os
import re
import shutil
import signal
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QStandardPaths, QTimer, pyqtSignal

_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


# ----------------------------------------------------------------- поиск steamcmd

class SteamCmdLocator:
    SDK_RELATIVE = ("tools/ContentBuilder/builder_linux/steamcmd.sh", "builder_linux/steamcmd.sh", "steamcmd.sh")

    def __init__(self, app_dir: Path):
        self.app_dir = app_dir

    def from_sdk(self, sdk_folder: str) -> Path | None:
        if not sdk_folder.strip():
            return None
        root = Path(sdk_folder).expanduser()
        for rel in self.SDK_RELATIVE:
            if (root / rel).is_file():
                return root / rel
        return None

    def detect(self) -> Path | None:
        home = Path.home()
        sdk_roots = [self.app_dir / "steamworks_sdk" / "sdk", home / "sdk", home / "SteamworksSDK"]
        sdk_roots += sorted(home.glob("steamworks_sdk*/sdk"), reverse=True)
        for root in sdk_roots:
            if found := self.from_sdk(str(root)):
                return found
        candidates = [home / ".steam/steamcmd/steamcmd.sh", home / "Steam/steamcmd/steamcmd.sh"]
        on_path = shutil.which("steamcmd")
        if on_path:
            candidates.append(Path(on_path))
        candidates += [Path("/usr/bin/steamcmd"), Path("/usr/games/steamcmd")]
        return next((p for p in candidates if p.is_file()), None)

    @staticmethod
    def resolve(sdk_folder: str, steamcmd_path: str, locator: "SteamCmdLocator") -> Path | None:
        """Порядок: явно указанный файл → SDK из настроек → автопоиск."""
        if steamcmd_path.strip() and Path(steamcmd_path).expanduser().is_file():
            return Path(steamcmd_path).expanduser()
        return locator.from_sdk(sdk_folder) or locator.detect()

    @staticmethod
    def sdk_root(steamcmd: Path | None) -> Path | None:
        """Корень ContentBuilder, если steamcmd взят из Steamworks SDK."""
        if steamcmd and steamcmd.parent.name == "builder_linux":
            return steamcmd.parent.parent
        return None


# ----------------------------------------------------------------- команды

class Outcome(Enum):
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CommandResult:
    outcome: Outcome
    message: str = ""                 # ключ перевода
    args: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


class Prompt(Enum):
    GUARD_CODE = "guard_code"
    PASSWORD = "password"


class SteamCmdCommand:
    """Базовая операция. Наследник задаёт аргументы и разбор строк вывода."""
    status_key = "status.working"

    # Общие признаки входа (разные версии steamcmd пишут по-разному).
    LOGIN_OK = re.compile(r"Logged in OK|Waiting for user info\.\.\.\s*OK|Logging in user .*\.\.\.\s*OK", re.I)
    LOGIN_FAIL = (
        (re.compile(r"Invalid Password|password.*incorrect", re.I), "login.fail_password"),
        (re.compile(r"Two-factor code mismatch|Invalid Login Auth Code|Invalid Steam Guard", re.I), "login.fail_guard"),
        (re.compile(r"Rate Limit Exceeded", re.I), "login.fail_rate_limit"),
        (re.compile(r"No Connection|Service Unavailable|Timeout", re.I), "login.fail_connection"),
        (re.compile(r"Cached credentials not found|No cached credentials", re.I), "login.fail_no_session"),
        (re.compile(r"Account Logon Denied|Login Failure|FAILED.*login|ERROR.*login", re.I), "login.fail_generic"),
    )
    PROMPTS = (
        (re.compile(r"(Steam Guard code|Two[- ]factor code|Auth Code)[^:]*:\s*$", re.I), Prompt.GUARD_CODE),
        (re.compile(r"^password:\s*$", re.I), Prompt.PASSWORD),
    )
    MOBILE_CONFIRM = re.compile(r"confirm the login in the Steam Mobile app", re.I)

    def __init__(self, username: str):
        self.username = username
        self.logged_in = False
        self.login_error = ""

    def args(self, script_dir: Path) -> list[str]:
        raise NotImplementedError

    def secrets(self) -> list[str]:
        return []

    def cleanup(self) -> None:
        pass

    def prompt_policy(self) -> bool:
        """True — на запрос кода/пароля спрашиваем пользователя; False — это ошибка (без интерактива)."""
        return False

    def feed(self, line: str, runner: "SteamCmdRunner") -> None:
        if not self.logged_in and self.LOGIN_OK.search(line):
            self.logged_in = True
            runner.login_confirmed.emit(self.username)
            return
        if self.MOBILE_CONFIRM.search(line):
            runner.notice.emit("login.confirm_mobile")
            return
        if not self.logged_in and not self.login_error:
            for rx, key in self.LOGIN_FAIL:
                if rx.search(line):
                    self.login_error = key
                    break

    def result(self, exit_code: int) -> CommandResult:
        if self.logged_in:
            return CommandResult(Outcome.OK)
        return CommandResult(Outcome.FAILED, self.login_error or "login.fail_generic", {"code": exit_code})

    @staticmethod
    def quote(value: str) -> str:
        """Аргумент консольной команды steamcmd: в кавычках, если в нём пробел или ';'."""
        return f'"{value}"' if re.search(r'[\s;]', value) else value


class LoginCommand(SteamCmdCommand):
    status_key = "status.logging_in"

    def __init__(self, username: str, password: str, guard_code: str = ""):
        super().__init__(username)
        self.password, self.guard_code = password, guard_code
        self._script: Path | None = None

    def args(self, script_dir: Path) -> list[str]:
        if not self.password:   # только имя — steamcmd возьмёт сохранённую сессию или спросит пароль
            return ["+login", self.username, "+quit"]
        parts = ["login", self.quote(self.username), self.quote(self.password)]
        if self.guard_code:
            parts.append(self.guard_code)
        script_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix="login-", suffix=".txt", dir=script_dir)   # создаётся с правами 0600
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(" ".join(parts) + "\nquit\n")
        self._script = Path(name)
        return ["+runscript", name]

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.guard_code) if s]

    def cleanup(self) -> None:
        if self._script:
            self._script.unlink(missing_ok=True)
            self._script = None

    def prompt_policy(self) -> bool:
        return True


class SessionCheckCommand(SteamCmdCommand):
    """Есть ли у steamcmd сохранённый вход для этого пользователя (без вопросов о пароле)."""
    status_key = "status.checking_session"

    def args(self, script_dir: Path) -> list[str]:
        return ["+@NoPromptForPassword", "1", "+login", self.username, "+quit"]


class BuildCommand(SteamCmdCommand):
    status_key = "status.uploading"

    BUILDING_DEPOT = re.compile(r"Building depot (\d+)", re.I)
    PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
    SUCCESS = re.compile(r"Successfully finished App ?ID (\d+) build \(BuildID (\d+)\)", re.I)
    ERROR = re.compile(r"^\s*ERROR[!:( ]|Build for depot \d+ failed|failed to commit|\bFailed\b.*build", re.I)
    STAGES = (
        (re.compile(r"Scanning content", re.I), "stage.scanning"),
        (re.compile(r"Uploading content|Upload\w* \d+ chunks", re.I), "stage.uploading"),
        (re.compile(r"Committing|Finalizing", re.I), "stage.committing"),
    )

    def __init__(self, username: str, app_vdf: Path, depot_count: int):
        super().__init__(username)
        self.app_vdf, self.depot_count = app_vdf, depot_count
        self.depot_index = 0
        self.build_id = ""
        self.errors: list[str] = []

    def args(self, script_dir: Path) -> list[str]:
        return ["+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
                "+login", self.username, "+run_app_build", str(self.app_vdf), "+quit"]

    def feed(self, line: str, runner: "SteamCmdRunner") -> None:
        super().feed(line, runner)
        if m := self.SUCCESS.search(line):
            self.build_id = m.group(2)
            return
        if m := self.BUILDING_DEPOT.search(line):
            self.depot_index = min(self.depot_index + 1, self.depot_count)
            runner.progress.emit("stage.depot", {"n": self.depot_index, "total": self.depot_count, "id": m.group(1)}, -1)
            return
        for rx, key in self.STAGES:
            if rx.search(line):
                runner.progress.emit(key, {}, -1)
                break
        if self.logged_in and (m := self.PERCENT.search(line)):
            runner.progress.emit("stage.uploading", {}, min(100, round(float(m.group(1)))))
        if self.logged_in and self.ERROR.search(line):
            self.errors.append(line.strip())

    def result(self, exit_code: int) -> CommandResult:
        if not self.logged_in:
            key = self.login_error or "login.fail_generic"
            if key in ("login.fail_generic", "login.fail_no_session", "login.fail_password"):
                key = "build.fail_session"   # сохранённый вход недействителен — нужно войти заново
            return CommandResult(Outcome.FAILED, key, {"code": exit_code})
        if self.build_id:
            return CommandResult(Outcome.OK, "build.done", {"build_id": self.build_id})
        detail = self.errors[-1] if self.errors else f"exit code {exit_code}"
        return CommandResult(Outcome.FAILED, "build.fail", {"detail": detail})


# ----------------------------------------------------------------- запуск

class SteamCmdRunner(QObject):
    output = pyqtSignal(str)                    # строка вывода steamcmd (пароль замаскирован)
    notice = pyqtSignal(str)                    # ключ перевода — важное событие для пользователя
    progress = pyqtSignal(str, dict, int)       # ключ этапа, параметры, процент (-1 — неизвестно)
    prompt = pyqtSignal(object)                 # Prompt — steamcmd ждёт ввода
    login_confirmed = pyqtSignal(str)
    started = pyqtSignal(object)                # SteamCmdCommand
    finished = pyqtSignal(object, object)       # SteamCmdCommand, CommandResult
    failed_to_start = pyqtSignal(str)           # текст ошибки

    def __init__(self):
        super().__init__()
        self.steamcmd: Path | None = None
        self._command: SteamCmdCommand | None = None
        self._cancelled = ""          # "" — нет; "user" — отменил пользователь; "prompt" — steamcmd ждал ввода
        self._tail = ""
        self._prompted = False
        self._prompt_text = ""
        runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
        self.script_dir = Path(runtime or tempfile.gettempdir()) / "steampipe-gui"
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if hasattr(QProcess, "UnixProcessParameters"):   # своя группа процессов — отмена гасит и детей steamcmd.sh
            params = QProcess.UnixProcessParameters()
            params.flags = QProcess.UnixProcessFlag.CreateNewSession
            self._process.setUnixProcessParameters(params)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(5000)
        self._kill_timer.timeout.connect(lambda: self._signal_group(signal.SIGKILL))
        self._cleanup_stale_scripts()

    @property
    def busy(self) -> bool:
        return self._command is not None

    @property
    def command(self) -> SteamCmdCommand | None:
        return self._command

    def run(self, command: SteamCmdCommand) -> bool:
        if self.busy:
            return False
        if not self.steamcmd or not self.steamcmd.is_file():
            self.failed_to_start.emit("steamcmd not found")
            return False
        try:
            args = command.args(self.script_dir)
        except OSError as error:
            command.cleanup()
            self.failed_to_start.emit(str(error))
            return False
        self._command, self._cancelled, self._tail, self._prompted = command, "", "", False
        self._prompt_text = ""
        program = str(self.steamcmd)
        if program.endswith(".sh"):   # у steamcmd.sh из распакованного SDK бывает снят бит запуска
            args, program = [program] + args, "bash"
        self._process.setWorkingDirectory(str(self.steamcmd.parent))
        self.started.emit(command)
        self._process.start(program, args)
        return True

    def cancel(self, reason: str = "user") -> None:
        if not self.busy or self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancelled = self._cancelled or reason
        self._signal_group(signal.SIGTERM)
        self._kill_timer.start()

    def _signal_group(self, sig: int) -> None:
        """steamcmd.sh запускает настоящий steamcmd дочерним процессом — сигнал всей группе."""
        pid = self._process.processId()
        if not pid:
            return
        try:
            if os.getpgid(pid) == pid:   # процесс — лидер своей группы (CreateNewSession)
                os.killpg(pid, sig)
                return
        except (ProcessLookupError, PermissionError):
            pass
        self._process.kill() if sig == signal.SIGKILL else self._process.terminate()

    def send_input(self, text: str) -> None:
        if self.busy:
            self._process.write((text + "\n").encode())
            self._prompted = False

    def wait(self, msecs: int = 5000) -> None:
        if self.busy:
            self._process.waitForFinished(msecs)

    # ----- внутреннее -----
    def _mask(self, line: str) -> str:
        for secret in self._command.secrets() if self._command else []:   # только целым словом
            line = re.sub(rf"(?<!\w){re.escape(secret)}(?!\w)", "********", line)
        return line

    def _read(self) -> None:
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self._tail += _ANSI.sub("", data)
        *lines, self._tail = re.split(r"\r\n|\r|\n", self._tail)
        for line in lines:
            self._handle_line(line)
        if self._tail.strip():
            self._check_prompt(self._tail)   # приглашение ко вводу приходит без перевода строки

    def _handle_line(self, line: str) -> None:
        if not line.strip() or self._command is None:
            return
        line = self._mask(line)
        self.output.emit(line)
        self._command.feed(line, self)
        if line.strip() == self._prompt_text:   # это приглашение уже обработано, пока строка была неполной
            self._prompt_text = ""
            return
        self._check_prompt(line)

    def _check_prompt(self, text: str) -> None:
        if self._prompted or self._command is None:
            return
        for rx, kind in SteamCmdCommand.PROMPTS:
            if rx.search(text.strip()):
                self._prompted = True
                self._prompt_text = text.strip()
                if self._command.prompt_policy():
                    self.prompt.emit(kind)
                else:   # без интерактива: steamcmd ждёт ввода, которого не будет
                    self.cancel("prompt")
                return

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._command is not None:
            command, self._command = self._command, None
            command.cleanup()
            self.failed_to_start.emit(self._process.errorString())

    def _on_finished(self, exit_code: int, _status) -> None:
        self._kill_timer.stop()
        if self._tail.strip():
            self._handle_line(self._tail)
        self._tail = ""
        command, self._command = self._command, None
        if command is None:
            return
        command.cleanup()
        if self._cancelled == "user":
            result = CommandResult(Outcome.CANCELLED, "status.cancelled")
        elif self._cancelled == "prompt" and not command.login_error:
            command.login_error = "login.fail_no_session"
            result = command.result(exit_code)
        else:
            result = command.result(exit_code)
        self.finished.emit(command, result)

    def _cleanup_stale_scripts(self) -> None:
        """Скрипты входа, оставшиеся после аварийного завершения."""
        for stale in self.script_dir.glob("login-*.txt"):
            stale.unlink(missing_ok=True)
