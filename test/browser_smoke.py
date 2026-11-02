"""Check the generated default demo with real Pyodide and browser filtering.

Run the no-argument demo first, install Playwright/Chromium, then execute:
    python test/browser_smoke.py
BROWSER_EXECUTABLE optionally selects an existing Chromium executable.
"""
import os
from pathlib import Path
import tempfile

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
            check_messages(page)
            assert not errors, errors
        finally:
            browser.close()


def check_messages(page):
    import diagnostics_report
    from eval_live.render import write_html

    data = {"measurements": [{"run_id": "a", "status": "timed-out", "value": None},
                              {"run_id": "b", "status": "success", "value": 2}]}
    selections = {output.id: {"data": ["a", "b"]} for output in diagnostics_report.reg._outputs}
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "messages.html"
        write_html(path, data, diagnostics_report.reg._outputs, selections,
                   source=Path(diagnostics_report.__file__), title="Computed diagnostics")
        page.goto(path.as_uri())
        warnings = page.locator(".computed-container .table-section").filter(
            has=page.locator(".table-title", has_text="Warnings"))
        page.wait_for_selector(".graph-display img", timeout=60000)
        warnings.wait_for(timeout=15000)
        assert warnings.locator("tbody tr").count() == 1
        assert page.locator(".raw-container .table-title", has_text="Warnings").count() == 0
        measurements = page.locator(".raw-container .table-section").filter(
            has=page.locator(".table-title", has_text="measurements"))
        measurements.locator(".sql-filter-input").fill("status = 'success'")
        warnings.wait_for(state="detached", timeout=15000)
        page.get_by_text("Clear all filters", exact=True).click()
        warnings.wait_for(timeout=15000)
        assert warnings.locator("tbody tr").count() == 1
        assert set(data) == {"measurements"}
        print("Browser: computed warnings deduplicate, update with filtering, and leave raw data unchanged.")


if __name__ == "__main__":
    main()
