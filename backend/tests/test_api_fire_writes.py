"""Write endpoints with repository, notify and alarm service stubbed."""

import json
import uuid
from datetime import datetime, timezone

import pytest

from backend.auth import get_current_user
from backend.fire import repository
from backend.routers import fire as fire_router

pytestmark = pytest.mark.Trait("Task", "T18")

T = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
HOSTILE = '<img src=x onerror=alert(1)>'


def _hotspot(**over):
    row = {'id': uuid.uuid4(), 'source': 'field_report', 'effis_id': None, 'acquired_at': T, 'latitude': 42.5,
           'longitude': 24.5, 'effis_class': None, 'dismissed_at': None, 'dismiss_notes': None,
           'extinguished_at': None, 'notes': None, 'reported_device_id': None, 'suppressed_by_zone_id': None}
    row.update(over)
    return row


def _zone(**over):
    row = {'id': uuid.uuid4(), 'label': 'Solar park', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 1000,
           'is_active': True, 'disabled_at': None, 'notes': None, 'created_at': T, 'updated_at': T}
    row.update(over)
    return row


@pytest.fixture()
def calls(monkeypatch):
    seen = {'evaluate': 0, 'data_changed': 0}

    class _Alarm:
        def request_evaluation(self):
            seen['evaluate'] += 1

    async def _changed(conn):
        seen['data_changed'] += 1
    monkeypatch.setattr(fire_router, 'fire_alarm', _Alarm())
    monkeypatch.setattr(fire_router, 'notify_data_changed', _changed)
    return seen


def _stub(monkeypatch, name, result):
    captured = {}

    async def fake(*args):
        captured['args'] = args
        return result
    monkeypatch.setattr(repository, name, fake)
    return captured


def test_dismiss_stores_notes_and_triggers_evaluation(client, monkeypatch, calls, admin_user):
    captured = _stub(monkeypatch, 'dismiss_hotspot', _hotspot(source='viirs', dismissed_at=T, dismiss_notes='solar'))
    hid = uuid.uuid4()
    resp = client.post(f'/api/fire/hotspots/{hid}/dismiss', json={'notes': 'solar'})
    assert resp.status_code == 200 and resp.json()['properties']['state'] == 'dismissed'
    assert captured['args'][1:] == (str(hid), admin_user['id'], 'solar')
    assert calls == {'evaluate': 1, 'data_changed': 1}


def test_dismiss_unknown_is_404_and_requires_admin(client, monkeypatch, calls, viewer_user, test_app):
    _stub(monkeypatch, 'dismiss_hotspot', None)
    assert client.post(f'/api/fire/hotspots/{uuid.uuid4()}/dismiss', json={}).status_code == 404
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user
    assert client.post(f'/api/fire/hotspots/{uuid.uuid4()}/dismiss', json={}).status_code == 403
    assert calls['evaluate'] == 0


def test_dismiss_notes_are_length_limited(client, calls):
    assert client.post(f'/api/fire/hotspots/{uuid.uuid4()}/dismiss', json={'notes': 'x' * 1001}).status_code == 422


def test_field_report_at_coordinates_keeps_notes_as_given(client, monkeypatch, calls, viewer_user, test_app):
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user     # any logged-in user may report
    captured = _stub(monkeypatch, 'insert_field_report', _hotspot(notes=HOSTILE))
    resp = client.post('/api/fire/field-reports', json={'latitude': 42.5, 'longitude': 24.5, 'notes': HOSTILE})
    assert resp.status_code == 201
    assert resp.json()['properties']['notes'] == HOSTILE       # stored as text; the UI renders it as text
    assert captured['args'][1:3] == (42.5, 24.5)
    assert calls == {'evaluate': 1, 'data_changed': 1}


def test_field_report_at_device_uses_server_side_position(client, monkeypatch, calls):
    dev = uuid.uuid4()
    _stub(monkeypatch, 'latest_device_position', {'latitude': 43.1, 'longitude': 25.2})
    captured = _stub(monkeypatch, 'insert_field_report', _hotspot(reported_device_id=dev))
    assert client.post('/api/fire/field-reports', json={'device_id': str(dev)}).status_code == 201
    assert captured['args'][1:3] == (43.1, 25.2)
    assert captured['args'][4] == str(dev)


def test_field_report_device_without_recent_position_is_409(client, monkeypatch, calls):
    _stub(monkeypatch, 'latest_device_position', None)
    resp = client.post('/api/fire/field-reports', json={'device_id': str(uuid.uuid4())})
    assert resp.status_code == 409 and resp.json() == {'detail': 'no_recent_position'}


@pytest.mark.parametrize('body', [
    {}, {'latitude': 42.5}, {'device_id': str(uuid.uuid4()), 'latitude': 42.5, 'longitude': 24.5},
    {'latitude': 91, 'longitude': 24.5}, {'latitude': 42.5, 'longitude': 24.5, 'notes': 'x' * 1001},
])
def test_field_report_validation(client, calls, body):
    assert client.post('/api/fire/field-reports', json=body).status_code == 422


