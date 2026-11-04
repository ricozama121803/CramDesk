"""Where crm keeps its files. Set CRM_HOME to put everything in one folder."""
import os
from pathlib import Path


def _base(env: str, fallback: str, sub: str) -> Path:
    home = os.environ.get("CRM_HOME")
    if home:
        p = Path(home).expanduser() / sub
    else:
        p = Path(os.environ.get(env) or Path.home() / fallback) / "crm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config", "config")


def data_dir() -> Path:
    return _base("XDG_DATA_HOME", ".local/share", "data")


def cache_dir() -> Path:
    return _base("XDG_CACHE_HOME", ".cache", "cache")


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_file() -> Path:
    return data_dir() / "data.json"
