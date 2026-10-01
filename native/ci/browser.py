"""Browser acceptance on the isolated Linux server; never export credentials."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from playwright.sync_api import sync_playwright, expect

assert os.environ.get("GITHUB_ACTIONS") == "true"
admin = json.loads(subprocess.check_output(["docker", "exec", "bits-independent-test",
    "cat", "/root/.bits/admin.json"]))
out = Path(".independent-results")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1080})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def api(path):
        response = context.request.get(admin["url"] + "/api/v1/" + path)
        assert response.ok, response.status
        return response.json()

    def wait_batch(batch_id, state):
        for _ in range(90):
            value = api("batches/" + batch_id)
            if value["state"] == state:
                return value
            page.wait_for_timeout(1000)
        raise AssertionError(value)

    def wait_live(minimum):
        # Observe rendered state without eval-based polling. Production CSP
        # intentionally refuses unsafe-eval and must stay enforced here.
        for _ in range(90):
            sequence = int(page.locator("#detail-body").get_attribute("data-live-sequence") or 0)
            if sequence >= minimum:
                return sequence
            page.wait_for_timeout(500)
        raise AssertionError("live DOM sequence did not advance")

    try:
        page.goto(admin["url"])
        page.locator("#login-token").fill(admin["token"])
        page.locator("#login-form button").click()
        page.locator("#overview-node-list .node-card").wait_for()
        checks.append("authenticated operator login")
        # Real enrollment, credential download stays only in Playwright temporary storage.
        for name in ("LAB-010", "LAB-002"):
            page.locator("#enroll").click()
            page.locator("#node-id").fill(name)
            page.locator("#node-serial").fill("SYNTHETIC-" + name)
            with page.expect_download() as download:
                page.locator("#node-form button.primary").click()
            assert download.value.suggested_filename == name + ".bits.json"
            download.value.delete()
            expect(page.locator("#view-guide")).to_be_visible()
        checks.append("enroll node and download one-time connection file")
        page.locator('a[data-nav="nodes"]').click()
        page.locator("#node-list .node-card").first.wait_for()
        expect(page.locator("#node-list .node-name strong")).to_have_text(["BITS-CLOUD", "LAB-002", "LAB-010"])
        page.locator("#node-search").fill("LAB-002")
        expect(page.locator("#node-list .node-card")).to_have_count(1)
        page.locator("#node-search").fill("")
        checks.append("node search and natural numeric ordering")
        page.locator("#create").click()
        page.locator("#batch-node").select_option("BITS-CLOUD")
        page.locator("#batch-label").fill("BROWSER-LIVE")
        page.locator("#batch-next").click()
        for box in page.locator("#steps input").all():
            box.fill("180")
        page.locator("#batch-next").click()
        expect(page.locator("#batch-review")).to_contain_text("stress-ng")
        page.screenshot(path=str(out / "batch-wizard.png"))
        page.locator("#batch-submit").click()
        expect(page.locator("#detail-title")).to_have_text("BROWSER-LIVE")
        batch_id = page.url.split("batch/")[-1]
        page.wait_for_timeout(2500)
        assert api("batches/" + batch_id)["state"] == "draft"
        checks.append("three-stage wizard saves draft without dispatch")
        page.locator("#detail-body").get_by_role("button", name="开始压测", exact=True).click()
        page.locator("#confirm-submit").click()
        initial_sequence = wait_live(4)
        initial_elapsed = float(page.locator("#detail-body").get_attribute("data-elapsed"))
        wait_live(initial_sequence + 1)
        assert float(page.locator("#detail-body").get_attribute("data-elapsed")) > initial_elapsed
        expect(page.locator("#detail-body")).to_contain_text("整机 PSU 输入")
        page.screenshot(path=str(out / "batch-running.png"), full_page=True)
        checks.append("explicit start, advancing live samples and actual step elapsed")
        page.locator('a[data-nav="overview"]').click()
        page.locator("#overview-node-list .node-card.running").wait_for()
        expect(page.locator("#overview-node-list .node-card.running")).to_contain_text("108.0")
        page.screenshot(path=str(out / "dashboard.png"), full_page=True)
        page.locator("#overview-node-list .node-card.running").get_by_role("button", name="实时监控 ↗").click()
        expect(page.locator("#monitor-title")).to_have_text("BITS-CLOUD")
        expect(page.locator("#monitor-status")).to_have_text("实时采集中")
        expect(page.locator(".core-tile")).to_have_count(24)
        assert page.locator(".core-tile").evaluate_all("xs => xs.map(x => Number(x.dataset.cpu))") == list(range(24))
        expect(page.locator(".socket-card")).to_contain_text("w7-2495X")
        expect(page.locator(".socket-card")).to_contain_text("1.83 V")
        assert "hardware" not in api("live")["frames"][0]["sample"]
        detailed = api("batches/" + batch_id + "/live")["frames"][0]
        assert len(detailed["sample"]["hardware"]["cores"]) == 24
        assert all("hardware" not in h for h in detailed["history"])
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(out / "hardware-monitor.png"), full_page=True)
        page.get_by_role("button", name="详细表格", exact=True).click()
        expect(page.locator(".core-table tbody tr")).to_have_count(24)
        assert page.locator(".core-table tbody tr").evaluate_all("xs => xs.map(x => Number(x.dataset.cpu))") == list(range(24))
        expect(page.locator(".core-table tbody tr").first).to_contain_text("0.9100")
        expect(page.locator(".core-table tbody tr").first).to_contain_text("未提供")
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(out / "hardware-table.png"), full_page=True)
        page.locator(".core-table-scroll").evaluate("x => { x.scrollTop = 200; }")
        page.wait_for_timeout(3500)
        assert page.locator(".core-table-scroll").evaluate("x => x.scrollTop") >= 190
        checks.append("live core table retains its scroll position during refresh")
        checks.append("node card opens typed socket/core monitor, numeric order and detail-only arrays")
        page.get_by_role("button", name="核心矩阵", exact=True).click()
        page.set_viewport_size({"width":430, "height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "hardware mobile overflow"
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(out / "hardware-mobile.png"), full_page=True)
        page.get_by_role("button", name="详细表格", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "core table mobile overflow"
        checks.append("responsive hardware matrix and internally scrollable core table")
        page.set_viewport_size({"width":1440, "height":1080})
        # UI-only fixture for large multi-socket topology and missing metrics.
        # Production wire topology is validated by Go tests separately.
        def topology(route):
            response = route.fetch()
            value = response.json()
            for frame in value["frames"]:
                sample = frame["sample"]
                h = sample["hardware"]
                socket = dict(h["sockets"][0]); socket["id"] = 1
                h["sockets"].append(socket)
                original = h["cores"][0]
                h["cores"] = [dict(original, cpu=i, socket=0 if i < 70 else 1, vid_v=None) for i in reversed(range(140))]
                sample["extra_observed_at"] = "2020-01-01T00:00:00Z"
            route.fulfill(response=response, json=value)
        live_path = "**/api/v1/batches/" + batch_id + "/live"
        page.route(live_path, topology)
        expect(page.locator(".socket-card")).to_have_count(2, timeout=12000)
        expect(page.locator(".core-table tbody tr")).to_have_count(64)
        expect(page.locator(".core-table tbody tr").first.locator("td").nth(4)).to_have_text("—")
        page.get_by_role("button", name="下一页", exact=True).click()
        assert page.locator(".core-table tbody tr").first.get_attribute("data-cpu") == "64"
        page.locator(".core-filters").get_by_role("button", name="S1", exact=True).click()
        assert page.locator(".core-table tbody tr").first.get_attribute("data-cpu") == "70"
        expect(page.locator("#monitor-status")).to_have_text("实时采集中")
        expect(page.locator(".monitor-extra-age.stale")).to_be_visible()
        page.unroute(live_path, topology)
        expect(page.locator(".socket-card")).to_have_count(1, timeout=12000)
        expect(page.locator(".core-table tbody tr")).to_have_count(24)
        checks.append("large multi-socket UI pagination, missing values and independent supplemental freshness")
        context.set_offline(True)
        expect(page.locator("#monitor-status")).to_contain_text("连接中断", timeout=15000)
        context.set_offline(False)
        expect(page.locator("#monitor-status")).to_have_text("实时采集中", timeout=15000)
        checks.append("hardware monitor disconnect preserves clearly marked last readings")
        page.locator('a[data-nav="overview"]').click()
        page.set_viewport_size({"width":430, "height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile page overflows"
        expect(page.locator("#logout")).to_be_visible()
        page.screenshot(path=str(out / "dashboard-mobile.png"), full_page=True)
        page.locator('a[data-nav="batches"]').click()
        assert page.locator("#batch-list .badge").evaluate_all("xs => xs.every(x => x.getBoundingClientRect().height < 40)")
        checks.append("desktop/mobile overview, readable table and visible logout")
        # Loss of browser connection is a distinct state, never a green live badge.
        context.set_offline(True)
        expect(page.locator("#connection")).to_contain_text("连接中断", timeout=15000)
        context.set_offline(False)
        expect(page.locator("#connection")).to_contain_text("实时同步", timeout=15000)
        checks.append("browser disconnect and recovery feedback")
        page.set_viewport_size({"width":1440, "height":1080})
        page.goto(admin["url"] + "/#batch/" + batch_id)
        wait_live(1)
        # A fresh status pulse must not disguise a stale measurement.
        def stale(route):
            response = route.fetch()
            value = response.json()
            for f in value["frames"]:
                f["sample_received_at"] = "2020-01-01T00:00:00Z"
                if f.get("sample"):
                    f["sample"]["observed_at"] = "2020-01-01T00:00:00Z"
            route.fulfill(response=response, json=value)
        page.route("**/api/v1/**/live", stale)
        expect(page.locator(".freshness.stale")).to_contain_text("数据已过期", timeout=12000)
        page.unroute("**/api/v1/**/live", stale)
        expect(page.locator(".freshness").first).to_contain_text("采样接收", timeout=12000)
        checks.append("stale sample distinct from online heartbeat")
        page.locator("#detail-body").get_by_role("button", name="取消压测", exact=True).click()
        page.locator("#confirm-reason").fill("Isolated browser cancellation acceptance")
        page.locator("#confirm-submit").click()
        final = wait_batch(batch_id, "delivered")
        assert final["result"]["execution"] == "interrupted", final
        assert final["result"]["steps"][0]["cleanup_confirmed"]
        page.locator("#detail-body").get_by_role("button", name="硬件实时监控", exact=True).click()
        expect(page.locator("#monitor-status")).to_have_text("采集已结束 · 最后读数", timeout=15000)
        checks.append("terminal batch monitor never presents its cached reading as live")
        response = context.request.get(admin["url"] + "/api/v1/batches/" + batch_id + "/receipt")
        assert response.ok and hashlib.sha256(response.body()).hexdigest() == final["receipt_sha256"]
        page.locator('a[data-nav="reports"]').click()
        page.locator("#report-search").fill("BROWSER-LIVE")
        expect(page.locator("#report-list .report-card")).to_have_count(1, timeout=15000)
        report = page.locator("#report-list").get_by_role("link", name="查看 HTML ↗")
        assert context.request.get(admin["url"] + report.get_attribute("href")).ok
        checks.append("confirmed cancellation, cleanup, report access and receipt hash")
        page.locator("#report-list").get_by_role("button", name="全部文件").click()
        page.locator("#detail-body").get_by_role("button", name="复制计划为新草稿").click()
        expect(page.locator("#batch-label")).to_have_value("BROWSER-LIVE-COPY")
        page.locator("#batch-next").click()
        page.locator("#batch-next").click()
        page.locator("#batch-submit").click()
        expect(page.locator("#detail-title")).to_have_text("BROWSER-LIVE-COPY")
        copy_id = page.url.split("batch/")[-1]
        assert api("batches/" + copy_id)["state"] == "draft"
        checks.append("copy plan preserves explicit start boundary")
        page.locator("#logout").click()
        expect(page.locator("#login-dialog")).to_be_visible()
        assert not errors, errors
        checks.append("operator logout and no browser script errors")
    except BaseException:
        # Never screenshot an entered credential or connection-file contents.
        if not page.locator("#login-dialog").is_visible():
            page.screenshot(path=str(out / "browser-failure.png"), full_page=True)
        raise
    finally:
        context.close()
        browser.close()
(out / "browser.json").write_text(json.dumps({"status":"passed", "checks":checks,
    "certificate":"isolated self-signed fixture only; Go TLS validation tested separately",
    "readings":"synthetic fixed provider; actual Linux stress and systemd services"}, indent=2))
