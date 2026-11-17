"""Настройки и профили сборок (~/.config/SteamPipeGUI/config.json).

Профиль = одна игра: App ID, депо (у каждого своя папка и исключения), ветка, описание.
Старый плоский формат (last_appid, last_depot_id …) переносится в профиль при загрузке."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "SteamPipeGUI"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_EXCLUSIONS = ["*.pdb"]
DEFAULT_BRANCHES = ["beta"]


def _from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class DepotSpec:
    depot_id: str = ""
    content_root: str = ""
    exclusions: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUSIONS))

    @classmethod
    def from_dict(cls, data: dict) -> "DepotSpec":
        depot = _from_dict(cls, data)
        depot.exclusions = [str(e) for e in depot.exclusions if str(e).strip()]
        return depot


@dataclass
class BuildProfile:
    name: str = "Default"
    app_id: str = ""
    description: str = ""
    depots: list[DepotSpec] = field(default_factory=list)
    set_live: bool = False
    branch: str = "beta"
    preview: bool = False
    last_build_id: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "BuildProfile":
        profile = _from_dict(cls, {k: v for k, v in data.items() if k != "depots"})
        profile.depots = [DepotSpec.from_dict(d) for d in data.get("depots", []) if isinstance(d, dict)]
        return profile

    def copy(self, name: str) -> "BuildProfile":
        clone = BuildProfile.from_dict(json.loads(json.dumps(asdict(self))))
        clone.name, clone.last_build_id = name, ""
        return clone


@dataclass
class AppConfig:
    username: str = ""
    sdk_folder: str = ""
    steamcmd_path: str = ""
    log_max_lines: int = 2000
    language: str = ""                  # "" — язык системы
    check_session_on_start: bool = True
    known_branches: list[str] = field(default_factory=lambda: list(DEFAULT_BRANCHES))
    profiles: list[BuildProfile] = field(default_factory=list)
    current_profile: str = ""
    window_geometry: str = ""

    # ----- профили -----
    def profile(self) -> BuildProfile:
        """Текущий профиль; если профилей нет — создаётся пустой."""
        for p in self.profiles:
            if p.name == self.current_profile:
                return p
        if not self.profiles:
            self.profiles.append(BuildProfile())
        self.current_profile = self.profiles[0].name
        return self.profiles[0]

    def profile_names(self) -> list[str]:
        return [p.name for p in self.profiles]

    def unique_name(self, base: str) -> str:
        names, name, n = set(self.profile_names()), base, 2
        while name in names:
            name, n = f"{base} ({n})", n + 1
        return name

    def add_profile(self, profile: BuildProfile) -> BuildProfile:
        profile.name = self.unique_name(profile.name.strip() or "Default")
        self.profiles.append(profile)
        self.current_profile = profile.name
        return profile

    def remove_current_profile(self) -> None:
        current = self.profile()
        self.profiles = [p for p in self.profiles if p is not current]
        self.current_profile = self.profiles[0].name if self.profiles else ""

    def remember_branch(self, branch: str) -> None:
        branch = branch.strip()
        if branch and branch.lower() != "default" and branch not in self.known_branches:
            self.known_branches.append(branch)

    # ----- файл -----
    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CONFIG_FILE)   # атомарно: сбой при записи не портит старый файл
        except OSError as error:
            print(f"[config] cannot save {CONFIG_FILE}: {error}", file=sys.stderr)

    @classmethod
    def load(cls) -> "AppConfig":
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            print(f"[config] cannot read {CONFIG_FILE}: {error}", file=sys.stderr)
            return cls()
        if not isinstance(data, dict):
            return cls()
        config = _from_dict(cls, {k: v for k, v in data.items() if k != "profiles"})
        config.profiles = [BuildProfile.from_dict(p) for p in data.get("profiles", []) if isinstance(p, dict)]
        config._migrate_v1(data)
        return config

    def _migrate_v1(self, data: dict) -> None:
        """Версия 1.1: одна игра в плоских полях."""
        if "last_username" in data and not self.username:
            self.username = data.get("last_username") or ""
        if self.profiles or not any(data.get(k) for k in ("last_appid", "last_depot_id", "default_content_path")):
            return
        app_id = str(data.get("last_appid") or "")
        depot_id = str(data.get("last_depot_id") or "") or (str(int(app_id) + 1) if app_id.isdigit() else "")
        branch = str(data.get("last_branch") or "")
        profile = BuildProfile(
            name=app_id or "Default", app_id=app_id,
            depots=[DepotSpec(depot_id, str(data.get("default_content_path") or ""))],
            set_live=bool(data.get("set_live_after_upload")) and branch.lower() not in ("", "default"),
            branch=branch if branch.lower() not in ("", "default", "public") else "beta",
        )
        self.profiles.append(profile)
        self.current_profile = profile.name
