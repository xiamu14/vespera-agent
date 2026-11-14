"""Trusted user configuration; project files never grant permissions."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import to_jsonable_python


from integrations.config_home import user_config


class PermissionConfigError(ValueError):
    pass


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: str = Field(min_length=1)
    pattern: str = "*"
    argument: str | None = None
    action: Literal["deny", "ask", "allow"]
    remember: bool = False


class ShellScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str = Field(min_length=1)
    workspace: str | None = None
    write_paths: list[str] = Field(min_length=1, max_length=32)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["auto", "read-only", "full"] = "auto"
    rules: list[Rule] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=lambda: [
        ".git", ".git/**", ".env*", "**/.env*", "*.pem", "*.key", "credentials*", "**/credentials*",
    ])
    sandbox: bool = True
    shell_scopes: list[ShellScope] = Field(default_factory=list)


def config_path() -> Path:
    return user_config("CODER_PERMISSION_CONFIG", "permissions.json")


def load_settings(path: Path) -> tuple[Settings, str]:
    try:
        with path.open("rb") as source:
            data = source.read(65537)
    except FileNotFoundError:
        if "CODER_PERMISSION_CONFIG" in os.environ:
            raise PermissionConfigError("指定的权限配置不存在") from None
        data = b"{}"
    if len(data) > 65536:
        raise PermissionConfigError("权限配置超过 64 KiB")
    try:
        config = Settings.model_validate_json(data)
    except ValidationError:
        raise PermissionConfigError("权限配置无效；拒绝使用损坏或含未知字段的配置") from None
    return config, hashlib.sha256(data).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(to_jsonable_python(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def redact(value: object) -> str:
    def clean(item):
        if isinstance(item, dict):
            return {key: "[REDACTED]" if any(word in key.lower() for word in ("authorization", "token", "password", "secret", "api_key"))
                    else clean(content) for key, content in item.items()}
        if isinstance(item, (tuple, list)):
            return [clean(content) for content in item]
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError:
                return item
            if isinstance(parsed, (dict, list)):
                return clean(parsed)
        return item
    text = json.dumps(clean(to_jsonable_python(value)), ensure_ascii=False, indent=2)
    for key, secret in os.environ.items():
        if len(secret) >= 8 and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(secret, "[REDACTED]")
    return text
