from __future__ import annotations

from typing import Any

from telegram import Update
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .chat_format import normalize_chat_input
from .config_store import ConfigStore
from .logging_config import logger
from .runtime import ForwarderRuntime


class BotManager:
    def __init__(self, config_store: ConfigStore, runtime: ForwarderRuntime) -> None:
        self.config_store = config_store
        self.runtime = runtime
        self._application: Application | None = None
        self._conversations: dict[int, dict[str, Any]] = {}

    def _get_chat_id(self, update: Update) -> int | None:
        chat = update.effective_chat
        return chat.id if chat else None

    def _new_target_template(self) -> dict[str, Any]:
        return {
            "source": "",
            "destination": "",
            "startup_last_message_id": None,
            "blacklist_keywords": [],
            "whitelist_keywords": [],
            "blacklist_regex": [],
            "whitelist_regex": [],
            "caption_prefix": "",
        }

    def _split_csv(self, text: str) -> list[str]:
        text = text.strip()
        if not text or text == "-":
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    def _normalize_chat_value(self, raw_text: str) -> int | str:
        return normalize_chat_input(raw_text)

    def _target_detail_buttons(self, index: int) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("改来源", callback_data=f"edit:source:{index}"),
                InlineKeyboardButton("改目标", callback_data=f"edit:destination:{index}"),
            ],
            [
                InlineKeyboardButton("改黑名单关键词", callback_data=f"edit:blacklist:{index}"),
                InlineKeyboardButton("改白名单关键词", callback_data=f"edit:whitelist:{index}"),
            ],
            [
                InlineKeyboardButton("改黑名单正则", callback_data=f"edit:black_regex:{index}"),
                InlineKeyboardButton("改白名单正则", callback_data=f"edit:white_regex:{index}"),
            ],
            [
                InlineKeyboardButton("改前缀", callback_data=f"edit:caption_prefix:{index}"),
            ],
            [
                InlineKeyboardButton("启用此规则", callback_data=f"toggle:enable:{index}"),
                InlineKeyboardButton("停用此规则", callback_data=f"toggle:disable:{index}"),
            ],
            [
                InlineKeyboardButton("删除此规则", callback_data=f"delete_target:{index}"),
            ],
            [
                InlineKeyboardButton("返回规则列表", callback_data="targets:list"),
            ],
        ]
        return InlineKeyboardMarkup(rows)

    def _back_to_targets_buttons(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("返回规则列表", callback_data="targets:list")]]
        )

    def _field_prompt(self, field_name: str) -> str:
        mapping = {
            "blacklist": "请输入黑名单关键词，多个用英文逗号分隔；输入 - 清空：",
            "whitelist": "请输入白名单关键词，多个用英文逗号分隔；输入 - 清空：",
            "black_regex": "请输入黑名单正则，多个用英文逗号分隔；输入 - 清空：",
            "white_regex": "请输入白名单正则，多个用英文逗号分隔；输入 - 清空：",
            "caption_prefix": "请输入新的 caption_prefix；输入 - 清空：",
            "source": "请输入新的来源频道，支持 @username、username、t.me 链接、数值 ID：",
            "destination": "请输入新的目标频道，支持 @username、username、t.me 链接、数值 ID：",
        }
        return mapping[field_name]

    async def _begin_field_edit(self, query, index: int, field_name: str) -> None:
        chat_id = query.message.chat_id if query.message else None
        if chat_id is None:
            return
        self._conversations[chat_id] = {
            "state": "await_field_edit",
            "index": index,
            "field_name": field_name,
        }
        await query.edit_message_text(
            f"正在编辑规则 #{index + 1} 的字段 {field_name}。\n{self._field_prompt(field_name)}"
        )

    async def _apply_target_updates(
        self,
        update: Update,
        index: int,
        updates: dict[str, Any],
        success_text: str,
    ) -> None:
        try:
            self.config_store.update_target_fields(index, updates)
        except IndexError:
            await update.effective_message.reply_text("索引不存在。")
            return
        await self.runtime.reload()
        await update.effective_message.reply_text(success_text)

    def _target_buttons(self, targets: list[dict[str, Any]]) -> InlineKeyboardMarkup:
        rows = []
        for index, item in enumerate(targets, start=1):
            status = "启用" if item.get("enabled", True) else "停用"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"编辑 #{index} [{status}]",
                        callback_data=f"target:{index - 1}",
                    )
                ]
            )
        return InlineKeyboardMarkup(rows)

    def _is_admin(self, update: Update) -> bool:
        config = self.config_store.load()
        admin_ids = {int(x) for x in config.get("admin_user_ids", [])}
        user = update.effective_user
        return bool(user and user.id in admin_ids)

    async def _reject_non_admin(self, update: Update) -> bool:
        if self._is_admin(update):
            return False
        if update.effective_message:
            await update.effective_message.reply_text("无权限。")
        return True

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        await update.effective_message.reply_text(
            "可用命令：/status /targets /reload /add_target /remove_target"
        )

    async def status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        config = self.config_store.load()
        targets = config.get("targets", [])
        enabled_count = sum(1 for item in targets if item.get("enabled", True))
        disabled_count = len(targets) - enabled_count
        degraded_rules = self.runtime.native_forward_disabled_rules()
        queue_size = self.runtime.queue_size()
        persistent_queue_size = self.runtime.persistent_queue_size()
        consumer_status = self.runtime.consumer_status_text()

        lines = [
            f"当前运行状态：{self.runtime.status_text()}",
            f"规则总数：{len(targets)}",
            f"启用规则：{enabled_count}",
            f"停用规则：{disabled_count}",
            f"待转发队列：{queue_size}",
            f"持久化队列：{persistent_queue_size}",
            f"消费者状态：{consumer_status}",
        ]

        if degraded_rules:
            lines.append("强制重发规则：")
            lines.extend([f"- {item}" for item in degraded_rules])
        else:
            lines.append("强制重发规则：无")

        await update.effective_message.reply_text("\n".join(lines))

    async def targets_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        targets = self.config_store.list_targets()
        if not targets:
            await update.effective_message.reply_text("当前没有转发规则。")
            return
        lines = []
        for index, item in enumerate(targets, start=1):
            lines.append(f"{index}. {item.get('source')} -> {item.get('destination')}")
        await update.effective_message.reply_text(
            "\n".join(lines),
            reply_markup=self._target_buttons(targets),
        )

    async def targets_list_button_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        fake_update = Update(update.update_id, callback_query=query)
        if await self._reject_non_admin(fake_update):
            return
        await query.answer()
        targets = self.config_store.list_targets()
        if not targets:
            await query.edit_message_text("当前没有转发规则。")
            return
        lines = []
        for index, item in enumerate(targets, start=1):
            status = "启用" if item.get("enabled", True) else "停用"
            lines.append(f"{index}. [{status}] {item.get('source')} -> {item.get('destination')}")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=self._target_buttons(targets),
        )

    async def set_blacklist_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        args = context.args
        if len(args) < 2 or not args[0].isdigit():
            await update.effective_message.reply_text("用法：/set_blacklist <index> <v1,v2,...>")
            return
        index = int(args[0]) - 1
        values = self._split_csv(" ".join(args[1:]))
        await self._apply_target_updates(update, index, {"blacklist_keywords": values}, "黑名单关键词已更新。")

    async def set_whitelist_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        args = context.args
        if len(args) < 2 or not args[0].isdigit():
            await update.effective_message.reply_text("用法：/set_whitelist <index> <v1,v2,...>")
            return
        index = int(args[0]) - 1
        values = self._split_csv(" ".join(args[1:]))
        await self._apply_target_updates(update, index, {"whitelist_keywords": values}, "白名单关键词已更新。")

    async def set_black_regex_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        args = context.args
        if len(args) < 2 or not args[0].isdigit():
            await update.effective_message.reply_text("用法：/set_black_regex <index> <r1,r2,...>")
            return
        index = int(args[0]) - 1
        values = self._split_csv(" ".join(args[1:]))
        await self._apply_target_updates(update, index, {"blacklist_regex": values}, "黑名单正则已更新。")

    async def set_white_regex_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        args = context.args
        if len(args) < 2 or not args[0].isdigit():
            await update.effective_message.reply_text("用法：/set_white_regex <index> <r1,r2,...>")
            return
        index = int(args[0]) - 1
        values = self._split_csv(" ".join(args[1:]))
        await self._apply_target_updates(update, index, {"whitelist_regex": values}, "白名单正则已更新。")

    async def target_button_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        fake_update = Update(update.update_id, callback_query=query)
        if await self._reject_non_admin(fake_update):
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith("target:"):
            return
        index = int(data.split(":", 1)[1])
        targets = self.config_store.list_targets()
        if index < 0 or index >= len(targets):
            await query.edit_message_text("该规则不存在。")
            return
        item = targets[index]
        status = "启用" if item.get("enabled", True) else "停用"
        await query.edit_message_text(
            f"规则 #{index + 1}\n"
            f"status: {status}\n"
            f"source: {item.get('source')}\n"
            f"destination: {item.get('destination')}\n"
            f"blacklist_keywords: {item.get('blacklist_keywords', [])}\n"
            f"whitelist_keywords: {item.get('whitelist_keywords', [])}\n"
            f"blacklist_regex: {item.get('blacklist_regex', [])}\n"
            f"whitelist_regex: {item.get('whitelist_regex', [])}\n\n"
            f"编辑命令示例：\n"
            f"/set_blacklist {index + 1} a,b\n"
            f"/set_whitelist {index + 1} a,b\n"
            f"/set_black_regex {index + 1} foo.*bar\n"
            f"/set_white_regex {index + 1} hello.*world",
            reply_markup=self._target_detail_buttons(index),
        )
        return

    async def edit_button_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        fake_update = Update(update.update_id, callback_query=query)
        if await self._reject_non_admin(fake_update):
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith("edit:"):
            return
        _, field_name, index_text = data.split(":", 2)
        await self._begin_field_edit(query, int(index_text), field_name)

    async def reload_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        await self.runtime.reload()
        await update.effective_message.reply_text("配置已重载。")

    async def add_target_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        chat_id = self._get_chat_id(update)
        if chat_id is None:
            return
        self._conversations[chat_id] = {
            "state": "await_source",
            "target": self._new_target_template(),
        }
        await update.effective_message.reply_text(
            "开始添加转发规则。\n请输入来源频道 source："
        )

    async def text_message_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        chat_id = self._get_chat_id(update)
        if chat_id is None or chat_id not in self._conversations:
            return

        text = (update.effective_message.text or "").strip()
        session = self._conversations[chat_id]
        state = session["state"]

        if text.lower() == "/cancel":
            del self._conversations[chat_id]
            await update.effective_message.reply_text("已取消当前添加流程。")
            return

        if state == "await_field_edit":
            index = int(session["index"])
            field_name = str(session["field_name"])
            if field_name in {"source", "destination"}:
                try:
                    updates = {field_name: self._normalize_chat_value(text)}
                except ValueError as exc:
                    await update.effective_message.reply_text(f"字段格式无效：{exc}")
                    return
            elif field_name == "caption_prefix":
                updates = {"caption_prefix": "" if text == "-" else text}
            else:
                values = self._split_csv(text)
                field_map = {
                    "blacklist": "blacklist_keywords",
                    "whitelist": "whitelist_keywords",
                    "black_regex": "blacklist_regex",
                    "white_regex": "whitelist_regex",
                }
                updates = {field_map[field_name]: values}
            try:
                self.config_store.update_target_fields(index, updates)
            except IndexError:
                del self._conversations[chat_id]
                await update.effective_message.reply_text("索引不存在。")
                return
            await self.runtime.reload()
            del self._conversations[chat_id]
            await update.effective_message.reply_text("字段已更新并完成热重载。")
            return

        target = session.get("target")
        if target is None:
            del self._conversations[chat_id]
            await update.effective_message.reply_text("当前会话状态异常，已自动取消，请重新开始。")
            return

        if state == "await_source":
            try:
                target["source"] = self._normalize_chat_value(text)
            except ValueError as exc:
                await update.effective_message.reply_text(f"来源频道格式无效：{exc}")
                return
            session["state"] = "await_destination"
            await update.effective_message.reply_text("请输入目标频道 destination：")
            return

        if state == "await_destination":
            try:
                target["destination"] = self._normalize_chat_value(text)
            except ValueError as exc:
                await update.effective_message.reply_text(f"目标频道格式无效：{exc}")
                return
            session["state"] = "await_blacklist_keywords"
            await update.effective_message.reply_text("请输入黑名单关键词，多个用英文逗号分隔；留空或输入 - 表示跳过：")
            return

        if state == "await_blacklist_keywords":
            target["blacklist_keywords"] = self._split_csv(text)
            session["state"] = "await_whitelist_keywords"
            await update.effective_message.reply_text("请输入白名单关键词，多个用英文逗号分隔；留空或输入 - 表示跳过：")
            return

        if state == "await_whitelist_keywords":
            target["whitelist_keywords"] = self._split_csv(text)
            session["state"] = "await_blacklist_regex"
            await update.effective_message.reply_text("请输入黑名单正则，多个用英文逗号分隔；留空或输入 - 表示跳过：")
            return

        if state == "await_blacklist_regex":
            target["blacklist_regex"] = self._split_csv(text)
            session["state"] = "await_whitelist_regex"
            await update.effective_message.reply_text("请输入白名单正则，多个用英文逗号分隔；留空或输入 - 表示跳过：")
            return

        if state == "await_whitelist_regex":
            target["whitelist_regex"] = self._split_csv(text)
            session["state"] = "await_caption_prefix"
            await update.effective_message.reply_text("请输入 caption 前缀；留空或输入 - 表示跳过：")
            return

        if state == "await_caption_prefix":
            target["caption_prefix"] = "" if text == "-" else text
            self.config_store.add_target(target)
            await self.runtime.reload()
            del self._conversations[chat_id]
            await update.effective_message.reply_text(
                "转发规则已添加并重载完成。\n"
                f"source: {target['source']}\n"
                f"destination: {target['destination']}"
            )
            return

    async def remove_target_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_non_admin(update):
            return
        args = context.args
        if len(args) != 1 or not args[0].isdigit():
            await update.effective_message.reply_text("用法：/remove_target <index>")
            return
        index = int(args[0]) - 1
        try:
            self.config_store.remove_target(index)
        except IndexError:
            await update.effective_message.reply_text("索引不存在。")
            return
        await self.runtime.reload()
        await update.effective_message.reply_text("转发规则已删除。")

    async def delete_target_button_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        fake_update = Update(update.update_id, callback_query=query)
        if await self._reject_non_admin(fake_update):
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith("delete_target:"):
            return
        index = int(data.split(":", 1)[1])
        try:
            self.config_store.remove_target(index)
        except IndexError:
            await query.edit_message_text("该规则不存在。")
            return
        await self.runtime.reload()
        await query.edit_message_text(f"规则 #{index + 1} 已删除并完成热重载。", reply_markup=self._back_to_targets_buttons())

    async def toggle_target_button_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        fake_update = Update(update.update_id, callback_query=query)
        if await self._reject_non_admin(fake_update):
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith("toggle:"):
            return
        _, action, index_text = data.split(":", 2)
        index = int(index_text)
        enabled = action == "enable"
        try:
            self.config_store.update_target_fields(index, {"enabled": enabled})
        except IndexError:
            await query.edit_message_text("该规则不存在。")
            return
        await self.runtime.reload()
        await query.edit_message_text(
            f"规则 #{index + 1} 已{'启用' if enabled else '停用'}并完成热重载。",
            reply_markup=self._back_to_targets_buttons(),
        )

    async def start(self) -> None:
        config = self.config_store.load()
        bot_token = config.get("bot_token", "")
        if not bot_token:
            logger.warning("未配置 bot_token，管理 Bot 不启动。")
            return

        application = Application.builder().token(bot_token).build()
        application.add_handler(CommandHandler("start", self.start_cmd))
        application.add_handler(CommandHandler("help", self.start_cmd))
        application.add_handler(CommandHandler("status", self.status_cmd))
        application.add_handler(CommandHandler("targets", self.targets_cmd))
        application.add_handler(CommandHandler("reload", self.reload_cmd))
        application.add_handler(CommandHandler("add_target", self.add_target_cmd))
        application.add_handler(CommandHandler("remove_target", self.remove_target_cmd))
        application.add_handler(CommandHandler("set_blacklist", self.set_blacklist_cmd))
        application.add_handler(CommandHandler("set_whitelist", self.set_whitelist_cmd))
        application.add_handler(CommandHandler("set_black_regex", self.set_black_regex_cmd))
        application.add_handler(CommandHandler("set_white_regex", self.set_white_regex_cmd))
        application.add_handler(CallbackQueryHandler(self.target_button_cmd, pattern=r"^target:\d+$"))
        application.add_handler(CallbackQueryHandler(self.edit_button_cmd, pattern=r"^edit:[a-z_]+:\d+$"))
        application.add_handler(CallbackQueryHandler(self.delete_target_button_cmd, pattern=r"^delete_target:\d+$"))
        application.add_handler(CallbackQueryHandler(self.toggle_target_button_cmd, pattern=r"^toggle:(enable|disable):\d+$"))
        application.add_handler(CallbackQueryHandler(self.targets_list_button_cmd, pattern=r"^targets:list$"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_message_cmd))

        commands = [
            BotCommand("start", "显示帮助信息"),
            BotCommand("help", "显示帮助信息"),
            BotCommand("status", "查看运行状态"),
            BotCommand("targets", "查看转发规则列表"),
            BotCommand("reload", "重载转发器"),
            BotCommand("add_target", "添加转发规则"),
            BotCommand("remove_target", "删除转发规则"),
            BotCommand("set_blacklist", "设置关键词黑名单"),
            BotCommand("set_whitelist", "设置关键词白名单"),
            BotCommand("set_black_regex", "设置正则黑名单"),
            BotCommand("set_white_regex", "设置正则白名单"),
            BotCommand("cancel", "取消当前对话流程"),
        ]

        self._application = application
        await application.initialize()
        await application.bot.set_my_commands(commands)
        await application.start()
        try:
            await application.updater.start_polling()
        except Conflict as exc:
            logger.error("检测到 Telegram Bot polling 冲突：%s", exc)
            logger.error("请确认只保留一个 Bot 实例在运行，否则 getUpdates 会相互冲突。")
            await application.stop()
            await application.shutdown()
            self._application = None
            return
        logger.info("管理 Bot 已启动，并已注册快捷命令。")

    async def stop(self) -> None:
        if not self._application:
            return
        await self._application.updater.stop()
        await self._application.stop()
        await self._application.shutdown()
        logger.info("管理 Bot 已停止。")
