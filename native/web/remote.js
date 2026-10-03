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
  if (remoteState.node === node) return;
  remoteDisconnect();
  remoteState.node = node;
  remoteState.built = null;
  $("remote-body").replaceChildren();
}
function remoteDisconnect(clearPassword = true) {
  const r = remoteState,
    id = r.id;
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
  head.append(el("h2", r.node));
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
    button("断开连接", () => {
      remoteDisconnect();
      form.hidden = false;
      work.hidden = true;
      remoteMessage("已断开");
    }),
  );
  const terminal = el("div", undefined, "remote-terminal");
  terminal.id = "remote-terminal";
  const files = el("section", undefined, "remote-files");
  files.id = "remote-files";
  const filehead = el("div", undefined, "remote-file-head");
  filehead.append(el("h3", "文件 · SFTP"));
  const pathform = el("form", undefined, "remote-path-form"),
    pathinput = el("input");
  pathinput.id = "remote-path";
  pathinput.setAttribute("aria-label", "远程目录");
  pathinput.placeholder = "/home/user";
  const go = el("button", "进入");
  go.type = "submit";
  pathform.append(pathinput, go);
  pathform.onsubmit = (event) => {
    event.preventDefault();
    remoteFiles(pathinput.value);
  };
  const upload = el("input");
  upload.type = "file";
  upload.id = "remote-upload";
  upload.setAttribute("aria-label", "上传文件");
  upload.onchange = () => {
    if (upload.files[0]) remoteUpload(upload.files[0]);
  };
  const progress = el("progress");
  progress.id = "remote-upload-progress";
  progress.hidden = true;
  progress.max = 100;
  const status = el("p", "", "muted");
  status.id = "remote-file-status";
  status.setAttribute("role", "status");
  const list = el("div", undefined, "remote-file-list");
  list.id = "remote-file-list";
  files.append(
    filehead,
    pathform,
    upload,
    el("small", "单文件上限 1 GiB，同名文件不覆盖。", "muted"),
    progress,
    status,
    list,
  );
  work.append(toolbar, terminal, files);
  root.replaceChildren(head, form, message, work);
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
      fontSize: 14,
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
        r.fit.fit();
        r.size = {
          cols: Math.max(20, Math.min(400, r.terminal.cols)),
          rows: Math.max(5, Math.min(150, r.terminal.rows)),
        };
        remoteSend(epoch);
      }, 160);
    });
    r.resize.observe($("remote-terminal"));
    r.fit.fit();
    r.terminal.focus();
    remoteMessage("已连接");
    remotePoll(epoch);
    $("remote-files").hidden = !result.sftp;
    if (result.sftp) await remoteFiles("");
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
        r.id = null;
        $("remote-login").hidden = false;
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
async function remoteFiles(directory) {
  const r = remoteState,
    id = r.id;
  if (!id) return;
  try {
    const v = await api(
      "remote/sessions/" + id + "/files?path=" + encodeURIComponent(directory),
    );
    if (id !== r.id) return;
    $("remote-path").value = v.path;
    const list = $("remote-file-list");
    list.replaceChildren();
    if (v.path !== "/")
      list.append(
        button(
          "↑ 上级目录",
          () => remoteFiles(v.path.slice(0, v.path.lastIndexOf("/")) || "/"),
          "quiet",
        ),
      );
    for (const f of v.entries) {
      const row = el("div", undefined, "remote-file-row"),
        full = (v.path === "/" ? "" : v.path) + "/" + f.name;
      row.append(
        el("span", f.directory ? "目录" : f.symlink ? "链接" : "文件", "muted"),
      );
      const open = button(
        f.name,
        () => {
          if (f.directory || f.symlink) remoteFiles(full);
          else {
            const a = el("a");
            a.href =
              "/api/v1/remote/sessions/" +
              id +
              "/download?path=" +
              encodeURIComponent(full);
            a.download = f.name;
            document.body.append(a);
            a.click();
            a.remove();
          }
        },
        "quiet",
      );
      open.title = f.name;
      row.append(
        open,
        el("span", f.directory ? "—" : bytes(f.bytes)),
        el("small", f.mode, "muted"),
      );
      list.append(row);
    }
    $("remote-file-status").textContent = v.truncated
      ? "仅显示前 2000 项，可输入完整路径访问子目录。"
      : v.entries.length + " 项";
  } catch (e) {
    if (id === r.id) {
      $("remote-file-status").textContent = e.message;
      $("remote-file-list").replaceChildren();
    }
  }
}
function remoteUpload(file) {
  const r = remoteState,
    id = r.id,
    input = $("remote-upload"),
    progress = $("remote-upload-progress"),
    status = $("remote-file-status");
  if (!id || r.upload) return;
  if (file.size > 1073741824) {
    status.textContent = "文件超过 1 GiB";
    input.value = "";
    return;
  }
  const directory = $("remote-path").value,
    target = (directory === "/" ? "" : directory) + "/" + file.name;
  const xhr = new XMLHttpRequest();
  r.upload = xhr;
  input.disabled = true;
  progress.hidden = false;
  progress.value = 0;
  xhr.open(
    "POST",
    "/api/v1/remote/sessions/" +
      id +
      "/upload?path=" +
      encodeURIComponent(target),
  );
  xhr.setRequestHeader("X-BITS-Request", "1");
  xhr.timeout = 240000;
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) progress.value = (100 * e.loaded) / e.total;
  };
  xhr.onload = () => {
    let v;
    try {
      v = JSON.parse(xhr.responseText);
    } catch {
      v = { error: "上传失败" };
    }
    if (id !== r.id) return;
    if (xhr.status === 200) {
      status.textContent = "上传完成";
      remoteFiles(directory);
    } else status.textContent = v.error || "上传失败";
  };
  xhr.onerror = xhr.ontimeout = () => {
    status.textContent = "上传中断，请检查连接";
  };
  xhr.onloadend = () => {
    if (r.upload === xhr) r.upload = null;
    input.disabled = false;
    input.value = "";
    progress.hidden = true;
  };
  xhr.send(file);
}
window.addEventListener("pagehide", remoteDisconnect);
