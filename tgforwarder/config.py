import tomllib
from typing import Any

from .paths import CONFIG_PATH, ensure_data_dirs


DEFAULT_CONFIG: dict[str, Any] = {
    "api_id": 123456,
    "api_hash": "your_api_hash",
    "session_name": "session_name",
    "proxy": None,
    "bot_token": "",
    "admin_user_ids": [],
    "targets": [
        {
            "source": "source_channel_username_or_id",
            "destination": "target_channel_username_or_id",
            "enabled": True,
            "startup_last_message_id": None,
            "blacklist_keywords": [],
            "whitelist_keywords": [],
            "blacklist_regex": [],
            "whitelist_regex": [],
            "caption_prefix": "",
        }
    ],
}


DEFAULT_CONFIG_TOML = """# Telegram API ID，可从 https://my.telegram.org 获取
api_id = 123456

# Telegram API Hash，可从 https://my.telegram.org 获取
api_hash = "your_api_hash"

# Telethon session 名称，实际 session 文件会保存在 data/sessions/ 目录
session_name = "session_name"

# 代理配置；不使用代理时保留为字符串空值
proxy = ""

# 管理 Bot 的 token
bot_token = ""

# 允许操作管理 Bot 的 Telegram 用户 ID 列表
admin_user_ids = [123456789]

[[targets]]
# 来源频道用户名或频道 ID
source = "source_channel_username_or_id"

# 目标频道用户名或频道 ID
destination = "target_channel_username_or_id"

# 是否启用此规则
enabled = true

# 首次启动处理的起始消息 ID，留空请写 0，表示自动从当前最新消息开始
startup_last_message_id = 0

# 黑名单关键词，命中即跳过
blacklist_keywords = []

# 白名单关键词，非空时必须命中其一才转发
whitelist_keywords = []

# 黑名单正则，命中即跳过
blacklist_regex = []

# 白名单正则，非空时必须命中其一才转发
whitelist_regex = []

# 原生转发失败时，重发消息附加的前缀
caption_prefix = ""
"""


def ensure_config() -> dict[str, Any]:
    ensure_data_dirs()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            DEFAULT_CONFIG_TOML,
            encoding="utf-8",
        )
        raise SystemExit(f"已创建配置文件: {CONFIG_PATH}，请先填写后再运行。")

    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def validate_config(config: dict[str, Any]) -> None:
    required_fields = ["api_id", "api_hash", "session_name", "bot_token", "admin_user_ids", "targets"]
    missing = [field for field in required_fields if field not in config]
    if missing:
        raise ValueError(f"config.toml 缺少字段: {', '.join(missing)}")

    if not isinstance(config.get("admin_user_ids"), list):
        raise ValueError("config.toml 中 admin_user_ids 必须是列表")

    if not isinstance(config.get("targets"), list) or not config["targets"]:
        raise ValueError("config.toml 中 targets 必须是非空列表")

    for index, target in enumerate(config["targets"], start=1):
        if not isinstance(target, dict):
            raise ValueError(f"targets[{index}] 必须是对象")
        for field in ["source", "destination"]:
            if field not in target or str(target[field]).strip() == "":
                raise ValueError(f"targets[{index}] 缺少有效字段: {field}")


def build_proxy(proxy_value: Any):
    if not proxy_value:
        return None
    if isinstance(proxy_value, (list, tuple)):
        return tuple(proxy_value)
    raise ValueError("proxy 必须为 null 或 Telethon 支持的元组/list")
