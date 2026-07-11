"""Browser layer — a real browser the agent can drive, not just read.

One persistent headless Chromium session per process (lazy-launched on the
first browser tool call, so nothing pays the cost unless the agent browses).
The model interacts through *snapshots*: every action returns the page title,
URL, visible text, and a numbered list of interactive elements. Click/type
target either one of those numbers or a raw Playwright selector.

Playwright is an optional dependency — the rest of the OS works without it.
Install with: pip install playwright && playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

SNAPSHOT_TEXT_LIMIT = 6000
MAX_ELEMENTS = 60

INTERACTIVE_SELECTOR = (
    "a[href], button, input, textarea, select, [role='button'], [role='link'], [onclick]"
)


class BrowserSession:
    """Wraps one Playwright page. All methods return a text snapshot the
    model can act on next iteration."""

    def __init__(self, workspace: Path, timeout: float = 20.0):
        self.workspace = workspace
        self.timeout_ms = int(timeout * 1000)
        self._pw = None
        self._browser = None
        self._page = None
        self._elements: list = []  # handles from the last snapshot, by index

    # -- lifecycle -----------------------------------------------------------

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "browser tools need playwright: "
                "pip install playwright && playwright install chromium"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self._page

    def close(self):
        for obj in (self._browser, self._pw):
            try:
                if obj is not None:
                    obj.stop() if obj is self._pw else obj.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        self._pw = self._browser = self._page = None
        self._elements = []

    # -- snapshot ------------------------------------------------------------

    def snapshot(self) -> str:
        page = self._ensure_page()
        if page.url == "about:blank":
            return "no page loaded yet — call browser_goto first"
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001 — busy pages never go idle; snapshot anyway
            pass

        text = page.inner_text("body").strip()
        if len(text) > SNAPSHOT_TEXT_LIMIT:
            text = text[:SNAPSHOT_TEXT_LIMIT] + "\n…(truncated)"

        self._elements = page.query_selector_all(INTERACTIVE_SELECTOR)[:MAX_ELEMENTS]
        lines = []
        for i, el in enumerate(self._elements):
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                label = (el.inner_text() or "").strip()
                if not label:
                    label = el.get_attribute("placeholder") or el.get_attribute(
                        "aria-label"
                    ) or el.get_attribute("name") or el.get_attribute("value") or ""
                if tag == "input":
                    tag = f"input:{el.get_attribute('type') or 'text'}"
                href = el.get_attribute("href") if tag == "a" else None
                extra = f" -> {href}" if href else ""
                lines.append(f"  [{i}] <{tag}> {label[:80]!r}{extra}")
            except Exception:  # noqa: BLE001 — element detached mid-walk
                lines.append(f"  [{i}] (stale element)")

        elements = "\n".join(lines) if lines else "  (none)"
        return (
            f"page: {page.title()}\nurl: {page.url}\n\n"
            f"visible text:\n{text}\n\n"
            f"interactive elements (use the [n] number with browser_click / browser_type):\n"
            f"{elements}"
        )

    # -- actions -------------------------------------------------------------

    def goto(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"only http(s) URLs are allowed: {url}")
        page = self._ensure_page()
        page.goto(url, wait_until="domcontentloaded")
        return self.snapshot()

    def _locate(self, target: str | int):
        """An element number from the last snapshot, or a Playwright selector."""
        if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
            idx = int(target)
            if not 0 <= idx < len(self._elements):
                raise ValueError(
                    f"element [{idx}] is not in the last snapshot "
                    f"(0..{len(self._elements) - 1}) — call browser_read to refresh"
                )
            return self._elements[idx]
        return self._ensure_page().locator(target).first

    def click(self, target: str | int) -> str:
        self._ensure_page()
        self._locate(target).click()
        return self.snapshot()

    def type_text(self, target: str | int, text: str, press_enter: bool = False) -> str:
        self._ensure_page()
        el = self._locate(target)
        el.fill(text)
        if press_enter:
            el.press("Enter")
        return self.snapshot()

    def screenshot(self, filename: str = "screenshot.png") -> str:
        page = self._ensure_page()
        target = (self.workspace / filename).resolve()
        if not target.is_relative_to(self.workspace.resolve()):
            raise ValueError(f"path escapes workspace: {filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True)
        return f"saved screenshot of {page.url} to {target}"
