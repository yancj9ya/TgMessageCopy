import asyncio
from collections import deque
import os
import random
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.errors import (
    ChatAdminRequiredError,
    ChatForwardsRestrictedError,
    FloodWaitError,
    RPCError,
)

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


ONLINE_RETRY_MAX_ATTEMPTS = 5
ONLINE_RETRY_INITIAL_SECONDS = 5.0
ONLINE_RETRY_MAX_SECONDS = 80.0


def get_online_retry_delay(retry_count: int) -> float:
    return min(
        ONLINE_RETRY_INITIAL_SECONDS * (2 ** retry_count),
        ONLINE_RETRY_MAX_SECONDS,
    )


class RecoveryRateLimiter:
    """Limit sends restored from the persistent queue without slowing live messages."""

    def __init__(
        self,
        max_messages: int = 10,
        window_seconds: float = 60.0,
        min_delay_seconds: float = 3.0,
        max_delay_seconds: float = 5.0,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_delay: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self._clock = clock
        self._sleep = sleep
        self._random_delay = random_delay
        self._send_times: deque[float] = deque()
        self._last_send_time: float | None = None

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        return asyncio.get_running_loop().time()

    async def acquire(self) -> None:
        delay = 0.0
        if self._last_send_time is not None:
            delay = self._random_delay(self.min_delay_seconds, self.max_delay_seconds)

        while True:
            now = self._now()
            while self._send_times and now - self._send_times[0] >= self.window_seconds:
                self._send_times.popleft()

            wait_for_gap = 0.0
            if self._last_send_time is not None:
                wait_for_gap = self._last_send_time + delay - now

            wait_for_window = 0.0
            if len(self._send_times) >= self.max_messages:
                wait_for_window = self._send_times[0] + self.window_seconds - now

            wait_seconds = max(wait_for_gap, wait_for_window, 0.0)
            if wait_seconds <= 0:
                send_time = self._now()
                self._send_times.append(send_time)
                self._last_send_time = send_time
                return

            logger.info("持久化队列恢复限速，等待 %.1f 秒后发送下一条消息。", wait_seconds)
            await self._sleep(wait_seconds)


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
        await client.forward_messages(destination, message)
        return

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
    recovery_rate_limiter: RecoveryRateLimiter | None = None,
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

    if recovery_rate_limiter is not None:
        await recovery_rate_limiter.acquire()

    use_native_forward = state_key not in native_forward_disabled
    if not use_native_forward:
        logger.info("规则 %s 已进入强制重发模式，跳过原生转发。", state_key)

    while True:
        try:
            await safe_forward_message(
                client,
                message,
                destination_entity,
                target.get("caption_prefix", ""),
                use_native_forward=use_native_forward,
            )
            logger.info(
                "转发成功: %s -> %s, message_id=%s",
                target["source"],
                target["destination"],
                message.id,
            )
            return True, True
        except FloodWaitError as exc:
            logger.warning(
                "消息 %s 触发 FloodWait，等待 %s 秒后重试。",
                message.id,
                exc.seconds,
            )
            await asyncio.sleep(exc.seconds)
            continue
        except (ChatAdminRequiredError, ChatForwardsRestrictedError) as exc:
            if not use_native_forward:
                logger.warning("消息 %s 在重发模式下仍无发送权限，稍后重试: %s", message.id, exc)
                return False, False
            native_forward_disabled.add(state_key)
            use_native_forward = False
            logger.warning(
                "规则 %s 不允许原生转发，后续到下次重载前使用重发模式: %s",
                state_key,
                exc,
            )
            continue
        except RPCError as exc:
            logger.warning(
                "消息 %s 遇到 Telegram RPC 异常，不切换重发模式，稍后重试: %s",
                message.id,
                exc,
            )
            return False, False
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "消息 %s 转发遇到网络异常，保留在持久化队列中稍后重试: %s",
                message.id,
                exc,
            )
            return False, False



