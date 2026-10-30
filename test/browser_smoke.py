"""Check the generated default demo with real Pyodide and browser filtering.

Run the no-argument demo first, install Playwright/Chromium, then execute:
    python test/browser_smoke.py
BROWSER_EXECUTABLE optionally selects an existing Chromium executable.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        options = {"headless": True}
        if os.environ.get("BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["BROWSER_EXECUTABLE"]
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto((ROOT / "demo/out/paper/index.html").as_uri())
            page.wait_for_selector(".graph-display img", timeout=60000)
            summary = page.locator(".computed-container .table-section").filter(
                has=page.locator(".table-title", has_text="Summary"))
            assert summary.locator("tbody tr").count() == 6
            assert page.locator(".graph-btn").count() == 2
            previous = page.locator(".graph-display img").get_attribute("src")
            summary.locator(".sql-filter-input").fill("algorithm = 'set' AND size = 500")
            page.wait_for_function("""() => Array.from(document.querySelectorAll('.raw-container .table-section'))
                .find(el => el.querySelector('.table-title').textContent === 'measurements')
                .querySelector('.row-count').textContent.includes('3/27')""", timeout=15000)
            page.wait_for_function("old => document.querySelector('.graph-display img').src !== old",
                                   arg=previous, timeout=15000)
            assert not errors, errors
            print("Browser: both graphs, summary, and propagated filtering passed.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
