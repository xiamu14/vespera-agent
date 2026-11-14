"""Resolve the shared user configuration directory."""
import os
from pathlib import Path


def config_home() -> Path:
    parent = Path.home() / ".config"
    return parent / ".ani bot"


def user_config(variable: str, filename: str) -> Path:
    override = os.getenv(variable)
    return (Path(override).expanduser() if override is not None else config_home() / filename).resolve()
