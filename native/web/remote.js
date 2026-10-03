"use strict";
const remoteState = {
  node: null,
  built: null,
  id: null,
  epoch: 0,
  terminal: null,
  fit: null,
  resize: null,
  profiles: [],
  offset: 0,
  queue: "",
  sending: false,
  upload: null,
  connecting: false,
  size: null,
};
function systemIPText(n) {
  const v = n?.network;
  return (
    "系统 IP " +
    (v?.primary || "等待上报") +
    (v?.primary && (!online(n) || age(v.received_at) > 90) ? " · 最近记录" : "")
  );
}
function openSystem(id) {
  location.hash = "system/" + encodeURIComponent(id);
}
function remoteField(form, label, type, id, value = "") {
  const wrap = el("label", label),
    input = el(type === "select" ? "select" : "input");
  input.id = id;
  if (type !== "select") input.type = type;
  input.value = value;
  input.autocomplete = type === "password" ? "new-password" : "off";
  wrap.append(input);
  form.append(wrap);
  return input;
}
function remoteMessage(message, bad = false) {
  const p = $("remote-message");
  if (p) {
    p.textContent = message;
    p.className = "remote-message" + (bad ? " error" : "");
  }
}
async function remoteCopy(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    remoteMessage("请使用浏览器菜单复制选中的文本");
  }
}
function remoteRoute(node) {
  document.body.classList.toggle("system-page", !!node);
  if (remoteState.node === node) return;
  remoteDisconnect();
  remoteState.node = node;
  remoteState.built = null;
  $("remote-body").replaceChildren();
}
function remoteDisconnect(clearPassword = true) {
  const r = remoteState,
    id = r.id;
  remoteCloseFiles();
  remoteMaximize(false);
  r.id = null;
  r.epoch++;
  r.queue = "";
  r.sending = false;
  r.connecting = false;
  r.size = null;
  if (r.upload) {
    r.upload.abort();
    r.upload = null;
  }
  r.resize?.disconnect();
  r.resize = null;
  r.terminal?.dispose();
  r.terminal = null;
  r.fit = null;
  if ($("remote-login")) $("remote-login").hidden = false;
  if ($("remote-work")) $("remote-work").hidden = true;
  if (clearPassword && $("remote-password")) $("remote-password").value = "";
  if (id)
    fetch("/api/v1/remote/sessions/" + id + "/close", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-BITS-Request": "1" },
      keepalive: true,
    }).catch(() => {});
}
function remoteFontSize() {
  try {
    const value = Number(localStorage.getItem("bits-terminal-font"));
    if (value >= 12 && value <= 24) return value;
  } catch {}
  return 16;
}
function remoteFit() {
  const r = remoteState;
  if (!r.terminal || !r.fit || !$("remote-terminal")?.clientHeight) return;
  r.fit.fit();
  r.size = {
    cols: Math.max(20, Math.min(400, r.terminal.cols)),
    rows: Math.max(5, Math.min(150, r.terminal.rows)),
  };
  if (r.terminal.cols !== r.size.cols || r.terminal.rows !== r.size.rows)
    r.terminal.resize(r.size.cols, r.size.rows);
  $("remote-size").textContent = r.size.cols + " 列 × " + r.size.rows + " 行";
  remoteSend(r.epoch);
}
function remoteChangeFont(delta) {
  const r = remoteState;
  if (!r.terminal) return;
  const value = Math.max(12, Math.min(24, r.terminal.options.fontSize + delta));
  r.terminal.options.fontSize = value;
  $("remote-font-size").textContent = value + " px";
  try { localStorage.setItem("bits-terminal-font", String(value)); } catch {}
  requestAnimationFrame(remoteFit);
}
async function remoteMaximize(enabled) {
  const root = $("remote-body");
  if (!root) return;
  root.classList.toggle("remote-maximized", enabled);
  const control = $("remote-fullscreen");
  if (control) {
    control.textContent = enabled ? "退出全屏" : "全屏";
    control.setAttribute("aria-pressed", String(enabled));
  }
  try {
    if (enabled && !document.fullscreenElement && root.requestFullscreen)
      await root.requestFullscreen();
    else if (!enabled && document.fullscreenElement === root)
      await document.exitFullscreen();
  } catch {}
  requestAnimationFrame(remoteFit);
}
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement) remoteMaximize(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.fullscreenElement &&
      $("remote-body")?.classList.contains("remote-maximized") &&
      !$("remote-files")?.open) remoteMaximize(false);
});
async function loadRemoteProfiles(selected) {
  const node = remoteState.node;
  const result = await api("remote/config?node=" + encodeURIComponent(node));
  if (node !== remoteState.node) return;
  remoteState.profiles = result.profiles;
  const select = $("remote-profile");
  if (!select) return;
  select.replaceChildren(el("option", "临时账号"));
  select.firstChild.value = "";
  for (const p of result.profiles) {
    const o = el("option", p.name + " · " + p.username);
    o.value = p.id;
    select.append(o);
  }
  select.value = selected === undefined ? result.profile : selected;
  remoteProfileChanged();
}
function remoteProfileChanged() {
  const p = remoteState.profiles.find(
    (p) => p.id === $("remote-profile").value,
  );
  $("remote-user").value = p?.username || "";
  $("remote-port").value = p?.port || 22;
  $("remote-password").value = "";
  for (const id of ["remote-user", "remote-port", "remote-password"])
    $(id).parentNode.hidden = !!p;
  $("remote-remember").parentNode.hidden = !p;
  $("remote-edit-profile").disabled = !p;
}
function renderRemote() {
  const r = remoteState;
  if (!r.node || !authenticated) return;
  const n = snapshot.nodes.find((n) => n.id === r.node);
  if (r.built === r.node) {
    if ($("remote-ip-note")) $("remote-ip-note").textContent = systemIPText(n);
    if (!r.id && !r.connecting && $("remote-connect")) {
      $("remote-connect").disabled =
        !n ||
        !online(n) ||
        !n.network?.primary ||
        age(n.network.received_at) > 90;
      const select = $("remote-address"),
        addresses = n?.network?.addresses || [],
        key = JSON.stringify(addresses);
      if (select.dataset.addresses !== key) {
        const old = select.value;
        select.replaceChildren();
        for (const a of addresses) {
          const o = el("option", a.address + " · " + a.interface);
          o.value = a.address;
          select.append(o);
        }
        select.value = addresses.some((a) => a.address === old)
          ? old
          : n?.network?.primary || "";
        select.dataset.addresses = key;
      }
    }
    return;
  }
  r.built = r.node;
  const root = $("remote-body"),
    head = el("div", undefined, "remote-heading");
  const back = el("a", "← 节点", "remote-back");
  back.href = "#nodes";
  head.append(back, el("h2", r.node));
  const note = el("p", systemIPText(n), "muted");
  note.id = "remote-ip-note";
  head.append(note);
  const form = el("form", undefined, "remote-login");
  form.id = "remote-login";
  const address = remoteField(form, "系统 IP", "select", "remote-address");
  for (const a of n?.network?.addresses || []) {
    const o = el("option", a.address + " · " + a.interface);
    o.value = a.address;
    address.append(o);
  }
  address.value = n?.network?.primary || "";
  remoteField(form, "登录模板", "select", "remote-profile").onchange =
    remoteProfileChanged;
  const user = remoteField(form, "系统用户名", "text", "remote-user");
  user.maxLength = 64;
  const password = remoteField(form, "系统密码", "password", "remote-password");
  password.maxLength = 1024;
  const port = remoteField(form, "SSH 端口", "number", "remote-port", 22);
  port.min = 1;
  port.max = 65535;
  const remember = remoteField(
    form,
    "此节点记住模板",
    "checkbox",
    "remote-remember",
  );
  remember.checked = true;
  const actions = el("div", undefined, "remote-login-actions");
  actions.append(
    button("新建模板", () => editSSHProfile(), "", "remote-new-profile"),
  );
  const edit = button("编辑模板", () =>
    editSSHProfile(r.profiles.find((p) => p.id === $("remote-profile").value)),
  );
  edit.id = "remote-edit-profile";
  actions.append(edit);
  const submit = el("button", "连接系统", "primary");
  submit.type = "submit";
  submit.id = "remote-connect";
  submit.disabled = !n || !online(n) || !n.network?.primary;
  actions.append(submit);
  form.append(actions);
  const message = el("p", "", "remote-message");
  message.id = "remote-message";
  message.setAttribute("role", "status");
  const work = el("div", undefined, "remote-work");
  work.id = "remote-work";
  work.hidden = true;
  const toolbar = el("div", undefined, "remote-toolbar"),
    label = el("strong");
  label.id = "remote-connected";
  toolbar.append(
    label,
    button("文件传输", () => remoteOpenFiles(), "primary", "remote-show-files"),
    button("复制", () => {
      remoteCopy(r.terminal?.getSelection());
    }),
    button("粘贴", async () => {
      try {
        r.terminal?.paste(await navigator.clipboard.readText());
        r.terminal?.focus();
      } catch {
        remoteMessage("请在终端中使用 Ctrl+Shift+V 粘贴");
      }
    }),
    button("全屏", () => remoteMaximize(!$("remote-body").classList.contains("remote-maximized")), "", "remote-fullscreen"),
    button("断开连接", () => {
      remoteDisconnect();
      form.hidden = false;
      work.hidden = true;
      remoteMessage("已断开");
    }),
  );
  const terminal = el("div", undefined, "remote-terminal");
  for (const key of ["remote-show-files", "remote-fullscreen"])
    toolbar.querySelector('[data-focus-key="' + key + '"]').id = key;
  terminal.id = "remote-terminal";
  const foot = el("div", undefined, "remote-terminal-foot"), size = el("span", "", "muted"), font = el("span", remoteFontSize() + " px");
  size.id = "remote-size";
  font.id = "remote-font-size";
  const smaller = button("A−", () => remoteChangeFont(-1), "quiet", "remote-font-smaller"), larger = button("A+", () => remoteChangeFont(1), "quiet", "remote-font-larger");
  smaller.setAttribute("aria-label", "缩小终端字号");
  larger.setAttribute("aria-label", "放大终端字号");
  foot.append(size, smaller, font, larger);
  head.append(message);
  work.append(toolbar, terminal, foot);
  root.replaceChildren(head, form, work);
  form.onsubmit = remoteConnect;
  loadRemoteProfiles().catch((e) => remoteMessage(e.message, true));
}
async function editSSHProfile(profile) {
  const dialog = el("dialog", undefined, "remote-profile-dialog"),
    form = el("form");
  dialog.setAttribute(
    "aria-label",
    profile ? "编辑 SSH 模板" : "新建 SSH 模板",
  );
  form.append(el("h2", profile ? "编辑 SSH 模板" : "新建 SSH 模板"));
  const name = remoteField(
    form,
    "模板名称",
    "text",
    "ssh-profile-name",
    profile?.name || "",
  );
  name.required = true;
  name.maxLength = 80;
  const user = remoteField(
    form,
    "系统用户名",
    "text",
    "ssh-profile-user",
    profile?.username || "",
  );
  user.required = true;
  user.maxLength = 64;
  const pass = remoteField(
    form,
    profile ? "新密码（留空保留）" : "系统密码",
    "password",
    "ssh-profile-password",
  );
  pass.required = !profile;
  pass.maxLength = 1024;
  const port = remoteField(
    form,
    "SSH 端口",
    "number",
    "ssh-profile-port",
    profile?.port || 22,
  );
  port.min = 1;
  port.max = 65535;
  port.required = true;
  const error = el("p", "", "error");
  error.setAttribute("role", "alert");
  form.append(error);
  const actions = el("div", undefined, "actions"),
    save = el("button", "保存模板", "primary");
  save.type = "submit";
  actions.append(button("取消", () => dialog.close()));
  if (profile)
    actions.append(
      button("删除模板", async () => {
        if (!window.confirm("删除模板「" + profile.name + "」？")) return;
        try {
          await api("remote/profiles/" + profile.id, "DELETE", {
            revision: profile.revision,
          });
          await loadRemoteProfiles("");
          dialog.close();
        } catch (e) {
          error.textContent = e.message;
        }
      }),
    );
  actions.append(save);
  form.append(actions);
  dialog.append(form);
  document.body.append(dialog);
  dialog.onclose = () => {
    pass.value = "";
    dialog.remove();
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const p = await api("remote/profiles", "POST", {
        id: profile?.id || "",
        revision: profile?.revision || "",
        name: name.value,
        username: user.value,
        password: pass.value,
        port: Number(port.value),
      });
      pass.value = "";
      await loadRemoteProfiles(p.id);
      dialog.close();
    } catch (e) {
      error.textContent = e.message;
    } finally {
      save.disabled = false;
    }
  };
  dialog.showModal();
}
async function remoteConnect(event) {
  event.preventDefault();
  const r = remoteState;
  if (r.connecting) return;
  remoteDisconnect(false);
  const epoch = ++r.epoch,
    form = $("remote-login"),
    submit = $("remote-connect");
  r.connecting = true;
  submit.disabled = true;
  const indata = {
    node: r.node,
    address: $("remote-address").value,
    profile: $("remote-profile").value,
    username: $("remote-user").value,
    password: $("remote-password").value,
    port: Number($("remote-port").value),
    remember_template: $("remote-remember").checked,
    cols: 100,
    rows: 28,
  };
  try {
    remoteMessage("正在连接…");
    let result = await api("remote/connect", "POST", indata);
    if (epoch !== r.epoch) {
      if (result.id)
        await api("remote/sessions/" + result.id + "/close", "POST", {});
      return;
    }
    if (result.state === "host_key") {
      const k = result.host_key;
      if (k.changed) {
        if (
          !window.confirm(
            "主机指纹发生变化。请先核对服务器。\n旧指纹：" +
              k.previous +
              "\n新指纹：" +
              k.fingerprint +
              "\n确认是重装或更换的主机后，重置此地址的信任记录？",
          )
        ) {
          remoteMessage("已取消连接");
          return;
        }
        await api("remote/host-key", "DELETE", {
          node: indata.node,
          address: indata.address,
          port:
            r.profiles.find((p) => p.id === indata.profile)?.port ||
            indata.port,
          previous: k.previous,
        });
      } else if (
        !window.confirm(
          "首次连接 " +
            indata.address +
            "\n" +
            k.algorithm +
            "\n" +
            k.fingerprint +
            "\n确认主机指纹并连接？",
        )
      ) {
        remoteMessage("已取消连接");
        return;
      }
      indata.trust_fingerprint = k.fingerprint;
      result = await api("remote/connect", "POST", indata);
    }
    if (result.state !== "connected")
      throw new Error("主机指纹发生变化，请重新核对");
    if (epoch !== r.epoch) {
      await api("remote/sessions/" + result.id + "/close", "POST", {});
      return;
    }
    r.id = result.id;
    r.offset = 0;
    r.queue = "";
    r.sending = false;
    $("remote-password").value = "";
    form.hidden = true;
    $("remote-work").hidden = false;
    $("remote-connected").textContent =
      result.username + "@" + result.address + ":" + result.port;
    $("remote-terminal").replaceChildren();
    r.terminal = new Terminal({
      cursorBlink: true,
      fontSize: remoteFontSize(),
      fontFamily: '"Cascadia Mono", "Consolas", monospace',
      scrollback: 5000,
      theme: { background: "#112b36", foreground: "#e5eef1" },
      allowProposedApi: false,
    });
    r.fit = new FitAddon.FitAddon();
    r.terminal.loadAddon(r.fit);
    r.terminal.open($("remote-terminal"));
    r.terminal.onData((data) => {
      if (r.queue.length + data.length > 65536) {
        remoteMessage("输入过长，请分段粘贴", true);
        return;
      }
      r.queue += data;
      remoteSend(epoch);
    });
    r.terminal.attachCustomKeyEventHandler((e) => {
      if (
        e.type === "keydown" &&
        e.ctrlKey &&
        e.shiftKey &&
        e.code === "KeyC"
      ) {
        remoteCopy(r.terminal.getSelection());
        return false;
      }
      return true;
    });
    let resizeTimer;
    r.resize = new ResizeObserver(() => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (epoch !== r.epoch || !r.terminal) return;
        remoteFit();
      }, 160);
    });
    r.resize.observe($("remote-terminal"));
    remoteFit();
    r.terminal.focus();
    remoteMessage("已连接");
    remotePoll(epoch);
    $("remote-show-files").disabled = !result.sftp;
    if (result.sftp) remoteCreateFiles(result);
  } catch (e) {
    if (epoch === r.epoch) remoteMessage(e.message, true);
  } finally {
    indata.password = "";
    if (epoch === r.epoch) {
      r.connecting = false;
      $("remote-password").value = "";
      submit.disabled = false;
    }
  }
}
async function remoteSend(epoch) {
  const r = remoteState;
  if (r.sending || !r.id || epoch !== r.epoch) return;
  r.sending = true;
  try {
    while ((r.queue || r.size) && epoch === r.epoch) {
      let end = Math.min(4096, r.queue.length);
      if (
        end < r.queue.length &&
        r.queue.charCodeAt(end - 1) >= 0xd800 &&
        r.queue.charCodeAt(end - 1) <= 0xdbff
      )
        end--;
      const data = r.queue.slice(0, end);
      r.queue = r.queue.slice(end);
      const size = r.size || {};
      r.size = null;
      await api("remote/sessions/" + r.id + "/input", "POST", {
        data,
        ...size,
      });
    }
  } catch (e) {
    r.queue = "";
    remoteMessage(e.message, true);
  } finally {
    if (epoch === r.epoch) r.sending = false;
  }
}
async function remotePoll(epoch) {
  const r = remoteState;
  try {
    while (r.id && epoch === r.epoch) {
      const v = await api(
        "remote/sessions/" + r.id + "/output?offset=" + r.offset,
      );
      if (epoch !== r.epoch) return;
      r.offset = v.offset;
      if (v.truncated)
        r.terminal.write("\r\n[输出过多，部分较早内容已丢弃]\r\n");
      if (v.data) {
        const raw = Uint8Array.from(atob(v.data), (c) => c.charCodeAt(0));
        await new Promise((resolve) => r.terminal.write(raw, resolve));
      }
      if (v.closed) {
        remoteMessage(v.reason || "已断开");
        remoteDisconnect();
        return;
      }
    }
  } catch (e) {
    if (epoch === r.epoch) {
      remoteMessage(e.message, true);
      const form = $("remote-login");
      remoteDisconnect();
      if (form) form.hidden = false;
    }
  }
}
window.addEventListener("pagehide", remoteDisconnect);
