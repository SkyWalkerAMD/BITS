"""Browser-only fixture: credentials remain in memory, never screenshots or artifacts."""
import json
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright

admin = json.loads(subprocess.check_output(["docker", "exec", "bits-independent-test",
    "cat", "/root/.bits/admin.json"]))
out = Path(".independent-results")
with sync_playwright() as p:
    browser = p.chromium.launch()
    # Fixture self-signed certificate; production clients never disable checks.
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1080})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(admin["url"])
    page.locator("#login-token").fill(admin["token"])
    page.locator("#login-form button").click()
    page.locator("#node-list .node-card").wait_for()
    page.locator("#create").click()
    page.locator("#batch-label").fill("BROWSER-DRAFT")
    page.locator("#batch-form button.primary").click()
    page.get_by_text("BROWSER-DRAFT", exact=True).wait_for()
    row = page.locator("#batch-list tr").filter(has_text="BROWSER-DRAFT")
    assert "待开始" in row.inner_text()
    row.get_by_text("详情", exact=True).click()
    page.locator("#detail-title").filter(has_text="BROWSER-DRAFT").wait_for()
    page.screenshot(path=str(out / "dashboard.png"), full_page=True)
    page.set_viewport_size({"width": 430, "height": 932})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page overflows mobile viewport"
    assert page.locator("#batch-list .badge").evaluate_all(
        "items => items.every(item => item.getBoundingClientRect().height < 40)"), "mobile status text became vertical"
    row.get_by_text("开始", exact=True).scroll_into_view_if_needed()
    assert row.get_by_text("开始", exact=True).is_visible()
    page.locator("#batches .table-wrap").evaluate("element => element.scrollLeft = 0")
    page.screenshot(path=str(out / "dashboard-mobile.png"), full_page=True)
    assert not errors, errors
    context.close()
    browser.close()
(out / "browser.json").write_text(json.dumps({"status": "passed", "checks": [
    "login", "node listing", "create draft without dispatch", "batch details", "mobile layout"],
    "certificate": "isolated self-signed fixture only; Go TLS validation tested separately"}))
