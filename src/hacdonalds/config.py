"""Explicit environment or project-local credential configuration."""

from dataclasses import dataclass, field
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import tempfile

from .errors import HacdonaldsError

ENDPOINT = "https://mcp.mcd.cn"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def validate_token(value: str) -> str:
    token = value.strip()
    if not token or any(c.isspace() or not c.isascii() or ord(c) < 33 for c in token):
        raise HacdonaldsError("INVALID_TOKEN", "请输入原始 Token，不含 Bearer 前缀、空格或换行。")
    if token.startswith("${") or token in {"YOUR_MCP_TOKEN", "your-token-here"}:
        raise HacdonaldsError("INVALID_TOKEN", "请配置实际 Token，而非示例占位符。")
    return token


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    timeout_seconds: float = 30.0

    def __post_init__(self):
        validate_token(self.token)
        if self.token != self.token.strip():
            raise HacdonaldsError("INVALID_TOKEN", "Token 首尾不能包含空白。")
        if not 1 <= self.timeout_seconds <= 120:
            raise HacdonaldsError("INVALID_TIMEOUT", "超时必须为 1 到 120 秒。")


def credential_path(root: Path = PROJECT_ROOT) -> Path:
    return root / ".local" / "credentials.json"


def load_settings(root: Path = PROJECT_ROOT, *, timeout: float = 30.0) -> Settings:
    if "MCD_MCP_TOKEN" in os.environ:
        return Settings(validate_token(os.environ["MCD_MCP_TOKEN"]), timeout)
    path = credential_path(root)
    try:
        if path.is_symlink():
            raise HacdonaldsError("UNSAFE_CONFIG", "凭证文件不能是符号链接。")
        info = path.stat()
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise HacdonaldsError("UNSAFE_CONFIG", "凭证文件权限应为 600，请重新运行 setup。")
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("MCD_MCP_TOKEN") if isinstance(data, dict) else None
        if not isinstance(value, str):
            raise ValueError("invalid credential shape")
        return Settings(validate_token(value), timeout)
    except FileNotFoundError:
        raise HacdonaldsError("TOKEN_MISSING", "尚未配置 Token。请运行 python3 scripts/run.py setup。") from None
    except (OSError, ValueError):
        raise HacdonaldsError("INVALID_CONFIG", "无法读取凭证文件，请重新运行 setup。") from None


def save_token(value: str, root: Path = PROJECT_ROOT) -> Path:
    token = validate_token(value)
    path = credential_path(root)
    directory = path.parent
    if directory.is_symlink():
        raise HacdonaldsError("UNSAFE_CONFIG", "凭证目录不能是符号链接。")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        directory.chmod(0o700)
    fd, temp_name = tempfile.mkstemp(prefix="credentials-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"MCD_MCP_TOKEN": token}, handle)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return path


def dependency_status() -> dict:
    result = {}
    for name, major in (("mcp", 2), ("httpx2", None), ("jsonschema", 4), ("socksio", 1)):
        try:
            version = importlib.metadata.version(name)
            compatible = major is None or int(version.split(".")[0]) == major
            result[name] = {"installed": True, "version": version, "compatible": compatible}
        except importlib.metadata.PackageNotFoundError:
            result[name] = {"installed": False, "compatible": False}
    return result
