from __future__ import annotations

import asyncio

from .config_store import ConfigStore
from .forwarder import run_forwarder
from .logging_config import logger
from .state import load_state


class ForwarderRuntime:
    RESTART_BACKOFF_INITIAL_SECONDS = 5
    RESTART_BACKOFF_MAX_SECONDS = 300
    RESTART_BACKOFF_RESET_SECONDS = 60

    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store
        self._task: asyncio.Task | None = None
        self._reload_lock = asyncio.Lock()
        self._last_config: dict | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._supervise())
        logger.info("ForwarderRuntime 已启动。")

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        backoff = self.RESTART_BACKOFF_INITIAL_SECONDS
        restart_count = 0
        while not self._stopping:
            if restart_count > 0:
                logger.info("forwarder 第 %d 次重启开始。", restart_count)
            started_at = loop.time()
            try:
                config = self.config_store.load()
                state = load_state()
                self._last_config = config
                await run_forwarder(config, state)
                if self._stopping:
                    return
                logger.info("forwarder 主动退出，将按退避策略重启。")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stopping:
                    return
                logger.error(
                    "forwarder 异常退出，%s 秒后自动重启: %s",
                    backoff, exc, exc_info=True,
                )

            if loop.time() - started_at >= self.RESTART_BACKOFF_RESET_SECONDS:
                backoff = self.RESTART_BACKOFF_INITIAL_SECONDS

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, self.RESTART_BACKOFF_MAX_SECONDS)
            restart_count += 1

    async def reload(self) -> None:
        async with self._reload_lock:
            await self.stop()
            await self.start()
            logger.info("ForwarderRuntime 已重载。")

    async def stop(self) -> None:
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("ForwarderRuntime 已停止。")
            await asyncio.sleep(0.5)
        self._task = None

    def status_text(self) -> str:
        if self._task and not self._task.done():
            return "运行中"
        return "未运行"

    def native_forward_disabled_rules(self) -> list[str]:
        if not self._last_config:
            return []
        runtime_state = self._last_config.get("_runtime_state", {})
        disabled = runtime_state.get("native_forward_disabled", set())
        return sorted(str(item) for item in disabled)

    def queue_size(self) -> int:
        if not self._last_config:
            return 0
        runtime_state = self._last_config.get("_runtime_state", {})
        return int(runtime_state.get("queue_size", 0))

    def persistent_queue_size(self) -> int:
        if not self._last_config:
            return 0
        runtime_state = self._last_config.get("_runtime_state", {})
        return int(runtime_state.get("persistent_queue_size", 0))

    def consumer_status_text(self) -> str:
        if not self._last_config:
            return "未知"
        runtime_state = self._last_config.get("_runtime_state", {})
        return "运行中" if runtime_state.get("consumer_running", False) else "未运行"
