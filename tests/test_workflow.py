from pathlib import Path
import base64
import sqlite3
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

import app as workbench


def test_local_workflow_and_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "DATA", tmp_path)
    monkeypatch.setattr(workbench, "DB", tmp_path / "workbench.db")
    workbench.init_db()
    client = TestClient(workbench.app)

    sample = Path(workbench.ROOT / "sample_products_us.csv").read_text(encoding="utf-8")
    csv_text = sample + "BROKEN,Wrong ring,ring,new,0,USD,-1,alloy,US 7,red,https://example.com/x.jpg,synthetic\n"
    imported = client.post("/api/products/import", json={"csv_text": csv_text})
    assert imported.status_code == 200
    assert imported.json()["imported"] == 3
    assert len(imported.json()["errors"]) == 1
    assert imported.json()["errors"][0]["line"] == 5

    product = next(p for p in client.get("/api/state").json()["products"] if p["sku"] == "US-RNG-001")
    assert product["currency"] == "USD"
    draft_id = client.post(f"/api/products/{product['id']}/draft").json()["id"]
    assert client.post(f"/api/drafts/{draft_id}/queue", json={"account_alias": "test-01"}).status_code == 409

    edited = client.put(f"/api/drafts/{draft_id}", json={"title": "Authentic diamond ring", "description": "Great quality"})
    assert "没有商品字段依据" in str(edited.json()["issues"])
    assert client.post(f"/api/drafts/{draft_id}/approve").status_code == 422

    fixed = client.put(f"/api/drafts/{draft_id}", json={"title": "Minimalist band ring", "description": "Material: stainless steel. Size: US 7. Color: silver."})
    assert fixed.json()["issues"] == []
    assert client.post(f"/api/drafts/{draft_id}/approve").json()["status"] == "approved"
    task_id = client.post(f"/api/drafts/{draft_id}/queue", json={"account_alias": "test-01"}).json()["id"]
    assert client.post(f"/api/tasks/{task_id}/result", json={"status": "posted", "marketplace_url": "https://example.com"}).status_code == 422
    assert client.post(f"/api/tasks/{task_id}/result", json={"status": "posted", "marketplace_url": "https://www.facebook.com/marketplace/item/123456"}).status_code == 200
    share_task_id = client.post(f"/api/drafts/{draft_id}/queue", json={"account_alias": "test-02"}).json()["id"]
    share_result = client.post(f"/api/tasks/{share_task_id}/result", json={"status": "posted", "marketplace_url": "https://www.facebook.com/share/1F4HqBedSA/"})
    assert share_result.status_code == 200
    assert not workbench.accepted_listing_url("https://facebook.com.evil.example/share/1F4HqBedSA/")
    assert not workbench.accepted_listing_url("https://www.facebook.com/posts/123456")

    inquiry_id = client.post("/api/inquiries", json={"task_id": task_id, "buyer_alias": "buyer-01", "question": "How much is it?"}).json()["id"]
    inquiry = client.get("/api/state").json()["inquiries"][0]
    assert "68.00 USD" in inquiry["reply_draft"]
    assert client.put(f"/api/inquiries/{inquiry_id}/reply", json={"reply_draft": inquiry["reply_draft"], "reply_status": "sent_manually"}).status_code == 409
    assert client.put(f"/api/inquiries/{inquiry_id}/reply", json={"reply_draft": inquiry["reply_draft"], "reply_status": "approved"}).status_code == 200
    assert client.put(f"/api/inquiries/{inquiry_id}/reply", json={"reply_draft": "Changed without approval", "reply_status": "sent_manually"}).status_code == 409
    assert client.put(f"/api/inquiries/{inquiry_id}/reply", json={"reply_draft": inquiry["reply_draft"], "reply_status": "sent_manually"}).status_code == 200
    assert client.put(f"/api/inquiries/{inquiry_id}/followup", json={"stage": "negotiating", "channel": "whatsapp_manual", "next_followup_at": "2026-10-27T09:00"}).status_code == 200
    exported = client.get("/api/export").json()
    assert len(exported["audit_events"]) >= 8