def test_extinguish_only_field_reports(client, monkeypatch, calls):
    _stub(monkeypatch, 'extinguish_field_report', _hotspot(extinguished_at=T))
    assert client.post(f'/api/fire/field-reports/{uuid.uuid4()}/extinguish').json()['properties']['state'] == 'extinguished'
    _stub(monkeypatch, 'extinguish_field_report', None)
    assert client.post(f'/api/fire/field-reports/{uuid.uuid4()}/extinguish').status_code == 404


def test_zone_create_list_update_disable(client, monkeypatch, calls):
    _stub(monkeypatch, 'create_zone', _zone())
    resp = client.post('/api/fire/suppression-zones', json={'label': 'Solar park', 'latitude': 42.5, 'longitude': 24.5})
    assert resp.status_code == 201 and resp.json()['radius_m'] == 1000
    listed = _stub(monkeypatch, 'list_zones', [_zone(), _zone(is_active=False, disabled_at=T)])
    assert len(client.get('/api/fire/suppression-zones?include_disabled=true').json()) == 2
    assert listed['args'][1] is True
    _stub(monkeypatch, 'update_zone', _zone(radius_m=800))
    assert client.put(f'/api/fire/suppression-zones/{uuid.uuid4()}',
                      json={'label': 'Solar park', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 800,
                            'expected_updated_at': T.isoformat()}).json()['radius_m'] == 800
    _stub(monkeypatch, 'disable_zone', _zone(is_active=False, disabled_at=T))
    assert client.delete(f'/api/fire/suppression-zones/{uuid.uuid4()}').json()['is_active'] is False
    assert calls['evaluate'] == 3


@pytest.mark.parametrize('body', [
    {'label': '', 'latitude': 42.5, 'longitude': 24.5},
    {'label': 'x', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 49},
    {'label': 'x', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 20001},
    {'label': 'x' * 256, 'latitude': 42.5, 'longitude': 24.5},
])
def test_zone_validation(client, calls, body):
    assert client.post('/api/fire/suppression-zones', json=body).status_code == 422


def test_zone_writes_require_admin(client, calls, viewer_user, test_app):
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user
    assert client.post('/api/fire/suppression-zones', json={'label': 'x', 'latitude': 42.5, 'longitude': 24.5}).status_code == 403
    assert client.delete(f'/api/fire/suppression-zones/{uuid.uuid4()}').status_code == 403


# -- additions: unstorable operator text is refused, never stored (B25/B27/B31 applied to user input) -------------

def test_text_guard_refuses_lone_surrogates():
    # pydantic already rejects a lone surrogate in a JSON body; the guard backs that up for any other path
    with pytest.raises(ValueError):
        fire_router._text_ok('lone' + chr(0xD800) + 'x', 1000)
    assert fire_router._text_ok('Слънчев парк', 255) == 'Слънчев парк'


@pytest.mark.parametrize('text', ['bad\x00nul'])
def test_notes_and_label_reject_unstorable_text(client, calls, text):
    def post(path, body):
        return client.post(path, content=json.dumps(body), headers={'Content-Type': 'application/json'})

    assert post(f'/api/fire/hotspots/{uuid.uuid4()}/dismiss', {'notes': text}).status_code == 422
    assert post('/api/fire/field-reports', {'latitude': 42.5, 'longitude': 24.5, 'notes': text}).status_code == 422
    assert post('/api/fire/suppression-zones', {'label': text, 'latitude': 42.5, 'longitude': 24.5}).status_code == 422
    assert post('/api/fire/suppression-zones',
                {'label': 'x', 'latitude': 42.5, 'longitude': 24.5, 'notes': text}).status_code == 422
    assert calls == {'evaluate': 0, 'data_changed': 0}


def test_extinguish_triggers_evaluation_and_refetch(client, monkeypatch, calls):
    _stub(monkeypatch, 'extinguish_field_report', _hotspot(extinguished_at=T))
    assert client.post(f'/api/fire/field-reports/{uuid.uuid4()}/extinguish').status_code == 200
    assert calls == {'evaluate': 1, 'data_changed': 1}


def test_unknown_zone_is_404_on_update_and_delete(client, monkeypatch, calls):
    _stub(monkeypatch, 'update_zone', None)
    _stub(monkeypatch, 'disable_zone', None)
    body = {'label': 'x', 'latitude': 42.5, 'longitude': 24.5, 'expected_updated_at': T.isoformat()}
    assert client.put(f'/api/fire/suppression-zones/{uuid.uuid4()}', json=body).status_code == 404
    assert client.delete(f'/api/fire/suppression-zones/{uuid.uuid4()}').status_code == 404
    assert calls['evaluate'] == 0
