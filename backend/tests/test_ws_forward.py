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


@pytest.mark.Trait("Bug", "B30")
async def test_payload_cannot_override_the_channel_type():
    manager = ws.WSManager()
    client = _Client()
    manager._clients.add(client)
    await manager._forward(None, 1, 'fire_data_updated', json.dumps({'type': 'sos_alert', 'hotspot_count': 3}))
    assert client.sent == [{'type': 'fire_data_updated', 'hotspot_count': 3}]


@pytest.mark.Trait("Bug", "B30")
@pytest.mark.parametrize('payload', ['[1, 2]', '"text"', '42', 'null', 'not json'])
async def test_non_object_payload_is_dropped_with_a_warning(payload, caplog):
    manager = ws.WSManager()
    client = _Client()
    manager._clients.add(client)
    with caplog.at_level('WARNING'):
        await manager._forward(None, 1, 'sos_alert', payload)
    assert client.sent == []
    assert 'sos_alert' in caplog.text
