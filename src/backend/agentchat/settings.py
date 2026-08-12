import os
import re
from pathlib import Path

import yaml
from typing import Literal, Optional
from loguru import logger
from types import SimpleNamespace
from pydantic.v1 import BaseSettings, Field

from agentchat.schemas.common import MultiModels, ModelConfig, Tools, Rag, StorageConfig, ServerConfig

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


def _expand_env_text(value: str) -> str:
    def replace(match):
        name, default = match.group(1).strip(), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        logger.warning(f"Environment variable {name} not set, keeping placeholder")
        return match.group(0)

    return _ENV_PATTERN.sub(replace, value)


def _expand_env_value(value):
    if isinstance(value, dict):
        return {key: _expand_env_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_value(item) for item in value]
    if isinstance(value, str):
        return _expand_env_text(value)
    return value


class Settings(BaseSettings):
    redis: dict = {}
    mysql: dict = {}
    langfuse: dict = {}
    whitelist_paths: list = []
    wechat_config: dict = {}
    default_config: dict = {}

    server: Optional[ServerConfig] = ServerConfig()
    rag: Optional[Rag] = None
    tools: Optional[Tools] = None
    storage: Optional[StorageConfig] = None
    multi_models: Optional[MultiModels] = None


app_settings = Settings()

async def init_app_settings(file_path: str = None):
    global app_settings

    if load_dotenv:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        if env_file.exists():
            load_dotenv(env_file)

    if file_path is None:
        file_path = Path(__file__).resolve().parent / "config.yaml"
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            data = _expand_env_value(data)
            if data is None:
                logger.error("YAML 文件解析为空")
                return

            # 特殊处理multi_models配置
            if "multi_models" in data:
                data["multi_models"] = MultiModels(**data["multi_models"])

            if "tools" in data:
                data["tools"] = Tools(**data["tools"])

            if "rag" in data:
                data["rag"] = Rag(**data["rag"])

            if "storage" in data:
                data["storage"] = StorageConfig(**data["storage"])

            if "server" in data:
                data["server"] = ServerConfig(**data["server"])

            for key, value in data.items():
                setattr(app_settings, key, value)
    except Exception as e:
        logger.error(f"Yaml file loading error: {e}")
