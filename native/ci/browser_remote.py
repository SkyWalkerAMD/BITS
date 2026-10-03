"""Real OpenSSH/PTY/SFTP interactions, included by the private browser fixture."""
import hashlib
import json
import subprocess
from playwright.sync_api import expect


def verify_remote(page, context, admin, out, checks):
    subprocess.check_call(["docker", "exec", "-e", "GITHUB_ACTIONS=true", "bits-independent-test",
                           "python3", "-I", "-B", "/src/native/ci/ssh_fixture.py"])
    login = json.loads(subprocess.check_output(["docker", "exec", "bits-independent-test",
                                              "cat", "/root/.bits/ssh-fixture.json"]))
    page.goto(admin["url"] + "/#nodes")
    card = page.locator("#node-list .node-card").filter(has_text="BITS-CLOUD")
    expect(card).to_contain_text("系统 IP")
    card.get_by_role("button", name="系统终端 ↗").click()
    expect(page.locator("#remote-address option")).not_to_have_count(0)
    page.get_by_role("button", name="新建模板", exact=True).click()
    page.locator("#ssh-profile-name").fill("CI Linux 登录")
    page.locator("#ssh-profile-user").fill(login["username"])
    page.locator("#ssh-profile-password").fill(login["password"])
    page.locator("#ssh-profile-port").fill(str(login["port"]))
    page.get_by_role("button", name="保存模板", exact=True).click()
    expect(page.locator(".remote-profile-dialog")).to_have_count(0)
    expect(page.locator("#remote-profile option:checked")).to_contain_text("CI Linux 登录")
    confirmations = []

    def confirm(dialog):
        confirmations.append(dialog.message)
        dialog.accept()

    page.on("dialog", confirm)
    try:
        page.locator("#remote-connect").click()
        expect(page.locator("#remote-work")).to_be_visible(timeout=30000)
        expect(page.locator("#remote-message")).to_have_text("已连接")
        assert len(confirmations) == 1 and "SHA256:" in confirmations[0]
        page.locator(".xterm-helper-textarea").press_sequentially("printf 'REMOTE_SHELL_OK\\n'", delay=3)
        page.locator(".xterm-helper-textarea").press("Enter")
        for _ in range(40):
            text = page.evaluate("remoteState.terminal.buffer.active.getLine(remoteState.terminal.buffer.active.cursorY-1)?.translateToString() || ''")
            if "REMOTE_SHELL_OK" in text:
                break
            page.wait_for_timeout(250)
        else:
            raise AssertionError("real shell did not execute input")
        # Paste exercises xterm bracketed-paste and preserves a multiline payload.
        page.evaluate("text => remoteState.terminal.paste(text)", "printf 'PASTED_VALUE\\n'\n")
        page.wait_for_timeout(500)
        expect(page.locator("#remote-files")).to_be_visible()
        expect(page.locator("#remote-path")).to_have_value("/home/" + login["username"])
        body = b"BITS SFTP UTF-8 \xe6\xb5\x8b\xe8\xaf\x95\n" * 1000
        page.locator("#remote-upload").set_input_files({"name": "upload-check.txt", "mimeType": "text/plain", "buffer": body})
        file = page.locator("#remote-file-list").get_by_role("button", name="upload-check.txt", exact=True)
        expect(file).to_be_visible(timeout=20000)
        with page.expect_download() as downloaded:
            file.click()
        download = downloaded.value
        target = out / "ssh-download-check.txt"
        download.save_as(str(target))
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(body).digest()
        target.unlink()
        page.locator("#remote-upload").set_input_files({"name": "upload-check.txt", "mimeType": "text/plain", "buffer": b"must not overwrite"})
        expect(page.locator("#remote-file-status")).to_contain_text("同名文件已存在")
        actual = subprocess.check_output(["docker", "exec", "bits-independent-test", "cat", "/home/" + login["username"] + "/upload-check.txt"])
        assert actual == body
        page.screenshot(path=str(out / "system-terminal.png"), full_page=True)
        page.set_viewport_size({"width": 430, "height": 932})
        page.wait_for_timeout(300)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "SSH mobile overflow"
        page.screenshot(path=str(out / "system-terminal-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1080})
        page.get_by_role("button", name="断开连接", exact=True).click()
        page.locator("#remote-profile").select_option("")
        page.locator("#remote-user").fill(login["username"])
        page.locator("#remote-password").fill("incorrect-fixture-password")
        page.locator("#remote-port").fill(str(login["port"]))
        page.locator("#remote-connect").click()
        expect(page.locator("#remote-message")).to_contain_text("SSH 登录失败", timeout=20000)
        page.locator("#remote-password").fill(login["password"])
        page.locator("#remote-connect").click()
        expect(page.locator("#remote-message")).to_have_text("已连接", timeout=20000)
        assert len(confirmations) == 1, "pinned host asked for trust again"
        session_id = page.evaluate("remoteState.id")
        page.goto(admin["url"] + "/#nodes")
        request = context.request.get(admin["url"] + "/api/v1/remote/sessions/" + session_id + "/output?offset=0")
        assert request.ok and request.json()["closed"], "navigation retained remote shell"
        configs = context.request.get(admin["url"] + "/api/v1/remote/config").text()
        assert login["password"] not in configs and '"secret"' not in configs
        checks.append("actual OpenSSH PTY command and multiline paste, private SSH template login, first-host fingerprint, pinned-host temporary login, wrong password, real SFTP upload/download checksum and no overwrite, navigation disconnect, responsive mobile terminal")
    finally:
        page.remove_listener("dialog", confirm)
