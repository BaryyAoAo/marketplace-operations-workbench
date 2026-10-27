import json
from pathlib import Path

from fastapi.testclient import TestClient

import ai
import ai_knowledge
import ai_settings
import app
import mcp_server


class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_parsed": self.result})()


class FakeClient:
    def __init__(self, result):
        self.responses = FakeResponses(result)


class FakeChatCompletions:
    def __init__(self, content, finish_reason="stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": self.content})()
        choice = type("Choice", (), {"message": message, "finish_reason": self.finish_reason})()
        return type("Response", (), {"choices": [choice]})()


class FakeChatClient:
    def __init__(self, content, finish_reason="stop"):
        completions = FakeChatCompletions(content, finish_reason)
        self.chat = type("Chat", (), {"completions": completions})()


def test_llm_adapter_uses_structured_facts_without_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    product = {"sku":"RNG-001","name":"Minimalist band ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"QAR","stock":12,"material":"stainless steel","size":"US 7","color":"silver"}
    fake = FakeClient(ai.ListingCopy(title="Minimalist band ring", description="Stainless steel ring, US 7, silver.", review_notes=[]))
    copy, model = ai.listing_copy(product, client=fake, model="test-model")
    assert copy.title == "Minimalist band ring"
    assert model == "test-model"
    assert fake.responses.calls[0]["text_format"] is ai.ListingCopy
    assert fake.responses.calls[0]["store"] is False
    assert "68.00 QAR" in fake.responses.calls[0]["input"][1]["content"]


def test_siliconflow_chat_json_adapter_without_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("SILICONFLOW_MODEL", "example/model")
    assert ai.public_status()["configured"] is False
    assert ai.public_status()["model"] == "example/model"
    product = {"sku":"RNG-001","name":"Minimalist band ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"QAR","stock":12,"material":"stainless steel","size":"US 7","color":"silver"}
    fake = FakeChatClient('{"title":"Minimalist band ring","description":"Stainless steel, US 7, silver.","review_notes":[]}')
    copy, model = ai.listing_copy(product, client=fake)
    assert copy.title == "Minimalist band ring" and model == "example/model"
    call = fake.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "68.00 QAR" in call["messages"][1]["content"]
    assert "JSON" in call["messages"][0]["content"]

    bad = FakeChatClient('{"title":"incomplete"}', finish_reason="length")
    try:
        ai.listing_copy(product, client=bad)
    except ai.AIUnavailable:
        pass
    else:
        raise AssertionError("Truncated model output must be rejected")


def test_siliconflow_client_uses_official_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-only-key")
    monkeypatch.setenv("SILICONFLOW_MODEL", "example/model")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    captured = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return FakeChatClient('{"intent":"price","answer":"The listed price is 68.00 QAR.","review_notes":[]}')

    monkeypatch.setattr(ai, "OpenAI", make_client)
    product = {"sku":"RNG-001","name":"Minimalist band ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"QAR","stock":12,"material":"stainless steel","size":"US 7","color":"silver"}
    reply, model = ai.inquiry_reply(product, "How much?")
    assert captured == {"api_key": "test-only-key", "base_url": ai.SILICONFLOW_BASE_URL}
    assert reply.answer == "The listed price is 68.00 QAR." and model == "example/model"


def test_local_ai_settings_are_encrypted_and_used_immediately(monkeypatch):
    client = TestClient(app.app)
    payload = {
        "provider": "compatible", "base_url": "https://models.example.test/v1",
        "model": "example/reply", "api_key": "test-secret-only",
        "reply_system_prompt": "Use a friendly tone.",
    }
    saved = client.put("/api/ai/settings", json=payload)
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["api_key_set"] is True
    assert "test-secret-only" not in saved.text
    assert "test-secret-only" not in ai_settings.CONFIG_PATH.read_text(encoding="utf-8")
    assert client.get("/api/ai/status").json()["base_url"] == payload["base_url"]

    captured = {}
    def make_client(**kwargs):
        captured.update(kwargs)
        fake = FakeChatClient('{"intent":"availability","answer":"Please confirm availability.","review_notes":[]}')
        captured["client"] = fake
        return fake
    monkeypatch.setattr(ai, "OpenAI", make_client)
    product = {"sku":"RNG-001","name":"Ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"USD","stock":1,"material":"steel","size":"US 7","color":"silver"}
    ai.inquiry_reply(product, "Is it available?")
    assert captured["api_key"] == "test-secret-only" and captured["base_url"] == payload["base_url"]
    assert "Use a friendly tone." in captured["client"].chat.completions.calls[0]["messages"][0]["content"]

    retained = client.put("/api/ai/settings", json={**payload, "api_key":"", "model":"example/reply-v2"})
    assert retained.json()["configured"] is True
    changed_url = client.put("/api/ai/settings", json={**payload, "api_key":"", "base_url":"https://other.example.test/v1"})
    assert changed_url.json()["configured"] is False
    assert client.delete("/api/ai/settings").status_code == 200
    assert not ai_settings.CONFIG_PATH.exists()


def test_ai_settings_reject_invalid_base_url():
    client = TestClient(app.app)
    payload = {"provider":"compatible", "model":"example/reply", "api_key":"x", "reply_system_prompt":""}
    assert client.put("/api/ai/settings", json={**payload,"base_url":"http://remote.example/v1"}).status_code == 422
    assert client.put("/api/ai/settings", json={**payload,"base_url":"https://user:pass@example.test/v1"}).status_code == 422


def test_default_reply_configuration_and_full_context(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    monkeypatch.delenv("SILICONFLOW_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert ai.public_status()["model"] == "deepseek-ai/DeepSeek-V4.1-Flash"
    assert "Read the full conversation" in ai.public_status()["reply_system_prompt"]
    product = {"sku":"RNG-001","name":"Ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"USD","stock":1,"material":"steel","size":"US 7",
               "color":"silver","listing_title":"Silver ring","listing_description":"Steel band, size US 7."}
    history = [
        {"sender":"Buyer","direction":"in","time":"10:00","text":"Is this available?"},
        {"sender":"You","direction":"out","time":"10:01","text":"Yes, it is."},
        {"sender":"Buyer","direction":"in","time":"10:02","text":"Can you ship it?"},
    ]
    fake = FakeChatClient('{"intent":"delivery","answer":"I will confirm shipping options.","review_notes":["Shipping"]}')
    ai.inquiry_reply(product, "Can you ship it?", conversation_history=history, client=fake)
    request = fake.chat.completions.calls[0]["messages"]
    assert "Read the full conversation" in request[0]["content"]
    assert "untrusted data" in request[0]["content"]
    payload = json.loads(request[1]["content"])
    assert payload["conversation_history"] == history
    assert payload["product"]["listing_description"] == "Steel band, size US 7."


def test_reply_payload_uses_relevant_confirmed_knowledge(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    knowledge = ai_knowledge.save(
        {"shipping": "Shipping in the US is available after address confirmation.",
         "returns": "Returns within 7 days."},
        [{"intent": "shipping", "title": "Shipping tone", "content": "I can check the cost."},
         {"intent": "returns", "title": "Return tone", "content": "Please ask about returns."}])
    selected = ai_knowledge.select("Can you ship it?", knowledge)
    product = {"sku":"RNG-001","name":"Ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"USD","stock":1,"material":"steel","size":"US 7",
               "color":"silver","listing_title":"Silver ring","listing_description":"Steel band."}
    fake = FakeChatClient('{"intent":"shipping","answer":"I can check the shipping cost.","review_notes":[]}')
    ai.inquiry_reply(product, "Can you ship it?", knowledge_context=selected, client=fake)
    payload = json.loads(fake.chat.completions.calls[0]["messages"][1]["content"])
    customer_knowledge = payload["customer_service_knowledge"]
    assert customer_knowledge["policies"] == {"shipping": "Shipping in the US is available after address confirmation."}
    assert [item["title"] for item in customer_knowledge["reference_scripts"]] == ["Shipping tone"]


def test_default_reference_scripts_use_product_values(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    product = {"sku":"RNG-001","name":"Silver ring","category":"ring","condition":"new",
               "price_minor":6800,"currency":"USD","stock":1,"material":"steel","size":"US 7",
               "color":"silver","listing_title":"Silver ring","listing_description":"Steel band."}
    fake = FakeChatClient('{"intent":"availability","answer":"The Silver ring is currently available.","review_notes":[]}')
    ai.inquiry_reply(product, "Is it available?", client=fake)
    payload = json.loads(fake.chat.completions.calls[0]["messages"][1]["content"])
    scripts = payload["customer_service_knowledge"]["reference_scripts"]
    assert scripts == [{"intent":"availability", "title":"是否有货",
                        "content":"The Silver ring is currently available."}]
    product["stock"] = 0
    assert ai.reference_scripts_for_product(ai_knowledge.DEFAULT_REPLIES[:1], ai.product_facts(product))[0]["content"] == "The Silver ring is currently out of stock."
    product["size"] = ""
    assert ai.reference_scripts_for_product(ai_knowledge.DEFAULT_REPLIES[2:3], ai.product_facts(product)) == []


def test_agent_plans_read_only_lookups(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "siliconflow")
    fake = FakeChatClient('{"intent":"shipping","use_product":true,"use_history":false,"policy_topics":["shipping"],"product_query":"Silver ring"}')
    plan, _ = ai.plan_reply("Can you ship it?", "Buyer · Silver ring",
                            [{"direction":"in","text":"Can you ship it?"}], client=fake)
    assert plan.use_product is True
    assert plan.policy_topics == ["shipping"]
    request = fake.chat.completions.calls[0]["messages"]
    assert "read-only tools" in request[0]["content"]
    assert "Silver ring" in request[1]["content"]


def test_ai_routes_and_mcp_share_state(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA", tmp_path)
    monkeypatch.setattr(app, "DB", tmp_path / "workbench.db")
    app.init_db()
    client = TestClient(app.app)
    sample = Path(app.ROOT / "sample_products_us.csv").read_text(encoding="utf-8")
    assert client.post("/api/products/import", json={"csv_text": sample}).json()["imported"] == 3
    product_id = next(p["id"] for p in client.get("/api/state").json()["products"] if p["sku"] == "US-RNG-001")

    monkeypatch.setattr(ai, "listing_copy", lambda product: (
        ai.ListingCopy(title="Minimalist band ring", description="Stainless steel, US 7, silver.", review_notes=["Confirm photo"]),
        "test-model"))
    draft_result = client.post(f"/api/products/{product_id}/draft-ai").json()
    assert draft_result["issues"] == []
    draft_id = draft_result["id"]
    state = client.get("/api/state").json()
    draft = next(d for d in state["drafts"] if d["id"] == draft_id)
    assert draft["generation_source"] == "llm"
    assert draft["status"] == "pending"
    assert client.post(f"/api/drafts/{draft_id}/approve").status_code == 200
    task_id = client.post(f"/api/drafts/{draft_id}/queue", json={"account_alias":"test-01"}).json()["id"]
    inquiry_id = client.post("/api/inquiries", json={"task_id":task_id,"buyer_alias":"buyer-01","question":"How much?"}).json()["id"]
    assert client.post(f"/api/inquiries/{inquiry_id}/reply-ai", json={}).status_code == 422
    monkeypatch.setattr(ai, "inquiry_reply", lambda product, question, **kwargs: (
        ai.InquiryReply(intent="price", answer="The listed price is 68.00 USD. Please confirm availability.", review_notes=[]),
        "test-model"))
    result = client.post(f"/api/inquiries/{inquiry_id}/reply-ai", json={"allow_external_processing":True})
    assert result.status_code == 200
    inquiry = next(i for i in client.get("/api/state").json()["inquiries"] if i["id"] == inquiry_id)
    assert inquiry["reply_source"] == "llm" and inquiry["reply_status"] == "draft"

    tools = [tool.name for tool in mcp_server.mcp._tool_manager.list_tools()]
    assert {"get_product", "save_listing_draft", "review_listing_draft", "get_inquiry", "save_reply_draft"}.issubset(tools)
    mcp_draft = mcp_server.save_listing_draft(product_id, "Minimalist band ring", "Material: stainless steel. Size: US 7. Color: silver.")
    assert mcp_draft["status"] == "pending"
    assert mcp_server.review_listing_draft(mcp_draft["draft_id"])["issues"] == []
    assert mcp_server.get_inquiry(inquiry_id)["product"]["price"] == "68.00 USD"
