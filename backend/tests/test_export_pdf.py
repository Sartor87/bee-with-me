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
    modules = _fake_weasyprint([])
    fake = modules['weasyprint']
    fake.HTML = MagicMock()
    fake.HTML.return_value.write_pdf.return_value = b'%PDF-1.7'
    with patch.dict(sys.modules, modules):
        resp = client.get('/api/export/pdf')
    assert resp.status_code == 200
    assert resp.content == b'%PDF-1.7'
    kwargs = fake.HTML.call_args.kwargs
    # B7: a URLFetcher subclass (WeasyPrint 70 contract) that refuses everything, not a bare function
    fetcher = kwargs['url_fetcher']
    assert isinstance(fetcher, _FakeURLFetcher)
    for url in ('file:///etc/passwd', 'http://169.254.169.254/', 'https://example.org/x.png'):
        with pytest.raises(ValueError):
            fetcher(url)
    assert '&lt;img' in kwargs['string']


# ── B7: the refusing fetcher must satisfy WeasyPrint 70's url_fetcher contract ──

class _FakeURLFetcher:
    """Mirror of weasyprint.urls.URLFetcher (70.x): the attributes weasyprint.urls.fetch() reads."""

    def __init__(self, timeout=10, ssl_context=None, http_headers=None,
                 allowed_protocols=None, allow_redirects=True, fail_on_errors=False, **kwargs):
        self._allowed_protocols = allowed_protocols
        self._fail_on_errors = fail_on_errors

    def fetch(self, url, headers=None):
        raise AssertionError('the real fetch must never run')

    def __call__(self, url):
        return self.fetch(url)


def _fake_weasyprint(fetched):
    """Fake weasyprint whose write_pdf() fetches like weasyprint.urls.fetch() in 70.x."""
    fake_urls = types.ModuleType('weasyprint.urls')
    fake_urls.URLFetcher = _FakeURLFetcher

    class _HTML:
        def __init__(self, string=None, url_fetcher=None, **kwargs):
            self.string = string
            self.url_fetcher = url_fetcher

        def write_pdf(self):
            for url in ('file:///etc/passwd', 'http://169.254.169.254/'):
                try:
                    self.url_fetcher(url)
                except Exception as exc:          # weasyprint.urls.fetch()
                    if self.url_fetcher._fail_on_errors:
                        raise
                    fetched.append((url, type(exc).__name__))
                else:
                    fetched.append((url, 'FETCHED'))
            return b'%PDF-1.7'

    fake = types.ModuleType('weasyprint')
    fake.HTML = _HTML
    fake.urls = fake_urls
    return {'weasyprint': fake, 'weasyprint.urls': fake_urls}


@pytest.mark.Trait("Bug", "B7")
def test_pdf_endpoint_fetcher_follows_weasyprint70_contract(client, mock_conn):
    mock_conn.fetch = AsyncMock(return_value=[_row()])
    fetched = []
    with patch.dict(sys.modules, _fake_weasyprint(fetched)):
        resp = client.get('/api/export/pdf')
    assert resp.status_code == 200
    assert resp.content == b'%PDF-1.7'
    assert fetched == [('file:///etc/passwd', 'ValueError'), ('http://169.254.169.254/', 'ValueError')]


@pytest.mark.Trait("Bug", "B7")
def test_refusing_fetcher_is_a_urlfetcher_that_refuses_everything():
    with patch.dict(sys.modules, _fake_weasyprint([])):
        fetcher = export.make_refusing_url_fetcher()
        assert isinstance(fetcher, _FakeURLFetcher)
    assert fetcher._fail_on_errors is False
    assert fetcher._allowed_protocols == ()
    for url in ('file:///etc/passwd', 'data:text/plain,x', 'https://example.org/x.png'):
        with pytest.raises(ValueError):
            fetcher(url)
        with pytest.raises(ValueError):
            fetcher.fetch(url, headers={})


@pytest.mark.Trait("Bug", "B7")
def test_requirements_pin_weasyprint_70():
    from pathlib import Path
    req = (Path(export.__file__).resolve().parents[1] / 'requirements.txt').read_text(encoding='utf-8')
    assert 'weasyprint>=70,<71' in req.splitlines()


@pytest.mark.Trait("Bug", "B7")
def test_real_weasyprint_renders_and_refuses_hostile_image(monkeypatch):
    try:
        import weasyprint
    except (ImportError, OSError) as exc:   # OSError: native GTK/Pango libraries missing
        pytest.skip(f'WeasyPrint cannot load its native libraries: {exc}')
    calls = []
    original = export.refuse_url_fetch

    def spy(url, *args, **kwargs):
        calls.append(url)
        return original(url, *args, **kwargs)
    monkeypatch.setattr(export, 'refuse_url_fetch', spy)
    html = '<html><body><p>x</p><img src="file:///etc/passwd"></body></html>'
    pdf = weasyprint.HTML(string=html, url_fetcher=export.make_refusing_url_fetcher()).write_pdf()
    assert pdf.startswith(b'%PDF')
    assert 'file:///etc/passwd' in calls
