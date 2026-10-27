"""Local AI configuration. The API key is protected for the current Windows user."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse


CONFIG_PATH = Path(__file__).resolve().parent / "data" / "ai_settings.json"
PROVIDERS = {"siliconflow", "compatible", "openai"}


def validate_base_url(value: str, provider: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        if provider == "openai":
            return "https://api.openai.com/v1"
        if provider == "siliconflow":
            return "https://api.siliconflow.cn/v1"
        raise ValueError("兼容接口需要填写 API Base URL")
    parsed = urlparse(url)
    if (len(url) > 500 or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.params
            or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("API Base URL 需为 HTTPS 地址；本机服务可使用 HTTP localhost")
    return url


def load() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    key = ""
    if saved.get("protected_api_key"):
        try:
            import win32crypt
            key = win32crypt.CryptUnprotectData(
                base64.b64decode(saved["protected_api_key"]), None, None, None, 0
            )[1].decode("utf-8")
        except Exception:
            # A different Windows user cannot decrypt this key; ask for a new one.
            key = ""
    return {
        "provider": saved.get("provider", "siliconflow"),
        "base_url": saved.get("base_url", ""),
        "model": saved.get("model", ""),
        "api_key": key,
        "reply_system_prompt": saved.get("reply_system_prompt", ""),
    }


def save(*, provider: str, base_url: str, model: str, api_key: str,
         reply_system_prompt: str, clear_api_key: bool = False) -> None:
    if provider not in PROVIDERS:
        raise ValueError("请选择支持的模型服务类型")
    base_url = validate_base_url(base_url, provider)
    model = model.strip()
    if not model or len(model) > 200:
        raise ValueError("请填写不超过 200 字符的模型 ID")
    if len(reply_system_prompt) > 6000:
        raise ValueError("System prompt 不能超过 6000 字符")
    existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    same_destination = (existing.get("provider") == provider
                        and existing.get("base_url") == base_url)
    encrypted = existing.get("protected_api_key", "") if same_destination and not clear_api_key else ""
    if api_key.strip():
        if os.name != "nt":
            raise ValueError("当前系统不支持 Windows 用户密钥加密")
        try:
            import win32crypt
        except ImportError as exc:
            raise ValueError("请安装 pywin32 后再保存 API Key") from exc
        encrypted = base64.b64encode(win32crypt.CryptProtectData(
            api_key.strip().encode("utf-8"), "Marketplace Workbench AI key", None, None, None, 0
        )).decode("ascii")
    value = {
        "provider": provider, "base_url": base_url, "model": model,
        "reply_system_prompt": reply_system_prompt.strip(), "protected_api_key": encrypted,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, CONFIG_PATH)


def clear() -> None:
    CONFIG_PATH.unlink(missing_ok=True)
