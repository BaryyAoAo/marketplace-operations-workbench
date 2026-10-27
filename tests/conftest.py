import pytest

import ai_settings
import ai_knowledge


@pytest.fixture(autouse=True)
def isolated_ai_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_settings, "CONFIG_PATH", tmp_path / "ai_settings.json")
    monkeypatch.setattr(ai_knowledge, "KNOWLEDGE_PATH", tmp_path / "ai_reply_knowledge.json")
