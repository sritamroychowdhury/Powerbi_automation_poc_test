"""Page Object for Power BI report pages.

All Playwright interaction code (navigation, waits, locators, screenshotting)
lives here — step definitions and scripts call methods on this class instead
of touching `page.*` directly, so a DOM/selector change is a one-file edit.
"""

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


class PowerBIReportPage:
    """Wraps a single Power BI report tab."""

    # Locators — centralised here so DOM changes on Power BI's side only
    # require touching this file, not every step definition that needs them.
    VISUAL_CONTAINER_SELECTOR = "div.visual-container-group, div.visualContainer"
    LOADING_SPINNER_SELECTOR = "div.mat-progress-spinner, div.spinner"
    ERROR_BANNER_XPATH = "//div[contains(@class,'errorBanner') or contains(@class,'visual-error')]"
    VISUAL_TITLE_XPATH = ".//div[contains(@class,'visualTitle')]"

    def __init__(self, page: Page):
        self.page = page

    def open(self, url: str, wait_selector: str | None = None, wait_ms: int = 3000) -> "PowerBIReportPage":
        self.page.goto(url, wait_until="networkidle")
        self.wait_until_ready(wait_selector=wait_selector, wait_ms=wait_ms)
        return self

    def wait_until_ready(self, wait_selector: str | None = None, wait_ms: int = 3000) -> None:
        self.page.wait_for_selector(wait_selector or self.VISUAL_CONTAINER_SELECTOR, timeout=30000)
        try:
            self.page.wait_for_selector(self.LOADING_SPINNER_SELECTOR, state="detached", timeout=30000)
        except PlaywrightTimeoutError:
            pass  # no spinner ever appeared for this report — not an error
        self.page.wait_for_timeout(wait_ms)

    def has_error_banner(self) -> bool:
        return self.page.locator(self.ERROR_BANNER_XPATH).count() > 0

    def visual_titles(self) -> list[str]:
        return self.page.locator(self.VISUAL_TITLE_XPATH).all_inner_texts()

    def screenshot(self, output_path: str) -> str:
        self.page.screenshot(path=output_path, full_page=True)
        return output_path

    def close(self) -> None:
        self.page.close()
