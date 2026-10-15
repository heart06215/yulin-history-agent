"""Portable private material paths, including older Windows catalog entries."""

from pathlib import Path, PureWindowsPath
from .config import settings


def data_file(relative_name: str) -> Path:
    name = str(relative_name)
    relative = Path(name.replace("\\", "/"))
    if not name or relative.is_absolute() or PureWindowsPath(name).drive:
        raise ValueError("ASSET_NOT_FOUND")
    root = settings.data_dir.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("ASSET_NOT_FOUND")
    return path
