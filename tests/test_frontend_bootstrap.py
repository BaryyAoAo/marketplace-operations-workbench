"""Catch missing static elements that abort the entire browser script at startup."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

import app as workbench


ROOT = Path(__file__).resolve().parents[1]


def test_eager_event_handlers_target_existing_html_elements():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "main.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    eager_handlers = set(re.findall(r"\$\('([^']+)'\)\.addEventListener", script))
    assert eager_handlers <= ids, f"Missing HTML IDs: {sorted(eager_handlers - ids)}"


def test_inbox_has_own_route_and_page_sections():
    response = TestClient(workbench.app).get("/inbox")
    assert response.status_code == 200
    html = response.text
    assert 'href="/inbox"' in html
    assert 'id="live-inbox" class="panel-section" data-page="inbox"' in html
    assert 'id="products" class="panel-section" data-page="listing"' in html
    assert 'id="live-account-list"' in html
