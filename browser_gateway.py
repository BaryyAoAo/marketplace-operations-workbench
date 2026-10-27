"""Long-lived Chrome UI adapter for one authorized browser profile.

This module uses visible Facebook pages via Chrome DevTools MCP. It never uses
Facebook's private network endpoints, cookies, or saved credentials.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_DIR = Path(__file__).resolve().parent
FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}
CHAT_URL = re.compile(r"https://www\.facebook\.com/messages/t/(\d+)/?")
ITEM_URL = re.compile(r"https://www\.facebook\.com/marketplace/item/\d+/?(?:\?[^ ]*)?")
CREATE_URL = "https://www.facebook.com/marketplace/create/item"
INBOX_URL = "https://www.facebook.com/marketplace/inbox/"
SELLING_URL = "https://www.facebook.com/marketplace/you/selling/"
UID = re.compile(r"uid=(\S+)")


def result_text(result) -> str:
    value = "\n".join(block.text for block in result.content if block.type == "text")
    if result.isError:
        raise RuntimeError(value)
    return value


def pages_from_text(value: str) -> list[dict]:
    pages = []
    selected = []
    for line in value.splitlines():
        match = re.match(r"\s*(\d+):\s*(.*?)\s*\((https?://[^)]*)\)\s*(\[selected\])?\s*$", line)
        if match:
            page = {"page_id": int(match.group(1)), "title": match.group(2), "url": match.group(3)}
            (selected if match.group(4) else pages).append(page)
    return selected + pages


def facebook_pages(value: str) -> list[dict]:
    return [page for page in pages_from_text(value)
            if urlparse(page["url"]).hostname in FACEBOOK_HOSTS]


def named_uid(snapshot: str, role: str, label: str, *, contains: bool = False) -> str:
    matches = []
    for line in snapshot.splitlines():
        if f" {role} \"" not in line:
            continue
        match = UID.search(line)
        if not match:
            continue
        name = line.split(f" {role} \"", 1)[1]
        if (label in name if contains else name.startswith(label + '"')):
            matches.append(match.group(1))
    if len(matches) != 1:
        raise RuntimeError(f"Cannot identify unique {role} {label!r} ({len(matches)} matches)")
    return matches[0]


def marketplace_category_uid(snapshot: str) -> str:
    """Select Marketplace inside Chats, excluding Facebook's main navigation button."""
    lines = snapshot.splitlines()
    chats = next((i for i, line in enumerate(lines) if 'heading "Chats"' in line), -1)
    candidates = []
    for i, line in enumerate(lines):
        if chats >= 0 and i <= chats:
            continue
        if ' button "Marketplace' in line:
            match = UID.search(line)
            if match:
                candidates.append(match.group(1))
    if len(candidates) != 1:
        raise RuntimeError(f"Cannot identify Marketplace chat category ({len(candidates)} matches)")
    return candidates[0]


def parse_marketplace_conversations(snapshot: str) -> list[dict]:
    """Extract only links under the visible Marketplace Messenger category."""
    lines = snapshot.splitlines()
    sidebar_index = next((i for i, line in enumerate(lines)
                          if 'navigation "Marketplace sidebar"' in line), len(lines))
    heading_index = next((i for i, line in enumerate(lines)
                          if i < sidebar_index and 'heading "Marketplace" level="1"' in line), -1)
    if heading_index < 0:
        raise RuntimeError("Marketplace conversation category is not visible")
    conversations = []
    for i in range(heading_index + 1, len(lines)):
        line = lines[i]
        if re.match(r"  uid=", line) and "link " not in line and conversations:
            break
        url_match = CHAT_URL.search(line)
        if " link " not in line or not url_match:
            continue
        title_match = re.search(r'link "Group chat: ?(.*)" url=', line)
        title = title_match.group(1).strip() if title_match else "Marketplace 会话"
        if i + 1 < len(lines):
            child_title = re.search(r'uid=\S+ "(.*)"\s*$', lines[i + 1])
            if child_title:
                title = child_title.group(1)
        preview = ""
        for next_line in lines[i + 1:i + 6]:
            if " link " in next_line or " button " in next_line:
                break
            text_match = re.search(r'uid=\S+ "(.*)"\s*$', next_line)
            if text_match and (text_match.group(1).startswith("You:") or ": " in text_match.group(1)):
                preview = text_match.group(1)
                break
        conversations.append({
            "external_id": url_match.group(1), "url": url_match.group(0),
            "title": title, "preview": preview,
        })
    return conversations