def test_excel_import(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "DATA", tmp_path)
    monkeypatch.setattr(workbench, "DB", tmp_path / "workbench.db")
    workbench.init_db()
    book = Workbook()
    sheet = book.active
    sheet.append(workbench.FIELDS)
    sheet.append(["US-RNG-100", "Test ring", "ring", "new", 45.5, "USD", 3, "alloy", "US 7", "silver", "https://example.com/ring.jpg", "Synthetic sample"])
    data = BytesIO()
    book.save(data)
    response = TestClient(workbench.app).post("/api/products/import-xlsx", json={"base64_data": base64.b64encode(data.getvalue()).decode()})
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "errors": []}


def test_clear_current_catalog_retains_history_and_allows_sku_reimport(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "DATA", tmp_path)
    monkeypatch.setattr(workbench, "DB", tmp_path / "workbench.db")
    workbench.init_db()
    client = TestClient(workbench.app)
    sample = Path(workbench.ROOT / "sample_products_us.csv").read_text(encoding="utf-8")
    assert client.post("/api/products/import", json={"csv_text": sample}).json()["imported"] == 3
    repeated = client.post("/api/products/import", json={"csv_text": sample}).json()
    assert repeated["imported"] == 0
    assert len(repeated["errors"]) == 3
    original = next(p for p in client.get("/api/state").json()["products"] if p["sku"] == "US-RNG-001")
    with workbench.connect() as con:
        draft_id = con.execute("""INSERT INTO drafts(product_id,title,description,language,status,version,created_at)
            VALUES(?,?,?,?,?,?,?)""", (original["id"], "Old title", "Old description", "en", "approved", 1, "now")).lastrowid
        task_id = con.execute("""INSERT INTO publish_tasks(draft_id,account_alias,status,created_at,updated_at)
            VALUES(?,?,?,?,?)""", (draft_id, "test-01", "queued", "now", "now")).lastrowid
    assert client.post("/api/products/clear", json={"confirm": True}).status_code == 409
    with workbench.connect() as con:
        con.execute("UPDATE publish_tasks SET status='posted' WHERE id=?", (task_id,))
    assert client.post("/api/products/clear", json={"confirm": True}).json() == {"cleared": 3}
    cleared = client.get("/api/state").json()
    assert cleared["products"] == []
    assert len(cleared["archived_products"]) == 3
    assert len(cleared["publish_tasks"]) == 1
    assert client.post("/api/products/import", json={"csv_text": sample}).json()["imported"] == 3
    current = client.get("/api/state").json()
    replacement = next(p for p in current["products"] if p["sku"] == original["sku"])
    assert replacement["id"] != original["id"]
    assert current["drafts"][0]["product_id"] == original["id"]
    assert len(current["publish_tasks"]) == 1


def test_old_unique_sku_database_migrates_without_breaking_draft_links(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "DATA", tmp_path)
    monkeypatch.setattr(workbench, "DB", tmp_path / "workbench.db")
    with sqlite3.connect(workbench.DB) as con:
        con.execute("""CREATE TABLE products (
          id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          category TEXT NOT NULL, condition TEXT NOT NULL, price_minor INTEGER NOT NULL,
          currency TEXT NOT NULL, stock INTEGER NOT NULL, material TEXT NOT NULL,
          size TEXT NOT NULL, color TEXT NOT NULL, image_url TEXT NOT NULL,
          source_note TEXT NOT NULL, created_at TEXT NOT NULL)""")
        con.execute("""INSERT INTO products(sku,name,category,condition,price_minor,currency,stock,
          material,size,color,image_url,source_note,created_at) VALUES
          ('OLD-001','Old ring','ring','new',1200,'USD',1,'steel','7','silver','','source','now')""")
        con.execute("""CREATE TABLE drafts (
          id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
          title TEXT NOT NULL, description TEXT NOT NULL, language TEXT NOT NULL,
          status TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
          approved_at TEXT)""")
        con.execute("""INSERT INTO drafts(product_id,title,description,language,status,version,created_at)
          VALUES(1,'Old title','Old description','en','approved',1,'now')""")
    workbench.init_db()
    with workbench.connect() as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT product_id FROM drafts").fetchone()[0] == 1
        assert con.execute("SELECT active FROM products WHERE id=1").fetchone()[0] == 1
    workbench.init_db()
