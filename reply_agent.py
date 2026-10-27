"""Bounded, read-only planning and data lookup for Marketplace reply drafts."""

from __future__ import annotations

import re
import sqlite3

import ai
import ai_knowledge


class ProductMatchRequired(ValueError):
    pass


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _match_score(product: sqlite3.Row, title: str, query: str) -> int:
    title, query = _normalized(title), _normalized(query)
    labels = [_normalized(product["sku"]), _normalized(product["name"]),
              _normalized(product["listing_title"])]
    scores = []
    for label in labels:
        if not label or len(label) < 4:
            continue
        if query == label:
            scores.append(100)
        if f" {label} " in f" {title} ":
            scores.append(80 + min(len(label), 19))
        if len(query) >= 6 and f" {label} " in f" {query} ":
            scores.append(75 + min(len(label), 19))
    return max(scores, default=0)


def resolve_product(con: sqlite3.Connection, title: str, query: str,
                    selected_product_id: int | None) -> sqlite3.Row:
    if selected_product_id is not None:
        row = con.execute("SELECT * FROM products WHERE id=? AND active=1", (selected_product_id,)).fetchone()
        if row is None:
            raise ProductMatchRequired("所选商品已不在当前商品表，请重新选择")
        return row
    products = con.execute("SELECT * FROM products WHERE active=1").fetchall()
    ranked = sorted(((_match_score(row, title, query), row) for row in products),
                    key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 75 or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        raise ProductMatchRequired("无法从会话唯一识别商品，请在回复框下方选择对应商品后重试")
    return ranked[0][1]


def plan_and_read(con: sqlite3.Connection, conversation: sqlite3.Row,
                  history: list[dict], selected_product_id: int | None) -> dict:
    question = history[-1]["text"]
    recent = [{"direction": item["direction"], "text": item["text"]} for item in history[-3:]]
    plan, model_id = ai.plan_reply(question, conversation["title"], recent)
    product = resolve_product(con, conversation["title"], plan.product_query,
                              selected_product_id)
    detected = ai_knowledge.select(question)
    needs_product_facts = (plan.use_product or bool(selected_product_id) or
                           any(item in detected["intents"] for item in ("availability", "price", "size")))
    knowledge = ai_knowledge.select(question, extra_intents=plan.policy_topics)
    return {
        "plan": plan, "model_id": model_id, "product": product, "question": question,
        "conversation_history": history if plan.use_history else [],
        "knowledge": knowledge, "use_product_facts": needs_product_facts,
        "tool_trace": [
            f"识别商品 {product['sku']}",
            *( ["读取商品资料"] if needs_product_facts else [] ),
            *( [f"读取历史 {len(history)} 条"] if plan.use_history else [] ),
            *[f"读取{ai_knowledge.POLICY_FIELDS[key]}" for key in knowledge["policies"]],
        ],
    }