def parse_visible_messages(snapshot: str) -> list[dict]:
    start = snapshot.find('log "Messages in conversation')
    if start < 0:
        raise RuntimeError("Message history is not visible")
    end = snapshot.find('region "Thread composer"', start)
    section = snapshot[start:end if end > start else None]
    messages = []
    for line in section.splitlines():
        match = re.search(r'uid=\S+ generic "At ([^,]+), ([^:]+): (.*)"\s*$', line)
        if match:
            messages.append({"time_label": match.group(1), "sender": match.group(2),
                             "text": match.group(3), "direction": "out" if match.group(2) == "You" else "in"})
    return messages


def parse_account_menu(snapshot: str) -> tuple[str | None, str | None]:
    """Read the signed-in profile link, never a footer or business link."""
    start = snapshot.find('button "Account,')
    if start < 0:
        return None, None
    segment = snapshot[start:]
    end = segment.find('navigation "Marketplace sidebar"')
    if end >= 0:
        segment = segment[:end]
    for line in segment.splitlines():
        match = re.search(r'link "([^"]+)"[^\n]*url="(https://www\.facebook\.com/[^\"]+)"', line)
        if not match:
            continue
        name, url = match.groups()
        path = urlparse(url).path.rstrip('/')
        if path in {'/me', '/profile.php'} or path.startswith('/people/'):
            if name not in {'See your profile', 'Profile', 'Switch profile'}:
                return name, url
    return None, None


def parse_avatar_result(value: str) -> str | None:
    match = re.search(r'```json\s*(.*?)\s*```', value, re.S)
    if not match:
        return None
    try:
        candidate = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(candidate, str):
        return None
    parsed = urlparse(candidate)
    host = parsed.hostname or ''
    if parsed.scheme == 'https' and (host == 'fbcdn.net' or host.endswith('.fbcdn.net')
                                     or host == 'www.facebook.com'):
        return candidate
    return None


def publish_page_failure(snapshot: str) -> str | None:
    """Return a short, user-facing reason shown by Facebook during publishing."""
    messages = (
        ("Limit reached", "Facebook 提示 Limit reached：当前账号暂时不能创建新商品"),
        ("You are not able to create new listings", "Facebook 提示当前账号不能创建新商品"),
        ("Marketplace isn't available to you", "Facebook 提示此账号无法使用 Marketplace"),
        ("Something went wrong", "Facebook 提示 Something went wrong，请在页面检查后重试"),
    )
    return next((note for marker, note in messages if marker.lower() in snapshot.lower()), None)


def publish_form_failure(snapshot: str) -> str | None:
    """Surface visible form validation before a disabled Next button."""
    if "Please enter a valid description" in snapshot:
        return "Facebook 提示 Please enter a valid description（请填写有效的商品描述）"
    return None


def los_angeles_suggestion_uid(snapshot: str) -> str | None:
    """Choose only the Los Angeles, California suggestion, never a namesake."""
    for line in snapshot.splitlines():
        if not re.search(r"\s(?:generic|option|button|listitem)\b", line):
            continue
        if not re.search(r"Los Angeles", line, re.I):
            continue
        if not re.search(r"California|,\s*CA\b", line, re.I):
            continue
        if re.search(r"Mexico|México|Baja California|Iztapalapa", line, re.I):
            continue
        match = UID.search(line)
        if match:
            return match.group(1)
    return None


