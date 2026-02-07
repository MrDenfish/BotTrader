"""Tests for v2.core.event_bus.EventBus."""

import asyncio
from dataclasses import dataclass

import pytest

from v2.core.event_bus import EventBus


@dataclass(frozen=True)
class FakeEventA:
    value: int


@dataclass(frozen=True)
class FakeEventB:
    text: str


class TestSubscribeAndPublish:
    def test_handler_receives_correct_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(FakeEventA, received.append)

        bus.publish(FakeEventA(value=42))

        assert len(received) == 1
        assert received[0].value == 42

    def test_handler_not_called_for_other_event_types(self):
        bus = EventBus()
        received = []
        bus.subscribe(FakeEventA, received.append)

        bus.publish(FakeEventB(text="hello"))

        assert len(received) == 0

    def test_multiple_handlers_for_same_event(self):
        bus = EventBus()
        received_a = []
        received_b = []
        bus.subscribe(FakeEventA, received_a.append)
        bus.subscribe(FakeEventA, received_b.append)

        bus.publish(FakeEventA(value=1))

        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_multiple_events_published(self):
        bus = EventBus()
        received = []
        bus.subscribe(FakeEventA, received.append)

        bus.publish(FakeEventA(value=1))
        bus.publish(FakeEventA(value=2))
        bus.publish(FakeEventA(value=3))

        assert [e.value for e in received] == [1, 2, 3]


class TestSubscribeAll:
    def test_global_handler_receives_all_events(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(received.append)

        bus.publish(FakeEventA(value=1))
        bus.publish(FakeEventB(text="hi"))

        assert len(received) == 2
        assert isinstance(received[0], FakeEventA)
        assert isinstance(received[1], FakeEventB)


class TestUnsubscribe:
    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        received = []
        bus.subscribe(FakeEventA, received.append)

        bus.publish(FakeEventA(value=1))
        bus.unsubscribe(FakeEventA, received.append)
        bus.publish(FakeEventA(value=2))

        assert len(received) == 1

    def test_unsubscribe_nonexistent_handler_no_error(self):
        bus = EventBus()
        bus.unsubscribe(FakeEventA, lambda e: None)  # no crash

    def test_unsubscribe_all_global(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(received.append)

        bus.publish(FakeEventA(value=1))
        bus.unsubscribe_all(received.append)
        bus.publish(FakeEventA(value=2))

        assert len(received) == 1


class TestErrorHandling:
    def test_failing_handler_does_not_break_others(self):
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("oops")

        bus.subscribe(FakeEventA, bad_handler)
        bus.subscribe(FakeEventA, received.append)

        bus.publish(FakeEventA(value=1))

        # Second handler still called despite first raising
        assert len(received) == 1

    def test_failing_global_handler_does_not_break_others(self):
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("oops")

        bus.subscribe_all(bad_handler)
        bus.subscribe_all(received.append)

        bus.publish(FakeEventA(value=1))

        assert len(received) == 1


class TestIntrospection:
    def test_handler_count_specific_type(self):
        bus = EventBus()
        bus.subscribe(FakeEventA, lambda e: None)
        bus.subscribe(FakeEventA, lambda e: None)
        bus.subscribe(FakeEventB, lambda e: None)

        assert bus.handler_count(FakeEventA) == 2
        assert bus.handler_count(FakeEventB) == 1

    def test_handler_count_total(self):
        bus = EventBus()
        bus.subscribe(FakeEventA, lambda e: None)
        bus.subscribe_all(lambda e: None)

        assert bus.handler_count() == 2

    def test_clear(self):
        bus = EventBus()
        bus.subscribe(FakeEventA, lambda e: None)
        bus.subscribe_all(lambda e: None)

        bus.clear()

        assert bus.handler_count() == 0


class TestAsync:
    @pytest.mark.asyncio
    async def test_async_publish_with_sync_handler(self):
        bus = EventBus()
        received = []
        bus.subscribe(FakeEventA, received.append)

        await bus.publish_async(FakeEventA(value=99))

        assert len(received) == 1
        assert received[0].value == 99

    @pytest.mark.asyncio
    async def test_async_publish_with_async_handler(self):
        bus = EventBus()
        received = []

        async def async_handler(event):
            received.append(event)

        bus.subscribe(FakeEventA, async_handler)

        await bus.publish_async(FakeEventA(value=77))

        assert len(received) == 1
        assert received[0].value == 77

    @pytest.mark.asyncio
    async def test_async_failing_handler_does_not_break_others(self):
        bus = EventBus()
        received = []

        async def bad_handler(event):
            raise RuntimeError("async oops")

        bus.subscribe(FakeEventA, bad_handler)
        bus.subscribe(FakeEventA, received.append)

        await bus.publish_async(FakeEventA(value=1))

        assert len(received) == 1
