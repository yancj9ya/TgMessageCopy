import asyncio
import os
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import ChatAdminRequiredError, FloodWaitError, RPCError

from .chat_format import normalize_chat_input
from .filters import (
    keyword_allowed,
    regex_allowed,
    explain_keyword_filter,
    explain_regex_filter,
    extract_searchable_message_text,
)
from .logging_config import logger
from .paths import MEDIA_DIR, SESSION_DIR, ensure_data_dirs
from .queue_store import QueueStore
from .state import build_target_key, save_state


def build_caption(prefix: str, message_text: str | None) -> str | None:
    parts = []
    if prefix:
        parts.append(prefix.strip())
    if message_text:
        parts.append(message_text.strip())
    content = "\n\n".join([item for item in parts if item])
    return content or None


async def resolve_entity(client: TelegramClient, raw_value: Any):
    try:
        normalized = normalize_chat_input(raw_value)
        return await client.get_entity(normalized)
    except Exception as exc:
        raise RuntimeError(f"无法解析实体 {raw_value}: {exc}") from exc


async def init_target_state(
    client: TelegramClient,
    target: dict[str, Any],
    state: dict[str, int],
) -> tuple[Any, Any, str]:
    source_raw = target["source"]
    destination_raw = target["destination"]
    source_entity = await resolve_entity(client, source_raw)
    destination_entity = await resolve_entity(client, destination_raw)
    state_key = build_target_key(source_raw, destination_raw)

    if state_key not in state:
        startup_last_message_id = target.get("startup_last_message_id")
        if startup_last_message_id is None:
            latest_message = await client.get_messages(source_entity, limit=1)
            latest_id = 0
            if latest_message:
                latest_id = int(getattr(latest_message[0], "id", 0) or 0)
            state[state_key] = latest_id
            logger.info("首次启动，来源 %s 自动初始化为最新消息 ID=%s。", source_raw, latest_id)
        else:
            state[state_key] = int(startup_last_message_id)
            logger.info("来源 %s 初始化 last_message_id=%s。", source_raw, state[state_key])
        save_state(state)

    return source_entity, destination_entity, state_key


async def safe_forward_message(
    client: TelegramClient,
    message,
    destination,
    caption_prefix: str,
    use_native_forward: bool = True,
) -> None:
    if use_native_forward:
        try:
            await client.forward_messages(destination, message)
            return
        except (ChatAdminRequiredError, RPCError) as exc:
            logger.warning("原生转发失败，改为重发模式: %s", exc)
            raise

    caption = build_caption(caption_prefix, message.message)

    if message.media:
        MEDIA_DIR.mkdir(exist_ok=True)
        file_path = await message.download_media(file=MEDIA_DIR)
        if not file_path:
            raise RuntimeError("媒体下载失败")
        try:
            await client.send_file(destination, file_path, caption=caption)
        finally:
            try:
                os.remove(file_path)
            except OSError:
                logger.warning("临时文件删除失败: %s", file_path)
        return

    if caption:
        await client.send_message(destination, caption)


async def process_message(
    client: TelegramClient,
    message,
    target: dict[str, Any],
    destination_entity,
    state_key: str,
    native_forward_disabled: set[str],
) -> tuple[bool, bool]:
    if not message or not getattr(message, "id", None):
        return False, False

    message_text = extract_searchable_message_text(message)
    if not message.media and not message_text.strip():
        return True, True

    keyword_reason = explain_keyword_filter(message_text, target)
    if keyword_reason is not None and not keyword_allowed(message_text, target):
        logger.info("消息 %s 因关键词过滤被跳过：%s", message.id, keyword_reason)
        return True, True

    regex_reason = explain_regex_filter(message_text, target)
    if regex_reason is not None and not regex_allowed(message_text, target):
        logger.info("消息 %s 因正则过滤被跳过：%s", message.id, regex_reason)
        return True, True

    try:
        force_rebuild = state_key in native_forward_disabled
        if force_rebuild:
            logger.info("规则 %s 已进入强制重发模式，跳过原生转发。", state_key)
        await safe_forward_message(
            client,
            message,
            destination_entity,
            target.get("caption_prefix", ""),
            use_native_forward=not force_rebuild,
        )
        logger.info(
            "转发成功: %s -> %s, message_id=%s",
            target["source"],
            target["destination"],
            message.id,
        )
        return True, True
    except (ChatAdminRequiredError, RPCError) as exc:
        native_forward_disabled.add(state_key)
        logger.warning("规则 %s 首次原生转发失败，后续到下次重载前都将强制使用重发模式: %s", state_key, exc)
        await safe_forward_message(
            client,
            message,
            destination_entity,
            target.get("caption_prefix", ""),
            use_native_forward=False,
        )
        return True, True
    except FloodWaitError as exc:
        logger.warning("触发 FloodWait，等待 %s 秒", exc.seconds)
        await asyncio.sleep(exc.seconds)
        await safe_forward_message(
            client,
            message,
            destination_entity,
            target.get("caption_prefix", ""),
            use_native_forward=state_key not in native_forward_disabled,
        )
        return True, True


