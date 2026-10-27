from fastapi.testclient import TestClient

import ai_knowledge
import app as workbench


def test_knowledge_starts_with_business_defaults_and_round_trips():
    client = TestClient(workbench.app)
    assert client.get('/api/ai/knowledge').json() == ai_knowledge.empty()
    assert all(value and '【待确认】' not in value for value in ai_knowledge.empty()['policies'].values())
    assert len(ai_knowledge.empty()['quick_replies']) == len(ai_knowledge.INTENTS) - 1
    saved = client.put('/api/ai/knowledge', json={
        'policies': {'shipping': 'US shipping available after address confirmation.'},
        'quick_replies': [
            {'intent': 'shipping', 'title': 'Shipping question', 'content': 'I can check the shipping cost.'},
            {'intent': 'returns', 'title': 'Return question', 'content': 'Please ask about returns.'},
        ],
    })
    assert saved.status_code == 200
    assert saved.json()['version'] == 1
    assert client.get('/api/ai/knowledge').json() == saved.json()


def test_knowledge_selects_only_relevant_rules_and_scripts():
    saved = ai_knowledge.save(
        {'shipping': 'We ship in the US.', 'returns': 'Returns within 7 days.'},
        [
            {'intent': 'general', 'title': 'Tone', 'content': 'Thanks for asking.'},
            {'intent': 'shipping', 'title': 'Shipping', 'content': 'Shipping details can be checked.'},
            {'intent': 'returns', 'title': 'Returns', 'content': 'Please review returns.'},
        ])
    selected = ai_knowledge.select('Can you ship it?', saved)
    assert selected['intents'] == ['shipping']
    assert selected['policies'] == {'shipping': 'We ship in the US.'}
    assert [item['title'] for item in selected['quick_replies']] == ['Tone', 'Shipping']
    assert not selected['missing_facts']
    assert ai_knowledge.select('可以邮寄吗？', saved)['policies']['shipping'] == 'We ship in the US.'


def test_unknown_policy_is_flagged_and_invalid_entries_rejected():
    defaults = ai_knowledge.select('Can you ship?')
    assert defaults['policies']['shipping'] == ai_knowledge.DEFAULT_POLICIES['shipping']
    assert defaults['missing_facts'] == []
    assert [item['intent'] for item in defaults['quick_replies']] == ['shipping']
    blank = ai_knowledge.save({'shipping': ''}, ai_knowledge.empty()['quick_replies'])
    assert '配送规则未配置' in ai_knowledge.select('Can you ship?', blank)['missing_facts']
    client = TestClient(workbench.app)
    bad = client.put('/api/ai/knowledge', json={
        'policies': {'unknown': 'anything'}, 'quick_replies': []})
    assert bad.status_code == 422
    incomplete = client.put('/api/ai/knowledge', json={
        'policies': {}, 'quick_replies': [{'intent': 'shipping', 'title': '', 'content': 'Hi'}]})
    assert incomplete.status_code == 422
