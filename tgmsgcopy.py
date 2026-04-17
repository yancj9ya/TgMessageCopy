import asyncio

from tgforwarder.config import ensure_config, validate_config
from tgforwarder.bot_manager import BotManager
from tgforwarder.config_store import ConfigStore
from tgforwarder.logging_config import logger, setup_logging
from tgforwarder.runtime import ForwarderRuntime


async def main() -> None:
    setup_logging()
    config = ensure_config()
    validate_config(config)

    config_store = ConfigStore()
    runtime = ForwarderRuntime(config_store)
    bot_manager = BotManager(config_store, runtime)

    await runtime.start()
    await bot_manager.start()

    try:
        await asyncio.Event().wait()
    finally:
        await bot_manager.stop()
        await runtime.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序已退出。")
