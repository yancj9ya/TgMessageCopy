import unittest
from unittest.mock import AsyncMock, patch

from tgforwarder.forwarder import RecoveryRateLimiter, process_message


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class DummyMessage:
    def __init__(self, message_id: int, text: str = "待恢复消息") -> None:
        self.id = message_id
        self.message = text
        self.text = text
        self.media = None
        self.entities = []
        self.reply_markup = None


class RecoveryRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_random_gap_and_ten_per_rolling_minute(self) -> None:
        fake_time = FakeTime()
        limiter = RecoveryRateLimiter(
            clock=fake_time.monotonic,
            sleep=fake_time.sleep,
            random_delay=lambda minimum, maximum: 3.0,
        )

        send_times = []
        for _ in range(11):
            await limiter.acquire()
            send_times.append(fake_time.now)

        self.assertEqual(send_times[:10], [float(i * 3) for i in range(10)])
        self.assertEqual(send_times[10], 60.0)
        self.assertTrue(all(
            sum(start < sent_at <= start + 60 for sent_at in send_times) <= 10
            for start in send_times
        ))

    async def test_delay_is_selected_inside_configured_range(self) -> None:
        fake_time = FakeTime()
        requested_ranges = []

        def choose_delay(minimum: float, maximum: float) -> float:
            requested_ranges.append((minimum, maximum))
            return 4.0

        limiter = RecoveryRateLimiter(
            clock=fake_time.monotonic,
            sleep=fake_time.sleep,
            random_delay=choose_delay,
        )

        await limiter.acquire()
        await limiter.acquire()

        self.assertEqual(requested_ranges, [(3.0, 5.0)])
        self.assertEqual(fake_time.sleeps, [4.0])

    async def test_filtered_recovered_message_does_not_consume_send_quota(self) -> None:
        limiter = AsyncMock()
        target = {
            "source": "source",
            "destination": "destination",
            "blacklist_keywords": ["blocked"],
        }

        result = await process_message(
            client=object(),
            message=DummyMessage(1, "blocked content"),
            target=target,
            destination_entity=object(),
            state_key="source=>destination",
            native_forward_disabled=set(),
            recovery_rate_limiter=limiter,
        )

        self.assertEqual(result, (True, True))
        limiter.acquire.assert_not_awaited()

    async def test_sendable_recovered_message_acquires_quota_before_send(self) -> None:
        limiter = AsyncMock()
        target = {"source": "source", "destination": "destination"}

        with patch("tgforwarder.forwarder.safe_forward_message", new_callable=AsyncMock) as send:
            result = await process_message(
                client=object(),
                message=DummyMessage(2),
                target=target,
                destination_entity=object(),
                state_key="source=>destination",
                native_forward_disabled=set(),
                recovery_rate_limiter=limiter,
            )

        self.assertEqual(result, (True, True))
        limiter.acquire.assert_awaited_once()
        send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
