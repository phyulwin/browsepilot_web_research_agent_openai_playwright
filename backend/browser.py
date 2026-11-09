# backend/browser.py — isolated Playwright browser and a fixed read-only action interpreter.
import asyncio
import base64
import logging
import sys
from urllib.parse import parse_qs, quote_plus, urlsplit

from playwright.async_api import async_playwright

from backend.models import Action
from backend.config import settings
from backend.research import SEARCH_URLS, domain, is_search
from backend.safety import EgressProxy, validate_url

logger = logging.getLogger(__name__)


class BrowserStartupError(RuntimeError):
    """Expose only application-authored, actionable browser startup errors."""


class NavigationError(RuntimeError):
    """Carry a sanitized access reason and status without raw browser exception content."""

    def __init__(self, message, url, status=None, restricted=False):
        # Keep operational metadata separate from the safe user-facing message.
        super().__init__(message)
        self.url, self.status, self.restricted = url, status, restricted


class ResearchBrowser:
    """Observe real HTML and execute only the documented browser action vocabulary."""

    def __init__(self):
        # Track resources even during partial startup so cleanup always has ownership.
        self.proxy = EgressProxy()
        self.runtime = None
        self.playwright_manager = None
        self.browser = None
        self.context = None
        self.page = None
        self.links = []
        self.blocked_requests = 0
        self.blocked_domains = set()
        self.usable_pages = set()

    async def start(self):
        # Detect an incompatible host loop before creating the proxy or Playwright transport.
        if sys.platform == "win32" and not isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
            raise BrowserStartupError(
                "Playwright requires a Windows Proactor event loop for subprocesses. "
                "Restart from the project root with .\\.venv\\Scripts\\python.exe -m backend.run; "
                "do not use Uvicorn --reload or multiple workers."
            )
        try:
            await self._start()
        except BaseException as exc:
            # Release partially acquired resources even if startup is cancelled or times out.
            try:
                await self.close()
            except Exception as cleanup_error:
                logger.error("Partial browser cleanup failed (%s)", type(cleanup_error).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, NotImplementedError):
                raise BrowserStartupError(
                    "The server event loop cannot launch the Playwright subprocess. "
                    "On Windows, restart with .\\.venv\\Scripts\\python.exe -m backend.run without --reload."
                ) from exc
            if isinstance(exc, Exception):
                raise BrowserStartupError(
                    f"Browser startup failed ({type(exc).__name__}). Ensure the Playwright Chromium "
                    "browser is installed for the backend environment (python -m playwright install chromium), "
                    "and check host permissions, sandbox support, and browser system dependencies."
                ) from exc
            raise

    async def _start(self):
        # Force all browser traffic through the public-address proxy, including loopback URLs.
        proxy_url = await self.proxy.start()
        self.playwright_manager = async_playwright()
        self.runtime = await self.playwright_manager.start()
        self.browser = await self.runtime.chromium.launch(headless=True, chromium_sandbox=settings.chromium_sandbox, proxy={"server": proxy_url}, args=["--proxy-bypass-list=<-loopback>", "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=False, service_workers="block", permissions=[], ignore_https_errors=False)
        await self.context.route("**/*", self.guard_request)
        await self.context.route_web_socket("**/*", lambda socket: socket.close())
        self.context.on("page", self.guard_popup)
        self.page = await self.context.new_page()
        self.context.set_default_timeout(12000)
        self.context.set_default_navigation_timeout(20000)

    async def guard_request(self, route):
        # Apply URL and method checks to documents, redirects, fetches, and subresources alike.
        try:
            validate_url(route.request.url)
            host = domain(route.request.url)
            if any(host == item or host.endswith("." + item) for item in self.blocked_domains):
                raise ValueError("This host was excluded after an access restriction or repeated failures")
            if route.request.method not in {"GET", "HEAD"} or route.request.resource_type in {"media", "font", "websocket"}:
                raise ValueError("Write requests and unnecessary resource types are disabled")
            await route.continue_()
        except ValueError:
            self.blocked_requests += 1
            await route.abort("blockedbyclient")

    async def guard_popup(self, page):
        # A page cannot gain an uncontrolled popup or interactive dialog channel.
        page.on("dialog", lambda dialog: dialog.dismiss())
        if await page.opener():
            await page.close()

    async def observe(self):
        # Read the rendered DOM and enumerate only genuine navigation anchors.
        if not self.page or self.page.is_closed():
            raise RuntimeError("Active browser page is no longer available")
        result = await self.page.evaluate(r"""() => {
            // Return bounded visible text and public navigation targets from the current DOM.
            const links = [...document.querySelectorAll('a[href]')]
                .filter(a => a.getClientRects().length && a.innerText.trim())
                .slice(0, 60).map((a, id) => ({id, text: a.innerText.trim().slice(0, 100), url: a.href}));
            // Walk visible primary text without menus, scripts, forms, or repeated boilerplate.
            const primary = document.querySelector('main, [role="main"], article') || document.body;
            const walker = document.createTreeWalker(primary || document.documentElement, NodeFilter.SHOW_TEXT);
            const chunks = [], seen = new Set();
            let node, length = 0;
            while ((node = walker.nextNode()) && length < 100000) {
                const parent = node.parentElement;
                if (!parent || !parent.getClientRects().length ||
                    parent.closest('script,style,noscript,svg,nav,footer,button,form,[role="navigation"],[aria-hidden="true"]') ||
                    getComputedStyle(parent).visibility === 'hidden') continue;
                const value = node.textContent.trim();
                if (!value || (value.length > 80 && seen.has(value))) continue;
                chunks.push(value); seen.add(value); length += value.length;
            }
            const text = chunks.join('\n');
            const ratio = window.scrollY / Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
            const start = Math.floor(Math.max(0, text.length - 6000) * Math.min(1, ratio));
            // Gate detection also sees form text that is deliberately excluded from evidence.
            const gate = (document.body?.innerText || '').slice(0, 1600);
            return {title: document.title.slice(0, 180), text: text.slice(start, start + 6000),
                    links, gate, has_more_text: start + 6000 < text.length};
        }""")
        # Unwrap only known public search redirect links, then reapply URL safety validation.
        links = []
        for link in result["links"]:
            try:
                parts = urlsplit(link["url"])
                query = parse_qs(parts.query)
                if parts.hostname in {"duckduckgo.com", "html.duckduckgo.com"} and "uddg" in query:
                    link["url"] = query["uddg"][0]
                elif parts.hostname == "www.bing.com" and parts.path == "/ck/a" and query.get("u", [""])[0].startswith("a1"):
                    encoded = query["u"][0][2:]
                    link["url"] = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
                validate_url(link["url"])
                if is_search(self.page.url) and is_search(link["url"]):
                    continue
                if not any(item["url"] == link["url"] for item in links):
                    links.append(link)
            except (ValueError, UnicodeError):
                continue
        result["links"] = self.links = links[:40]
        gate = result.pop("gate").casefold()
        title = result["title"].casefold()
        issue = None
        if ("/sorry/" in self.page.url and is_search(self.page.url)) or any(
            phrase in gate for phrase in ("verify you are human", "unusual traffic from your computer",
                                         "automated queries", "confirm you are not a robot",
                                         "unfortunately, bots use duckduckgo too", "please complete the following challenge",
                                         "人間であることを確認", "ロボットではないことを確認")):
            issue = "Automated access challenge; use another source without bypassing it"
        elif any(phrase in title for phrase in ("access denied", "just a moment", "captcha", "403 forbidden")):
            issue = "Access denied or browser challenge; use another source"
        return {"url": self.page.url, **result, "usable": self.page in self.usable_pages and not issue,
                "access_issue": issue, "tabs": [{"id": index, "url": page.url} for index, page in enumerate(self.context.pages)],
                "blocked_requests": self.blocked_requests}

    async def navigate(self, url):
        # Navigation retains ordinary TLS validation and revalidates redirected requests.
        validate_url(url)
        self.usable_pages.discard(self.page)
        response = await self.page.goto(url, wait_until="domcontentloaded")
        if response and response.status >= 400:
            raise NavigationError(f"Website returned HTTP {response.status}; choose another page or source",
                                  self.page.url, response.status, response.status in {401, 403, 429})
        if response and not any(kind in response.headers.get("content-type", "") for kind in ("text/html", "text/plain", "application/xhtml")):
            raise ValueError("This version supports HTML and plain text pages only")
        self.usable_pages.add(self.page)

    async def execute(self, action: Action, search_provider=None):
        # Interpret fields explicitly; model output is never evaluated as code or a selector.
        match action.command:
            case "SEARCH":
                if search_provider not in SEARCH_URLS:
                    raise ValueError("A configured search provider is required")
                await self.navigate(SEARCH_URLS[search_provider] + quote_plus(action.query))
            case "NAVIGATE":
                await self.navigate(action.url)
            case "CLICK":
                link = next((link for link in self.links if link["id"] == action.link_id), None)
                if not link:
                    raise ValueError("Link ID was not present in the latest observation")
                await self.navigate(link["url"])
            case "SCROLL":
                await self.page.mouse.wheel(0, 650 if action.direction == "down" else -650)
                await asyncio.sleep(0.35)
            case "NEW_TAB":
                if len(self.context.pages) >= 4:
                    raise ValueError("Maximum of four browser tabs reached")
                self.page = await self.context.new_page()
                if action.url:
                    await self.navigate(action.url)
            case "SWITCH_TAB":
                if action.tab_id is None or not 0 <= action.tab_id < len(self.context.pages):
                    raise ValueError("Unknown tab ID")
                self.page = self.context.pages[action.tab_id]
            case "BACK":
                self.usable_pages.discard(self.page)
                response = await self.page.go_back(wait_until="domcontentloaded")
                if response and response.status < 400:
                    self.usable_pages.add(self.page)
            case "WAIT":
                await asyncio.sleep(action.seconds)
            case "READ_PAGE" | "EXTRACT" | "DONE":
                pass
        observation = await self.observe()
        if observation["access_issue"]:
            raise NavigationError(observation["access_issue"], observation["url"], restricted=True)
        return observation

    async def screenshot(self):
        # Screenshots come directly from Chromium; no simulated browser images are created.
        return await self.page.screenshot(type="jpeg", quality=65, timeout=6000)

    async def close(self):
        # Detach ownership first so startup cleanup and the agent's finally block are idempotent.
        browser, manager = self.browser, self.playwright_manager
        self.browser = self.context = self.page = self.runtime = self.playwright_manager = None
        self.usable_pages.clear()
        try:
            if browser:
                await asyncio.wait_for(browser.close(), timeout=5)
        finally:
            try:
                # Retain the manager even if start failed before returning a runtime handle.
                if manager:
                    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=5)
            finally:
                await self.proxy.close()
