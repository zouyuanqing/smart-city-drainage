import asyncio

import pytest

from app.services.sse_manager import SSEManager


@pytest.mark.asyncio
async def test_sse_manager_singleton():
    m1 = SSEManager.get_instance()
    m2 = SSEManager.get_instance()
    assert m1 is m2


@pytest.mark.asyncio
async def test_sse_connect_disconnect():
    manager = SSEManager()
    client_id, _queue = await manager.connect()
    assert client_id in manager._clients
    await manager.disconnect(client_id)
    assert client_id not in manager._clients


@pytest.mark.asyncio
async def test_sse_broadcast():
    manager = SSEManager()
    client_id, queue = await manager.connect()
    await manager.broadcast("test_event", {"message": "hello"})
    client = manager._clients[client_id]
    event = await asyncio.wait_for(client.queue.get(), timeout=1.0)
    assert event["event"] == "test_event"
    await manager.disconnect(client_id)
