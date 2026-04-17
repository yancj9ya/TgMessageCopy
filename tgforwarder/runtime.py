from __future__ import annotations

import asyncio

from .config_store import ConfigStore
from .forwarder import run_forwarder
from .logging_config import logger
from .state import load_state


class ForwarderRuntime:
    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store
        self._task: asyncio.Task | None = None
        self._reload_lock = asyncio.Lock()
        self._last_config: dict | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        config = self.config_store.load()
        state = load_state()
        self._last_config = config
        self._task = asyncio.create_task(run_forwarder(config, state))
        self._task.add_done_callback(self._handle_task_done)
        logger.info("ForwarderRuntime 已启动。")

    def _handle_task_done(self, task: asyncio.Task) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error("ForwarderRuntime 任务异常退出: %s", exc)

    async def reload(self) -> None:
        async with self._reload_lock:
            await self.stop()
            await self.start()
            logger.info("ForwarderRuntime 已重载。")

    async def stop(self) -> None:
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
