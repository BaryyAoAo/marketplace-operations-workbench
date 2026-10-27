import sqlite3

import pytest

import ai
import app as workbench
import reply_agent


def _database():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE products (id INTEGER PRIMARY KEY,sku TEXT,name TEXT,listing_title TEXT,active INTEGER)')
    con.execute("INSERT INTO products VALUES(1,'RNG-001','Silver ring','Silver band ring',1)")
    return con


def test_agent_finds_matching_product_and_reads_only_needed_context(monkeypatch):
    con = _database()
    monkeypatch.setattr(ai, 'plan_reply', lambda question, title, recent: (
        ai.ReplyPlan(intent='shipping', use_product=False, use_history=False,
                     policy_topics=['shipping'], product_query='Silver ring'), 'model'))
    conversation = {'title': 'Buyer · Silver ring'}
    history = [{'direction':'in','text':'Can you ship it?'}]
    result = reply_agent.plan_and_read(con, conversation, history, None)
    assert result['product']['sku'] == 'RNG-001'
    assert result['conversation_history'] == []
    assert not result['use_product_facts']
    assert 'shipping' in result['knowledge']['policies']


def test_agent_requires_human_product_choice_if_ambiguous():
    con = _database()
    con.execute("INSERT INTO products VALUES(2,'RNG-002','Silver ring','Silver band ring',1)")
    with pytest.raises(reply_agent.ProductMatchRequired, match='唯一识别商品'):
        reply_agent.resolve_product(con, 'Buyer · Silver ring', 'Silver ring', None)
    assert reply_agent.resolve_product(con, 'Buyer · Silver ring', 'Silver ring', 2)['sku'] == 'RNG-002'


def test_stock_claims_are_checked_against_product_table():
    product = {'material':'steel','size':'US 7','color':'silver','currency':'USD',
               'price_minor':6800,'stock':0}
    assert '回复库存状态与结构化库存不一致' in workbench.reply_issues(
        product, 'Is it available?', 'The ring is currently available.')
    product['stock'] = 1
    assert '回复库存状态与结构化库存不一致' in workbench.reply_issues(
        product, 'Is it available?', 'The ring is out of stock.')
