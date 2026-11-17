"""SteamPipe GUI — точка входа."""
import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from steampipe_gui import APP_ID, i18n, theme
from steampipe_gui.config import AppConfig
from steampipe_gui.session import SteamSession
from steampipe_gui.ui.main_window import MainWindow

APP_DIR = Path(__file__).resolve().parent


def _excepthook(kind, value, tb):
    # PyQt6 аварийно завершает процесс на исключении в слоте — печатаем и продолжаем работу.
    traceback.print_exception(kind, value, tb)


def main() -> int:
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)   # иконка и группировка окна в Wayland
    icon = APP_DIR / "steampipe-gui-logo.png"
    app.setWindowIcon(QIcon(str(icon)) if icon.exists() else QIcon.fromTheme("steam"))

    config = AppConfig.load()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    catalog = i18n.Catalog(data_home / APP_ID / "locales", APP_DIR / "steampipe_gui" / "locales")
    i18n.install(catalog)
    catalog.set_language(config.language)

    style = theme.create(app)
    session = SteamSession(config, APP_DIR)
    window = MainWindow(config, session, style)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
