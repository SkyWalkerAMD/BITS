"""Browser acceptance on the isolated Linux server; never export credentials."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from playwright.sync_api import sync_playwright, expect
from browser_remote import verify_remote

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

    def navigate(name):
        target = page.locator('a[data-nav="' + name + '"]')
        # A viewport resize returns before matchMedia's change callback moves
        # the sidebar. Wait for the target layout, not an instantaneous probe.
        if page.viewport_size["width"] <= 830:
            expect(page.locator("#navigation-toggle")).to_have_attribute("aria-label", "打开导航")
            if not page.locator("#navigation-dialog").is_visible():
                page.locator("#navigation-toggle").click()
            expect(page.locator("#navigation-dialog")).to_be_visible()
        else:
            expect(page.locator("body > #main-navigation")).to_be_visible()
        expect(target).to_be_visible()
        target.click()

    try:
        page.goto(admin["url"])
        page.locator("#login-token").fill(admin["token"])
        page.locator("#login-form button").click()
        page.locator("#overview-node-list .node-card").wait_for()
        checks.append("authenticated operator login")
        # No running batch is needed to open a node and receive new readings.
        batch_count = len(api("overview")["batches"])
        page.locator("#overview-node-list .node-card").get_by_role("button", name="实时监控 ↗").click()
        expect(page.locator("#monitor-title")).to_have_text("BITS-CLOUD")
        expect(page.locator(".monitor-subtitle")).to_have_text("日常硬件监控")
        expect(page.locator("#monitor-status")).to_have_text("实时采集中", timeout=20000)
        expect(page.locator(".core-tile")).to_have_count(24)
        initial_idle_sequence = int(page.locator("#monitor-body").get_attribute("data-live-sequence"))
        for _ in range(30):
            if int(page.locator("#monitor-body").get_attribute("data-live-sequence")) > initial_idle_sequence:
                break
            page.wait_for_timeout(500)
        else:
            raise AssertionError("idle monitor samples did not advance")
        assert len(api("overview")["batches"]) == batch_count
        page.screenshot(path=str(out / "hardware-idle.png"), full_page=True)
        page.set_viewport_size({"width":430, "height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "idle mobile overflow"
        page.screenshot(path=str(out / "hardware-idle-mobile.png"), full_page=True)
        page.set_viewport_size({"width":1440, "height":1080})
        navigate("overview")
        checks.append("idle node monitor advances without creating a batch, on desktop and mobile")
        page.locator("#overview-node-list .node-card").get_by_role("button", name="硬件信息 ↗").click()
        expect(page.locator("#hardware-info-title")).to_have_text("BITS-CLOUD")
        expect(page.locator("#hardware-info-status")).to_have_text("已更新", timeout=30000)
        expect(page.locator('.info-section[data-section="CPU"]')).to_contain_text("w7-2495X")
        expect(page.locator(".info-memory-table tbody tr")).to_have_count(4)
        expect(page.locator('.info-section[data-section="Memory Timings"]')).to_contain_text("tCWL")
        assert "tRFC" not in page.locator("#hardware-info-body").inner_text()
        assert len(api("overview")["batches"]) == batch_count
        page.screenshot(path=str(out / "hardware-info.png"), full_page=True)
        page.locator("#hardware-info-search").fill("HMCG94")
        expect(page.locator(".info-section")).to_have_count(1)
        expect(page.locator("#hardware-info-search")).to_be_focused()
        page.wait_for_timeout(2200)
        expect(page.locator("#hardware-info-search")).to_have_value("HMCG94")
        page.locator("#hardware-info-search").fill("")
        page.get_by_role("button", name="内存主时序", exact=True).click()
        expect(page.locator(".info-section")).to_have_count(1)
        page.locator(".info-navigation").get_by_role("button", name="全部", exact=True).click()
        page.set_viewport_size({"width":430,"height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "hardware info mobile overflow"
        page.screenshot(path=str(out / "hardware-info-mobile.png"), full_page=True)
        page.set_viewport_size({"width":1440,"height":1080})
        saved_info = api("nodes/BITS-CLOUD/hardware-info")
        def deny_info(route):
            value = dict(saved_info, status="unavailable")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(value))
        page.route("**/api/v1/nodes/BITS-CLOUD/hardware-info", deny_info)
        page.locator("#refresh").click()
        expect(page.locator("#hardware-info-status")).to_have_text("采集不可用 · 上次信息")
        expect(page.locator('.info-section[data-section="CPU"]')).to_contain_text("w7-2495X")
        page.unroute("**/api/v1/nodes/BITS-CLOUD/hardware-info", deny_info)
        page.locator("#refresh").click()
        expect(page.locator("#hardware-info-status")).to_have_text("已更新")
        page.reload()
        expect(page.locator("#hardware-info-title")).to_have_text("BITS-CLOUD")
        navigate("overview")
        checks.append("hardware info: licensed projection, searchable sections, Primary-only timings, desktop/mobile and failed-read historical display")
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
        navigate("nodes")
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
        navigate("overview")
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
        page.locator(".monitor-section-nav").get_by_role("button", name="逐核心", exact=True).click()
        assert page.evaluate("window.scrollY") > 300
        sidebar = page.locator("#main-navigation").bounding_box()
        topbar = page.locator(".topbar").bounding_box()
        assert sidebar["y"] == 0 and sidebar["height"] == page.viewport_size["height"]
        assert topbar["y"] == 0
        expect(page.locator('a[data-nav="guide"]')).to_be_in_viewport()
        expect(page.locator("#back-to-top")).to_be_visible()
        page.screenshot(path=str(out / "hardware-scrolled.png"))
        page.locator("#navigation-toggle").click()
        assert page.locator("#main-navigation").bounding_box()["width"] == 80
        page.reload()
        expect(page.locator("#monitor-title")).to_have_text("BITS-CLOUD")
        assert page.locator("#main-navigation").bounding_box()["width"] == 80
        page.screenshot(path=str(out / "workspace-compact.png"))
        page.locator("#navigation-toggle").click()
        assert page.locator("#main-navigation").bounding_box()["width"] > 180
        checks.append("desktop sidebar/topbar remain in the viewport after scrolling; compact navigation persists")
        page.locator(".skip-link").focus()
        page.keyboard.press("Enter")
        assert page.url.endswith("monitor-node/BITS-CLOUD")
        expect(page.locator("#workspace")).to_be_focused()
        checks.append("keyboard skip link keeps the current route and focuses the workspace")
        page.get_by_role("button", name="详细表格", exact=True).click()
        expect(page.locator(".core-table tbody tr")).to_have_count(24)
        assert page.locator(".core-table tbody tr").evaluate_all("xs => xs.map(x => Number(x.dataset.cpu))") == list(range(24))
        expect(page.locator(".core-table tbody tr").first).to_contain_text("0.9100")
        expect(page.locator(".core-table tbody tr").first).to_contain_text("未提供")
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(out / "hardware-table.png"), full_page=True)
        core_scroll_before = page.locator(".core-table-scroll").evaluate(
            "x => { x.focus({preventScroll:true}); x.scrollTop = 200; return x.scrollTop; }")
        assert core_scroll_before >= 190, {"initial_scroll": core_scroll_before}
        page.wait_for_timeout(3500)
        core_scroll_after = page.locator(".core-table-scroll").evaluate("x => x.scrollTop")
        assert abs(core_scroll_after-core_scroll_before) <= 1, {"before":core_scroll_before,"after":core_scroll_after}
        expect(page.locator(".core-table-scroll")).to_be_focused()
        page.locator("#refresh").click()
        expect(page.locator("#refresh")).to_be_enabled(timeout=12000)
        assert abs(page.locator(".core-table-scroll").evaluate("x => x.scrollTop")-core_scroll_before) <= 1
        checks.append("live core table retains its scroll position during refresh")
        checks.append("node card opens typed socket/core monitor, numeric order and detail-only arrays")
        page.get_by_role("button", name="核心矩阵", exact=True).click()
        page.set_viewport_size({"width":430, "height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "hardware mobile overflow"
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(out / "hardware-mobile.png"), full_page=True)
        page.locator("#navigation-toggle").click()
        expect(page.locator("#navigation-dialog")).to_be_visible()
        expect(page.locator('a[data-nav="guide"]')).to_be_in_viewport()
        page.screenshot(path=str(out / "navigation-mobile.png"))
        page.keyboard.press("Escape")
        expect(page.locator("#navigation-dialog")).not_to_be_visible()
        expect(page.locator("#navigation-toggle")).to_be_focused()
        # Both directions stay within the drawer, including at its last link.
        page.locator("#navigation-toggle").click()
        for _ in range(9):
            page.keyboard.press("Tab")
            assert page.evaluate("document.getElementById('navigation-dialog').contains(document.activeElement)")
        for _ in range(9):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate("document.getElementById('navigation-dialog').contains(document.activeElement)")
        page.locator("#navigation-close").click()
        checks.append("mobile drawer retains every navigation label, traps focus and closes with Escape")
        page.get_by_role("button", name="详细表格", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "core table mobile overflow"
        checks.append("responsive hardware matrix and internally scrollable core table")
        page.set_viewport_size({"width":1440, "height":1080})
        page.locator(".monitor-section-nav").get_by_role("button", name="逐核心", exact=True).click()
        old_scroll = page.evaluate("window.scrollY")
        navigate("batches")
        page.go_back()
        expect(page.locator("#monitor-title")).to_have_text("BITS-CLOUD")
        page.wait_for_timeout(2500)
        assert abs(page.evaluate("window.scrollY") - old_scroll) < 30
        checks.append("back navigation restores hardware reading position after polling")
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
                stamp = datetime.now(timezone.utc)
                frame["history"] = [{"sequence":i + 1, "available":True,
                    "observed_at":(stamp - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
                    "temp_c":30 + i, "package_w":100 + i}
                    for i, seconds in enumerate((18, 4, 2))]
            route.fulfill(response=response, json=value)
        live_path = "**/api/v1/nodes/BITS-CLOUD/live"
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
        chart = page.locator(".monitor-trends .trend-chart").first
        expect(chart.locator(".trend-label")).to_have_count(3)
        assert chart.locator("path").get_attribute("d").count("M") == 2
        checks.append("trend axes display measured ranges and leave a gap across missing sample time")
        page.unroute(live_path, topology)
        expect(page.locator(".socket-card")).to_have_count(1, timeout=12000)
        expect(page.locator(".core-table tbody tr")).to_have_count(24)
        checks.append("large multi-socket UI pagination, missing values and independent supplemental freshness")
        context.set_offline(True)
        expect(page.locator("#monitor-status")).to_contain_text("连接中断", timeout=15000)
        context.set_offline(False)
        expect(page.locator("#monitor-status")).to_have_text("实时采集中", timeout=15000)
        checks.append("hardware monitor disconnect preserves clearly marked last readings")
        # Viewport evidence, rather than a full-page stitch, proves fixed navigation.
        page.set_viewport_size({"width":1024, "height":300})
        page.locator('a[data-nav="guide"]').scroll_into_view_if_needed()
        expect(page.locator('a[data-nav="guide"]')).to_be_in_viewport()
        assert page.locator("#main-navigation").bounding_box()["y"] == 0
        page.set_viewport_size({"width":1440, "height":1080})
        checks.append("short desktop viewport keeps navigation reachable through its own scroll area")
        navigate("overview")
        page.set_viewport_size({"width":430, "height":932})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile page overflows"
        expect(page.locator("#logout")).to_be_visible()
        page.screenshot(path=str(out / "dashboard-mobile.png"), full_page=True)
        navigate("batches")
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
        ended_sample = api("batches/" + batch_id + "/live")["frames"][0]["sample"]
        page.get_by_role("button", name="节点当前状态 ↗", exact=True).click()
        expect(page.locator("#monitor-status")).to_have_text("实时采集中", timeout=20000)
        expect(page.locator(".monitor-subtitle")).to_have_text("日常硬件监控")
        assert api("batches/" + batch_id + "/live")["frames"][0]["sample"] == ended_sample
        def node_offline(route):
            response = route.fetch()
            value = response.json()
            for node in value["nodes"]:
                if node["id"] == "BITS-CLOUD":
                    node["last_seen"] = "2020-01-01T00:00:00Z"
            route.fulfill(response=response, json=value)
        page.route("**/api/v1/overview", node_offline)
        expect(page.locator("#monitor-status")).to_have_text("系统未连接 · 最后读数", timeout=15000)
        expect(page.locator("#monitor-body")).to_have_class("monitor-history")
        page.unroute("**/api/v1/overview", node_offline)
        expect(page.locator("#monitor-status")).to_have_text("实时采集中", timeout=15000)
        checks.append("completed batch stays sealed while node monitoring resumes; disconnected node cannot show live readings")
        response = context.request.get(admin["url"] + "/api/v1/batches/" + batch_id + "/receipt")
        assert response.ok and hashlib.sha256(response.body()).hexdigest() == final["receipt_sha256"]
        navigate("reports")
        page.locator("#report-search").fill("BROWSER-LIVE")
        expect(page.locator("#report-list .report-card")).to_have_count(1, timeout=15000)
        report = page.locator("#report-list").get_by_role("link", name="查看 HTML ↗")
        assert context.request.get(admin["url"] + report.get_attribute("href")).ok
        checks.append("confirmed cancellation, cleanup, report access and receipt hash")
        def archives(route):
            response = route.fetch()
            value = response.json()
            template = next(b for b in value["batches"] if b["id"] == batch_id)
            value["batches"] = []
            for i in range(61):
                row = json.loads(json.dumps(template))
                row["id"] = "{:032x}".format(10000 + i)
                row["plan"]["label"] = "ARCHIVE-{:03d}".format(i)
                value["batches"].append(row)
            route.fulfill(response=response, json=value)
        page.route("**/api/v1/overview", archives)
        navigate("batches")
        expect(page.locator("#batch-list tr")).to_have_count(25, timeout=15000)
        page.locator("#batch-pagination").get_by_role("button", name="下一页 →").click()
        expect(page.locator("#batch-list tr").first).to_contain_text("ARCHIVE-025")
        page.get_by_role("checkbox", name="选择批次 ARCHIVE-025", exact=True).check()
        page.wait_for_timeout(2500)
        expect(page.locator("#batch-list tr").first).to_contain_text("ARCHIVE-025")
        page.locator("#batch-search").fill("ARCHIVE-060")
        expect(page.locator("#batch-list tr")).to_have_count(1)
        expect(page.locator("#batches-delete-toolbar")).to_contain_text("已选 1 项（其他页 1 项）")
        page.locator("#batches-delete-toolbar").get_by_role("button", name="清空选择").click()
        navigate("reports")
        page.locator("#report-search").fill("")
        expect(page.locator("#report-list .report-card")).to_have_count(18)
        page.locator("#report-pagination").get_by_role("button", name="下一页 →").click()
        expect(page.locator("#report-list .report-card").first).to_contain_text("ARCHIVE-018")
        page.locator("#report-search").fill("ARCHIVE-060")
        expect(page.locator("#report-list .report-card")).to_have_count(1)
        for width in (320, 768, 820, 1280):
            page.set_viewport_size({"width":width,"height":800})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            expect(page.locator("#logout")).to_be_in_viewport()
        page.set_viewport_size({"width":1440,"height":1080})
        page.unroute("**/api/v1/overview", archives)
        page.locator("#report-search").fill("BROWSER-LIVE")
        page.locator("#refresh").click()
        expect(page.locator("#report-list .report-card")).to_have_count(1, timeout=15000)
        checks.append("batch/report pagination, filter reset and polling position retention with 61 simulated records")
        checks.append("320px to desktop layouts retain visible account controls without page overflow")
        page.locator("#report-list").get_by_role("button", name="全部文件").click()
        page.locator("#detail-body").get_by_role("button", name="复制计划为新草稿").click()
        expect(page.locator("#batch-label")).to_have_value("BROWSER-LIVE-COPY")
        page.locator("#batch-next").click()
        page.locator("#batch-next").click()
        pending_posts = []
        def hold_post(route):
            if route.request.method == "POST":
                pending_posts.append(route)
            else:
                route.continue_()
        page.route("**/api/v1/batches", hold_post)
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/v1/batches")):
            page.locator("#batch-submit").click()
        expect(page.locator("#batch-form")).to_have_attribute("aria-busy", "true")
        expect(page.locator('#batch-dialog button[data-close]').first).to_be_disabled()
        page.keyboard.press("Escape")
        expect(page.locator("#batch-dialog")).to_be_visible()
        page.locator("#batch-form").dispatch_event("submit")
        assert len(pending_posts) == 1
        pending_posts[0].continue_()
        page.unroute("**/api/v1/batches", hold_post)
        expect(page.locator("#detail-title")).to_have_text("BROWSER-LIVE-COPY")
        copy_id = page.url.split("batch/")[-1]
        assert api("batches/" + copy_id)["state"] == "draft"
        checks.append("copy plan preserves explicit start boundary")
        checks.append("in-flight form cannot close or submit a duplicate; only one draft is created")
        # Two-node dispatch: one real isolated Linux worker and one protocol-only
        # peer. BMC wake/failure/restart behavior is exercised with Go fakes.
        headers = {"X-BITS-Request":"1", "Content-Type":"application/json"}
        peer_response = context.request.post(admin["url"] + "/api/v1/nodes", headers=headers,
            data=json.dumps({"id":"GROUP-PEER", "serial":"SIMULATED-PEER", "keep_on":True}))
        assert peer_response.ok, peer_response.status
        peer = peer_response.json()
        peer_headers = {"X-BITS-Node":peer["node"], "Authorization":"Bearer " + peer["token"], "X-BITS-Version":"browser-protocol-fixture"}
        def peer_heartbeat():
            response = context.request.get(admin["url"] + "/node/v1/heartbeat", headers=peer_headers)
            assert response.ok
        peer_heartbeat()
        navigate("dispatch")
        page.locator("#group-create").click()
        page.locator("#group-label").fill("BROWSER-GROUP")
        expect(page.get_by_role("checkbox", name="选择 LAB-002", exact=True)).to_be_disabled()
        page.get_by_role("checkbox", name="选择 BITS-CLOUD", exact=True).check()
        page.get_by_role("checkbox", name="选择 GROUP-PEER", exact=True).check()
        expect(page.locator("#group-selection-count")).to_contain_text("已选 2")
        page.locator("#group-next").click()
        for box in page.locator("#group-steps input").all():
            box.fill("2")
        page.locator("#group-save-template").check()
        page.locator("#group-next").click()
        expect(page.locator("#group-review")).to_contain_text("2 台节点")
        peer_heartbeat()
        page.locator("#group-submit").click()
        expect(page.locator("#group-body h2")).to_have_text("BROWSER-GROUP")
        group_id = page.url.split("group/")[-1]
        group = api("dispatch/groups/" + group_id)
        assert group["counts"] == {"draft":2}
        assert len(api("dispatch/templates")) == 1
        checks.append("multi-node plan, offline node exclusion, immutable template and explicit draft")
        peer_heartbeat()
        page.locator("#group-body").get_by_role("button", name="确认分发并开始", exact=True).click()
        expect(page.locator("#confirm-description")).to_contain_text("2 台")
        page.locator("#confirm-submit").click()
        for _ in range(20):
            group = api("dispatch/groups/" + group_id)
            if any(m["state"] != "draft" for m in group["members"]):
                break
            page.wait_for_timeout(500)
        assert all(m["state"] != "draft" for m in group["members"])
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        actual = next(m for m in group["members"] if m["node"] == "BITS-CLOUD")
        wait_batch(actual["id"], "delivered")
        peer_heartbeat()
        page.locator("#refresh").click()
        expect(page.locator("#group-body")).to_contain_text("已核验交付")
        page.get_by_role("checkbox", name="选择任务 GROUP-PEER", exact=True).check()
        page.locator("#group-body").get_by_role("button", name="取消所选任务", exact=True).click()
        page.locator("#confirm-reason").fill("protocol fixture cancelled without executing")
        page.locator("#confirm-submit").click()
        expect(page.locator("#group-body")).to_contain_text("已取消")
        page.screenshot(path=str(out / "dispatch-group.png"), full_page=True)
        assert len(api("dispatch/groups/" + group_id + "/operations")) == 2
        checks.append("atomic two-node authorization, independent real result and selected cancellation with audit")
        page.set_viewport_size({"width":390, "height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out / "dispatch-mobile.png"), full_page=True)
        page.set_viewport_size({"width":1440, "height":1080})
        navigate("dispatch")
        page.screenshot(path=str(out / "dispatch-workspace.png"), full_page=True)
        page.locator("#group-create").click()
        page.locator("#group-label").fill("REUSE-PLAN")
        page.get_by_role("checkbox", name="选择 BITS-CLOUD", exact=True).check()
        page.locator("#group-next").click()
        saved_template = api("dispatch/templates")[0]
        page.locator("#group-template").select_option(saved_template["id"])
        assert [box.input_value() for box in page.locator("#group-steps input").all()] == ["2", "2"]
        page.locator('#group-dialog button[data-close]').click()
        checks.append("saved plan reuse and responsive task group results")
        # UI-only management reachability fixture; no IPMI traffic is sent.
        def bmc_ui_save(route):
            value = route.request.post_data_json
            assert value["node"] == "LAB-002" and value["cipher"] == 17
            assert value["password"] == "UI-ONLY-PASSWORD"
            route.fulfill(status=200, content_type="application/json",
                body=json.dumps({"node":"LAB-002", "status":"configured", "power_command_sent":False}))
        def bmc_ui_states(route):
            response = route.fetch()
            value = response.json()
            for n in value["nodes"]:
                if n["node"] == "LAB-002":
                    n.update(state="wakeable", can_select=True, agent_online=False,
                        reason="BMC 可达，已关机；仅浏览器状态模拟",
                        power={"configured":True, "address":"192.168.50.21", "state":"off",
                            "checked_at":datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
            route.fulfill(response=response, json=value)
        page.route("**/api/v1/dispatch/bmc", bmc_ui_save)
        page.locator(".dispatch-node").filter(has_text="LAB-002").get_by_role("button", name="绑定 BMC").click()
        page.locator("#bmc-address").fill("192.168.50.21")
        page.locator("#bmc-user").fill("operator")
        page.locator("#bmc-password").fill("UI-ONLY-PASSWORD")
        page.route("**/api/v1/dispatch/nodes", bmc_ui_states)
        page.locator("#bmc-form button.primary").click()
        expect(page.locator("#bmc-dialog")).not_to_be_visible()
        expect(page.locator("#bmc-password")).to_have_value("")
        expect(page.locator(".dispatch-node").filter(has_text="LAB-002")).to_contain_text("已关机 · 可唤醒")
        page.screenshot(path=str(out / "dispatch-workspace.png"), full_page=True)
        page.unroute("**/api/v1/dispatch/nodes", bmc_ui_states)
        page.unroute("**/api/v1/dispatch/bmc", bmc_ui_save)
        checks.append("BMC binding form clears secrets and separates OS connectivity from simulated powered-off BMC reachability")
        # The same BMC result must appear on overview, node cards and details.
        # All states below are explicitly browser fixtures, not physical IPMI.
        power_case = "off"
        def fleet_power_states(route):
            response = route.fetch()
            value = response.json()
            stamp = datetime.now(timezone.utc)
            checked = stamp + timedelta(seconds=120) if power_case == "future" else stamp - timedelta(seconds=120) if power_case == "stale" else stamp
            node = {"id":"LAB-002", "last_seen":(stamp - timedelta(hours=1)).isoformat(),
                "agent_version":"browser-fixture", "disabled":False,
                "power":{"configured":True, "address":"192.168.50.21", "state":"off" if power_case in ("stale", "future", "off-newer", "heartbeat-newer") else power_case,
                    "checked_at":checked.isoformat()}}
            if power_case == "off-newer":
                node["last_seen"] = (stamp - timedelta(seconds=2)).isoformat()
            elif power_case == "heartbeat-newer":
                node["last_seen"] = stamp.isoformat()
                node["power"]["checked_at"] = (stamp - timedelta(seconds=2)).isoformat()
            value.update(nodes=[node], batches=[])
            route.fulfill(response=response, json=value)
        page.route("**/api/v1/overview", fleet_power_states)
        navigate("overview")
        page.locator("#refresh").click()
        expect(page.locator("#overview-node-list .badge")).to_have_text("已关机 · 可唤醒", timeout=15000)
        expect(page.locator("#summary .stat").first).to_contain_text("系统在线 0 台 · 已关机可唤醒 1 台")
        expect(page.locator("#summary .stat strong").first).to_have_text("1 / 1")
        page.screenshot(path=str(out / "bmc-overview.png"), full_page=True)
        navigate("nodes")
        expect(page.locator("#node-list .badge")).to_have_text("已关机 · 可唤醒")
        page.locator('#node-filters button[data-filter="wakeable"]').click()
        expect(page.locator("#node-list .node-card")).to_have_count(1)
        page.locator("#node-list").get_by_role("button", name="节点详情 ↗").click()
        expect(page.locator("#node-detail")).to_contain_text("已关机 · 可唤醒")
        expect(page.locator("#node-detail")).to_contain_text("BMC 192.168.50.21 · 电源关")
        page.set_viewport_size({"width":390, "height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out / "bmc-nodes-mobile.png"), full_page=True)
        page.locator('#node-filters button[data-filter="all"]').click()
        for power_case, label in (("on","已开机 · 等待系统"),("unknown","状态待确认"),
                                  ("stale","状态待确认"),("future","状态待确认"),
                                  ("off-newer","已关机 · 可唤醒"),("heartbeat-newer","空闲")):
            page.locator("#refresh").click()
            expect(page.locator("#node-list .badge")).to_have_text(label, timeout=15000)
        # Polling may have a fetch in flight when the last fixture assertion ends.
        page.unroute_all(behavior="wait")
        page.locator("#refresh").click()
        checks.append("overview, node cards, details and mobile filters share BMC power state; stale/future state rejected and newer heartbeat wins")
        # Credential template is saved via the real center API, without a
        # discovery candidate: this cannot send remote IPMI commands.
        navigate("dispatch")
        page.locator("#bmc-profiles-open").click()
        expect(page.locator("#bmc-profiles-dialog")).to_be_visible()
        page.locator("#bmc-profile-name").fill("browser-fixture")
        page.locator("#bmc-profile-networks").fill("192.168.50.0/24")
        page.locator("#bmc-profile-prefix").fill("LAB-")
        page.locator("#bmc-profile-user").fill("operator")
        page.locator("#bmc-profile-password").fill("UI-TEMPLATE-SECRET")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator("#bmc-profiles-form button.primary").click()
        expect(page.locator("#bmc-profiles-dialog")).not_to_be_visible()
        expect(page.locator("#bmc-profile-password")).to_have_value("")
        profiles = api("dispatch/bmc-profiles")
        assert len(profiles) == 1 and "password" not in profiles[0]
        assert profiles[0]["enabled"] and profiles[0]["node_prefix"] == "LAB-"
        page.locator("#bmc-profiles-open").click()
        page.locator("#bmc-profile-select").select_option("browser-fixture")
        expect(page.locator("#bmc-profile-password")).to_have_value("")
        page.locator("#bmc-profile-enabled").uncheck()
        page.locator("#bmc-profiles-form button.primary").click()
        expect(page.locator("#bmc-profiles-dialog")).not_to_be_visible()
        assert not api("dispatch/bmc-profiles")[0]["enabled"]
        checks.append("mobile credential template creation/edit/disable uses real admin API; blank edit retains password and GET never returns it")

        # Create a custom template within enrollment without losing node fields.
        page.locator("#enroll").click()
        page.locator("#node-id").fill("LAB-PROFILE")
        page.locator("#node-serial").fill("SYNTHETIC-PROFILE")
        expect(page.locator("#node-submit")).to_be_enabled()
        assert page.locator('#node-bmc-profile option[value="browser-fixture"]').count() == 0
        page.locator("#node-profile-create").click()
        expect(page.locator("#bmc-profiles-dialog")).to_be_visible()
        page.locator("#bmc-profile-name").fill("onboarding-fixture")
        page.locator("#bmc-profile-networks").fill("192.168.50.0/24")
        page.locator("#bmc-profile-prefix").fill("LAB-")
        page.locator("#bmc-profile-user").fill("lab-operator")
        page.locator("#bmc-profile-password").fill("ENROLL-FIXTURE-KEY")
        page.locator("#bmc-profiles-form button.primary").click()
        expect(page.locator("#bmc-profiles-dialog")).not_to_be_visible()
        expect(page.locator("#node-bmc-profile")).to_have_value("onboarding-fixture")
        expect(page.locator("#node-id")).to_have_value("LAB-PROFILE")
        expect(page.locator("#node-serial")).to_have_value("SYNTHETIC-PROFILE")
        expect(page.locator("#node-profile-summary")).to_contain_text("lab-operator")
        expect(page.locator("#node-profile-summary")).to_contain_text("Cipher 17")
        expect(page.locator("#bmc-profile-password")).to_have_value("")
        assert "ENROLL-FIXTURE-KEY" not in page.locator("#node-dialog").inner_text()
        page.set_viewport_size({"width":1440, "height":1080})
        page.locator("#node-id").scroll_into_view_if_needed()
        page.screenshot(path=str(out / "node-template.png"))
        page.set_viewport_size({"width":390, "height":844})
        page.locator("#node-bmc-profile").scroll_into_view_if_needed()
        assert page.locator("#node-dialog").evaluate("e => e.scrollWidth <= e.clientWidth")
        page.screenshot(path=str(out / "node-template-mobile.png"))
        with page.expect_download() as download:
            page.locator("#node-submit").click()
        cfg = json.loads(Path(download.value.path()).read_text())
        assert set(cfg) == {"url", "node", "token", "ca_pem", "serial", "keep_on"}
        assert cfg["node"] == "LAB-PROFILE"
        assert "ENROLL-FIXTURE-KEY" not in json.dumps(cfg)
        download.value.delete()
        del cfg
        selected = next(n for n in api("overview")["nodes"] if n["id"] == "LAB-PROFILE")
        assert selected["bmc_profile"] == "onboarding-fixture"
        assert api("dispatch/bmc-discoveries")["LAB-PROFILE"]["profile"] == "onboarding-fixture"
        checks.append("custom BMC template created during enrollment, selected automatically, persisted on center and omitted from connection download; desktop/mobile layout")

        # Load errors block enrollment, and a stale disabled selection cannot
        # silently become automatic matching or create a partial node.
        page.locator("#enroll").click()
        page.locator("#node-id").fill("LAB-REJECTED")
        page.locator("#node-serial").fill("SYNTHETIC-REJECTED")
        expect(page.locator("#node-bmc-profile")).to_be_enabled()
        page.locator("#node-bmc-profile").select_option("onboarding-fixture")
        def unavailable_profiles(route):
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"error":"Fixture template list unavailable"}))
        page.route("**/api/v1/dispatch/bmc-profiles", unavailable_profiles)
        page.locator("#node-profile-reload").click()
        expect(page.locator("#node-profile-error")).to_contain_text("Fixture template list unavailable")
        expect(page.locator("#node-submit")).to_be_disabled()
        page.unroute("**/api/v1/dispatch/bmc-profiles", unavailable_profiles)
        page.locator("#node-profile-reload").click()
        expect(page.locator("#node-submit")).to_be_enabled()
        expect(page.locator("#node-bmc-profile")).to_have_value("onboarding-fixture")
        disabled = next(p for p in api("dispatch/bmc-profiles") if p["name"] == "onboarding-fixture")
        disabled["enabled"] = False
        response = context.request.post(admin["url"] + "/api/v1/dispatch/bmc-profiles", data=disabled, headers={"X-BITS-Request":"1"})
        assert response.ok
        page.locator("#node-submit").click()
        expect(page.locator("#node-error")).to_contain_text("节点未创建")
        assert not any(n["id"] == "LAB-REJECTED" for n in api("overview")["nodes"])
        page.locator("#node-profile-reload").click()
        expect(page.locator("#node-profile-summary")).to_contain_text("已停用或不存在")
        expect(page.locator("#node-bmc-profile")).to_have_value("onboarding-fixture")
        expect(page.locator("#node-submit")).to_be_disabled()
        page.locator("#node-bmc-profile").select_option("")
        expect(page.locator("#node-submit")).to_be_enabled()
        page.locator('#node-dialog button[data-close="node-dialog"]').last.click()
        checks.append("template list failure retry and concurrent template disable preserve explicit selection and prevent partial enrollment")
        # Standalone wake UI uses an explicit two-node BMC simulation. No physical
        # power command is sent; Go tests exercise the real scheduler with a driver.
        page.set_viewport_size({"width":1440, "height":1080})
        wake_posts, wake_records = [], []
        def wake_overview(route):
            response = route.fetch()
            value = response.json()
            for n in value["nodes"]:
                if n["id"] in ("LAB-002", "LAB-010"):
                    n.update(last_seen="2020-01-01T00:00:00Z", agent_version="0.4.3", disabled=False,
                        power={"configured":True, "address":"192.168.50.21" if n["id"] == "LAB-002" else "192.168.50.22",
                               "state":"off", "checked_at":datetime.now(timezone.utc).isoformat()})
                    if wake_records:
                        n["wake"] = next(m for m in wake_records[0]["members"] if m["node"] == n["id"])
            route.fulfill(response=response, json=value)
        def wake_fleet(route):
            response = route.fetch()
            value = response.json()
            for n in value["nodes"]:
                if n["node"] in ("LAB-002", "LAB-010"):
                    n.update(state="waking" if wake_records else "wakeable", waking=bool(wake_records),
                        can_select=not wake_records, agent_online=False, reason="浏览器模拟 · 等待系统连接", active_batch="",
                        power={"configured":True, "address":"192.168.50.21", "state":"off",
                               "checked_at":datetime.now(timezone.utc).isoformat()})
            route.fulfill(response=response, json=value)
        def wake_requests(route):
            if route.request.method == "GET":
                route.fulfill(status=200, json=wake_records)
                return
            value = route.request.post_data_json
            assert sorted(value["nodes"]) == ["LAB-002", "LAB-010"]
            assert len(value["request_id"]) == 32
            wake_posts.append(value)
            stamp = datetime.now(timezone.utc).isoformat()
            wake_records.append({"id":value["request_id"], "created_at":stamp,
                "counts":{"waiting_agent":2}, "members":[{"node":n,"state":"waiting_agent","requested_at":stamp,
                    "deadline":(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()} for n in value["nodes"]]})
            route.fulfill(status=200, json=wake_records[0])
        page.route("**/api/v1/overview", wake_overview)
        page.route("**/api/v1/dispatch/nodes", wake_fleet)
        page.route("**/api/v1/dispatch/wakes**", wake_requests)
        navigate("nodes")
        page.locator("#node-search").fill("LAB-002")
        page.locator("#refresh").click()
        page.locator("#node-list").get_by_role("button", name="实时监控 ↗").click()
        expect(page.locator("#monitor-title")).to_have_text("LAB-002")
        page.get_by_role("button", name="唤醒机器", exact=True).click()
        expect(page.locator("#confirm-description")).to_contain_text("不执行压测")
        page.locator('#confirm-dialog button[data-close]').last.click()
        assert not wake_posts
        navigate("dispatch")
        page.get_by_role("checkbox", name="选择唤醒 LAB-002", exact=True).check()
        page.get_by_role("checkbox", name="选择唤醒 LAB-010", exact=True).check()
        page.locator("#refresh").click()
        expect(page.get_by_role("checkbox", name="选择唤醒 LAB-002", exact=True)).to_be_checked()
        page.locator("#wake-toolbar").get_by_role("button", name="唤醒所选机器").click()
        expect(page.locator("#confirm-description")).to_contain_text("唤醒 2 台机器")
        page.screenshot(path=str(out / "wake-confirm.png"), full_page=True)
        page.locator("#confirm-submit").click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        expect(page.locator("#wake-history .operation-result")).to_have_count(2)
        assert len(wake_posts) == 1
        page.screenshot(path=str(out / "wake-progress.png"), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        page.locator("#wake-history").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out / "wake-mobile.png"), full_page=True)
        page.unroute("**/api/v1/overview", wake_overview)
        page.unroute("**/api/v1/dispatch/nodes", wake_fleet)
        page.unroute("**/api/v1/dispatch/wakes**", wake_requests)
        checks.append("standalone monitor wake confirmation cancels without POST; bulk wake retains choices across polling and displays independent progress on desktop/mobile (simulated BMC)")
        # Permanently delete only the isolated completed browser reports. This
        # exercises real authenticated API, background cleanup and removed files.
        page.set_viewport_size({"width":1440,"height":1080})
        navigate("reports")
        page.locator("#report-search").fill("")
        page.locator("#refresh").click()
        completed = [b for b in api("overview")["batches"] if b["state"] == "delivered"]
        before_delete_ids = {b["id"] for b in api("overview")["batches"]}
        assert len(completed) >= 2
        first = next(b for b in completed if b["id"] == batch_id)
        card = page.locator("#report-list .report-card").filter(has_text=first["plan"]["label"])
        card.get_by_role("button", name="删除", exact=True).click()
        expect(page.locator("#confirm-description")).to_contain_text("无法恢复")
        expect(page.locator("#confirm-description")).to_contain_text(first["plan"]["label"])
        page.locator('#confirm-dialog button[data-close]').last.click()
        assert api("batches/" + first["id"])["state"] == "delivered"
        card.get_by_role("checkbox").check()
        second = next(b for b in completed if b["id"] != first["id"])
        page.locator("#report-list .report-card").filter(has_text=second["plan"]["label"]).get_by_role("checkbox").check()
        page.locator("#refresh").click()
        expect(page.locator("#reports-delete-toolbar")).to_contain_text("已选 2 项")
        page.locator("#reports-delete-toolbar").get_by_role("button", name="删除所选").click()
        expect(page.locator("#confirm-description")).to_contain_text(first["plan"]["label"])
        expect(page.locator("#confirm-description")).to_contain_text(second["plan"]["label"])
        page.screenshot(path=str(out / "delete-confirm.png"), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out / "delete-mobile.png"), full_page=True)
        page.locator("#confirm-submit").click()
        expect(page.locator("#confirm-dialog")).not_to_be_visible()
        expect(page.locator("#reports-deletion-history")).to_contain_text("已删除 2 / 2 项", timeout=20000)
        for b in (first,second):
            assert not context.request.get(admin["url"]+"/api/v1/batches/"+b["id"]+"/files/report.html").ok
            assert subprocess.call(["docker","exec","bits-independent-test","test","!","-e","/var/lib/bits/center/artifacts/"+b["id"]]) == 0
        remaining = api("overview")["batches"]
        assert {b["id"] for b in remaining} == before_delete_ids - {first["id"],second["id"]}
        assert all(b["id"] not in (first["id"],second["id"]) for b in remaining)
        checks.append("single report delete confirmation lists target and cancels without change; two selected reports permanently deleted through real API with center files and download access removed; cross-page batch selection and mobile confirmation")
        page.set_viewport_size({"width":1440, "height":1080})
        verify_remote(page, context, admin, out, checks)
        page.locator("#logout").click()
        expect(page.locator("#login-dialog")).to_be_visible()
        assert not errors, errors
        checks.append("operator logout and no browser script errors")
    except BaseException:
        # Never screenshot an entered credential or connection-file contents.
        if not any(page.locator(selector).is_visible() for selector in ("#login-dialog", "#bmc-dialog", "#bmc-profiles-dialog")) and not page.locator(".remote-profile-dialog").count() and not page.locator("#remote-login").is_visible():
            page.screenshot(path=str(out / "browser-failure.png"), full_page=True)
        raise
    finally:
        context.close()
        browser.close()
(out / "browser.json").write_text(json.dumps({"status":"passed", "checks":checks,
    "certificate":"isolated self-signed fixture only; Go TLS validation tested separately",
    "readings":"synthetic fixed provider; actual Linux stress and systemd services"}, indent=2))
