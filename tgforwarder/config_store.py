from __future__ import annotations

from copy import deepcopy
from typing import Any

import tomllib

from .config import DEFAULT_CONFIG, DEFAULT_CONFIG_TOML, validate_config
from .paths import CONFIG_PATH, ensure_data_dirs


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    raise TypeError(f"不支持的 TOML 值类型: {type(value)}")


def dump_config_toml(config: dict[str, Any]) -> str:
    lines = [
        "# Telegram API ID，可从 https://my.telegram.org 获取",
        f"api_id = {_format_toml_value(config['api_id'])}",
        "",
        "# Telegram API Hash，可从 https://my.telegram.org 获取",
        f"api_hash = {_format_toml_value(config['api_hash'])}",
        "",
        "# Telethon session 名称，实际 session 文件会保存在 data/sessions/ 目录",
        f"session_name = {_format_toml_value(config['session_name'])}",
        "",
        "# 代理配置；不使用代理时保留为空字符串",
        f"proxy = {_format_toml_value(config.get('proxy') or '')}",
        "",
        "# 管理 Bot 的 token",
        f"bot_token = {_format_toml_value(config['bot_token'])}",
        "",
        "# 允许操作管理 Bot 的 Telegram 用户 ID 列表",
        f"admin_user_ids = {_format_toml_value(config['admin_user_ids'])}",
        "",
    ]

    for target in config.get("targets", []):
        lines.extend(
            [
                "[[targets]]",
                "# 来源频道用户名或频道 ID",
                f"source = {_format_toml_value(target['source'])}",
                "",
                "# 目标频道用户名或频道 ID",
                f"destination = {_format_toml_value(target['destination'])}",
                "",
                "# 是否启用此规则",
                f"enabled = {_format_toml_value(target.get('enabled', True))}",
                "",
                "# 首次启动处理的起始消息 ID，0 表示自动从当前最新消息开始",
                f"startup_last_message_id = {_format_toml_value(target.get('startup_last_message_id', 0) or 0)}",
                "",
                "# 黑名单关键词，命中即跳过",
                f"blacklist_keywords = {_format_toml_value(target.get('blacklist_keywords', []))}",
                "",
                "# 白名单关键词，非空时必须命中其一才转发",
                f"whitelist_keywords = {_format_toml_value(target.get('whitelist_keywords', []))}",
                "",
                "# 黑名单正则，命中即跳过",
                f"blacklist_regex = {_format_toml_value(target.get('blacklist_regex', []))}",
                "",
                "# 白名单正则，非空时必须命中其一才转发",
                f"whitelist_regex = {_format_toml_value(target.get('whitelist_regex', []))}",
                "",
                "# 原生转发失败时，重发消息附加的前缀",
                f"caption_prefix = {_format_toml_value(target.get('caption_prefix', ''))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


class ConfigStore:
    def __init__(self) -> None:
        ensure_data_dirs()

    def load(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            data = deepcopy(DEFAULT_CONFIG)
            self.save(data)
            return data

        raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        merged = deepcopy(DEFAULT_CONFIG)
        merged.update(raw)
        if "targets" in raw:
            merged["targets"] = raw["targets"]
        validate_config(merged)
        return merged

    def save(self, config: dict[str, Any]) -> None:
        ensure_data_dirs()
        validate_config(config)
        CONFIG_PATH.write_text(dump_config_toml(config), encoding="utf-8")

    def list_targets(self) -> list[dict[str, Any]]:
        return self.load().get("targets", [])

    def add_target(self, target: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        config.setdefault("targets", []).append(target)
        self.save(config)
        return config

    def remove_target(self, index: int) -> dict[str, Any]:
        config = self.load()
        targets = config.get("targets", [])
        if index < 0 or index >= len(targets):
            raise IndexError("target 索引越界")
        del targets[index]
        self.save(config)
        return config

    def update_target_fields(self, index: int, updates: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        targets = config.get("targets", [])
        if index < 0 or index >= len(targets):
            raise IndexError("target 索引越界")
        targets[index].update(updates)
        self.save(config)
        return config
