"""Скрипты сборки SteamPipe (формат KeyValues / VDF).

  • KeyValues        — запись и разбор текста VDF;
  • ProfileValidator — проверка профиля до запуска steamcmd;
  • BuildScripts     — app_build_<appid>.vdf + depot_build_<id>.vdf по профилю;
  • VdfImporter      — профиль из готового app_build-скрипта (например, из SDK/ContentBuilder/scripts).

steamcmd не понимает escape-последовательностей в VDF (в его же примерах пути вида
"D:\\build" пишутся как есть), поэтому кавычку в значении записать нельзя: в путях
она запрещена проверкой, а в описании заменяется на типографскую."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import BuildProfile, DepotSpec

Node = dict[str, "str | Node | list"]


class KeyValues:
    @staticmethod
    def dump(root: str, node: Node) -> str:
        lines: list[str] = []
        KeyValues._dump_node(root, node, 0, lines)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _dump_node(key: str, node: Node, depth: int, out: list[str]) -> None:
        pad = "\t" * depth
        out.append(f'{pad}"{key}"')
        out.append(f"{pad}{{")
        for k, value in node.items():
            for item in (value if isinstance(value, list) else [value]):   # список — повтор ключа
                if isinstance(item, dict):
                    KeyValues._dump_node(k, item, depth + 1, out)
                else:
                    out.append(f'{pad}\t"{k}"\t"{item}"')
        out.append(f"{pad}}}")

    _TOKEN = re.compile(r'"([^"]*)"|(\{)|(\})|([^\s{}"]+)')

    @staticmethod
    def parse(text: str) -> tuple[str, Node]:
        """Корневой ключ и его содержимое. Ключи — в нижнем регистре (VDF к регистру безразличен),
        повторяющиеся ключи собираются в список."""
        text = re.sub(r"//[^\n]*", "", text)
        tokens: list[tuple[str, str]] = []
        for m in KeyValues._TOKEN.finditer(text):
            if m.group(2):
                tokens.append(("{", ""))
            elif m.group(3):
                tokens.append(("}", ""))
            else:
                tokens.append(("s", m.group(1) if m.group(1) is not None else m.group(4)))
        pos = 0

        def read_node() -> Node:
            nonlocal pos
            node: Node = {}
            while pos < len(tokens) and tokens[pos][0] != "}":
                kind, key = tokens[pos]
                pos += 1
                if kind != "s" or pos >= len(tokens):
                    raise ValueError("malformed VDF")
                if tokens[pos][0] == "{":
                    pos += 1
                    value = read_node()
                    pos += 1   # }
                elif tokens[pos][0] == "s":
                    value = tokens[pos][1]
                    pos += 1
                else:
                    raise ValueError("malformed VDF")
                key = key.lower()
                if key in node:
                    prev = node[key]
                    node[key] = (prev if isinstance(prev, list) else [prev]) + [value]
                else:
                    node[key] = value
            return node

        if len(tokens) < 2 or tokens[0][0] != "s" or tokens[1][0] != "{":
            raise ValueError("malformed VDF")
        root = tokens[0][1].lower()
        pos = 2
        return root, read_node()


@dataclass
class Issue:
    """Ошибка в профиле: ключ перевода + параметры."""
    key: str
    args: dict = field(default_factory=dict)


class ProfileValidator:
    def validate(self, profile: BuildProfile) -> list[Issue]:
        issues: list[Issue] = []
        if not profile.app_id.isdigit():
            issues.append(Issue("error.app_id"))
        if not profile.depots:
            issues.append(Issue("error.no_depots"))
        seen: set[str] = set()
        for n, depot in enumerate(profile.depots, 1):
            issues += self._depot(n, depot, seen)
        if profile.set_live:
            branch = profile.branch.strip()
            if not branch:
                issues.append(Issue("error.branch_empty"))
            elif branch.lower() == "default":
                issues.append(Issue("error.branch_default"))
            elif '"' in branch:
                issues.append(Issue("error.quote", {"what": branch}))
        return issues

    @staticmethod
    def _depot(n: int, depot: DepotSpec, seen: set[str]) -> list[Issue]:
        issues: list[Issue] = []
        if not depot.depot_id.isdigit():
            issues.append(Issue("error.depot_id", {"n": n}))
        elif depot.depot_id in seen:
            issues.append(Issue("error.depot_duplicate", {"id": depot.depot_id}))
        seen.add(depot.depot_id)
        root = Path(depot.content_root).expanduser()
        if not depot.content_root.strip():
            issues.append(Issue("error.content_empty", {"n": n}))
        elif '"' in depot.content_root:
            issues.append(Issue("error.quote", {"what": depot.content_root}))
        elif not root.is_dir():
            issues.append(Issue("error.content_missing", {"path": depot.content_root}))
        elif not any(root.iterdir()):
            issues.append(Issue("error.content_empty_dir", {"path": depot.content_root}))
        issues += [Issue("error.quote", {"what": e}) for e in depot.exclusions if '"' in e]
        return issues


class BuildScripts:
    """Пишет скрипты сборки в out_dir и возвращает путь к app_build-скрипту."""

    def __init__(self, out_dir: Path, build_output: Path):
        self.out_dir, self.build_output = out_dir, build_output

    @staticmethod
    def clean_text(text: str) -> str:
        return " ".join(text.replace('"', "”").split())

    def app_build(self, profile: BuildProfile) -> Node:
        node: Node = {
            "AppID": profile.app_id,
            "Desc": self.clean_text(profile.description),
            "BuildOutput": str(self.build_output),
            "ContentRoot": "",
            "SetLive": profile.branch.strip() if profile.set_live else "",
            "Preview": "1" if profile.preview else "0",
            "Depots": {d.depot_id: str(self.depot_path(d)) for d in profile.depots},
        }
        return node

    @staticmethod
    def depot_build(depot: DepotSpec) -> Node:
        node: Node = {
            "DepotID": depot.depot_id,
            "ContentRoot": str(Path(depot.content_root).expanduser()),
            "FileMapping": {"LocalPath": "*", "DepotPath": ".", "Recursive": "1"},
        }
        exclusions = [e.strip() for e in depot.exclusions if e.strip()]
        if exclusions:
            node["FileExclusion"] = exclusions
        return node

    def depot_path(self, depot: DepotSpec) -> Path:
        return self.out_dir / f"depot_build_{depot.depot_id}.vdf"

    def app_path(self, profile: BuildProfile) -> Path:
        return self.out_dir / f"app_build_{profile.app_id}.vdf"

    def preview(self, profile: BuildProfile) -> str:
        parts = [f"// {self.app_path(profile)}", KeyValues.dump("AppBuild", self.app_build(profile))]
        for depot in profile.depots:
            parts += [f"// {self.depot_path(depot)}", KeyValues.dump("DepotBuildConfig", self.depot_build(depot))]
        return "\n".join(parts)

    def write(self, profile: BuildProfile) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.build_output.mkdir(parents=True, exist_ok=True)
        for depot in profile.depots:
            self.depot_path(depot).write_text(
                KeyValues.dump("DepotBuildConfig", self.depot_build(depot)), encoding="utf-8")
        path = self.app_path(profile)
        path.write_text(KeyValues.dump("AppBuild", self.app_build(profile)), encoding="utf-8")
        return path


class VdfImporter:
    """Профиль из app_build-скрипта. Пути из Windows/Wine (Z:\\home\\…) переводятся в Linux-пути."""

    def load(self, path: Path) -> BuildProfile:
        root, node = KeyValues.parse(path.read_text(encoding="utf-8", errors="replace"))
        if root != "appbuild":
            raise ValueError(f"not an AppBuild script: {root}")
        base = path.parent
        app_root = self._path(self._str(node, "contentroot"), base)
        depots = node.get("depots", {})
        profile = BuildProfile(
            name=self._str(node, "appid") or path.stem,
            app_id=self._str(node, "appid"),
            description=self._str(node, "desc"),
            preview=self._str(node, "preview") == "1",
        )
        branch = self._str(node, "setlive")
        if branch:
            profile.set_live, profile.branch = True, branch
        if isinstance(depots, dict):
            for depot_id, value in depots.items():
                profile.depots.append(self._depot(depot_id, value, base, app_root))
        return profile

    def _depot(self, depot_id: str, value, base: Path, app_root: str) -> DepotSpec:
        if isinstance(value, str):   # ссылка на отдельный depot_build-скрипт
            file = Path(self._path(value, base))
            if not file.is_file():   # путь из Windows (D:\…\scripts\depot.vdf) — ищем файл рядом со скриптом
                file = base / value.replace("\\", "/").rsplit("/", 1)[-1]
            try:
                _, value = KeyValues.parse(file.read_text(encoding="utf-8", errors="replace"))
                base = file.parent
            except (OSError, ValueError):
                return DepotSpec(depot_id, app_root)
        if not isinstance(value, dict):
            return DepotSpec(depot_id, app_root)
        root = self._path(self._str(value, "contentroot"), base) or app_root
        mapping = value.get("filemapping")
        if isinstance(mapping, dict):   # LocalPath вида "build/*" — папка внутри ContentRoot
            local = self._str(mapping, "localpath").replace("\\", "/")
            folder = local.rsplit("/", 1)[0] if "/" in local else ""
            if folder and "*" not in folder:
                root = str(Path(root) / folder) if root else folder
        exclusions = value.get("fileexclusion", [])
        exclusions = [exclusions] if isinstance(exclusions, str) else [e for e in exclusions if isinstance(e, str)]
        return DepotSpec(self._str(value, "depotid") or depot_id, root, exclusions)

    @staticmethod
    def _str(node: Node, key: str) -> str:
        value = node.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _path(value: str, base: Path) -> str:
        if not value:
            return ""
        m = re.match(r"^[A-Za-z]:\\", value)
        if m and value[0].upper() == "Z":   # диск Z: в Wine — корень Linux
            return "/" + value[3:].replace("\\", "/")
        if m:
            return value   # путь Windows без соответствия в Linux — пусть пользователь поправит
        value = value.replace("\\", "/")
        path = Path(value).expanduser()
        return str(path if path.is_absolute() else (base / path).resolve())
