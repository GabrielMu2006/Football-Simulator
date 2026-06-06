import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


APP_NAME = "Football Simulator"
SHARED_CONFIG_FILE_NAME = "足球模拟器总配置.json"
ALTERNATE_SHARED_CONFIG_FILE_NAMES = ("football_simulator_config.json",)
SAVE_CONFIG_FILE_NAME = "config.json"
CURRENT_SAVE_FILE_NAME = "current_save.txt"


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / "Library" / "Application Support" / APP_NAME


def shared_config_path() -> Path:
    if getattr(sys, "frozen", False):
        external_path = sibling_shared_config_path()
        if external_path is not None and external_path.exists():
            return external_path

        bundled_path = bundled_shared_config_path()
        if bundled_path.exists():
            return bundled_path

        fallback_path = user_data_root() / SHARED_CONFIG_FILE_NAME
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        if bundled_path.exists() and not fallback_path.exists():
            shutil.copy2(bundled_path, fallback_path)
        return fallback_path

    return resource_root() / SHARED_CONFIG_FILE_NAME


def bundled_shared_config_path() -> Path:
    for file_name in (SHARED_CONFIG_FILE_NAME, *ALTERNATE_SHARED_CONFIG_FILE_NAMES):
        candidate = resource_root() / file_name
        if candidate.exists():
            return candidate
    return resource_root() / SHARED_CONFIG_FILE_NAME


def sibling_shared_config_path() -> Optional[Path]:
    if not getattr(sys, "frozen", False):
        return resource_root() / SHARED_CONFIG_FILE_NAME

    executable = Path(sys.executable).resolve()
    if ".app" not in executable.as_posix():
        for file_name in (SHARED_CONFIG_FILE_NAME, *ALTERNATE_SHARED_CONFIG_FILE_NAMES):
            candidate = executable.parent / file_name
            if candidate.exists():
                return candidate
        return None

    app_bundle = next((parent for parent in executable.parents if parent.suffix == ".app"), None)
    if app_bundle is None:
        return None
    for file_name in (SHARED_CONFIG_FILE_NAME, *ALTERNATE_SHARED_CONFIG_FILE_NAMES):
        candidate = app_bundle.parent / file_name
        if candidate.exists():
            return candidate
    return app_bundle.parent / SHARED_CONFIG_FILE_NAME


def save_root() -> Path:
    if getattr(sys, "frozen", False):
        root = user_data_root() / "saves"
        root.mkdir(parents=True, exist_ok=True)
        _seed_default_saves(root)
        return root
    root = resource_root() / "saves"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_save_names() -> list[str]:
    root = save_root()
    return sorted(entry.name for entry in root.iterdir() if entry.is_dir())


def create_save_directory(save_name: str) -> Path:
    normalized_name = normalize_save_name(save_name)
    path = save_root() / normalized_name
    if path.exists():
        raise ValueError(f"存档 '{normalized_name}' 已存在。")
    path.mkdir(parents=True, exist_ok=False)
    return path


def delete_save_directory(save_name: str) -> None:
    normalized_name = normalize_save_name(save_name)
    path = save_root() / normalized_name
    if not path.exists():
        raise FileNotFoundError(f"未找到存档 '{normalized_name}'。")
    shutil.rmtree(path)


def save_exists(save_name: str) -> bool:
    normalized_name = normalize_save_name(save_name)
    return (save_root() / normalized_name).is_dir()


def save_config_path(save_name: str) -> Path:
    normalized_name = normalize_save_name(save_name)
    path = save_root() / normalized_name
    path.mkdir(parents=True, exist_ok=True)
    return path / SAVE_CONFIG_FILE_NAME


def load_current_save_name(default_name: str = "default") -> str:
    path = current_save_path()
    if path.exists():
        try:
            saved_name = path.read_text(encoding="utf-8").strip()
        except OSError:
            saved_name = ""
        if saved_name and save_exists(saved_name):
            return saved_name

    if save_exists(default_name):
        return default_name

    available_saves = list_save_names()
    if available_saves:
        return available_saves[0]

    save_root().mkdir(parents=True, exist_ok=True)
    (save_root() / default_name).mkdir(parents=True, exist_ok=True)
    return default_name


def store_current_save_name(save_name: str) -> None:
    normalized_name = normalize_save_name(save_name)
    path = current_save_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized_name, encoding="utf-8")


def current_save_path() -> Path:
    if getattr(sys, "frozen", False):
        root = user_data_root()
    else:
        root = resource_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / CURRENT_SAVE_FILE_NAME


def normalize_save_name(save_name: str) -> str:
    normalized_name = save_name.strip()
    if not normalized_name:
        raise ValueError("存档名不能为空。")
    if normalized_name in {".", ".."} or "/" in normalized_name:
        raise ValueError("存档名不能包含路径分隔符。")
    return normalized_name


def _seed_default_saves(destination_root: Path) -> None:
    source_root = resource_root() / "saves"
    if not source_root.exists():
        return
    for source_entry in source_root.iterdir():
        destination_entry = destination_root / source_entry.name
        if not source_entry.is_dir():
            continue
        destination_entry.mkdir(parents=True, exist_ok=True)
        source_config = source_entry / "config.json"
        destination_config = destination_entry / "config.json"
        if not source_config.exists():
            continue
        if not destination_config.exists():
            shutil.copy2(source_config, destination_config)
            continue
        if source_entry.name == "default":
            _refresh_default_config_if_safe(source_config, destination_config)


def _refresh_default_config_if_safe(source_config: Path, destination_config: Path) -> None:
    try:
        source_data = json.loads(source_config.read_text(encoding="utf-8"))
        destination_data = json.loads(destination_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    # Only refresh the built-in default save when the user has not changed team names.
    if destination_data.get("teams") != source_data.get("teams"):
        return

    source_real_players = source_data.get("real_players", [])
    destination_real_players = destination_data.get("real_players", [])
    needs_refresh = (
        len(destination_real_players) < len(source_real_players)
        or destination_data.get("real_player_ability_min") != source_data.get("real_player_ability_min")
        or destination_data.get("real_player_ability_max") != source_data.get("real_player_ability_max")
    )
    if needs_refresh:
        shutil.copy2(source_config, destination_config)
