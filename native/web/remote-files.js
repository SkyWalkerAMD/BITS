"use strict";
let transferState = null;
function fileAction(text, fn, id, cls = "") {
  const control = button(text, fn, cls);
  if (id) control.id = id;
  return control;
}
function fileStatus(text, bad = false) {
  const status = $("remote-file-status");
  if (status) {
    status.textContent = text;
    status.classList.toggle("error", bad);
  }
}
function remoteCloseFiles() {
  const state = transferState;
  transferState = null;
  if (!state) return;
  state.disposed = true;
  state.active?.xhr?.abort();
  state.dialog.close();
  state.dialog.remove();
  state.local = [];
  state.stack = [];
  state.queue = [];
}
function fileCurrent(state) {
  return state === transferState && !state.disposed && state.id === remoteState.id;
}
function remoteCreateFiles(connection) {
  remoteCloseFiles();
  const dialog = el("dialog", undefined, "remote-files");
  dialog.id = "remote-files";
  dialog.setAttribute("aria-label", "文件传输");
  const state = {
    id: remoteState.id, dialog, disposed: false, busy: false, active: null,
    path: null, entries: [], remoteSelected: new Set(), local: [], localSelected: new Set(),
    stack: [], localVersion: 0, queue: [], serial: 0, paused: false, pendingPath: null,
  };
  transferState = state;
  const header = el("header", undefined, "file-window-heading"), title = el("div");
  title.append(el("h2", "文件传输"), el("span", connection.username + "@" + connection.address, "muted"));
  header.append(title, fileAction("关闭", () => dialog.close(), "remote-files-close"));
  const panes = el("div", undefined, "file-panes"), local = filePane("本地文件", "local"), remote = filePane("远程目录", "remote");
  const picker = el("input"), folder = el("input");
  picker.type = folder.type = "file";
  picker.multiple = folder.multiple = true;
  picker.hidden = folder.hidden = true;
  picker.id = "remote-upload";
  folder.id = "remote-folder-input";
  folder.setAttribute("webkitdirectory", "");
  picker.onchange = () => { fileSetLocal([...picker.files]); picker.value = ""; };
  folder.onchange = () => {
    const files = [...folder.files];
    folder.value = "";
    if (!files.length) return;
    state.stack = [];
    state.fallback = files.slice(0, 5000);
    fileFallbackDirectory(files[0].webkitRelativePath.split("/")[0] + "/");
    if (files.length > 5000) fileStatus("本地目录仅载入前 5000 个文件");
  };
  local.tools.append(
    fileAction("选择文件", () => picker.click(), "local-pick-files"),
    fileAction("打开文件夹", filePickDirectory, "local-pick-folder"),
    fileAction("上传所选 →", fileUploadSelected, "local-upload-selected", "primary"), picker, folder,
  );
  local.path.readOnly = true;
  local.path.value = "选择本地文件或文件夹";
  local.path.setAttribute("aria-label", "本地目录");
  local.up.onclick = () => fileLocalUp();
  local.refresh.onclick = () => fileRefreshLocal();
  local.all.onchange = () => {
    state.localSelected = new Set(local.all.checked ? state.local.filter(f => !f.directory).map(f => f.key) : []);
    fileRenderLocal();
  };
  remote.tools.append(
    fileAction("登录目录", () => remoteFiles(""), "remote-home"),
    fileAction("下载所选", fileDownloadSelected, "remote-download-selected"),
    el("small", "可拖入文件上传", "muted"),
  );
  remote.path.id = "remote-path";
  remote.path.setAttribute("aria-label", "远程目录");
  remote.path.placeholder = "/home/user";
  const go = el("button", "进入");
  go.type = "submit";
  remote.pathform.append(go);
  remote.pathform.classList.add("remote-path-form");
  remote.pathform.onsubmit = event => { event.preventDefault(); remoteFiles(remote.path.value); };
  remote.up.onclick = () => state.path && remoteFiles(state.path.slice(0, state.path.lastIndexOf("/")) || "/");
  remote.refresh.onclick = () => remoteFiles(state.path || "");
  remote.all.style.visibility = "hidden";
  remote.root.id = "remote-drop-zone";
  remote.list.id = "remote-file-list";
  const drop = el("div", "松开上传到当前远程目录", "file-drop-hint");
  remote.root.append(drop);
  for (const pane of [local, remote]) {
    pane.root.ondragover = event => {
      if ([...event.dataTransfer.types].some(t => t === "Files" || t === "application/x-bits-local-files")) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        pane.root.classList.add("file-drag-over");
      }
    };
    pane.root.ondragleave = event => { if (!pane.root.contains(event.relatedTarget)) pane.root.classList.remove("file-drag-over"); };
    pane.root.ondrop = event => {
      event.preventDefault();
      pane.root.classList.remove("file-drag-over");
      if (event.dataTransfer.types.includes("application/x-bits-local-files")) {
        if (pane === remote) fileUploadSelected();
        return;
      }
      const items = [...event.dataTransfer.items];
      if (items.some(item => item.webkitGetAsEntry?.()?.isDirectory)) {
        fileStatus("请打开文件夹后选择其中的文件", true);
        return;
      }
      const files = [...event.dataTransfer.files];
      if (!files.length) return;
      fileSetLocal(files);
      if (pane === remote) fileUploadSelected();
    };
  }
  const status = el("p", "", "file-window-status");
  status.id = "remote-file-status";
  status.setAttribute("role", "status");
  const queue = el("section", undefined, "file-queue"), queuehead = el("div", undefined, "file-queue-heading"), summary = el("strong", "上传队列");
  summary.id = "file-queue-summary";
  queuehead.append(summary,
    fileAction("暂停队列", () => { state.paused = !state.paused; fileRenderQueue(); fileRunQueue(); }, "file-queue-pause"),
    fileAction("清除已结束", () => { state.queue = state.queue.filter(item => ["waiting", "uploading"].includes(item.state)); fileRenderQueue(); }, "file-queue-clear"));
  const queuebody = el("div", undefined, "file-queue-list");
  queuebody.id = "file-queue-list";
  queue.append(queuehead, queuebody);
  panes.append(local.root, remote.root);
  dialog.append(header, panes, status, queue, el("small", "单文件上限 1 GiB · 同名文件不覆盖", "file-window-note"));
  dialog.onclose = () => { if (remoteState.id === state.id) remoteState.terminal?.focus(); };
  $("remote-body").append(dialog);
  fileRenderLocal();
  fileRenderRemote();
  fileRenderQueue();
}
function filePane(title, side) {
  const root = el("section", undefined, "file-pane"), heading = el("div", undefined, "file-pane-heading"), count = el("span", "", "muted"), tools = el("div", undefined, "file-pane-tools");
  count.id = side + "-file-count";
  heading.append(el("h3", title), count);
  const pathform = el("form", undefined, "file-path"), path = el("input"), up = fileAction("↑", () => {}, side + "-up"), refresh = fileAction("↻", () => {}, side + "-refresh");
  up.setAttribute("aria-label", title + "上级目录");
  refresh.setAttribute("aria-label", "刷新" + title);
  path.id = side + "-path";
  pathform.append(up, path, refresh);
  pathform.onsubmit = event => event.preventDefault();
  const labels = el("div", undefined, "file-columns"), all = el("input");
  all.type = "checkbox";
  all.id = side + "-select-all";
  all.setAttribute("aria-label", "全选" + title);
  labels.append(all, el("span", "名称"), el("span", "大小"), el("span", "修改时间"));
  const list = el("div", undefined, "file-entry-list");
  list.id = side + "-file-list";
  root.append(heading, tools, pathform, labels, list);
  return { root, tools, pathform, path, up, refresh, all, list };
}
function remoteOpenFiles() {
  const state = transferState;
  if (!state || !fileCurrent(state)) return;
  if (!state.dialog.open) state.dialog.showModal();
  if (state.path === null) remoteFiles("");
}
function fileSetLocal(files) {
  const state = transferState;
  if (!state) return;
  state.localVersion++;
  state.stack = [];
  state.fallback = null;
  state.local = files.slice(0, 2000).map((file, index) => ({ key: String(index), name: file.name, file, bytes: file.size, modified: file.lastModified }));
  state.localSelected = new Set(state.local.map(file => file.key));
  $("local-path").value = "已选择的文件";
  fileRenderLocal();
  if (files.length > 2000) fileStatus("一次最多选择 2000 个本地文件", true);
}
async function filePickDirectory() {
  const state = transferState;
  if (!state) return;
  if (!window.showDirectoryPicker) { $("remote-folder-input").click(); return; }
  try {
    const handle = await window.showDirectoryPicker({ mode: "read", id: "bits-sftp-local" });
    if (!fileCurrent(state)) return;
    state.fallback = null;
    state.stack = [handle];
    await fileReadDirectory();
  } catch (error) {
    if (fileCurrent(state) && error.name !== "AbortError") fileStatus("无法打开本地文件夹，请使用“选择文件”", true);
  }
}
async function fileReadDirectory() {
  const state = transferState, handle = state?.stack.at(-1);
  if (!handle) return;
  const version = ++state.localVersion, entries = [];
  state.local = [];
  state.localSelected.clear();
  $("local-path").value = state.stack.map(h => h.name).join("/");
  fileRenderLocal();
  try {
    for await (const [name, child] of handle.entries()) {
      if (!fileCurrent(state) || version !== state.localVersion) return;
      if (entries.length >= 2000) break;
      const directory = child.kind === "directory", file = directory ? null : await child.getFile();
      entries.push({ key: name, name, directory, handle: child, file, bytes: file?.size, modified: file?.lastModified });
    }
    if (!fileCurrent(state) || version !== state.localVersion) return;
    state.local = entries;
    state.localSelected.clear();
    $("local-path").value = state.stack.map(h => h.name).join("/");
    fileRenderLocal();
    if (entries.length === 2000) fileStatus("本地目录仅显示前 2000 项");
  } catch {
    if (fileCurrent(state)) fileStatus("本地目录读取失败，请重新选择文件夹", true);
  }
}
function fileFallbackDirectory(prefix) {
  const state = transferState, entries = new Map();
  if (!state?.fallback) return;
  state.localVersion++;
  state.prefix = prefix;
  for (const file of state.fallback) {
    if (!file.webkitRelativePath.startsWith(prefix)) continue;
    const relative = file.webkitRelativePath.slice(prefix.length), slash = relative.indexOf("/");
    if (slash < 0) entries.set(relative, { key: relative, name: relative, file, bytes: file.size, modified: file.lastModified });
    else {
      const name = relative.slice(0, slash);
      entries.set(name, { key: name, name, directory: true, prefix: prefix + name + "/" });
    }
  }
  state.local = [...entries.values()].slice(0, 2000);
  state.localSelected.clear();
  $("local-path").value = prefix;
  fileRenderLocal();
}
function fileLocalUp() {
  const state = transferState;
  if (state?.stack.length > 1) { state.stack.pop(); return fileReadDirectory(); }
  if (state?.fallback && state.prefix.split("/").length > 2)
    fileFallbackDirectory(state.prefix.slice(0, -1).split("/").slice(0, -1).join("/") + "/");
}
function fileRefreshLocal() {
  if (transferState?.stack.length) return fileReadDirectory();
  fileRenderLocal();
}
function fileEntry(entry, selected, changed, open) {
  const row = el("div", undefined, "file-entry"), check = el("input");
  check.type = "checkbox";
  check.checked = selected;
  check.disabled = !!(entry.directory || entry.symlink);
  check.setAttribute("aria-label", "选择 " + entry.name);
  check.onchange = () => changed(check.checked);
  const name = fileAction(entry.name, open, "", "file-name");
  name.title = entry.name;
  name.dataset.kind = entry.directory ? "folder" : entry.symlink ? "link" : "file";
  const modified = entry.modified ? new Date(entry.modified).toLocaleString() : "—";
  row.append(check, name, el("span", entry.directory ? "—" : bytes(entry.bytes || 0), "file-bytes"), el("small", modified, "file-modified"));
  row.classList.toggle("file-selected", selected);
  return row;
}
function fileSelectControl(side, count, total) {
  const all = $(side + "-select-all");
  all.checked = total > 0 && count === total;
  all.indeterminate = count > 0 && count < total;
  all.disabled = !total;
  $(side + "-file-count").textContent = total + " 个文件 · 已选 " + count;
}
function fileRenderLocal() {
  const state = transferState;
  if (!state) return;
  const list = $("local-file-list");
  list.replaceChildren();
  state.local.sort((a, b) => Number(!!b.directory) - Number(!!a.directory) || a.name.localeCompare(b.name));
  for (const entry of state.local) {
    const row = fileEntry(entry, state.localSelected.has(entry.key), checked => {
      if (checked) state.localSelected.add(entry.key); else state.localSelected.delete(entry.key);
      fileRenderLocal();
    }, () => {
      if (entry.directory) {
        if (entry.handle) { state.stack.push(entry.handle); return fileReadDirectory(); }
        return fileFallbackDirectory(entry.prefix);
      }
      if (state.localSelected.has(entry.key)) state.localSelected.delete(entry.key); else state.localSelected.add(entry.key);
      fileRenderLocal();
    });
    row.draggable = !entry.directory;
    row.ondragstart = event => {
      if (!state.localSelected.has(entry.key)) {
        state.localSelected = new Set([entry.key]);
        row.classList.add("file-selected");
      }
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-bits-local-files", "selected");
    };
    list.append(row);
  }
  if (!state.local.length) list.append(el("p", "选择文件或打开本地文件夹", "file-empty"));
  const total = state.local.filter(f => !f.directory).length;
  fileSelectControl("local", state.localSelected.size, total);
  $("local-upload-selected").disabled = !state.localSelected.size || state.path === null;
  $("local-up").disabled = !(state.stack.length > 1 || (state.fallback && state.prefix.split("/").length > 2));
}
async function remoteFiles(directory) {
  const state = transferState;
  if (!state || !fileCurrent(state)) return;
  if (state.busy) { state.pendingPath = directory; fileStatus("传输结束后刷新目录"); return; }
  state.busy = true;
  fileStatus("正在读取目录…");
  try {
    const response = await api("remote/sessions/" + state.id + "/files?path=" + encodeURIComponent(directory));
    if (!fileCurrent(state)) return;
    state.path = response.path;
    state.entries = response.entries;
    state.remoteSelected.clear();
    $("remote-path").value = state.path;
    fileRenderRemote();
    fileRenderLocal();
    fileStatus(response.truncated ? "仅显示前 2000 项" : response.entries.length + " 项");
  } catch (error) {
    if (fileCurrent(state)) {
      state.path = null;
      state.entries = [];
      state.remoteSelected.clear();
      fileRenderRemote();
      fileRenderLocal();
      fileStatus(error.message, true);
    }
  } finally {
    state.busy = false;
    if (fileCurrent(state)) fileNextOperation();
  }
}
function fileRenderRemote() {
  const state = transferState;
  if (!state) return;
  const list = $("remote-file-list");
  list.replaceChildren();
  for (const entry of state.entries) {
    const full = (state.path === "/" ? "" : state.path) + "/" + entry.name;
    list.append(fileEntry(entry, state.remoteSelected.has(entry.name), checked => {
      state.remoteSelected = new Set(checked ? [entry.name] : []);
      fileRenderRemote();
    }, () => entry.directory || entry.symlink ? remoteFiles(full) : fileDownload(entry.name, full)));
  }
  if (!state.entries.length) list.append(el("p", state.path ? "目录为空，可将文件拖到这里上传" : "等待选择远程目录", "file-empty"));
  fileSelectControl("remote", state.remoteSelected.size, state.entries.filter(f => !f.directory && !f.symlink).length);
  $("remote-download-selected").disabled = !state.remoteSelected.size || state.busy;
  $("remote-up").disabled = !state.path || state.path === "/";
}
function fileDownload(name, path) {
  const state = transferState;
  if (!state || !fileCurrent(state)) return;
  if (state.busy) { fileStatus("请等待当前文件操作完成后下载", true); return; }
  const a = el("a");
  a.href = "/api/v1/remote/sessions/" + state.id + "/download?path=" + encodeURIComponent(path);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  fileStatus("已交给浏览器下载：" + name);
}
function fileDownloadSelected() {
  const state = transferState;
  if (!state?.path) return;
  // Browser downloads own their progress; do not report them as completed here.
  for (const name of state.remoteSelected)
    fileDownload(name, (state.path === "/" ? "" : state.path) + "/" + name);
}
function fileUploadSelected() {
  const state = transferState;
  if (!state || state.path === null) { fileStatus("请先打开目标远程目录", true); return; }
  const selected = state.local.filter(f => state.localSelected.has(f.key) && !f.directory);
  for (const entry of selected) {
    if (state.queue.length >= 2000) { fileStatus("队列已满，请清除已结束的记录", true); break; }
    const file = entry.file, target = (state.path === "/" ? "" : state.path) + "/" + entry.name;
    if (state.queue.some(item => item.target === target && ["waiting", "uploading"].includes(item.state))) continue;
    const error = !file || file.size > 1073741824 ? "文件超过 1 GiB 或不可读取" : /[\/\\\x00\r\n]/.test(file.name) ? "文件名无效" : "";
    state.queue.push({ serial: ++state.serial, name: entry.name, file, target, state: error ? "failed" : "waiting", error, progress: 0 });
  }
  fileRenderQueue();
  fileRunQueue();
}
function fileNextOperation() {
  const state = transferState;
  if (!state || state.busy) return;
  if (state.pendingPath !== null) {
    const path = state.pendingPath;
    state.pendingPath = null;
    remoteFiles(path);
  } else fileRunQueue();
}
async function fileRunQueue() {
  const state = transferState;
  if (!state || !fileCurrent(state) || state.busy || state.paused) return;
  const item = state.queue.find(item => item.state === "waiting");
  if (!item) return;
  state.busy = true;
  state.active = item;
  item.state = "uploading";
  fileRenderQueue();
  try {
    await fileSend(state, item);
    if (!fileCurrent(state)) return;
    item.state = "completed";
    item.progress = 100;
    item.file = null;
    fileStatus("上传完成：" + item.name);
  } catch (error) {
    if (!fileCurrent(state)) return;
    item.state = item.cancelled ? "cancelled" : "failed";
    item.error = item.cancelled ? "已取消，请刷新目录核对" : error.message;
    fileStatus(item.error, !item.cancelled);
  } finally {
    state.busy = false;
    state.active = null;
    if (fileCurrent(state)) {
      fileRenderQueue();
      if (state.pendingPath === null && state.path !== null) state.pendingPath = state.path;
      fileNextOperation();
    }
  }
}
function fileSend(state, item) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    xhr.open("POST", "/api/v1/remote/sessions/" + state.id + "/upload?path=" + encodeURIComponent(item.target));
    xhr.setRequestHeader("X-BITS-Request", "1");
    xhr.timeout = 240000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable && fileCurrent(state)) {
        item.progress = Math.min(99, Math.floor(100 * event.loaded / event.total));
        const row = $("transfer-" + item.serial);
        if (row) { row.querySelector("progress").value = item.progress; row.querySelector(".file-transfer-state").textContent = item.progress === 99 ? "正在保存" : item.progress + "%"; }
      }
    };
    xhr.onload = () => {
      let result;
      try { result = JSON.parse(xhr.responseText); } catch { result = {}; }
      if (xhr.status === 200 && result.ok) resolve(); else reject(new Error(result.error || "上传失败"));
    };
    xhr.onerror = xhr.ontimeout = () => reject(new Error("上传中断，请检查连接后重试"));
    xhr.onabort = () => reject(new Error("已取消"));
    xhr.onloadend = () => { item.xhr = null; };
    xhr.send(item.file);
  });
}
function fileRenderQueue() {
  const state = transferState;
  if (!state) return;
  const list = $("file-queue-list"), labels = { waiting: "等待上传", uploading: "上传中", completed: "已完成", failed: "失败", cancelled: "已取消" };
  list.replaceChildren();
  for (const item of state.queue) {
    const row = el("div", undefined, "file-transfer-row"), name = el("div"), progress = el("progress"), text = el("span", item.error || labels[item.state], "file-transfer-state");
    row.id = "transfer-" + item.serial;
    row.dataset.state = item.state;
    name.append(el("strong", item.name), el("small", item.target));
    progress.max = 100;
    progress.value = item.progress;
    progress.setAttribute("aria-label", item.name + " 上传进度");
    const action = el("div");
    if (["waiting", "uploading"].includes(item.state)) action.append(fileAction("取消", () => {
      item.cancelled = true;
      if (item.state === "uploading") item.xhr?.abort();
      else { item.state = "cancelled"; item.file = null; fileRenderQueue(); }
    }));
    if (item.state === "failed" && item.file) action.append(fileAction("重试", () => {
      item.state = "waiting"; item.error = ""; item.progress = 0; fileRenderQueue(); fileRunQueue();
    }));
    row.append(name, progress, text, action);
    list.append(row);
  }
  if (!state.queue.length) list.append(el("p", "暂无传输", "file-empty"));
  const completed = state.queue.filter(item => item.state === "completed").length, pending = state.queue.filter(item => ["waiting", "uploading"].includes(item.state)).length;
  $("file-queue-summary").textContent = "上传队列 · " + completed + " 完成 · " + pending + " 待完成";
  $("file-queue-pause").textContent = state.paused ? "继续队列" : "暂停队列";
  $("file-queue-pause").setAttribute("aria-pressed", String(state.paused));
}
