"""B50: json_agg columns reach the client as real arrays, not JSON strings."""
from unittest.mock import AsyncMock

import pytest

from backend.json_columns import decode_json_columns

pytestmark = [pytest.mark.Trait("Bug", "B50")]


@pytest.mark.Trait("Bug", "B50")
def test_decodes_json_string_to_list():
    row = {'device_id': 'd1', 'groups': '[{"id": "g1", "color": "#fff", "is_leader": true}]'}
    out = decode_json_columns(row, 'groups')
    assert out['groups'] == [{'id': 'g1', 'color': '#fff', 'is_leader': True}]
    assert out['device_id'] == 'd1'


@pytest.mark.Trait("Bug", "B50")
def test_real_list_null_and_garbage():
    assert decode_json_columns({'groups': [1]}, 'groups')['groups'] == [1]
    assert decode_json_columns({'groups': None}, 'groups')['groups'] == []
    assert decode_json_columns({'groups': 'not json'}, 'groups')['groups'] == []
    assert decode_json_columns({'a': 1}, 'groups') == {'a': 1}


@pytest.mark.Trait("Bug", "B50")
def test_bytes_and_input_row_untouched():
    row = {'members': b'[]'}
    assert decode_json_columns(row, 'members')['members'] == []
    assert row == {'members': b'[]'}


@pytest.mark.Trait("Bug", "B50")
@pytest.mark.db
@pytest.mark.asyncio
async def test_live_positions_groups_is_a_list(migrated_conn):
    from backend.routers import locations
    c = migrated_conn
    uid = await c.fetchval("INSERT INTO users (full_name, role) VALUES ('Test Rescuer', 'rescuer') RETURNING id")
    gid = await c.fetchval("INSERT INTO groups (name, color) VALUES ('Alpha', '#112233') RETURNING id")
    await c.execute('INSERT INTO user_groups (user_id, group_id, is_leader) VALUES ($1, $2, TRUE)', uid, gid)
    dev = await c.fetchval('INSERT INTO devices (dev_sn, user_id) VALUES (50, $1) RETURNING id', uid)
    await c.execute(
        "INSERT INTO location_events (device_id, user_id, msg_id, mgrs, latitude, longitude, position, received_at, recorded_at) "
        "VALUES ($1, $2, 1, '35TLF1234512345', 42.5, 24.5, ST_SetSRID(ST_MakePoint(24.5, 42.5), 4326), NOW(), NOW())", dev, uid)
    rows = await locations.live_positions(c, {'id': uid})
    assert len(rows) == 1
    assert isinstance(rows[0]['groups'], list)
    assert rows[0]['groups'][0]['color'] == '#112233'
    assert rows[0]['groups'][0]['is_leader'] is True


G = '[{"id": "g1", "name": "A", "color": "#123456", "is_leader": true}]'


@pytest.mark.Trait("Bug", "B50")
def test_users_list_returns_groups_as_array(client, mock_conn):
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.fetch = AsyncMock(return_value=[{'id': 'u1', 'username': 'a', 'groups': G}])
    items = client.get('/api/users/').json()['items']
    assert items[0]['groups'][0]['color'] == '#123456'


@pytest.mark.Trait("Bug", "B50")
def test_groups_list_returns_members_as_array(client, mock_conn):
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.fetch = AsyncMock(return_value=[{'id': 'g1', 'name': 'A', 'members': '[{"id": "u1"}]'}])
    items = client.get('/api/groups/?include_members=true').json()['items']
    assert items[0]['members'] == [{'id': 'u1'}]


@pytest.mark.Trait("Bug", "B50")
def test_groups_list_without_members_untouched(client, mock_conn):
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.fetch = AsyncMock(return_value=[{'id': 'g1', 'name': 'A', 'member_count': 0}])
    assert client.get('/api/groups/').json()['items'][0]['member_count'] == 0


@pytest.mark.Trait("Bug", "B50")
def test_live_positions_returns_groups_as_array(client, mock_conn):
    mock_conn.fetch = AsyncMock(return_value=[{'device_id': 'd1', 'groups': G}])
    rows = client.get('/api/locations/live').json()
    assert rows[0]['groups'][0]['is_leader'] is True
