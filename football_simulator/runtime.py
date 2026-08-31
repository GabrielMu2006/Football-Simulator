import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


APP_NAME = "Football Simulator"
SHARED_CONFIG_FILE_NAME = "足球模拟器总配置.json"
ALTERNATE_SHARED_CONFIG_FILE_NAMES = ("football_simulator_config.json",)
SAVE_CONFIG_FILE_NAME = "config.json"
CURRENT_SAVE_FILE_NAME = "current_save.txt"
SAVE_DATABASE_FILE_NAME = "save.sqlite3"

# 存档名白名单：中文、字母、数字、空格、下划线、连字符，1–64 个字符，
# 首字符不能是空格或连字符。任何路径分隔符、盘符、控制字符、点号开头的
# 形式都会被拒绝；这同时保证了存档目录一定是 save_root 的直接子目录。
_SAVE_NAME_PATTERN = re.compile(r"^[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\-\u4e00-\u9fff ]{0,63}$")
_WINDOWS_RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


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


# 测试专用：重定向存档根目录（传 None 恢复默认位置）。生产代码不得调用。
_SAVE_ROOT_OVERRIDE: Optional[Path] = None


def set_save_root_override(path: Optional[Path]) -> None:
    global _SAVE_ROOT_OVERRIDE
    _SAVE_ROOT_OVERRIDE = Path(path).resolve() if path is not None else None


def save_root() -> Path:
    if _SAVE_ROOT_OVERRIDE is not None:
        root = _SAVE_ROOT_OVERRIDE
        root.mkdir(parents=True, exist_ok=True)
        return root
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
    root = save_root().resolve()
    path = root / normalized_name
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(f"未找到存档 '{normalized_name}'。")
    # 先检查符号链接再做 resolve 包含性判断，防止借链接越出存档根目录删除。
    if path.is_symlink():
        raise ValueError("存档路径不能是符号链接。")
    resolved = path.resolve()
    if resolved == root:
        raise ValueError("不能删除存档根目录本身。")
    if resolved.parent != root:
        raise ValueError("只能删除存档根目录下的直接子目录。")
    shutil.rmtree(path)


def save_exists(save_name: str) -> bool:
    normalized_name = normalize_save_name(save_name)
    return (save_root() / normalized_name).is_dir()


def save_config_path(save_name: str) -> Path:
    normalized_name = normalize_save_name(save_name)
    path = save_root() / normalized_name
    path.mkdir(parents=True, exist_ok=True)
    return path / SAVE_CONFIG_FILE_NAME


def _safe_save_dir(save_name: str) -> Path:
    """返回校验后的存档目录（拒绝符号链接与越界）。"""
    root = save_root().resolve()
    normalized_name = normalize_save_name(save_name)
    path = root / normalized_name
    if path.is_symlink():
        raise ValueError("存档路径不能是符号链接。")
    resolved = path.resolve()
    if resolved != root and resolved.parent != root:
        raise ValueError("只能访问存档根目录下的直接子目录。")
    return path


def backup_save(save_name: str) -> Path:
    """用 SQLite Online Backup API 为存档创建独立备份（WAL 安全）。"""
    normalized_name = normalize_save_name(save_name)
    db_path = _safe_save_dir(normalized_name) / SAVE_DATABASE_FILE_NAME
    if not db_path.exists():
        raise ValueError(f"存档 '{normalized_name}' 还没有数据库（未初始化）。")
    backup_dir = save_root().parent / "backups" / normalized_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{timestamp}.sqlite3"
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(target))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return target


def list_backups(save_name: str) -> list[Path]:
    normalized_name = normalize_save_name(save_name)
    backup_dir = save_root().parent / "backups" / normalized_name
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.name, reverse=True)


def export_save(save_name: str, dest_path: Path) -> Path:
    """把存档数据库导出到用户选择的位置（Online Backup API）。"""
    normalized_name = normalize_save_name(save_name)
    db_path = _safe_save_dir(normalized_name) / SAVE_DATABASE_FILE_NAME
    if not db_path.exists():
        raise ValueError(f"存档 '{normalized_name}' 还没有数据库（未初始化）。")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(dest_path))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return dest_path


def import_save_database(save_name: str, src_path: Path) -> Path:
    """从外部 .sqlite3 导入为存档数据库（同名存档目录已存在时覆盖）。"""
    normalized_name = normalize_save_name(save_name)
    src_path = Path(src_path)
    if not src_path.exists():
        raise FileNotFoundError(f"导入文件不存在：{src_path}")
    target_dir = _safe_save_dir(normalized_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / SAVE_DATABASE_FILE_NAME
    src_conn = sqlite3.connect(str(src_path))
    dst_conn = sqlite3.connect(str(target))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return target


def move_save_to_trash(save_name: str) -> Path:
    """把存档移入回收站（而非直接删除），可以恢复。"""
    normalized_name = normalize_save_name(save_name)
    root = save_root().resolve()
    path = root / normalized_name
    if not path.exists() or path.is_symlink():
        raise FileNotFoundError(f"未找到存档 '{normalized_name}'。")
    trash_dir = save_root().parent / "trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    target = trash_dir / normalized_name
    if target.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = trash_dir / f"{normalized_name}-{timestamp}"
    shutil.move(str(path), str(target))
    return target


def list_trash_saves() -> list[Path]:
    trash_dir = save_root().parent / "trash"
    if not trash_dir.exists():
        return []
    return sorted(trash_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)


def restore_trash_save(trash_path: Path, save_name: Optional[str] = None) -> Path:
    trash_path = Path(trash_path)
    if not trash_path.exists() or not trash_path.is_dir():
        raise FileNotFoundError(f"回收站中不存在：{trash_path}")
    target_name = normalize_save_name(save_name or trash_path.name)
    target = save_root() / target_name
    if target.exists():
        raise ValueError(f"存档 '{target_name}' 已存在，无法恢复。")
    shutil.move(str(trash_path), str(target))
    return target


def empty_trash() -> list[Path]:
    trash_dir = save_root().parent / "trash"
    if not trash_dir.exists():
        return []
    removed: list[Path] = []
    for entry in list(trash_dir.iterdir()):
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.is_symlink() or entry.is_file():
            entry.unlink()
        removed.append(entry)
    return removed


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
    if not _SAVE_NAME_PATTERN.fullmatch(normalized_name):
        raise ValueError("存档名只能由中文、字母或数字开头，并仅包含中文、字母、数字、空格、下划线和连字符（1–64 字符）。")
    stem = normalized_name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED_DEVICE_NAMES:
        raise ValueError("存档名不能使用系统保留设备名。")
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
