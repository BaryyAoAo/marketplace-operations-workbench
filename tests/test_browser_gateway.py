import asyncio

import pytest

import browser_gateway

from browser_gateway import facebook_pages, listing_item_url, listing_under_review, location_field_line, los_angeles_suggestion_uid, marketplace_category_uid, parse_account_menu, parse_avatar_result, parse_marketplace_conversations, parse_visible_messages, publish_form_failure, publish_page_failure


def test_publish_failure_reason_keeps_limit_reached_message():
    snapshot = 'heading "Limit reached"\nparagraph: You are not able to create new listings at the moment.'
    assert "Limit reached" in publish_page_failure(snapshot)
    assert publish_page_failure('textbox "Title"\nbutton "Publish"') is None


def test_disabled_next_reports_facebook_description_validation():
    snapshot = '''textbox "Description"
      generic: Please enter a valid description.
    button "Next" disableable disabled'''
    assert publish_form_failure(snapshot) == (
        "Facebook 提示 Please enter a valid description（请填写有效的商品描述）")


def test_location_suggestion_rejects_mexico_namesake():
    snapshot = '''uid=1_4 combobox "Location": IztapalapaLos Angeles
      uid=1_5 generic description="Los Ángeles Apanoaya, Iztapalapa, Distrito Federal, Mexico"
      uid=1_6 generic description="Los Angeles, Baja California, Mexico"
      uid=1_7 generic description="Los Angeles, California, United States"'''
    assert los_angeles_suggestion_uid(snapshot) == "1_7"
    assert "Iztapalapa" in location_field_line(snapshot)
    assert los_angeles_suggestion_uid(snapshot.replace("      uid=1_7 generic description=", "      uid=1_7 other description=")) is None


def test_seller_list_distinguishes_review_from_verified_item_link():
    title = "Gold-tone pendant necklace — test item"
    reviewing = f'''heading "Your listings"
      generic "This listing is being reviewed."
      heading "{title}"
      generic "$3,500"'''
    active = f'''heading "Your listings"
      link "{title}" url="https://www.facebook.com/marketplace/item/123456/"
      generic "$3,500"'''
    assert listing_under_review(reviewing, title)
    assert listing_item_url(reviewing, title) is None
    assert not listing_under_review(active, title)
    assert listing_item_url(active, title) == "https://www.facebook.com/marketplace/item/123456/"
    assert not listing_under_review(reviewing, "Different item")


def test_recheck_accepts_reviewing_item_as_posted(monkeypatch):
    gateway = browser_gateway.BrowserGateway()
    gateway.state = "online"
    gateway.page_id = 1
    title = "Gold-tone pendant necklace — test item"
    async def fake_call(_name, **_kwargs):
        if _name == "list_pages":
            return "1: Selling (https://www.facebook.com/marketplace/you/selling/)"
        return f'heading "Your listings"\n generic "This listing is being reviewed."\n heading "{title}"'
    monkeypatch.setattr(gateway, "_call_unlocked", fake_call)
    result = asyncio.run(gateway.verify_listing(title))
    assert result["state"] == "posted"
    assert "仍在审核" in result["note"]


def test_disconnect_closes_mcp_context_in_owning_task(monkeypatch):
    exits = []

    class TaskBoundContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            self.owner = asyncio.current_task()
            return self.value

        async def __aexit__(self, *_args):
            assert asyncio.current_task() is self.owner
            exits.append(self.value)

    class FakeSession:
        async def initialize(self):
            pass

    monkeypatch.setattr(browser_gateway.shutil, "which", lambda _name: "npx")
    monkeypatch.setattr(browser_gateway, "stdio_client", lambda _server: TaskBoundContext((None, None)))
    monkeypatch.setattr(browser_gateway, "ClientSession", lambda *_args, **_kwargs: TaskBoundContext(FakeSession()))

    async def fake_refresh(self):
        self.page_id = 1
        self.state = "online"
        return self.status()

    monkeypatch.setattr(browser_gateway.BrowserGateway, "refresh_account", fake_refresh)

    async def scenario():
        gateway = browser_gateway.BrowserGateway()
        await gateway.start()
        for _ in range(20):
            if gateway.state == "online":
                break
            await asyncio.sleep(0)
        assert gateway.state == "online"
        assert (await gateway.disconnect())["state"] == "disconnected"
        assert (await gateway.disconnect())["state"] == "disconnected"
        assert gateway._task is None

    asyncio.run(scenario())
    assert len(exits) == 2


