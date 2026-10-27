"""Operator-maintained, global customer-service knowledge for AI reply drafts."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


KNOWLEDGE_PATH = Path(__file__).resolve().parent / "data" / "ai_reply_knowledge.json"
POLICY_FIELDS = {
    "shipping": "配送规则",
    "pickup": "面交规则",
    "payment": "付款方式",
    "returns": "退换规则",
    "negotiation": "议价规则",
}
INTENTS = {
    "general": "通用", "availability": "库存/是否有货", "price": "价格",
    "size": "尺寸/规格", "shipping": "配送", "pickup": "面交",
    "payment": "付款", "returns": "退换", "negotiation": "议价",
}
PATTERNS = {
    "availability": r"\b(?:available|availability|in stock|still have|sold)\b|有货|还有吗",
    "price": r"\b(?:price|cost|how much)\b|多少钱|价格",
    "size": r"\b(?:size|fit|measure|length)\b|尺寸|尺码|长度",
    "shipping": r"\b(?:ship|shipping|deliver|delivery|postage|mail|env[ií]o)\b|配送|邮寄|快递",
    "pickup": r"\b(?:pick\s?up|meet\s?up|meet|collect|collection)\b|面交|自提",
    "payment": r"\b(?:pay|payment|cash|transfer)\b|付款|支付|现金",
    "returns": r"\b(?:return|refund|exchange)\b|退货|退款|换货",
    "negotiation": r"\b(?:discount|lower|best price|offer|negotiable)\b|便宜|折扣|议价",
}
POLICIES_FOR_INTENT = {
    "shipping": ("shipping",), "pickup": ("pickup",),
    "payment": ("payment",), "returns": ("returns",),
    "negotiation": ("negotiation",),
}

DEFAULT_POLICIES = {
    "shipping": "可根据买家所在地区评估配送或邮寄。工作人员确认目的地、运费和预计时间后再告知买家；未确认前不承诺包邮或具体到货日期。",
    "pickup": "支持与买家协商面交。具体地点和时间由工作人员与买家确认后约定；不在公开回复中提供私人住址。",
    "payment": "交易前由工作人员与买家确认付款方式和付款时点。不在聊天中索取银行卡信息、密码或验证码，也不要求未经确认的提前转账。",
    "returns": "退换申请由工作人员根据商品情况和交易约定处理。未核实适用条件前，不承诺无条件退货、退款或换货。",
    "negotiation": "商品表价格是当前标价。可以记录买家报价并交由工作人员确认；未经确认，不承诺折扣、降价或最低价。",
}
DEFAULT_REPLIES = [
    {"intent": "availability", "title": "是否有货", "content": "The {product_name} is currently {availability}."},
    {"intent": "price", "title": "商品价格", "content": "The listed price for the {product_name} is {price}."},
    {"intent": "size", "title": "商品尺寸", "content": "The listed size for the {product_name} is {size}."},
    {"intent": "shipping", "title": "配送地区与费用", "content": "I can check shipping to your ZIP code. Please send the ZIP code, and I can confirm the shipping cost and estimated arrival time."},
    {"intent": "pickup", "title": "预约面交", "content": "In-person pickup can be arranged. What area and time work for you? I can confirm the meeting point."},
    {"intent": "payment", "title": "付款方式", "content": "We can confirm the payment method once we agree on pickup or delivery. Which option works for you?"},
    {"intent": "returns", "title": "退换咨询", "content": "Returns are reviewed case by case under the sale terms. I can explain what applies to this item before you decide."},
    {"intent": "negotiation", "title": "买家还价", "content": "The listed price is {price}. What offer did you have in mind? I can review it."},
]


def empty() -> dict:
    return {"version": 0, "updated_at": None,
            "policies": DEFAULT_POLICIES.copy(),
            "quick_replies": [item.copy() for item in DEFAULT_REPLIES]}


def load() -> dict:
    if not KNOWLEDGE_PATH.exists():
        return empty()
    raw = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    return {
        "version": int(raw.get("version", 0)), "updated_at": raw.get("updated_at"),
        "policies": {key: str(raw.get("policies", {}).get(key, "")) for key in POLICY_FIELDS},
        "quick_replies": raw.get("quick_replies", []),
    }


def save(policies: dict, quick_replies: list[dict]) -> dict:
    unknown = set(policies) - set(POLICY_FIELDS)
    if unknown:
        raise ValueError("经营规则包含未知字段")
    cleaned_policies = {}
    for key in POLICY_FIELDS:
        value = str(policies.get(key, "")).strip()
        if len(value) > 1200:
            raise ValueError(f"{POLICY_FIELDS[key]}不能超过 1200 字符")
        cleaned_policies[key] = value
    if len(quick_replies) > 20:
        raise ValueError("常见话术最多保存 20 条")
    cleaned_replies = []
    for item in quick_replies:
        intent, title, content = item.get("intent"), str(item.get("title", "")).strip(), str(item.get("content", "")).strip()
        if intent not in INTENTS or not title or not content:
            raise ValueError("每条话术都需填写类型、标题和内容")
        if len(title) > 80 or len(content) > 1200:
            raise ValueError("话术标题最多 80 字符，内容最多 1200 字符")
        cleaned_replies.append({"intent": intent, "title": title, "content": content})
    current = load()
    value = {"version": current["version"] + 1,
             "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "policies": cleaned_policies, "quick_replies": cleaned_replies}
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = KNOWLEDGE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, KNOWLEDGE_PATH)
    return value


def select(question: str, knowledge: dict | None = None, *, extra_intents: list[str] | None = None) -> dict:
    knowledge = load() if knowledge is None else knowledge
    detected = [name for name, pattern in PATTERNS.items() if re.search(pattern, question, re.I)]
    intents = list(dict.fromkeys([*detected, *(item for item in (extra_intents or []) if item in INTENTS)]))
    policy_keys = list(dict.fromkeys(key for intent in intents for key in POLICIES_FOR_INTENT.get(intent, ())))
    policies = {key: knowledge["policies"][key] for key in policy_keys
                if knowledge["policies"].get(key)}
    missing = [POLICY_FIELDS[key] + "未配置" for key in policy_keys if key not in policies]
    quick_replies = [item for item in knowledge["quick_replies"]
                     if item["intent"] == "general" or item["intent"] in intents]
    return {"version": knowledge["version"], "intents": intents,
            "policies": policies, "missing_facts": missing, "quick_replies": quick_replies}
