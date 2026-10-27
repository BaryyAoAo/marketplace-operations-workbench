"""Local MCP tools over the same SQLite state as the web workbench.

Run with ``python mcp_server.py`` and connect over stdio. No Meta access.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import app as core


mcp = FastMCP("Marketplace Workbench")


@mcp.tool()
def list_products() -> list[dict]:
    """List local products and their authoritative price, stock, and specifications."""
    with core.connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM products WHERE active=1 ORDER BY sku")]


@mcp.tool()
def get_product(product_id: int) -> dict:
    """Read one product before drafting; use its fields as the only source of claims."""
    with core.connect() as con:
        return dict(core.one(con, "products", product_id))


@mcp.tool()
def save_listing_draft(product_id: int, title: str, description: str) -> dict:
    """Save LLM-written English copy as an unapproved draft and return rule-review issues."""
    title, description = title.strip(), description.strip()
    if not title or not description or len(title) > 100 or len(description) > 1000:
        raise ValueError("Title or description is empty or exceeds its length limit")
    with core.connect() as con:
        product = core.one(con, "products", product_id)
        version = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM drafts WHERE product_id=?", (product_id,)).fetchone()[0]
        cur = con.execute("""INSERT INTO drafts
          (product_id,title,description,language,status,version,created_at,generation_source,model_id,review_notes)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (product_id, title, description, "en", "pending", version, core.now(), "agent_skill", None, "[]"))
        core.event(con, "agent_draft_created", "draft", cur.lastrowid, f"product={product_id}")
        return {"draft_id": cur.lastrowid, "status": "pending", "issues": core.draft_issues(product, title, description)}


@mcp.tool()
def review_listing_draft(draft_id: int) -> dict:
    """Review a draft against deterministic price, currency, length, and claim checks."""
    with core.connect() as con:
        draft = core.one(con, "drafts", draft_id)
        product = core.one(con, "products", draft["product_id"])
        return {"draft": dict(draft), "product_sku": product["sku"],
                "authoritative_price_minor": product["price_minor"], "currency": product["currency"],
                "issues": core.draft_issues(product, draft["title"], draft["description"])}


@mcp.tool()
def get_inquiry(inquiry_id: int) -> dict:
    """Read one manually entered inquiry and its linked product facts."""
    with core.connect() as con:
        inquiry = core.one(con, "inquiries", inquiry_id)
        task = core.one(con, "publish_tasks", inquiry["task_id"])
        draft = core.one(con, "drafts", task["draft_id"])
        product = core.one(con, "products", draft["product_id"])
        return {"inquiry_id": inquiry_id, "question": inquiry["question"],
                "reply_status": inquiry["reply_status"], "product": ai_facts(product)}


def ai_facts(product) -> dict:
    from ai import product_facts
    return product_facts(product)


@mcp.tool()
def save_reply_draft(inquiry_id: int, answer: str) -> dict:
    """Save LLM-written reply as an unapproved draft. Never sends a message."""
    answer = answer.strip()
    if not answer or len(answer) > 2000:
        raise ValueError("Reply is empty or exceeds 2000 characters")
    with core.connect() as con:
        inquiry = core.one(con, "inquiries", inquiry_id)
        if inquiry["reply_status"] == "sent_manually":
            raise ValueError("Already marked as sent; cannot replace reply")
        task = core.one(con, "publish_tasks", inquiry["task_id"])
        draft = core.one(con, "drafts", task["draft_id"])
        product = core.one(con, "products", draft["product_id"])
        issues = core.reply_issues(product, inquiry["question"], answer)
        if issues:
            return {"saved": False, "issues": issues}
        con.execute("""UPDATE inquiries SET reply_draft=?,reply_status='draft',reply_source='agent_skill',
          reply_model=NULL,ai_notes='[]',updated_at=? WHERE id=?""", (answer, core.now(), inquiry_id))
        core.event(con, "agent_reply_created", "inquiry", inquiry_id)
        return {"saved": True, "reply_status": "draft", "issues": []}


if __name__ == "__main__":
    mcp.run(transport="stdio")