def test_account_menu_prefers_signed_in_profile_over_advertising_footer():
    snapshot = '''uid=1_16 button "Account, your profile" expandable expanded
      uid=9_1 link "Test Seller" url="https://www.facebook.com/me/"
      uid=9_16 link "Advertising" url="https://www.facebook.com/business/"
    uid=1_34 navigation "Marketplace sidebar"
'''
    assert parse_account_menu(snapshot) == ("Test Seller", "https://www.facebook.com/me/")


def test_avatar_result_accepts_facebook_cdn_only():
    assert parse_avatar_result('Script ran on page and returned:\n```json\n"https://scontent.xx.fbcdn.net/p.jpg?x=1"\n```') == "https://scontent.xx.fbcdn.net/p.jpg?x=1"
    assert parse_avatar_result('```json\n"https://example.com/not-an-avatar.jpg"\n```') is None


def test_marketplace_chat_button_is_scoped_below_chats_heading():
    snapshot = '''uid=1_1 button "Marketplace"
      uid=1_2 heading "Chats" level="2"
      uid=1_3 button "Marketplace   about an hour ago"
'''
    assert marketplace_category_uid(snapshot) == "1_3"


def test_marketplace_sidebar_heading_is_not_mistaken_for_chat_category():
    snapshot = '''uid=1_0 RootWebArea
  uid=1_1 navigation "Marketplace sidebar"
    uid=1_2 heading "Marketplace" level="1"
'''
    with pytest.raises(RuntimeError, match="category is not visible"):
        parse_marketplace_conversations(snapshot)


def test_only_facebook_pages_are_account_candidates():
    pages = facebook_pages("""## Pages
1: Workbench (http://127.0.0.1:8765/)
2: Marketplace (https://www.facebook.com/marketplace/item/123/)
3: Other (https://example.com/)
""")
    assert [page["page_id"] for page in pages] == [2]


def test_selected_facebook_tab_is_recognized_and_preferred():
    pages = facebook_pages("""## Pages
1: Facebook (https://www.facebook.com/) [selected]
2: Marketplace 运营工作台 (http://127.0.0.1:8765/)
3: Facebook (https://www.facebook.com/)
""")
    assert [page["page_id"] for page in pages] == [1, 3]


def test_two_visible_marketplace_conversations_are_extracted():
    snapshot = """uid=1_0 RootWebArea
  uid=1_1 generic
      uid=2_0 banner
        uid=2_1 heading "Marketplace" level="1"
      uid=2_2 link "Group chat: Seller A · Watch" url="https://www.facebook.com/messages/t/111/"
        uid=2_3 "Seller A · Watch"
        uid=2_4 "You: Is this item available?"
      uid=2_5 button "More options"
      uid=2_6 link "Group chat: Seller B · Ring \"Gold\"" url="https://www.facebook.com/messages/t/222/"
        uid=2_7 "Seller B · Ring \"Gold\""
        uid=2_8 "Seller B: Yes, it is."
  uid=1_5 button "Close"
"""
    found = parse_marketplace_conversations(snapshot)
    assert [item["external_id"] for item in found] == ["111", "222"]
    assert found[0]["preview"].startswith("You:")
    assert found[1]["preview"].startswith("Seller B:")
    assert found[1]["title"] == 'Seller B · Ring "Gold"'


def test_visible_message_history_distinguishes_direction():
    snapshot = """uid=1_0 RootWebArea
      uid=1_82 log "Messages in conversation titled Seller B" live="polite"
        uid=1_93 generic "At 22:48, You: Hello"
        uid=2_21 generic "At 22:50, Seller B: Yes, it is."
      uid=1_105 region "Thread composer"
"""
    found = parse_visible_messages(snapshot)
    assert [(item["direction"], item["text"]) for item in found] == [
        ("out", "Hello"), ("in", "Yes, it is.")]
