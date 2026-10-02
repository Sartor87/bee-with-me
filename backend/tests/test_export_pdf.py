"""PDF export must render user-supplied text as text, and must never fetch resources."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routers import export

HOSTILE = '<img src="file:///etc/passwd">'


def _row(**over):
    row = {
        'full_name': HOSTILE, 'rank': '<b>Sgt</b>', 'groups': 'A & B', 'mgrs': '35TLF<script>',
        'altitude_m': 100, 'speed_knots': 0.0, 'battery_voltage': 3.9, 'sos_active': False,
        'recorded_at': '2026-10-02T10:00:00+00:00',
    }
    row.update(over)
    return row


@pytest.mark.Trait("Task", "T2")
def test_report_html_escapes_every_interpolated_value():
    html = export.build_report_html([_row()], period='all → now')
    assert '<img' not in html
    assert '<script>' not in html
    assert '<b>Sgt' not in html
    assert '&lt;img src=&quot;file:///etc/passwd&quot;&gt;' in html
    assert 'A &amp; B' in html


@pytest.mark.Trait("Task", "T2")
def test_report_html_escapes_the_period():
    html = export.build_report_html([], period='<x>')
    assert '&lt;x&gt;' in html
    assert '<x>' not in html


@pytest.mark.Trait("Task", "T2")
def test_missing_values_render_as_dash_not_none():
    html = export.build_report_html([_row(full_name=None, rank=None, groups=None)], period='p')
    assert 'None' not in html
    assert html.count('<td>—</td>') >= 3


@pytest.mark.Trait("Task", "T2")
@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://169.254.169.254/', 'https://example.org/x.png'])
def test_url_fetcher_refuses_everything(url):
    with pytest.raises(ValueError):
        export.refuse_url_fetch(url)


@pytest.mark.Trait("Task", "T2")
def test_pdf_endpoint_passes_escaped_html_and_refusing_fetcher(client, mock_conn):
    mock_conn.fetch = AsyncMock(return_value=[_row()])
    fake = types.SimpleNamespace(HTML=MagicMock())
    fake.HTML.return_value.write_pdf.return_value = b'%PDF-1.7'
    with patch.dict(sys.modules, {'weasyprint': fake}):
        resp = client.get('/api/export/pdf')
    assert resp.status_code == 200
    assert resp.content == b'%PDF-1.7'
    kwargs = fake.HTML.call_args.kwargs
    assert kwargs['url_fetcher'] is export.refuse_url_fetch
    assert '&lt;img' in kwargs['string']