def location_field_line(snapshot: str) -> str:
    return next((line for line in snapshot.splitlines()
                 if 'combobox "Location"' in line), "")


def listing_under_review(snapshot: str, title: str) -> bool:
    """Require the submitted title beside Facebook's review notice."""
    if 'Your listings' not in snapshot or not title:
        return False
    start = 0
    while (position := snapshot.find(title, start)) >= 0:
        nearby = snapshot[max(0, position - 400):position + len(title) + 400]
        if 'This listing is being reviewed' in nearby:
            return True
        start = position + len(title)
    return False


def listing_item_url(snapshot: str, title: str) -> str | None:
    """Read an item link adjacent to the exact title in the seller list."""
    start = 0
    while (position := snapshot.find(title, start)) >= 0:
        nearby = snapshot[max(0, position - 300):position + len(title) + 300]
        if 'This listing is being reviewed' not in nearby:
            match = ITEM_URL.search(nearby)
            if match:
                return match.group(0)
        start = position + len(title)
    return None


class BrowserGateway:
    def __init__(self) -> None:
        self.state = "disconnected"
        self.error: str | None = None
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self.page_id: int | None = None
        self.page_url: str | None = None
        self.display_name: str | None = None
        self.profile_url: str | None = None
        self.avatar_url: str | None = None

    def status(self) -> dict:
        return {"state": self.state, "error": self.error,
                "source": "Chrome 当前默认资料目录", "page_url": self.page_url,
                "account_key": "chrome-default" if self.page_id is not None else None,
                "display_name": self.display_name, "profile_url": self.profile_url,
                "avatar_url": self.avatar_url,
                "browser_profile_count": 1 if self.page_id is not None else 0}

    async def start(self) -> dict:
        async with self._lifecycle_lock:
            if self._task is None or self._task.done():
                self.state = "connecting"
                self.error = None
                self._stop_event = asyncio.Event()
                self._task = asyncio.create_task(self._connect(self._stop_event))
        return self.status()

    async def _connect(self, stop_event: asyncio.Event) -> None:
        stack = AsyncExitStack()
        try:
            npx = shutil.which("npx.cmd") or shutil.which("npx")
            if not npx:
                raise RuntimeError("未找到 npx；请安装 Node.js")
            server = StdioServerParameters(
                command=npx,
                args=["-y", "chrome-devtools-mcp@1.12.0", "--autoConnect",
                      "--no-usage-statistics", "--no-performance-crux",
                      f"--workspace={PROJECT_DIR}"], cwd=PROJECT_DIR,
            )
            read, write = await stack.enter_async_context(stdio_client(server))
            session = await stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=90)))
            await session.initialize()
            self._stack, self._session = stack, session
            self.state = "awaiting_permission"
            try:
                await self.refresh_account()
            except Exception as exc:
                self.error = str(exc)[:300]
                self.state = "awaiting_permission"
            # Keep the MCP context open in its owning task. Its task group must
            # also be closed here, rather than by a later FastAPI request task.
            await stop_event.wait()
        except Exception as exc:
            self.state = "error"
            self.error = str(exc)[:300]
        finally:
            try:
                await stack.aclose()
            except Exception as exc:
                if not stop_event.is_set():
                    self.state = "error"
                    self.error = str(exc)[:300]
            finally:
                self._stack = None
                self._session = None

    async def disconnect(self) -> dict:
        async with self._lifecycle_lock:
            task = self._task
            if task is not None and not task.done():
                if self._stop_event is not None:
                    self._stop_event.set()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=8)
                except TimeoutError:
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=8)
                    except (Exception, asyncio.CancelledError):
                        pass
                except (Exception, asyncio.CancelledError):
                    pass
            self._task = None
            self._stop_event = None
            self._stack = None
            self._session = None
            self.page_id = None
            self.page_url = None
            self.display_name = None
            self.profile_url = None
            self.avatar_url = None
            self.state = "disconnected"
            self.error = None
        return self.status()

    async def _call_unlocked(self, name: str, **args) -> str:
        if self._session is None:
            raise RuntimeError("浏览器尚未连接；请先点击连接 Chrome")
        result = await self._session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=90))
        return result_text(result)

    async def refresh_account(self) -> dict:
        async with self._lock:
            pages = facebook_pages(await self._call_unlocked("list_pages"))
            if not pages:
                self.page_id = None
                self.page_url = None
                self.state = "no_facebook_tab"
                self.error = "当前 Chrome 资料目录没有打开 Facebook 页面"
                return self.status()
            # Prefer the visible Marketplace tab; do not inspect unrelated sites.
            chosen = next((page for page in pages if "/marketplace" in page["url"]), pages[0])
            self.page_id, self.page_url = chosen["page_id"], chosen["url"]
            self.display_name = None
            self.profile_url = None
            self.avatar_url = None
            snapshot = await self._call_unlocked("take_snapshot", pageId=self.page_id)
            if ('button "Account, your profile"' in snapshot or 'button "Account,' in snapshot):
                self.state = "online"
                self.error = None
                menu_open = False
                try:
                    account_button = named_uid(snapshot, "button", "Account,", contains=True)
                    await self._call_unlocked("click", pageId=self.page_id, uid=account_button)
                    menu_open = True
                    menu = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                    self.display_name, self.profile_url = parse_account_menu(menu)
                    if self.profile_url:
                        result = await self._call_unlocked(
                            "evaluate_script", pageId=self.page_id, waitForStableDom=False,
                            function="""() => {
                              const link = [...document.querySelectorAll('a[href]')].find(a => {
                                try { return new URL(a.href).pathname.replace(/\\/$/, '') === '/me'; }
                                catch { return false; }
                              });
                              const image = link?.querySelector('img, image');
                              return image?.getAttribute('src') || image?.getAttribute('href') ||
                                image?.getAttribute('xlink:href') || null;
                            }""")
                        self.avatar_url = parse_avatar_result(result)
                except Exception:
                    # Identity discovery is best effort; connection status still comes from the page.
                    pass
                finally:
                    if menu_open:
                        try:
                            state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                            await self._call_unlocked("click", pageId=self.page_id,
                                                      uid=named_uid(state, "button", "Account,", contains=True))
                        except Exception:
                            pass
            else:
                self.state = "login_required"
                self.error = "Facebook 页面未显示已登录账号控件"
            return self.status()

    async def debug_snapshot(self) -> str:
        async with self._lock:
            if self.page_id is None:
                raise RuntimeError("没有 Facebook 页面")
            return await self._call_unlocked("take_snapshot", pageId=self.page_id)

    async def debug_pages(self) -> str:
        async with self._lock:
            return await self._call_unlocked("list_pages")

    async def probe_inbox(self) -> dict:
        """Read-only diagnostic: open one existing Buying thread and report URL shape."""
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线")
            await self._call_unlocked("navigate_page", pageId=self.page_id,
                                      type="url", url=INBOX_URL)
            for _ in range(20):
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                if 'tab "Buying"' in state:
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Inbox 标签未加载")
            await self._call_unlocked("click", pageId=self.page_id,
                                      uid=named_uid(state, "tab", "Buying"))
            for _ in range(20):
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                if 'tab "Buying"' in state and 'selected' in next(
                        (line for line in state.splitlines() if 'tab "Buying"' in line), ''):
                    rows = []
                    after_tab = False
                    for line in state.splitlines():
                        if 'tab "Buying"' in line:
                            after_tab = True
                        elif after_tab and ' button "New message"' in line:
                            break
                        elif after_tab and ' button "' in line:
                            match = UID.search(line)
                            if match:
                                rows.append(match.group(1))
                    if rows:
                        break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Buying 会话按钮未加载")
            await self._call_unlocked("click", pageId=self.page_id, uid=rows[0])
            for _ in range(12):
                await asyncio.sleep(0.5)
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                pages = pages_from_text(await self._call_unlocked("list_pages"))
                current = next((page for page in pages if page["page_id"] == self.page_id), None)
                if current and (CHAT_URL.search(current["url"]) or
                                'log "Messages in conversation' in state):
                    return {"url": current["url"],
                            "snapshot_chat_urls": list(dict.fromkeys(CHAT_URL.findall(state))),
                            "has_message_log": 'log "Messages in conversation' in state,
                            "page_title": current["title"][:80]}
            return {"url": current["url"] if current else None,
                    "snapshot_chat_urls": list(dict.fromkeys(CHAT_URL.findall(state))),
                    "has_message_log": 'log "Messages in conversation' in state}

    async def refresh_conversations(self) -> list[dict]:
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线；请刷新账号状态")
            state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
            try:
                existing = parse_marketplace_conversations(state)
                if existing:
                    return existing
            except RuntimeError:
                pass
            if 'button "Messenger"' not in state:
                await self._call_unlocked("navigate_page", pageId=self.page_id,
                                          type="url", url="https://www.facebook.com/marketplace/")
                for _ in range(15):
                    state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                    if 'button "Messenger"' in state:
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError("Marketplace 页面未显示 Messenger 入口")
                self.page_url = "https://www.facebook.com/marketplace/"
            if 'heading "Chats"' not in state:
                await self._call_unlocked("click", pageId=self.page_id,
                                          uid=named_uid(state, "button", "Messenger"))
            for attempt in range(2):
                for _ in range(12):
                    state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                    if 'heading "Chats"' in state and ' button "Marketplace' in state:
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError("Messenger 未显示 Marketplace 分类入口")
                await self._call_unlocked("click", pageId=self.page_id,
                                          uid=marketplace_category_uid(state))
                for _ in range(15):
                    state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                    try:
                        items = parse_marketplace_conversations(state)
                        if items:
                            return items
                    except RuntimeError:
                        pass
                    await asyncio.sleep(0.5)
                # Facebook sometimes ignores the first click while the popover loads.
            raise RuntimeError("Marketplace 会话分类未显示列表；请在 Facebook 检查 Inbox")

    async def read_conversation(self, url: str) -> list[dict]:
        if not CHAT_URL.fullmatch(url):
            raise ValueError("不是已识别的 Facebook 会话链接")
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线")
            await self._call_unlocked("navigate_page", pageId=self.page_id, type="url", url=url)
            previous = None
            stable = 0
            observed = 0
            for _ in range(25):
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                if 'log "Messages in conversation' in state:
                    messages = parse_visible_messages(state)
                    signature = tuple((item["sender"], item["text"]) for item in messages)
                    stable = stable + 1 if signature == previous else 0
                    previous = signature
                    observed += 1
                    if observed >= 5 and stable >= 3:
                        self.page_url = url
                        return messages
                await asyncio.sleep(0.7)
            raise RuntimeError("会话页面未显示消息历史")

    async def send_message(self, url: str, message: str) -> dict:
        if not CHAT_URL.fullmatch(url):
            raise ValueError("不是已识别的 Facebook 会话链接")
        if not message.strip() or len(message) > 2000:
            raise ValueError("消息为空或超过 2000 字符")
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线")
            await self._call_unlocked("navigate_page", pageId=self.page_id, type="url", url=url)
            for _ in range(20):
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                if 'textbox "Write to' in state and 'log "Messages in conversation' in state:
                    break
                await asyncio.sleep(0.7)
            else:
                raise RuntimeError("会话输入框未加载")
            previous = sum(item["direction"] == "out" and item["text"] == message.strip()
                           for item in parse_visible_messages(state))
            composer = named_uid(state, "textbox", "Write to", contains=True)
            await self._call_unlocked("fill", pageId=self.page_id, uid=composer, value=message.strip())
            state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
            send = named_uid(state, "button", "Press Enter to send", contains=True)
            await self._call_unlocked("click", pageId=self.page_id, uid=send)
            for _ in range(8):
                state = await self._call_unlocked("take_snapshot", pageId=self.page_id)
                current = sum(item["direction"] == "out" and item["text"] == message.strip()
                              for item in parse_visible_messages(state))
                if current > previous:
                    break
                await asyncio.sleep(0.5)
            confirmed = current > previous and 'Message sent' in state
            return {"confirmed": confirmed, "state": "sent" if confirmed else "uncertain"}

    async def publish_listing(self, listing: dict) -> dict:
        """Fill and submit one operator-selected listing, stopping on unverified state."""
        image_path = Path(listing["image_path"]).resolve()
        if not image_path.is_relative_to(PROJECT_DIR) or not image_path.is_file():
            raise ValueError("商品图片必须先导入本项目的图片目录")
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线")
            page_id = self.page_id
            await self._call_unlocked("navigate_page", pageId=page_id, type="url", url=CREATE_URL)
            for _ in range(15):
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
                if failure := publish_page_failure(state):
                    return {"state": "blocked", "note": f"{failure}；未提交商品"}
                if 'textbox "Title"' in state and 'textbox "Price"' in state:
                    break
                await asyncio.sleep(0.7)
            else:
                return {"state": "needs_attention", "note": "新建商品页面未显示标题和价格字段"}
            photo = named_uid(state, "button", "Add photos or drag and drop")
            await self._call_unlocked("upload_file", pageId=page_id, uid=photo,
                                      filePaths=[str(image_path)])
            for _ in range(10):
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
                if failure := publish_page_failure(state):
                    return {"state": "blocked", "note": f"{failure}；未提交商品"}
                if "1 photo attached" in state:
                    break
                await asyncio.sleep(0.5)
            else:
                return {"state": "needs_attention", "note": "图片上传未获页面确认"}
            await self._call_unlocked("fill_form", pageId=page_id, elements=[
                {"uid": named_uid(state, "textbox", "Title"), "value": listing["title"]},
                {"uid": named_uid(state, "textbox", "Price"), "value": listing["price"]},
            ])
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "combobox", "Category"))
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "button", listing["category"], contains=True))
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "combobox", "Condition"))
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "option", listing["condition"]))
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if 'textbox "Description"' not in state:
                await self._call_unlocked("click", pageId=page_id,
                                          uid=named_uid(state, "button", "More details", contains=True))
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if listing.get("sku") and 'textbox "SKU"' in state:
                await self._call_unlocked("fill", pageId=page_id,
                                          uid=named_uid(state, "textbox", "SKU"),
                                          value=listing["sku"])
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if 'combobox "Location"' not in state:
                return {"state": "needs_attention", "note": "Facebook 页面未显示地点输入框；未提交"}
            location = named_uid(state, "combobox", "Location")
            await self._call_unlocked("click", pageId=page_id, uid=location)
            await self._call_unlocked("press_key", pageId=page_id, key="Control+A")
            await self._call_unlocked("press_key", pageId=page_id, key="Backspace")
            await self._call_unlocked("type_text", pageId=page_id, text="Los Angeles, CA")
            suggestion = None
            for _ in range(12):
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
                field = location_field_line(state)
                if re.search(r"Iztapalapa|Mexico|México", field, re.I):
                    return {"state": "needs_attention", "note": "地点原值没有被清空；请在 Facebook 页面核对，未提交"}
                suggestion = los_angeles_suggestion_uid(state)
                if suggestion:
                    break
                await asyncio.sleep(0.5)
            if not suggestion:
                return {"state": "needs_attention", "note": "未找到美国加州 Los Angeles 的地点建议；未提交"}
            await self._call_unlocked("click", pageId=page_id, uid=suggestion)
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if "Los Angeles" not in location_field_line(state):
                return {"state": "needs_attention", "note": "已点击加州洛杉矶建议，但地点框未确认选中；未提交"}
            # Fill the description last: later SKU/location updates can rerender the form.
            description_uid = named_uid(state, "textbox", "Description")
            await self._call_unlocked("fill", pageId=page_id,
                                      uid=description_uid, value=listing["description"])
            await asyncio.sleep(0.3)
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if (publish_form_failure(state) or "Description will appear here." in state):
                await self._call_unlocked("fill_form", pageId=page_id, elements=[
                    {"uid": named_uid(state, "textbox", "Description"),
                     "value": listing["description"]},
                ])
                await asyncio.sleep(0.3)
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if failure := publish_page_failure(state):
                return {"state": "blocked", "note": f"{failure}；未提交商品"}
            price_line = next((line for line in state.splitlines() if 'textbox "Price"' in line), "")
            if (listing["title"] not in state or "1 photo attached" not in state or
                    "$" not in price_line or "Los Angeles" not in location_field_line(state)):
                return {"state": "needs_attention", "note": "图片、标题、USD 价格或洛杉矶地点核对失败；未提交"}
            for _ in range(3):
                if 'button "Next" disableable disabled' not in state and 'button "Next"' in state:
                    break
                await asyncio.sleep(0.4)
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if 'button "Next" disableable disabled' in state or 'button "Next"' not in state:
                return {"state": "blocked", "note": f"{publish_form_failure(state) or 'Facebook 的 Next 不可用'}；未提交商品"}
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "button", "Next"))
            state = await self._call_unlocked("take_snapshot", pageId=page_id)
            if failure := publish_page_failure(state):
                return {"state": "blocked", "note": f"{failure}；未点击 Publish"}
            if 'button "Publish"' not in state:
                return {"state": "needs_attention", "note": "下一步页面未显示 Publish；请人工检查，未提交"}
            await self._call_unlocked("click", pageId=page_id,
                                      uid=named_uid(state, "button", "Publish"))
            for _ in range(12):
                pages = pages_from_text(await self._call_unlocked("list_pages"))
                current = next((page for page in pages if page["page_id"] == page_id), None)
                if current and ITEM_URL.fullmatch(current["url"]):
                    self.page_url = current["url"]
                    return {"state": "posted", "note": "页面跳转到商品链接", "url": current["url"]}
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
                if failure := publish_page_failure(state):
                    return {"state": "blocked", "note": f"{failure}；未核验到商品链接"}
                if listing_under_review(state, listing["title"]):
                    self.page_url = current["url"] if current else None
                    return {"state": "posted", "note": "Facebook 销售列表已显示此商品；平台仍在审核"}
                if url := listing_item_url(state, listing["title"]):
                    self.page_url = url
                    return {"state": "posted", "note": "销售列表核验到此商品详情链接", "url": url}
                await asyncio.sleep(0.8)
            return {"state": "uncertain", "note": "已点击 Publish，但未核验到新商品链接；请人工检查，系统不会自动重试"}

    async def verify_listing(self, title: str) -> dict:
        """Recheck a submitted listing in the signed-in seller's visible pages."""
        async with self._lock:
            if self.state != "online" or self.page_id is None:
                raise RuntimeError("Facebook 账号不在线")
            page_id = self.page_id
            pages = pages_from_text(await self._call_unlocked("list_pages"))
            current = next((page for page in pages if page["page_id"] == page_id), None)
            if not current or '/marketplace/you/selling' not in current["url"]:
                await self._call_unlocked("navigate_page", pageId=page_id, type="url", url=SELLING_URL)
            for _ in range(12):
                state = await self._call_unlocked("take_snapshot", pageId=page_id)
                if listing_under_review(state, title):
                    return {"state": "posted", "note": "Facebook 销售列表已显示此商品；平台仍在审核"}
                if url := listing_item_url(state, title):
                    return {"state": "posted", "note": "销售列表核验到此商品详情链接", "url": url}
                await asyncio.sleep(0.5)
            return {"state": "uncertain", "note": "销售列表未核验到该商品；请检查账号和 Facebook 页面"}


gateway = BrowserGateway()
