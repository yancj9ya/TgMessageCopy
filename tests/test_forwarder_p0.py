import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from telethon.errors import ChatAdminRequiredError, FloodWaitError, RPCError

from tgforwarder.forwarder import (
    ONLINE_RETRY_MAX_ATTEMPTS,
    RecoveryRateLimiter,
    consumer_loop,
    process_message,
    run_forwarder,
)


class DummyMessage:
    def __init__(self, message_id: int = 1) -> None:
        self.id = message_id
        self.message = "message"
        self.text = "message"
        self.media = None
        self.entities = []
        self.reply_markup = None


class ProcessMessageExceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_flood_wait_sleeps_and_does_not_disable_native_forward(self) -> None:
        disabled: set[str] = set()
        flood_wait = FloodWaitError(request=None, capture=7)

        with (
            patch(
                "tgforwarder.forwarder.safe_forward_message",
                new_callable=AsyncMock,
                side_effect=[flood_wait, None],
            ) as send,
            patch("tgforwarder.forwarder.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await process_message(
                client=object(),
                message=DummyMessage(),
                target={"source": "source", "destination": "destination"},
                destination_entity=object(),
                state_key="source=>destination",
                native_forward_disabled=disabled,
            )

        self.assertEqual(result, (True, True))
        sleep.assert_awaited_once_with(7)
        self.assertEqual(disabled, set())
        self.assertEqual(send.await_count, 2)
        self.assertTrue(send.await_args_list[1].kwargs["use_native_forward"])

    async def test_admin_error_disables_native_forward_and_falls_back(self) -> None:
        disabled: set[str] = set()

        with patch(
            "tgforwarder.forwarder.safe_forward_message",
            new_callable=AsyncMock,
            side_effect=[ChatAdminRequiredError(request=None), None],
        ) as send:
            result = await process_message(
                client=object(),
                message=DummyMessage(),
                target={"source": "source", "destination": "destination"},
                destination_entity=object(),
                state_key="source=>destination",
                native_forward_disabled=disabled,
            )

        self.assertEqual(result, (True, True))
        self.assertEqual(disabled, {"source=>destination"})
        self.assertFalse(send.await_args_list[1].kwargs["use_native_forward"])

    async def test_generic_rpc_error_does_not_disable_native_forward(self) -> None:
        disabled: set[str] = set()

        with patch(
            "tgforwarder.forwarder.safe_forward_message",
            new_callable=AsyncMock,
            side_effect=RPCError(request=None, message="temporary", code=500),
        ):
            result = await process_message(
                client=object(),
                message=DummyMessage(),
                target={"source": "source", "destination": "destination"},
                destination_entity=object(),
                state_key="source=>destination",
                native_forward_disabled=disabled,
            )

        self.assertEqual(result, (False, False))
        self.assertEqual(disabled, set())


class MemoryQueueStore:
    def __init__(self, items=None) -> None:
        self.items = list(items or [])
        self.removed = []

    def load(self):
        return list(self.items)

    def remove_first_match(self, state_key: str, message_id: int) -> None:
        self.removed.append((state_key, message_id))
        for index, item in enumerate(self.items):
            if item.get("state_key") == state_key and int(item.get("message_id", -1)) == message_id:
                del self.items[index]
                break


class ConsumerReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def _run_consumer(self, task, state, process_results):
        queue = asyncio.Queue()
        await queue.put(task)
        stop_event = asyncio.Event()
        stop_event.set()
        store = MemoryQueueStore([
            {"state_key": task["state_key"], "message_id": task["message"].id}
        ])
        runtime_state = {}
        limiter = RecoveryRateLimiter()

        with (
            patch(
                "tgforwarder.forwarder.process_message",
                new_callable=AsyncMock,
                side_effect=process_results,
            ) as process,
            patch("tgforwarder.forwarder.asyncio.sleep", new_callable=AsyncMock) as sleep,
            patch("tgforwarder.forwarder.save_state") as save,
        ):
            await consumer_loop(
                object(), queue, state, set(), runtime_state, stop_event, store, limiter
            )

        return store, runtime_state, process, sleep, save

    async def test_recovered_older_message_never_moves_state_backwards(self) -> None:
        task = {
            "message": DummyMessage(100),
            "target": {"source": "source", "destination": "destination"},
            "destination_entity": object(),
            "state_key": "source=>destination",
            "is_recovered": True,
        }
        state = {"source=>destination": 101}

        store, runtime_state, _, _, save = await self._run_consumer(
            task, state, [(True, True)]
        )

        self.assertEqual(state["source=>destination"], 101)
        save.assert_called_once_with(state)
        self.assertEqual(store.items, [])
        self.assertEqual(runtime_state["persistent_queue_size"], 0)

    async def test_network_failure_retries_online_then_succeeds(self) -> None:
        task = {
            "message": DummyMessage(200),
            "target": {"source": "source", "destination": "destination"},
            "destination_entity": object(),
            "state_key": "source=>destination",
        }
        state = {}

        store, _, process, sleep, _ = await self._run_consumer(
            task, state, [(False, False), (True, True)]
        )

        self.assertEqual(process.await_count, 2)
        sleep.assert_awaited_once_with(5.0)
        self.assertEqual(state["source=>destination"], 200)
        self.assertEqual(store.items, [])

    async def test_network_failure_stops_after_configured_online_retries(self) -> None:
        task = {
            "message": DummyMessage(300),
            "target": {"source": "source", "destination": "destination"},
            "destination_entity": object(),
            "state_key": "source=>destination",
        }
        state = {}
        failures = [(False, False)] * (ONLINE_RETRY_MAX_ATTEMPTS + 1)

        store, _, process, sleep, _ = await self._run_consumer(task, state, failures)

        self.assertEqual(process.await_count, ONLINE_RETRY_MAX_ATTEMPTS + 1)
        self.assertEqual(sleep.await_count, ONLINE_RETRY_MAX_ATTEMPTS)
        self.assertNotIn("source=>destination", state)
        self.assertEqual(len(store.items), 1)


class FakeEntity:
    id = 123


class BlockingTelegramClient:
    instance = None

    def __init__(self, *args, **kwargs) -> None:
        type(self).instance = self
        self.connected = True
        self.handler = None
        self.run_started = asyncio.Event()

    async def start(self) -> None:
        return None

    async def get_entity(self, value):
        return FakeEntity()

    async def get_messages(self, *args, **kwargs):
        if kwargs.get("limit") == 1:
            return [DummyMessage(10)]
        return DummyMessage(int(kwargs["ids"]))

    def on(self, event):
        def decorator(handler):
            self.handler = handler
            return handler
        return decorator

    async def run_until_disconnected(self) -> None:
        self.run_started.set()
        await asyncio.Event().wait()

    def is_connected(self) -> bool:
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False


class ForwarderLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_forwarder_cancels_consumer(self) -> None:
        consumer_started = asyncio.Event()
        consumer_cancelled = asyncio.Event()

        async def blocking_consumer(*args, **kwargs):
            consumer_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                consumer_cancelled.set()
                raise

        config = {
            "api_id": 1,
            "api_hash": "hash",
            "session_name": "test",
            "proxy": None,
            "targets": [{
                "source": "source",
                "destination": "destination",
                "enabled": True,
                "startup_last_message_id": 0,
            }],
        }

        with (
            patch("tgforwarder.forwarder.TelegramClient", BlockingTelegramClient),
            patch("tgforwarder.forwarder.QueueStore", return_value=MemoryQueueStore()),
            patch("tgforwarder.forwarder.consumer_loop", side_effect=blocking_consumer),
            patch("tgforwarder.forwarder.save_state"),
        ):
            forwarder_task = asyncio.create_task(run_forwarder(config, {}))
            await consumer_started.wait()
            await BlockingTelegramClient.instance.run_started.wait()
            forwarder_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await forwarder_task

        self.assertTrue(consumer_cancelled.is_set())
        self.assertFalse(BlockingTelegramClient.instance.connected)


class ReturningTelegramClient(BlockingTelegramClient):
    async def run_until_disconnected(self) -> None:
        return None


class RecoveryRuleConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def _config(self):
        return {
            "api_id": 1,
            "api_hash": "hash",
            "session_name": "test",
            "proxy": None,
            "targets": [{
                "source": "source",
                "destination": "destination",
                "enabled": True,
                "startup_last_message_id": 0,
                "blacklist_keywords": ["blocked"],
                "whitelist_keywords": ["allowed"],
                "blacklist_regex": ["deny"],
                "whitelist_regex": ["permit"],
                "caption_prefix": "current prefix",
            }],
        }

    async def test_recovery_uses_complete_current_rule(self) -> None:
        state_key = "source=>destination"
        store = MemoryQueueStore([{
            "source": "source",
            "destination": "destination",
            "state_key": state_key,
            "message_id": 42,
            "caption_prefix": "old prefix",
        }])
        captured = []

        async def capture_consumer(client, queue, *args):
            while not queue.empty():
                captured.append(await queue.get())
                queue.task_done()

        with (
            patch("tgforwarder.forwarder.TelegramClient", ReturningTelegramClient),
            patch("tgforwarder.forwarder.QueueStore", return_value=store),
            patch("tgforwarder.forwarder.consumer_loop", side_effect=capture_consumer),
            patch("tgforwarder.forwarder.save_state"),
        ):
            await run_forwarder(self._config(), {})

        self.assertEqual(len(captured), 1)
        recovered_target = captured[0]["target"]
        self.assertEqual(recovered_target["caption_prefix"], "current prefix")
        self.assertEqual(recovered_target["blacklist_keywords"], ["blocked"])
        self.assertEqual(recovered_target["whitelist_regex"], ["permit"])
        self.assertTrue(captured[0]["is_recovered"])

    async def test_recovery_discards_item_for_inactive_rule(self) -> None:
        store = MemoryQueueStore([{
            "source": "old-source",
            "destination": "old-destination",
            "state_key": "old-source=>old-destination",
            "message_id": 43,
        }])
        captured = []

        async def capture_consumer(client, queue, *args):
            while not queue.empty():
                captured.append(await queue.get())
                queue.task_done()

        with (
            patch("tgforwarder.forwarder.TelegramClient", ReturningTelegramClient),
            patch("tgforwarder.forwarder.QueueStore", return_value=store),
            patch("tgforwarder.forwarder.consumer_loop", side_effect=capture_consumer),
            patch("tgforwarder.forwarder.save_state"),
        ):
            await run_forwarder(self._config(), {})

        self.assertEqual(captured, [])
        self.assertEqual(store.items, [])
        self.assertEqual(store.removed, [("old-source=>old-destination", 43)])
