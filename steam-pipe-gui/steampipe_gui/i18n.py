"""Переводы: каждый файл locales/<код>.json — отдельный язык, списка языков в коде нет.

Ключи смысловые (`build.start`), образец для переводчиков — en.json.
Виджеты подписываются на `Catalog.language_changed` и сами обновляют свои подписи
в методе retranslate()."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLocale, QObject, QTranslator, pyqtSignal
from PyQt6.QtWidgets import QApplication

FALLBACK = "en"


def _key(value: str) -> str:
    """'zh_TW.UTF-8' → 'zh_tw', 'pt-BR' → 'pt_br'."""
    return value.split(".")[0].split("@")[0].replace("-", "_").strip().casefold()


class Catalog(QObject):
    language_changed = pyqtSignal(str)

    def __init__(self, *dirs: Path):
        super().__init__()
        self._translations: dict[str, dict[str, str]] = {}
        for directory in dirs:
            for path in sorted(directory.glob("*.json")):
                try:
                    entries = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as error:
                    print(f"[i18n] {path}: {error}", file=sys.stderr)
                    continue
                if isinstance(entries, dict):
                    self._translations.setdefault(path.stem, entries)
        self._qt_translator: QTranslator | None = None
        self.lang = self.detect()

    # ----- языки -----
    @property
    def languages(self) -> list[str]:
        return sorted(self._translations, key=self.language_name)

    def language_name(self, code: str) -> str:
        name = self._translations.get(code, {}).get("_language_name")
        if name:
            return name
        native = QLocale(code).nativeLanguageName()
        return native[:1].upper() + native[1:] if native else code

    def match(self, value: str) -> str:
        """Ближайший имеющийся язык: zh_TW → zh_TW, а если его нет — zh."""
        available = {_key(code): code for code in self._translations}
        code = _key(value)
        while code:
            if code in available:
                return available[code]
            code = code.rsplit("_", 1)[0] if "_" in code else ""
        return ""

    def detect(self) -> str:
        for env in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            for value in os.environ.get(env, "").split(":"):
                if code := self.match(value):
                    return code
        return self.match(FALLBACK) or next(iter(self._translations), FALLBACK)

    def set_language(self, code: str) -> str:
        """code = "" — язык системы."""
        self.lang = self.match(code) if code else self.detect()
        self.lang = self.lang or self.detect()
        self._install_qt_translator()
        self.language_changed.emit(self.lang)
        return self.lang

    def _install_qt_translator(self) -> None:
        """Стандартные кнопки Qt (OK/Cancel в диалогах) — на том же языке."""
        app = QApplication.instance()
        if app is None:
            return
        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
        self._qt_translator = QTranslator(app)
        locale = QLocale(self.lang)
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if self._qt_translator.load(locale, "qtbase", "_", path):
            app.installTranslator(self._qt_translator)
        QLocale.setDefault(locale)

    # ----- строки -----
    def tr(self, key: str, **kwargs) -> str:
        text = (self._translations.get(self.lang, {}).get(key)
                or self._translations.get(FALLBACK, {}).get(key)
                or key)
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return text
        return text


_catalog: Catalog | None = None


def install(catalog: Catalog) -> None:
    global _catalog
    _catalog = catalog


def catalog() -> Catalog:
    assert _catalog is not None, "i18n.install() must be called first"
    return _catalog


def tr(key: str, **kwargs) -> str:
    return catalog().tr(key, **kwargs)
