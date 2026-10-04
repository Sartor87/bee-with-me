"""B49: unencodable input gets a JSON-safe 422 (not a 500); the text validators sit on the real fields."""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.auth import get_current_user
from backend.database import get_conn
from backend.fire.parse import is_storable
from backend.main import app
from backend.routers import fire as fire_router

pytestmark = pytest.mark.Trait("Bug", "B49")

SURROGATE = r'{"notes": "\ud800"}'
HEADERS = {'Content-Type': 'application/json'}


@pytest.fixture()
def real_app_client(mock_conn, admin_user):
    """The real `backend.main.app` (global handlers included), without lifespan, DB or auth."""
    async def _get_conn():
        yield mock_conn

    async def _user():
        return admin_user

    app.dependency_overrides[get_conn] = _get_conn
    app.dependency_overrides[get_current_user] = _user
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _assert_safe_422(resp):
    assert resp.status_code == 422
    assert isinstance(resp.json()['detail'], list) and resp.json()['detail']
    resp.content.decode('ascii')   # the body must be plain JSON, no raw surrogate and no echo problem


@pytest.mark.parametrize('path', [
    f'/api/fire/hotspots/{uuid.uuid4()}/dismiss',
    '/api/fire/field-reports',
])
def test_surrogate_in_fire_notes_is_422(real_app_client, path):
    _assert_safe_422(real_app_client.post(path, content=SURROGATE, headers=HEADERS))


def test_surrogate_in_zone_label_is_422(real_app_client):
    body = r'{"label": "\ud800", "latitude": 42.5, "longitude": 24.5}'
    _assert_safe_422(real_app_client.post('/api/fire/suppression-zones', content=body, headers=HEADERS))


def test_surrogate_in_a_typed_field_is_422(real_app_client):
    """Any validator error echoes its input: a UUID field with a lone surrogate is the generic, validator-free case."""
    body = r'{"device_id": "\ud800"}'
    _assert_safe_422(real_app_client.post('/api/fire/field-reports', content=body, headers=HEADERS))


def test_ordinary_validation_error_keeps_shape(real_app_client):
    resp = real_app_client.post('/api/fire/suppression-zones', json={'label': 'x'})
    assert resp.status_code == 422
    err = resp.json()['detail'][0]
    assert {'type', 'loc', 'msg'} <= set(err)


@pytest.mark.parametrize('field', ['notes', 'label'])
def test_nul_still_rejected(real_app_client, field):
    body = {'label': 'ok', 'latitude': 42.5, 'longitude': 24.5, 'notes': 'ok'}
    body[field] = 'a\x00b'
    resp = real_app_client.post('/api/fire/suppression-zones', json=body)
    assert resp.status_code == 422
    assert resp.json()['detail'][0]['loc'][-1] == field


def test_is_storable_is_public():
    assert is_storable('ok') and not is_storable('a\x00') and not is_storable('\ud800')


@pytest.mark.parametrize('model', [fire_router.DismissIn, fire_router.FieldReportIn, fire_router.ZoneIn])
def test_every_guarded_field_has_a_validator(model):
    """Fails when a guarded field is renamed or the validator stops being attached to it."""
    for field in ('notes', 'label'):
        if field in model.model_fields:
            with pytest.raises(ValueError):
                model.model_validate({'label': 'x', 'latitude': 1, 'longitude': 1, field: 'a\x00b'})
