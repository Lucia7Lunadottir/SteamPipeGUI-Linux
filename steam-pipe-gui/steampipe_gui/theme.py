"""Оформление. Стиль — один QSS-шаблон с ролями цвета (@accent@, @surface0@, @red/60@).

Две реализации одного интерфейса ThemeBackend:
  • KitTheme     — если установлена equestria-qt-kit: темы персонажей Equestria OS,
                   выбор темы для приложения и перекраска на лету;
  • BuiltinTheme — своя тёмная палитра, когда библиотеки нет (обычная установка из AUR)."""
from __future__ import annotations

import re
from abc import abstractmethod
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

STYLE_TEMPLATE = Path(__file__).resolve().parent / "style.qss"
ICONS_DIR = Path(__file__).resolve().parent / "icons"
CACHE_DIR = Path.home() / ".cache" / "steampipe-gui" / "icons"

# Роли, которые использует шаблон и код. Совпадают с ролями equestria-qt-kit.
BUILTIN_PALETTE = {
    "base": "#1a1a2e", "mantle": "#16213e", "crust": "#0d1117",
    "surface0": "#0d1b2a", "surface1": "#1c3059", "surface2": "#2a4060",
    "text": "#e0e0e0", "subtext": "#9aa5b8",
    "accent": "#00d4ff", "accent2": "#5fb3ff",
    "green": "#7fd88f", "red": "#ff6b7d", "yellow": "#f5c96b",
    "on_accent": "#0d1117", "accent_hover": "#4de1ff",
}

_PALETTE_ROLES = (
    ("Window", "base"), ("WindowText", "text"), ("Base", "surface0"), ("AlternateBase", "mantle"),
    ("Text", "text"), ("Button", "surface0"), ("ButtonText", "text"), ("Highlight", "accent"),
    ("HighlightedText", "base"), ("ToolTipBase", "surface1"), ("ToolTipText", "text"),
    ("PlaceholderText", "subtext"), ("Link", "accent"),
)

_TOKEN = re.compile(r"@([a-z_0-9]+)(?:/(\d{1,3}))?@")
_SVG_TOKEN = re.compile(r"@svg:([^@]+?\.svg)@")


class ThemeBackend(QObject):
    """Общий интерфейс оформления. changed — после каждой перекраски."""
    changed = pyqtSignal()

    def __init__(self, app: QApplication, template: str):
        super().__init__()
        self.app, self.template = app, template

    @abstractmethod
    def color(self, role: str) -> QColor: ...

    def choices(self) -> list[tuple[str, str]]:
        """[(подпись, значение)] для списка тем; пустой — выбирать нечего.
        Подпись "" у значения означает «Как в системе» (переводит вызывающий)."""
        return []

    def current_choice(self) -> str:
        return ""

    def choose(self, value: str) -> None:
        pass


class BuiltinTheme(ThemeBackend):
    def __init__(self, app: QApplication, template: str):
        super().__init__(app, template)
        self.palette = dict(BUILTIN_PALETTE)
        pal = QPalette()
        for role, key in _PALETTE_ROLES:
            pal.setColor(getattr(QPalette.ColorRole, role), QColor(self.palette[key]))
        app.setPalette(pal)
        app.setStyleSheet(_TOKEN.sub(self._substitute, _SVG_TOKEN.sub(self._svg, template)))

    def _svg(self, m: re.Match) -> str:
        """@svg:путь.svg@ → копия SVG с цветами палитры (как делает equestria-qt-kit)."""
        try:
            text = _TOKEN.sub(self._substitute, Path(m.group(1)).read_text(encoding="utf-8"))
            out = CACHE_DIR / Path(m.group(1)).name
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if not out.exists() or out.read_text(encoding="utf-8") != text:
                out.write_text(text, encoding="utf-8")
            return out.as_posix()
        except OSError:
            return m.group(1)

    def _substitute(self, m: re.Match) -> str:
        value = self.palette.get(m.group(1))
        if value is None:
            return m.group(0)
        if m.group(2) is None:
            return value
        c = QColor(value)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {min(255, int(m.group(2)))})"

    def color(self, role: str) -> QColor:
        return QColor(self.palette.get(role, self.palette["text"]))


class KitTheme(ThemeBackend):
    def __init__(self, app: QApplication, template: str):
        super().__init__(app, template)
        from equestria_kit import qt as kit_qt, themes as kit_themes
        self._qt, self._themes = kit_qt, kit_themes
        self._manager = kit_qt.ThemeManager(app, template)
        self._manager.on_change(lambda _theme: self.changed.emit())

    def color(self, role: str) -> QColor:
        return self._qt.theme_color(role)

    def choices(self) -> list[tuple[str, str]]:
        return [("", self._themes.SYSTEM)] + [(name, name) for name in self._themes.THEMES]

    def current_choice(self) -> str:
        chosen = self._themes.choice()
        return chosen if chosen in self._themes.THEMES else self._themes.SYSTEM

    def choose(self, value: str) -> None:
        self._manager.apply(self._qt.pick_theme(value))


def create(app: QApplication) -> ThemeBackend:
    app.setStyle("Fusion")   # одинаковый вид в любом DE; цвета берутся из QPalette темы
    template = STYLE_TEMPLATE.read_text(encoding="utf-8").replace("@ICONS@", ICONS_DIR.as_posix())
    try:
        import equestria_kit.qt  # noqa: F401 — проверяем, установлена ли библиотека
    except ImportError:
        return BuiltinTheme(app, template)
    try:
        return KitTheme(app, template)
    except Exception as error:  # noqa: BLE001 — сломанная тема не должна мешать запуску
        print(f"[theme] equestria-qt-kit failed, using built-in theme: {error}")
        return BuiltinTheme(app, template)
