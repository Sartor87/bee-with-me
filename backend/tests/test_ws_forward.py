"""ws.WSManager forwards every configured pg_notify channel to browsers as {'type': channel, ...}."""

import json

import pytest

from backend import ws

pytestmark = [pytest.mark.Trait("Task", "T9"), pytest.mark.asyncio]


class _Client:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


async def test_fire_data_updated_is_forwarded():
    assert 'fire_data_updated' in ws.FORWARDED_CHANNELS
    assert {'location_update', 'sos_alert'} <= set(ws.FORWARDED_CHANNELS)
    manager = ws.WSManager()
    client = _Client()
    manager._clients.add(client)
    await manager._forward(None, 1, 'fire_data_updated', json.dumps({'hotspot_count': 3}))
    assert client.sent == [{'type': 'fire_data_updated', 'hotspot_count': 3}]