async def consumer_loop(
    client: TelegramClient,
    queue: asyncio.Queue,
    state: dict[str, int],
    native_forward_disabled: set[str],
    runtime_state: dict[str, Any],
    stop_event: asyncio.Event,
    queue_store: QueueStore,
) -> None:
    runtime_state["consumer_running"] = True
    try:
        while True:
            if stop_event.is_set() and queue.empty():
                break
            try:
                task = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            runtime_state["queue_size"] = queue.qsize()
            try:
                ok, should_advance_state = await process_message(
                    client,
                    task["message"],
                    task["target"],
                    task["destination_entity"],
                    task["state_key"],
                    native_forward_disabled,
                )
                if ok and should_advance_state:
                    state[task["state_key"]] = task["message"].id
                    save_state(state)
                    queue_store.remove_first_match(task["state_key"], task["message"].id)
            finally:
                queue.task_done()
                runtime_state["queue_size"] = queue.qsize()
    finally:
        runtime_state["consumer_running"] = False


async def run_forwarder(config: dict[str, Any], state: dict[str, int]) -> None:
    from .config import build_proxy

    ensure_data_dirs()
    queue_store = QueueStore()
    proxy = build_proxy(config.get("proxy"))
    session_path = SESSION_DIR / config["session_name"]
    client = TelegramClient(
        str(session_path),
        int(config["api_id"]),
        config["api_hash"],
        proxy=proxy,
        auto_reconnect=True,
        connection_retries=None,
        retry_delay=5,
    )

    try:
        await client.start()
        logger.info("Telegram 客户端已启动，session 持久化目录: %s", SESSION_DIR)

        watched_chat_ids: dict[int, list[dict[str, Any]]] = {}
        native_forward_disabled: set[str] = set()
        queue: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()
        runtime_state = config.setdefault("_runtime_state", {})
        runtime_state["native_forward_disabled"] = native_forward_disabled
        runtime_state["queue_size"] = 0
        runtime_state["consumer_running"] = False
        runtime_state["persistent_queue_size"] = 0

        persisted_items = queue_store.load()

        for target in config.get("targets", []):
            if not target.get("enabled", True):
                logger.info("规则已禁用，跳过: %s -> %s", target.get("source"), target.get("destination"))
                continue
            try:
                source_entity, destination_entity, state_key = await init_target_state(client, target, state)
            except Exception as exc:
                logger.error("跳过无效转发规则 %s -> %s: %s", target.get("source"), target.get("destination"), exc)
                continue

            source_id = getattr(source_entity, "id", None)
            if source_id is None:
                logger.error("跳过规则，来源频道无法获取 id: %s", target.get("source"))
                continue

            watched_chat_ids.setdefault(int(source_id), []).append(
                {
                    "target": target,
                    "destination_entity": destination_entity,
                    "state_key": state_key,
                }
            )
            logger.info("监听已注册: %s -> %s", target["source"], target["destination"])

        if not watched_chat_ids:
            logger.warning("没有可用的有效转发规则，forwarder 保持空闲。")
            return

        consumer_task = asyncio.create_task(
            consumer_loop(client, queue, state, native_forward_disabled, runtime_state, stop_event, queue_store)
        )

        for item in persisted_items:
            try:
                source_entity = await resolve_entity(client, item["source"])
                destination_entity = await resolve_entity(client, item["destination"])
                message = await client.get_messages(source_entity, ids=int(item["message_id"]))
                if not message:
                    queue_store.remove_first_match(item["state_key"], int(item["message_id"]))
                    continue
                target = {
                    "source": item["source"],
                    "destination": item["destination"],
                    "caption_prefix": item.get("caption_prefix", ""),
                }
                await queue.put(
                    {
                        "message": message,
                        "target": target,
                        "destination_entity": destination_entity,
                        "state_key": item["state_key"],
                    }
                )
            except Exception as exc:
                logger.error("恢复持久化队列项失败，已跳过 %s: %s", item, exc)
                queue_store.remove_first_match(item["state_key"], int(item["message_id"]))
        runtime_state["queue_size"] = queue.qsize()
        runtime_state["persistent_queue_size"] = len(queue_store.load())

        @client.on(events.NewMessage(chats=list(watched_chat_ids.keys())))
        async def handler(event):
            chat_id = getattr(event.chat, "id", None)
            if chat_id is None:
                return
            bindings = watched_chat_ids.get(int(chat_id))
            if not bindings:
                return
            for binding in bindings:
                message = event.message
                if not message or not getattr(message, "id", None):
                    continue
                if message.id <= state.get(binding["state_key"], 0):
                    continue
                await queue.put(
                    {
                        "message": message,
                        "target": binding["target"],
                        "destination_entity": binding["destination_entity"],
                        "state_key": binding["state_key"],
                    }
                )
                queue_store.append(
                    {
                        "source": binding["target"]["source"],
                        "destination": binding["target"]["destination"],
                        "state_key": binding["state_key"],
                        "message_id": message.id,
                        "caption_prefix": binding["target"].get("caption_prefix", ""),
                    }
                )
                runtime_state["queue_size"] = queue.qsize()
                runtime_state["persistent_queue_size"] = len(queue_store.load())

        logger.info("开始监听新消息。按 Ctrl+C 退出。")
        await client.run_until_disconnected()
        stop_event.set()
        await queue.join()
        await consumer_task
    finally:
        if client.is_connected():
            await client.disconnect()
