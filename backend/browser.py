# backend/browser.py — isolated Playwright browser and a fixed read-only action interpreter.
import asyncio
import logging
import sys
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from backend.models import Action
from backend.safety import EgressProxy, validate_url

logger = logging.getLogger(__name__)


class BrowserStartupError(RuntimeError):
    """Expose only application-authored, actionable browser startup errors."""


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
        self.browser = await self.runtime.chromium.launch(headless=True, chromium_sandbox=True, proxy={"server": proxy_url}, args=["--proxy-bypass-list=<-loopback>", "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
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
        result = await self.page.evaluate("""() => {
            // Return bounded visible text and public navigation targets from the current DOM.
            const links = [...document.querySelectorAll('a[href]')]
                .filter(a => a.getClientRects().length && a.innerText.trim())
                .slice(0, 90).map((a, id) => ({id, text: a.innerText.trim().slice(0, 140), url: a.href}));
            // Prefer primary content and shift the text window as long pages are scrolled.
            const primary = document.querySelector('main, [role="main"], article') || document.body;
            const text = primary?.innerText?.trim() ? primary.innerText : (document.body?.innerText || '');
            const ratio = window.scrollY / Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
            const start = Math.floor(Math.max(0, text.length - 14000) * Math.min(1, ratio));
            return {title: document.title, text: text.slice(start, start + 14000), links};
        }""")
        self.links = result["links"]
        return {"url": self.page.url, **result, "tabs": [{"id": index, "url": page.url} for index, page in enumerate(self.context.pages)], "blocked_requests": self.blocked_requests}

    async def navigate(self, url):
        # Navigation retains ordinary TLS validation and revalidates redirected requests.
        validate_url(url)
        response = await self.page.goto(url, wait_until="domcontentloaded")
        if response and response.status >= 400:
            raise RuntimeError(f"Website returned HTTP {response.status}")
        if response and not any(kind in response.headers.get("content-type", "") for kind in ("text/html", "text/plain", "application/xhtml")):
            raise ValueError("This version supports HTML and plain text pages only")

    async def execute(self, action: Action):
        # Interpret fields explicitly; model output is never evaluated as code or a selector.
        match action.command:
            case "SEARCH":
                await self.navigate("https://www.google.com/search?q=" + quote_plus(action.query))
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
                await self.page.go_back(wait_until="domcontentloaded")
            case "WAIT":
                await asyncio.sleep(action.seconds)
            case "READ_PAGE" | "EXTRACT" | "DONE":
                pass
        return await self.observe()

    async def screenshot(self):
        # Screenshots come directly from Chromium; no simulated browser images are created.
        return await self.page.screenshot(type="jpeg", quality=65, timeout=6000)

    async def close(self):
        # Detach ownership first so startup cleanup and the agent's finally block are idempotent.
        browser, manager = self.browser, self.playwright_manager
        self.browser = self.context = self.page = self.runtime = self.playwright_manager = None
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