async def consumer_loop(
    client: TelegramClient,
    queue: asyncio.Queue,
    state: dict[str, int],
    native_forward_disabled: set[str],
    runtime_state: dict[str, Any],
    stop_event: asyncio.Event,
    queue_store: QueueStore,
    recovery_rate_limiter: RecoveryRateLimiter,
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
                    recovery_rate_limiter if task.get("is_recovered", False) else None,
                )
                if ok and should_advance_state:
                    state[task["state_key"]] = max(
                        state.get(task["state_key"], 0),
                        int(task["message"].id),
                    )
                    save_state(state)
                    queue_store.remove_first_match(task["state_key"], task["message"].id)
                    runtime_state["persistent_queue_size"] = len(queue_store.load())
                elif not ok:
                    retry_count = int(task.get("retry_count", 0))
                    if retry_count < ONLINE_RETRY_MAX_ATTEMPTS:
                        retry_delay = get_online_retry_delay(retry_count)
                        logger.warning(
                            "消息 %s 将在 %.1f 秒后进行第 %s/%s 次在线重试。",
                            task["message"].id,
                            retry_delay,
                            retry_count + 1,
                            ONLINE_RETRY_MAX_ATTEMPTS,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_task = dict(task)
                        retry_task["retry_count"] = retry_count + 1
                        await queue.put(retry_task)
                    else:
                        logger.error(
                            "消息 %s 已达到在线重试上限 %s 次，仍保留在持久化队列中，等待下次重启或重载。",
                            task["message"].id,
                            ONLINE_RETRY_MAX_ATTEMPTS,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message_id = getattr(task.get("message"), "id", "?")
                logger.exception("消费者处理消息 %s 异常，已跳过本条: %s", message_id, exc)
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
    stop_event: asyncio.Event | None = None
    consumer_task: asyncio.Task | None = None

    try:
        await client.start()
        logger.info("Telegram 客户端已启动，session 持久化目录: %s", SESSION_DIR)

        watched_chat_ids: dict[int, list[dict[str, Any]]] = {}
        active_bindings: dict[str, dict[str, Any]] = {}
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

            binding = {
                "target": target,
                "source_entity": source_entity,
                "destination_entity": destination_entity,
                "state_key": state_key,
            }
            watched_chat_ids.setdefault(int(source_id), []).append(binding)
            active_bindings[state_key] = binding
            logger.info("监听已注册: %s -> %s", target["source"], target["destination"])

        if not watched_chat_ids:
            logger.warning("没有可用的有效转发规则，forwarder 保持空闲。")
            return

        recovery_rate_limiter = RecoveryRateLimiter()
        consumer_task = asyncio.create_task(
            consumer_loop(
                client,
                queue,
                state,
                native_forward_disabled,
                runtime_state,
                stop_event,
                queue_store,
                recovery_rate_limiter,
            )
        )

        for item in persisted_items:
            state_key = ""
            message_id = -1
            try:
                state_key = str(item.get("state_key", ""))
                message_id = int(item.get("message_id", -1))
                binding = active_bindings.get(state_key)
                if binding is None:
                    logger.warning(
                        "持久化队列项对应的规则已删除、停用或无效，丢弃: state_key=%s, message_id=%s",
                        state_key,
                        message_id,
                    )
                    queue_store.remove_first_match(state_key, message_id)
                    continue

                message = await client.get_messages(
                    binding["source_entity"],
                    ids=message_id,
                )
                if not message:
                    queue_store.remove_first_match(state_key, message_id)
                    continue
                await queue.put(
                    {
                        "message": message,
                        "target": binding["target"],
                        "destination_entity": binding["destination_entity"],
                        "state_key": state_key,
                        "is_recovered": True,
                    }
                )
            except Exception as exc:
                logger.error("恢复持久化队列项失败，已丢弃 %s: %s", item, exc)
                if state_key and message_id >= 0:
                    queue_store.remove_first_match(state_key, message_id)
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
        if stop_event is not None:
            stop_event.set()
        if consumer_task is not None and not consumer_task.done():
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        if client.is_connected():
            await client.disconnect()
