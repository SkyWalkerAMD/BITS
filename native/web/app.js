"use strict";
const $ = (id) => document.getElementById(id);
const activeStates = ["waiting_boot", "armed", "running", "finishing"];
const labels = {
  deleting: "正在永久删除",
  waiting_boot: "开机 / 等待系统连接",
  draft: "待开始",
  armed: "等待节点接受",
  running: "执行中",
  finishing: "整理 / 交付中",
  needs_attention: "需要处理",
  delivered: "已核验交付",
  closed_incomplete: "已关闭 · 未完成",
  cancelled: "已取消",
  not_started: "未开始",
  completed: "按计划结束",
  failed: "执行失败",
  interrupted: "已中断",
  preflight_failed: "预检未通过",
  readings_reported_validity_unknown: "读数有效性未知",
  not_collected: "尚未采集",
  partial: "数据不完整",
  unavailable: "不可用",
  generated: "已生成",
  not_generated: "尚未生成",
  prepared: "执行前检查",
  executing: "正在压测",
  finalizing: "生成报告",
  delivering: "上传与核验",
  blocked: "需要处理",
  duration_reached: "达到预定时长",
  exited_early: "提前退出",
  terminated: "已停止",
};
const pageInfo = {
  info: ["硬件信息", "HARDWARE PROFILE"],
  dispatch: ["任务分发", "DISPATCH WORKSPACE"],
  group: ["任务组详情", "GROUP WORKSPACE"],
  overview: ["运行总览", "OPERATIONS OVERVIEW"],
  nodes: ["测试节点", "NODE FLEET"],
  batches: ["压测批次", "TEST BATCHES"],
  reports: ["结果与报告", "RESULTS & EVIDENCE"],
  guide: ["部署与操作", "GETTING STARTED"],
  detail: ["批次详情", "BATCH WORKSPACE"],
  monitor: ["硬件实时监控", "LIVE HARDWARE MONITOR"],
};
const iconPaths = {
  overview: ["M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"],
  nodes: ["M4 3h16v7H4zM4 14h16v7H4zM7 6.5h.1M7 17.5h.1M11 6.5h6M11 17.5h6"],
  batches: ["M6 3h12v18H6zM9 7h6M9 12h6M9 17h4"],
  reports: ["M6 3h8l4 4v14H6zM14 3v5h4M9 17v-4M12 17v-7M15 17v-5"],
  guide: ["M3 4h7l2 2 2-2h7v15h-7l-2 2-2-2H3zM12 6v15"],
  search: ["M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15M16 16l5 5"],
  plus: ["M12 5v14M5 12h14"],
  logout: ["M10 4H4v16h6M9 12h12M17 8l4 4-4 4"],
  activity: ["M2 12h4l3-8 5 16 3-8h5"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v6h4"],
  alert: ["M12 3 2 21h20zM12 9v5M12 17h.1"],
  check: ["M4 12l5 5L20 6"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  refresh: [
    "M20 7v5h-5M4 17v-5h5M5.6 7a8 8 0 0 1 13-1L20 8M4 16l1.4 2a8 8 0 0 0 13-1",
  ],
};
let snapshot = { nodes: [], batches: [], tools: [], version: "" };
let frames = new Map(),
  nodeFrames = new Map(),
  history = new Map(),
  detailData = null,
  detailEvents = [],
  detailFrame = null;
let monitorNode = null,
  nodeMonitorData = null;
let page = "overview",
  selected = null,
  selectedNode = null,
  nodeFilter = "all",
  nodePage = 0;
let batchPage = 0,
  reportPage = 0;
const routePositions = new Map(),
  inFlightActions = new Set();
let routeKey = "",
  restorePosition = null,
  syncRequested = false;
let coreSocket = "all",
  coreMode = "map",
  corePage = 0;
let authenticated = false,
  syncing = false,
  connected = false,
  serverOffset = 0,
  lastOverview = 0,
  lastDetail = 0,
  lastNetwork = 0,
  nextConfirm = null,
  wizardStep = 0;
const collator = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});
const mobileNavigation = window.matchMedia("(max-width: 830px)");
let compactNavigation = false;
try {
  compactNavigation =
    localStorage.getItem("bits.navigation.compact") === "true";
} catch {}
function navigation() {
  const drawer = $("navigation-dialog"),
    sidebar = $("main-navigation");
  if (mobileNavigation.matches) {
    if (sidebar.parentNode !== drawer) drawer.append(sidebar);
  } else {
    drawer.close();
    if (sidebar.parentNode !== document.body)
      document.body.insertBefore(sidebar, drawer);
  }
  document.body.classList.toggle(
    "navigation-compact",
    compactNavigation && !mobileNavigation.matches,
  );
  const expanded = mobileNavigation.matches ? drawer.open : !compactNavigation;
  $("navigation-toggle").setAttribute("aria-expanded", String(expanded));
  const name = mobileNavigation.matches
    ? "打开导航"
    : expanded
      ? "收起导航"
      : "展开导航";
  $("navigation-toggle").setAttribute("aria-label", name);
  $("navigation-toggle").title = name;
}
function scrollToArea(selector) {
  document
    .querySelector(selector)
    ?.scrollIntoView({ block: "start", behavior: "instant" });
}
function renderPager(root, total, size, index, change) {
  const count = Math.ceil(total / size),
    children = [];
  if (count > 1) {
    const prev = button(
        "← 上一页",
        () => change(index - 1),
        "",
        root.id + "-prev",
      ),
      next = button("下一页 →", () => change(index + 1), "", root.id + "-next");
    prev.disabled = index === 0;
    next.disabled = index + 1 >= count;
    children.push(
      prev,
      el(
        "span",
        `${index + 1} / ${count} · ${index * size + 1}–${Math.min(total, (index + 1) * size)} / ${total}`,
      ),
      next,
    );
  }
  replace(root, ...children);
}
const formStates = new WeakMap();
function formBusy(form, busy) {
  const dialog = form.closest("dialog");
  if (busy) {
    dialog.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    const states = [
      ...form.querySelectorAll("button,input,select,textarea"),
    ].map((e) => [e, e.disabled]);
    formStates.set(form, states);
    for (const [e] of states) e.disabled = true;
  } else {
    delete dialog.dataset.submitting;
    form.removeAttribute("aria-busy");
    for (const [e, disabled] of formStates.get(form) || [])
      e.disabled = disabled;
    formStates.delete(form);
  }
}
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function svgEl(tag, attrs = {}) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function icon(name) {
  const s = svgEl("svg", {
    viewBox: "0 0 24 24",
    class: "icon",
    "aria-hidden": "true",
  });
  for (const d of iconPaths[name] || iconPaths.nodes)
    s.append(svgEl("path", { d }));
  return s;
}
function decorate() {
  document
    .querySelectorAll("[data-icon]")
    .forEach((e) => e.replaceChildren(icon(e.dataset.icon)));
}
function badge(value, custom) {
  return el(
    "span",
    custom || labels[value] || value,
    "badge " +
      (["delivered", "completed", "generated"].includes(value)
        ? "good"
        : [
              "needs_attention",
              "failed",
              "interrupted",
              "preflight_failed",
            ].includes(value)
          ? "bad"
          : ["armed", "partial"].includes(value)
            ? "warn"
            : value === "running"
              ? "live"
              : ""),
  );
}
function now() {
  return Date.now() + serverOffset;
}
function age(stamp) {
  return stamp ? Math.max(0, (now() - Date.parse(stamp)) / 1000) : Infinity;
}
function ago(stamp) {
  const s = age(stamp);
  if (!Number.isFinite(s)) return "尚无记录";
  if (s < 5) return "刚刚";
  if (s < 60) return Math.floor(s) + " 秒前";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  return Math.floor(s / 86400) + " 天前";
}
function dateText(s) {
  return s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
}
function timeText(s) {
  return s ? new Date(s).toLocaleTimeString("zh-CN", { hour12: false }) : "—";
}
function duration(n) {
  n = Math.max(0, Math.floor(n || 0));
  if (n < 60) return n + " 秒";
  if (n < 3600) return Math.floor(n / 60) + " 分 " + (n % 60) + " 秒";
  if (n < 86400)
    return Math.floor(n / 3600) + " 时 " + Math.floor((n % 3600) / 60) + " 分";
  return (
    Math.floor(n / 86400) + " 天 " + Math.floor((n % 86400) / 3600) + " 时"
  );
}
function bytes(n) {
  return n > 1048576
    ? (n / 1048576).toFixed(1) + " MiB"
    : n > 1024
      ? (n / 1024).toFixed(1) + " KiB"
      : n + " B";
}
function number(v, places = 0) {
  return typeof v === "number" && Number.isFinite(v)
    ? v.toLocaleString("zh-CN", {
        maximumFractionDigits: places,
        minimumFractionDigits: places,
      })
    : "—";
}
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").hidden = !text;
}
function replace(root, ...children) {
  const key = root.contains(document.activeElement)
    ? document.activeElement.dataset.focusKey
    : null;
  const scrolls = [...root.querySelectorAll("[data-scroll-key]")].map((e) => [
    e.dataset.scrollKey,
    e.scrollTop,
    e.scrollLeft,
  ]);
  root.replaceChildren(...children);
  for (const [name, top, left] of scrolls) {
    const e = [...root.querySelectorAll("[data-scroll-key]")].find(
      (e) => e.dataset.scrollKey === name,
    );
    if (e) {
      e.scrollTop = top;
      e.scrollLeft = left;
    }
  }
  if (key) {
    const b = [...root.querySelectorAll("[data-focus-key]")].find(
      (e) => e.dataset.focusKey === key,
    );
    if (b) b.focus({ preventScroll: true });
  }
}
function button(text, fn, cls = "", key = "") {
  const b = el("button", text, cls);
  b.type = "button";
  if (key) b.dataset.focusKey = key;
  b.onclick = async () => {
    b.disabled = true;
    try {
      await fn();
    } catch (e) {
      notice(e.message, true);
    } finally {
      b.disabled = false;
    }
  };
  return b;
}
function link(text, href, cls = "text-link") {
  const a = el("a", text, cls);
  a.href = href;
  return a;
}
function fileLink(b, name, text) {
  const a = link(
    text || name,
    "/api/v1/batches/" + b.id + "/files/" + encodeURIComponent(name),
  );
  a.target = "_blank";
  a.rel = "noopener";
  a.dataset.focusKey = "file-" + b.id + "-" + name;
  return a;
}
function empty(title, text) {
  const d = el("div", undefined, "empty");
  d.append(el("strong", title), el("p", text));
  return d;
}
function metric(label, value, unit, places = 0) {
  const d = el("div", undefined, "metric"),
    v = el("strong", number(value, places));
  v.append(el("small", unit));
  d.append(el("label", label), v);
  return d;
}
async function api(path, method = "GET", data) {
  const actionKey = method + " " + path;
  if (method !== "GET" && inFlightActions.has(actionKey))
    throw new Error("此操作正在提交，请等待结果。");
  if (method !== "GET") inFlightActions.add(actionKey);
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 10000);
  try {
    const r = await fetch("/api/v1/" + path, {
      method,
      credentials: "same-origin",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-BITS-Request": "1" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    if (r.status === 401) {
      authenticated = false;
      connected = false;
      for (const d of document.querySelectorAll("dialog[open]"))
        if (d.id !== "login-dialog") d.close();
      if (!$("login-dialog").open) $("login-dialog").showModal();
      throw new Error("请登录工作台");
    }
    const text = await r.text();
    let value;
    try {
      value = JSON.parse(text);
    } catch {
      value = { error: text };
    }
    if (!r.ok) throw new Error(value.error || "请求未完成");
    return value;
  } catch (e) {
    if (e.name === "AbortError")
      throw new Error("请求超时，尚未确认服务器状态");
    throw e;
  } finally {
    inFlightActions.delete(actionKey);
    clearTimeout(timer);
  }
}
function connectionFresh() {
  return connected && Date.now() - lastNetwork < 12000;
}
function connection() {
  const fresh = connectionFresh();
  $("connection").className = "connection " + (fresh ? "online" : "offline");
  $("connection").textContent = fresh
    ? "实时同步 · 2 秒"
    : "连接中断 · 状态待确认";
  $("connection").title = lastNetwork
    ? "浏览器最后同步：" + dateText(new Date(lastNetwork).toISOString())
    : "等待首次同步";
  $("connection-warning").hidden = fresh || !authenticated;
  return fresh;
}
function online(n) {
  const elapsed = (now() - Date.parse(n.last_seen)) / 1000;
  return (
    !n.disabled &&
    connectionFresh() &&
    elapsed >= -5 &&
    elapsed < 20 &&
    !(
      powerFresh(n) &&
      n.power.state === "off" &&
      Date.parse(n.power.checked_at) > Date.parse(n.last_seen)
    )
  );
}
function powerFresh(n) {
  const elapsed = (now() - Date.parse(n.power?.checked_at)) / 1000;
  return (
    connectionFresh() && n.power?.configured && elapsed >= -5 && elapsed < 90
  );
}
function reachable(n) {
  return (
    !n.disabled &&
    (online(n) || (powerFresh(n) && ["on", "off"].includes(n.power.state)))
  );
}
function nodePowerText(n) {
  if (!n.power?.configured) return "BMC 未绑定 · 电源状态未知";
  const value = powerFresh(n)
    ? { off: "电源关", on: "电源开" }[n.power.state]
    : null;
  return "BMC " + n.power.address + " · " + (value || "状态待确认");
}
const nodeStateLabels = {
  idle: "空闲",
  wakeable: "已关机 · 可唤醒",
  awaiting_agent: "已开机 · 等待系统",
  waking: "正在唤醒 · 等待系统",
  offline: "状态待确认",
  disabled: "已禁用",
};
function activeBatch(node) {
  return (
    snapshot.batches.find(
      (b) => b.plan.node === node && activeStates.includes(b.state),
    ) ||
    snapshot.batches.find(
      (b) => b.plan.node === node && b.state === "needs_attention",
    )
  );
}
function latestBatch(node) {
  return snapshot.batches.find((b) => b.plan.node === node);
}
function nodeKind(n) {
  const b = activeBatch(n.id);
  if (n.disabled) return "disabled";
  if (isWaking(n) && !online(n)) return "waking";
  if (!online(n))
    return powerFresh(n) && n.power.state === "off"
      ? "wakeable"
      : powerFresh(n) && n.power.state === "on"
        ? "awaiting_agent"
        : "offline";
  if (b?.state === "needs_attention") return "attention";
  return b ? "running" : "idle";
}
function frameFor(b) {
  const f = b ? frames.get(b.plan.node) : null;
  return f?.batch === b?.id ? f : null;
}
function nodeFrameFor(n, b) {
  if (b && ["armed", "running"].includes(b.state)) return frameFor(b);
  return nodeFrames.get(n.id) || frameFor(b);
}
function nodeMetrics(f, connected) {
  const metrics = el(
      "div",
      undefined,
      "node-metrics" + (connected && freshSample(f) ? "" : " stale"),
    ),
    sample = f?.sample || {};
  metrics.append(
    metric("CPU 温度", sample.temp_c, "°C"),
    metric("CPU Pkg", sample.package_w, "W", 1),
    metric("核心频率", sample.mhz, "MHz"),
  );
  return metrics;
}
function freshSample(f) {
  return (
    connectionFresh() &&
    !!f?.sample?.available &&
    age(f.sample_received_at) < 15 &&
    Math.abs(now() - Date.parse(f.sample.observed_at)) < 20000
  );
}
function validFrame(f) {
  return connectionFresh() && !!f && age(f.received_at) < 20;
}
function progress(elapsed, budget) {
  const d = el("div", undefined, "progress"),
    i = el("i"),
    pct = budget ? Math.max(0, Math.min(100, (elapsed / budget) * 100)) : 0;
  i.style.width = pct + "%";
  d.setAttribute("role", "progressbar");
  d.setAttribute("aria-label", "已用时长比例");
  d.setAttribute("aria-valuenow", String(Math.floor(pct)));
  d.setAttribute("aria-valuemin", "0");
  d.setAttribute("aria-valuemax", "100");
  d.append(i);
  return d;
}
function trend(rows, field, mini = false) {
  const w = mini ? 90 : 340,
    h = mini ? 30 : 126,
    pad = mini ? 2 : 7,
    left = mini ? pad : 44,
    s = svgEl("svg", {
      viewBox: `0 0 ${w} ${h}`,
      class: mini ? "mini-trend" : "trend-chart",
      role: "img",
      "aria-label":
        "CPU " +
        (field === "temp_c" ? "温度" : "封装功耗") +
        "趋势，来自程序报告值",
    }),
    values = rows
      .filter((v) => v.available && typeof v[field] === "number")
      .map((v) => v[field]);
  if (!values.length) {
    if (!mini) {
      const t = svgEl("text", {
        x: w / 2,
        y: h / 2,
        "text-anchor": "middle",
        class: "trend-label",
      });
      t.textContent = "尚无可绘制的采样";
      s.append(t);
    }
    return s;
  }
  const lo = Math.min(...values),
    hi = Math.max(...values),
    range = Math.max(hi - lo, 1),
    minimum = lo - range * 0.1,
    maximum = hi + range * 0.1,
    scale = maximum - minimum,
    times = rows.map((v) => Date.parse(v.observed_at)),
    timeAxis = times.every(Number.isFinite) && times.at(-1) > times[0],
    span = timeAxis ? times.at(-1) - times[0] : Math.max(1, rows.length - 1);
  if (!mini) {
    const title = svgEl("title");
    title.textContent = `最低 ${number(lo, 1)}，最高 ${number(hi, 1)} ${field === "temp_c" ? "°C" : "W"}；采样中断处不连线`;
    s.append(title);
    for (const [i, y] of [pad, h / 2, h - pad].entries()) {
      s.append(
        svgEl("line", {
          x1: left,
          y1: y,
          x2: w,
          y2: y,
          stroke: "#e7eef0",
          "stroke-width": 1,
          "stroke-dasharray": "3 4",
        }),
      );
      const label = svgEl("text", {
        x: left - 7,
        y: y + 3,
        "text-anchor": "end",
        class: "trend-label",
      });
      label.textContent = number(maximum - (scale * i) / 2, 1);
      s.append(label);
    }
  }
  let path = "",
    last = false,
    lastPoint = null;
  rows.forEach((v, i) => {
    if (!v.available || typeof v[field] !== "number") {
      last = false;
      return;
    }
    if (i > 0 && timeAxis && times[i] - times[i - 1] > 10000) last = false;
    const x =
        left + ((w - left - pad) * (timeAxis ? times[i] - times[0] : i)) / span,
      y = h - pad - ((v[field] - minimum) / scale) * (h - 2 * pad);
    path += (last ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    last = true;
    lastPoint = [x, y];
  });
  s.append(
    svgEl("path", {
      d: path,
      fill: "none",
      stroke: "currentColor",
      "stroke-width": mini ? 2 : 2.2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  );
  if (!mini && lastPoint)
    s.append(
      svgEl("circle", {
        cx: lastPoint[0],
        cy: lastPoint[1],
        r: 3,
        fill: "currentColor",
      }),
    );
  return s;
}
function nodeCard(n) {
  const b = activeBatch(n.id),
    last = latestBatch(n.id),
    f = frameFor(b),
    m = nodeFrameFor(n, b),
    kind = nodeKind(n),
    card = el("article", undefined, "node-card " + kind),
    head = el("div", undefined, "node-card-heading"),
    name = el("div", undefined, "node-name"),
    sym = el("span", undefined, "node-symbol");
  sym.append(icon("nodes"));
  name.append(sym, el("strong", n.id));
  const state =
    kind === "running"
      ? badge(
          "running",
          b?.cancel_requested
            ? "正在停止"
            : labels[f?.phase] || labels[b.state],
        )
      : kind === "attention"
        ? badge("needs_attention")
        : ["wakeable", "awaiting_agent", "waking"].includes(kind)
          ? el("span", nodeStateLabels[kind], "badge reachability " + kind)
          : badge(
              kind === "idle" ? "completed" : "offline",
              nodeStateLabels[kind],
            );
  head.append(name, state);
  card.append(head);
  if (b && activeStates.includes(b.state)) {
    const line = el("div", undefined, "node-task"),
      step = f?.step_tool || labels[f?.phase] || labels[b.state];
    line.append(
      el("strong", step),
      el("span", duration(f?.elapsed_s) + " / " + duration(b.budget_s)),
    );
    card.append(line, progress(f?.elapsed_s || 0, b.budget_s));
    card.append(nodeMetrics(m, online(n)));
  } else {
    const idle = el("div", undefined, "node-idle");
    idle.append(
      el(
        "strong",
        b?.state === "needs_attention"
          ? "测试需要人工处理"
          : kind === "waking"
            ? "已提交唤醒，等待节点系统连接"
            : kind === "wakeable"
              ? "管理口可达，机器已关机"
              : kind === "awaiting_agent"
                ? "电源已开启，等待节点程序连接"
                : kind === "offline"
                  ? n.power?.configured
                    ? "系统与管理口状态待确认"
                    : "系统未连接，尚未绑定 BMC"
                  : kind === "disabled"
                    ? "此节点已禁用"
                    : last?.state === "delivered"
                      ? "上一批已完成交付"
                      : "准备好下一次测试",
      ),
      el(
        "span",
        b?.result.error
          ? b.result.error.slice(0, 90)
          : last
            ? last.plan.label + " · " + labels[last.state]
            : n.last_seen
              ? "仅在明确点击开始后运行"
              : "添加连接文件并启动节点服务",
      ),
    );
    card.append(idle);
    if (online(n) || m?.sample) card.append(nodeMetrics(m, online(n)));
  }
  const foot = el("div", undefined, "node-card-foot"),
    info =
      m?.phase === "monitoring"
        ? freshSample(m) && online(n)
          ? "采样接收 " + ago(m.sample_received_at)
          : "最近采样 " + ago(m.sample_received_at)
        : b && activeStates.includes(b.state)
          ? ["finalizing", "delivering"].includes(f?.phase)
            ? "采集已结束 · 正在交付"
            : freshSample(f)
              ? "采样接收 " + ago(f.sample_received_at)
              : f?.sample
                ? "采样已过期 · " + ago(f.sample_received_at)
                : "等待采样"
          : n.last_seen
            ? "系统最后联系 " + ago(n.last_seen)
            : "系统尚未连接";
  card.append(el("small", nodePowerText(n), "node-power-note"));
  if (wakeNote(n)) card.append(el("small", wakeNote(n), "node-power-note"));
  foot.append(el("span", info));
  if (!n.disabled)
    foot.append(
      button(
        "实时监控 ↗",
        () => openNodeMonitor(n.id),
        "quiet",
        "monitor-" + n.id,
      ),
    );
  foot.append(
    button(
      "硬件信息 ↗",
      () => openHardwareInfo(n.id),
      "quiet",
      "info-" + n.id,
    ),
    button(
      b ? "查看批次 ↗" : "节点详情 ↗",
      () => (b ? openBatch(b.id) : inspectNode(n.id)),
      "quiet",
      "node-" + n.id,
    ),
  );
  const wake = wakeButton(n);
  if (wake) foot.append(wake);
  card.append(foot);
  return card;
}
function renderOverview() {
  const on = snapshot.nodes.filter(online).length,
    accessible = snapshot.nodes.filter(reachable).length,
    off = snapshot.nodes.filter((n) => nodeKind(n) === "wakeable").length,
    running = snapshot.batches.filter((b) =>
      activeStates.includes(b.state),
    ).length,
    attention = snapshot.batches.filter(
      (b) => b.state === "needs_attention",
    ).length,
    delivered = snapshot.batches.filter((b) => b.state === "delivered").length;
  replace(
    $("summary"),
    ...[
      [
        "可达节点",
        accessible,
        snapshot.nodes.length,
        `系统在线 ${on} 台 · 已关机可唤醒 ${off} 台`,
        "nodes",
        "",
      ],
      ["进行中批次", running, null, "正在执行", "activity", "accent"],
      [
        "需要处理",
        attention,
        null,
        attention ? "打开批次查看原因" : "当前无待处理异常",
        "alert",
        attention ? "attention" : "",
      ],
      ["近期已交付", delivered, null, "文件已核验，报告可查看", "reports", ""],
    ].map(([title, value, total, note, ico, cls]) => {
      const d = el("article", undefined, "stat " + cls),
        t = el("div", title, "stat-title"),
        v = el("strong", String(value));
      t.append(icon(ico));
      if (total !== null) v.append(el("small", " / " + total));
      d.append(
        t,
        v,
        el("span", note, "stat-note" + (cls === "accent" ? " active" : "")),
      );
      return d;
    }),
  );
  const priority = {
      running: 0,
      attention: 1,
      idle: 2,
      wakeable: 3,
      awaiting_agent: 4,
      offline: 5,
      disabled: 6,
    },
    nodes = [...snapshot.nodes].sort(
      (a, b) =>
        priority[nodeKind(a)] - priority[nodeKind(b)] ||
        collator.compare(a.id, b.id),
    );
  replace($("overview-node-list"), ...nodes.slice(0, 6).map(nodeCard));
  if (!nodes.length)
    $("overview-node-list").append(
      empty(
        "接入你的第一台测试节点",
        "点击右上角“添加节点”，下载专属连接文件。",
      ),
    );
  replace(
    $("recent-batches"),
    ...snapshot.batches.slice(0, 5).map((b) => {
      const d = el("div", undefined, "recent-row"),
        i = el("div", undefined, "recent-icon"),
        t = el("div");
      i.append(icon("batches"));
      t.append(
        el("strong", b.plan.label),
        el("small", b.plan.node + " · " + dateText(b.created_at)),
      );
      d.append(
        i,
        t,
        badge(b.state),
        button("查看 →", () => openBatch(b.id), "", "recent-" + b.id),
      );
      return d;
    }),
  );
  if (!snapshot.batches.length)
    $("recent-batches").append(
      empty("还没有批次", "编排压测步骤，开始第一轮验证。"),
    );
}
function renderNodes() {
  const query = $("node-search").value.toLowerCase(),
    nodes = snapshot.nodes
      .filter(
        (n) =>
          (nodeFilter === "all" || nodeKind(n) === nodeFilter) &&
          n.id.toLowerCase().includes(query),
      )
      .sort((a, b) => collator.compare(a.id, b.id)),
    count = 24;
  nodePage = Math.min(
    nodePage,
    Math.max(0, Math.ceil(nodes.length / count) - 1),
  );
  $("node-count").textContent =
    `${nodes.length} 台节点 · 可达 ${nodes.filter(reachable).length} 台 · 系统在线 ${nodes.filter(online).length} 台 · 已关机 ${nodes.filter((n) => nodeKind(n) === "wakeable").length} 台`;
  replace(
    $("node-list"),
    ...nodes.slice(nodePage * count, (nodePage + 1) * count).map(nodeCard),
  );
  if (!nodes.length)
    $("node-list").append(
      empty("没有符合条件的节点", "调整名称或状态筛选，或添加新的节点。"),
    );
  renderPager($("node-pagination"), nodes.length, count, nodePage, (index) => {
    nodePage = index;
    renderNodes();
    scrollToArea("#view-nodes .toolbar");
  });
  if (selectedNode) renderNodeDetail();
}
function inspectNode(id) {
  selectedNode = id;
  location.hash = "nodes";
  renderNodes();
  $("node-detail").hidden = false;
  scrollToArea("#node-detail");
}
function renderNodeDetail() {
  const n = snapshot.nodes.find((n) => n.id === selectedNode);
  if (!n) return;
  const root = $("node-detail"),
    head = el("div", undefined, "node-detail-head"),
    list = snapshot.batches.filter((b) => b.plan.node === n.id);
  head.append(
    el("h2", n.id),
    button(
      "收起",
      () => {
        selectedNode = null;
        root.hidden = true;
      },
      "quiet",
      "node-close",
    ),
  );
  const items = [
    head,
    el(
      "p",
      (nodeStateLabels[nodeKind(n)] ||
        (online(n) ? "系统在线" : "状态待确认")) +
        "　" +
        nodePowerText(n),
      "node-power-note",
    ),
    el(
      "p",
      "版本：" +
        (n.agent_version || "尚未连接") +
        "　最后联系：" +
        dateText(n.last_seen),
      "muted",
    ),
  ];
  if (n.bmc_profile)
    items.push(el("p", "接入时选择的 BMC 模板：" + n.bmc_profile, "muted"));
  const wake = wakeButton(n);
  if (wake) items.push(wake);
  items.push(
    button("硬件信息 ↗", () => openHardwareInfo(n.id), "", "node-info"),
  );
  if (wakeNote(n)) items.push(el("p", wakeNote(n), "muted"));
  for (const b of list.slice(0, 8)) {
    const row = el("div", undefined, "file-row");
    row.append(
      button(b.plan.label, () => openBatch(b.id), "quiet", "nodebatch-" + b.id),
      badge(b.state),
    );
    items.push(row);
  }
  if (!list.length) items.push(el("p", "此节点尚无批次。", "muted"));
  items.push(
    button(
      online(n) ? "为此节点新建批次" : "前往任务分发",
      () => (online(n) ? newBatch(n.id) : (location.hash = "dispatch")),
      "",
      "node-new",
    ),
  );
  replace(root, ...items);
  root.hidden = false;
}
function openBatch(id) {
  location.hash = "batch/" + id;
}
function openMonitor(id) {
  location.hash = "monitor/" + id;
}
function openNodeMonitor(id) {
  location.hash = "monitor-node/" + encodeURIComponent(id);
}
function operationButtons(b) {
  const ops = el("div", undefined, "actions");
  if (canDelete(b))
    ops.append(
      button(
        "永久删除",
        () => confirmDelete([b.id]),
        "danger",
        "delete-" + b.id,
      ),
    );
  if (b.group_id) ops.append(link("返回任务组", "#group/" + b.group_id));
  if (b.state === "draft" && !b.group_id)
    ops.append(
      button(
        "开始压测",
        () =>
          confirmAction(
            "开始压测",
            `将在 ${b.plan.node} 上执行 ${b.plan.label}。请确认这是可用测试节点；只运行此批次。`,
            false,
            () => api("batches/" + b.id + "/start", "POST", {}),
          ),
        "primary",
        "start-" + b.id,
      ),
    );
  if (
    ["draft", "waiting_boot", "armed", "running"].includes(b.state) &&
    !b.cancel_requested
  )
    ops.append(
      button(
        b.state === "draft" ? "取消草稿" : "取消压测",
        () =>
          confirmAction(
            "取消本批次",
            "运行中的步骤将请求停止，已采集的证据保留。离线时需要等待节点重新连接才能处理停止请求。",
            true,
            (reason) => api("batches/" + b.id + "/cancel", "POST", { reason }),
          ),
        "",
        "cancel-" + b.id,
      ),
    );
  if (b.state === "needs_attention")
    ops.append(
      button(
        "关闭未完成",
        () =>
          confirmAction(
            "关闭未完成批次",
            "请先核对节点压测进程已停止并保留失败证据。关闭后不再恢复此批次，不生成成功回执。",
            true,
            (reason) =>
              api("batches/" + b.id + "/close-incomplete", "POST", { reason }),
            true,
          ),
        "",
        "close-" + b.id,
      ),
    );
  return ops;
}
function renderBatches() {
  const query = $("batch-search").value.toLowerCase(),
    filter = $("batch-filter").value,
    items = snapshot.batches.filter(
      (b) =>
        (b.plan.node + " " + b.plan.label).toLowerCase().includes(query) &&
        (filter === "all" ||
          (filter === "active"
            ? activeStates.includes(b.state)
            : b.state === filter)),
    );
  const size = 25;
  batchPage = Math.min(
    batchPage,
    Math.max(0, Math.ceil(items.length / size) - 1),
  );
  $("batch-count").textContent =
    items.length + " 个符合条件的批次 · 每页 " + size + " 个";
  renderDeleteToolbar(
    "batches",
    items.slice(batchPage * size, (batchPage + 1) * size),
  );
  replace(
    $("batch-list"),
    ...items.slice(batchPage * size, (batchPage + 1) * size).map((b) => {
      const tr = el("tr"),
        title = el("td");
      title.append(
        deleteCheckbox(b, "batches"),
        el("strong", b.plan.label),
        el(
          "small",
          b.plan.node +
            " · " +
            b.step_count +
            " 个步骤 · " +
            duration(b.budget_s),
        ),
      );
      tr.append(title);
      for (const v of [
        b.state,
        b.result.execution,
        b.result.quality,
        b.result.report,
      ]) {
        const td = el("td");
        td.append(badge(v));
        tr.append(td);
      }
      const td = el("td");
      td.append(button("详情 →", () => openBatch(b.id), "", "details-" + b.id));
      if (canDelete(b))
        td.append(
          button(
            "删除",
            () => confirmDelete([b.id]),
            "danger",
            "delete-" + b.id,
          ),
        );
      if (b.group_id) td.append(link("任务组", "#group/" + b.group_id));
      if (b.state === "draft" && !b.group_id)
        td.append(
          button(
            "开始",
            () =>
              confirmAction(
                "开始压测",
                `确认在 ${b.plan.node} 开始 ${b.plan.label}？该操作会实际启动压测。`,
                false,
                () => api("batches/" + b.id + "/start", "POST", {}),
              ),
            "",
            "start-" + b.id,
          ),
        );
      tr.append(td);
      return tr;
    }),
  );
  renderPager($("batch-pagination"), items.length, size, batchPage, (index) => {
    batchPage = index;
    renderBatches();
    scrollToArea("#view-batches .toolbar");
  });
  if (!items.length) {
    const tr = el("tr"),
      td = el("td");
    td.colSpan = 6;
    td.append(empty("没有符合条件的批次", "可调整筛选条件，或新建批次草稿。"));
    tr.append(td);
    $("batch-list").append(tr);
  }
}
function renderReports() {
  const q = $("report-search").value.toLowerCase(),
    items = snapshot.batches.filter(
      (b) =>
        b.state === "delivered" &&
        (b.plan.label + " " + b.plan.node).toLowerCase().includes(q),
    );
  const size = 18;
  reportPage = Math.min(
    reportPage,
    Math.max(0, Math.ceil(items.length / size) - 1),
  );
  $("report-count").textContent =
    items.length + " 份已交付报告 · 每页 " + size + " 份";
  renderDeleteToolbar(
    "reports",
    items.slice(reportPage * size, (reportPage + 1) * size),
  );
  replace(
    $("report-list"),
    ...items.slice(reportPage * size, (reportPage + 1) * size).map((b) => {
      const d = el("article", undefined, "report-card"),
        head = el("div", undefined, "report-top"),
        ops = el("div", undefined, "actions");
      head.append(deleteCheckbox(b, "reports"), badge("delivered"));
      ops.append(
        fileLink(b, "report.html", "查看 HTML ↗"),
        fileLink(b, "monitor.xlsx", "Excel ↓"),
        button("全部文件", () => openBatch(b.id), "quiet", "report-" + b.id),
        button("删除", () => confirmDelete([b.id]), "danger", "delete-" + b.id),
      );
      d.append(
        head,
        el("h3", b.plan.label),
        el("p", b.plan.node + " · " + dateText(b.created_at)),
        badge(b.result.execution),
        el("p", "硬件合格性未判定"),
        ops,
      );
      return d;
    }),
  );
  renderPager(
    $("report-pagination"),
    items.length,
    size,
    reportPage,
    (index) => {
      reportPage = index;
      renderReports();
      scrollToArea("#view-reports .toolbar");
    },
  );
  if (!items.length)
    $("report-list").append(
      empty(
        "尚无符合条件的已交付报告",
        "任务结束并核验文件后，报告会自动出现在这里。",
      ),
    );
}
function renderDetail() {
  const b = detailData;
  if (!b || b.id !== selected) return;
  const frame = detailFrame?.batch === b.id ? detailFrame : frameFor(b),
    steps = b.result.steps || [],
    root = $("detail-body"),
    items = [],
    banner = el("div", undefined, "detail-banner"),
    title = el("div"),
    h = el("h2", b.plan.label);
  h.id = "detail-title";
  title.append(
    el("p", b.plan.node, "eyebrow"),
    h,
    el("p", "批次 " + b.id + " · " + dateText(b.created_at)),
  );
  const actions = operationButtons(b);
  if (b.state !== "draft")
    actions.prepend(
      button("硬件实时监控", () => openMonitor(b.id), "", "monitor-open"),
    );
  banner.append(title, actions);
  items.push(banner);
  const states = el("div", undefined, "detail-states");
  for (const [name, value] of [
    ["过程", b.state],
    ["执行", b.result.execution],
    ["监控", b.result.quality],
    ["报告", b.result.report],
  ]) {
    const d = el("div", undefined, "state-item");
    d.append(el("span", name), badge(value));
    states.append(d);
  }
  items.push(states);
  if (
    b.cancel_requested &&
    !["delivered", "closed_incomplete", "cancelled"].includes(b.state)
  )
    items.push(
      el(
        "p",
        "取消请求已保存。等待节点停止并处理证据；节点失联时不能确认压测已停止。",
        "info-note",
      ),
    );
  if (b.result.error) {
    items.push(el("p", b.result.error, "notice error"));
    items.push(
      el(
        "p",
        b.state === "needs_attention"
          ? "请检查节点服务与现场证据。已封存的报告或上传步骤由节点恢复处理，不重新压测；无法恢复时可核对残留进程后关闭未完成批次。"
          : "错误已记录；文件交付与压测执行结果分别保留。",
        "info-note",
      ),
    );
  }
  const layout = el("div", undefined, "detail-layout"),
    process = el("section", undefined, "panel"),
    live = el("section", undefined, "panel");
  process.append(el("h2", "执行步骤"));
  const timeline = el("ol", undefined, "steps-timeline");
  const ended = ["delivered", "cancelled", "closed_incomplete"].includes(
    b.state,
  );
  b.plan.steps.forEach((s, index) => {
    const r = steps.find((v) => v.id === s.id),
      isActive = frame?.step_id === s.id && activeStates.includes(b.state),
      done = !!r?.ended_at,
      li = el("li", undefined, isActive ? "active" : done ? "done" : ""),
      head = el("div", undefined, "step-header"),
      state = done
        ? r.execution || r.exit_reason || "已结束"
        : isActive
          ? validFrame(frame)
            ? "正在运行"
            : "状态过期"
          : r?.started_at
            ? "等待结果"
            : ended
              ? "未执行"
              : "等待执行";
    head.append(
      el("strong", s.tool),
      badge(
        done
          ? ["duration_reached", "finished"].includes(r.execution) &&
            r.cleanup_confirmed
            ? "completed"
            : "failed"
          : isActive
            ? "running"
            : "",
        labels[state] || state,
      ),
    );
    li.append(
      el("span", String(index + 1).padStart(2, "0"), "step-number"),
      head,
      el(
        "div",
        "预算 " +
          duration(s.seconds) +
          (r?.elapsed_s !== undefined
            ? " · 实际 " + duration(r.elapsed_s)
            : isActive
              ? " · 已运行 " + duration(frame.step_elapsed_s)
              : ""),
        "step-description",
      ),
    );
    if (r?.started_at)
      li.append(
        el(
          "div",
          timeText(r.started_at) +
            " → " +
            (r.ended_at ? timeText(r.ended_at) : "进行中") +
            (done
              ? " · 退出码 " +
                r.exit_code +
                " · 清理" +
                (r.cleanup_confirmed ? "已确认" : "未确认")
              : ""),
          "step-description",
        ),
      );
    if (isActive) {
      const p = progress(frame.step_elapsed_s, s.seconds);
      p.classList.add("step-progress");
      li.append(p);
    }
    timeline.append(li);
  });
  process.append(timeline);
  layout.append(process);
  const running =
      activeStates.includes(b.state) &&
      !["finalizing", "delivering"].includes(frame?.phase),
    data = frame?.sample || {},
    rows = frame?.history || history.get(b.id) || [];
  root.dataset.liveSequence = String(frame?.sample?.sequence || 0);
  root.dataset.elapsed = String(frame?.elapsed_s || 0);
  live.append(el("h2", running ? "运行读数与趋势" : "本次运行的最近读数"));
  const charts = el("div", undefined, "charts");
  for (const [field, label, unit] of [
    ["temp_c", "CPU 最高温度", "°C"],
    ["package_w", "CPU Pkg 功耗", "W"],
  ]) {
    const chart = el("div", undefined, "chart-box"),
      v = el(
        "div",
        number(data[field], field === "package_w" ? 1 : 0),
        "chart-number",
      );
    v.append(el("small", " " + unit));
    const axis = el("div", undefined, "chart-axis");
    axis.append(
      el("span", timeText(rows[0]?.observed_at)),
      el("span", timeText(rows.at(-1)?.observed_at)),
    );
    chart.append(el("h3", label), v, trend(rows, field), axis);
    charts.append(chart);
  }
  live.append(charts);
  const metrics = el("div", undefined, "metric-grid");
  for (const [label, key, unit, p] of [
    ["核心平均频率", "mhz", "MHz", 0],
    ["内存最高温度", "memory_temp_c", "°C", 0],
    ["内存 DRAM 功耗", "dram_w", "W", 1],
    ["整机 PSU 输入", "psu_w", "W", 1],
    ["VCCIN", "vccin_v", "V", 2],
    ["VID", "vid_v", "V", 4],
    ["TjMax", "tjmax_c", "°C", 0],
    ["系统负载", "load", "", 2],
  ])
    metrics.append(metric(label, data[key], unit, p));
  live.append(metrics);
  live.append(
    el(
      "p",
      !frame?.sample
        ? ended
          ? "暂无缓存，完整数据见报告。"
          : "等待采集"
        : running
          ? freshSample(frame)
            ? "采样接收 " + ago(frame.sample_received_at)
            : "数据已过期 / 不可用 · 最后接收 " + ago(frame.sample_received_at)
          : "采集已结束 · 最后读数",
      "freshness" + (running && !freshSample(frame) ? " stale" : ""),
    ),
  );
  live.append(
    el(
      "p",
      (running && age(data.extra_observed_at) > 50
        ? "补充读数已过期 / 尚未提供："
        : "补充读数采集：") +
        dateText(data.extra_observed_at) +
        " · 约 30 秒更新",
      "field-help",
    ),
    el("p", "— 未提供 · 读数有效性未验证", "field-help"),
  );
  layout.append(live);
  items.push(layout);
  const bottom = el("div", undefined, "detail-layout detail-support"),
    files = el("section", undefined, "panel"),
    events = el("section", undefined, "panel");
  files.append(el("h2", "报告与结果文件"));
  if (b.artifacts) {
    const list = el("div", undefined, "file-list");
    list.dataset.scrollKey = "files";
    for (const [name, m] of Object.entries(b.artifacts)) {
      const row = el("div", undefined, "file-row");
      row.append(fileLink(b, name), el("small", bytes(m.bytes)));
      list.append(row);
    }
    files.append(list);
    if (b.receipt_sha256) {
      const row = el("div", undefined, "file-row"),
        a = link(
          "receipt.json · 完成回执 ↓",
          "/api/v1/batches/" + b.id + "/receipt",
        );
      row.append(a);
      files.append(row, el("p", "回执 SHA-256：" + b.receipt_sha256, "hash"));
    }
  } else
    files.append(
      el("p", "任务结束后自动生成并交付，尚无可下载文件。", "muted"),
    );
  files.append(
    button(
      "复制计划为新草稿",
      () => newBatch(b.plan.node, b),
      "",
      "clone-" + b.id,
    ),
  );
  events.append(el("h2", "操作与状态记录"));
  const eventList = el("div", undefined, "events");
  eventList.dataset.scrollKey = "events";
  for (const event of detailEvents.slice(0, 40)) {
    const d = el("div", undefined, "event");
    d.append(
      el("time", dateText(event.at)),
      el("strong", event.kind),
      el("p", event.detail),
    );
    eventList.append(d);
  }
  events.append(eventList);
  bottom.append(files, events);
  items.push(bottom);
  replace(root, ...items);
}
function monitorValue(value, unit = "", places = 0) {
  return number(value, places) + (typeof value === "number" ? " " + unit : "");
}
function monitorPair(label, value) {
  const pair = el("div", undefined, "monitor-pair");
  pair.append(el("dt", label), el("dd", value));
  return pair;
}
function renderMonitor() {
  const byNode = !!monitorNode,
    b = byNode ? nodeMonitorData?.batch : detailData;
  if (byNode ? nodeMonitorData?.node !== monitorNode : !b || b.id !== selected)
    return;
  const f = byNode
      ? nodeMonitorData.frames[0]
      : detailFrame?.batch === b.id
        ? detailFrame
        : frameFor(b),
    sample = f?.sample || {},
    hardware = sample.available ? sample.hardware : null,
    nodeID = byNode ? monitorNode : b.plan.node,
    node = snapshot.nodes.find((n) => n.id === nodeID),
    running =
      (byNode && f?.phase === "monitoring") ||
      (b?.state === "running" && f?.phase === "executing"),
    fresh = running && !!node && online(node) && freshSample(f),
    supplementalFresh =
      fresh && Math.abs(now() - Date.parse(sample.extra_observed_at)) < 50000,
    status = !connectionFresh()
      ? "连接中断 · 保留最后读数"
      : node && !online(node)
        ? isWaking(node)
          ? "正在唤醒 · 等待系统连接"
          : nodeKind(node) === "wakeable"
            ? "已关机 · 最后读数"
            : "系统未连接 · 最后读数"
        : !byNode &&
            (!activeStates.includes(b.state) ||
              ["finalizing", "delivering"].includes(f?.phase))
          ? "采集已结束 · 最后读数"
          : !sample.sequence
            ? "等待采样"
            : !sample.available
              ? "采集不可用"
              : fresh
                ? "实时采集中"
                : "数据已过期 · 等待更新",
    root = $("monitor-body"),
    items = [],
    hero = el("section", undefined, "monitor-hero"),
    title = el("div"),
    heading = el("h2", nodeID),
    state = el(
      "span",
      status,
      "monitor-status" + (fresh ? " is-live" : " is-stale"),
    );
  heading.id = "monitor-title";
  state.id = "monitor-status";
  root.dataset.liveSequence = String(sample.sequence || 0);
  root.className = fresh ? "monitor-current" : "monitor-history";
  title.append(
    el("p", "SCKOCP / LIVE TELEMETRY", "eyebrow"),
    heading,
    el(
      "p",
      !b
        ? "日常硬件监控"
        : b.plan.label +
            " · " +
            (f?.step_tool || labels[f?.phase] || labels[b.state]),
      "monitor-subtitle",
    ),
  );
  const action = el("div", undefined, "monitor-hero-actions");
  action.append(state);
  action.append(
    button("硬件信息 ↗", () => openHardwareInfo(nodeID), "", "monitor-info"),
  );
  const wake = wakeButton(node);
  if (wake) action.append(wake);
  if (wakeNote(node)) title.append(el("p", wakeNote(node), "monitor-subtitle"));
  if (b)
    action.append(
      button("批次进度与报告 ↗", () => openBatch(b.id), "", "monitor-batch"),
    );
  if (!byNode)
    action.append(
      button(
        "节点当前状态 ↗",
        () => openNodeMonitor(nodeID),
        "",
        "monitor-node",
      ),
    );
  hero.append(title, action);
  items.push(hero);
  const sections = el("nav", undefined, "monitor-section-nav");
  sections.setAttribute("aria-label", "硬件信息分区");
  sections.dataset.scrollKey = "monitor-sections";
  sections.append(el("strong", nodeID, "monitor-context"));
  for (const [label, selector] of [
    ["整机概况", ".monitor-summary"],
    ["插槽信息", ".monitor-sockets"],
    ["逐核心", ".core-section"],
    ["趋势", ".monitor-trends"],
  ]) {
    const jump = button(
      label,
      () => scrollToArea(selector),
      "quiet",
      "jump-" + selector,
    );
    jump.disabled =
      !hardware && [".monitor-sockets", ".core-section"].includes(selector);
    sections.append(jump);
  }
  items.push(sections);
  const stamps = el("div", undefined, "monitor-stamps");
  stamps.append(
    el(
      "span",
      "主采样 " +
        timeText(sample.observed_at) +
        " · 中心接收 " +
        ago(f?.sample_received_at),
    ),
    el(
      "span",
      "补充读取 " + timeText(sample.extra_observed_at) + " · 约 30 秒更新",
      "monitor-extra-age" + (supplementalFresh ? "" : " stale"),
    ),
  );
  items.push(stamps);
  const summary = el(
    "section",
    undefined,
    "monitor-summary" + (fresh ? "" : " is-stale"),
  );
  summary.setAttribute("aria-label", "整机读数");
  for (const [label, key, unit, precision] of [
    ["CPU 最高温度", "temp_c", "°C", 0],
    ["CPU Pkg 总功耗", "package_w", "W", 1],
    ["PSU 整机输入 · 补充", "psu_w", "W", 1],
    ["核心平均频率", "mhz", "MHz", 0],
    ["系统负载", "load", "", 2],
  ]) {
    const card = metric(label, sample[key], unit, precision);
    if (key === "psu_w" && !supplementalFresh) card.classList.add("stale");
    summary.append(card);
  }
  items.push(summary);
  if (!hardware) {
    items.push(
      empty(
        sample.available
          ? "此节点尚未提供逐核心数据"
          : "当前没有可用的硬件快照",
        sample.available
          ? "旧版节点仅上报汇总指标。请在空闲时将中心和节点升级到 0.4.0-alpha.3 或更高兼容版本。"
          : byNode
            ? "节点系统在线并成功采样后自动显示，无需开始压测。请确认节点服务和 sckocp 采集可用。"
            : "此处显示本批次的采样；完整记录可在批次报告中查看。日常读数请打开节点当前状态。",
      ),
    );
  } else {
    const sockets = [...hardware.sockets].sort((a, z) => a.id - z.id),
      socketSection = el(
        "section",
        undefined,
        "monitor-sockets" + (fresh ? "" : " is-stale"),
      );
    for (const socket of sockets) {
      const x = socket.extra || {},
        card = el("article", undefined, "socket-card"),
        head = el("div", undefined, "socket-heading"),
        name = el("div"),
        metrics = el("div", undefined, "socket-metrics"),
        extra = el(
          "dl",
          undefined,
          "socket-extra" + (supplementalFresh ? "" : " stale"),
        );
      name.append(
        el("p", "SOCKET " + String(socket.id).padStart(2, "0"), "eyebrow"),
        el(
          "h3",
          x.model ||
            (hardware.vendor === "GenuineIntel" ? "Intel" : "AMD") +
              " · S" +
              socket.id,
        ),
        el(
          "p",
          (x.physical_cores
            ? x.physical_cores + " 核 / " + x.threads + " 线程"
            : "核心配置未提供") +
            " · 基础频率 " +
            monitorValue(socket.base_mhz, "MHz"),
          "muted",
        ),
      );
      head.append(name, el("span", "S" + socket.id, "socket-chip"));
      metrics.append(
        metric("最高温度", socket.temp_c, "°C"),
        metric("CPU Pkg", socket.package_w, "W", 1),
        metric("核心频率", socket.core_mhz, "MHz"),
      );
      const primary = el("dl", undefined, "socket-primary");
      primary.append(
        monitorPair("TjMax", monitorValue(socket.tjmax_c, "°C")),
        monitorPair("VID", monitorValue(socket.vid_v, "V", 4)),
      );
      for (const [label, key, unit, precision] of [
        ["VCCIN", "vccin_v", "V", 2],
        ["Mesh", "mesh_mhz", "MHz", 0],
        ["内存速率", "memory_mts", "MT/s", 0],
        ["DIMM 数量", "dimms", "条", 0],
        ["内存最高温度", "memory_temp_c", "°C", 0],
        ["DRAM 功耗", "dram_w", "W", 1],
        ["PC2", "pc2_pct", "%", 0],
        ["PC6", "pc6_pct", "%", 0],
      ])
        extra.append(monitorPair(label, monitorValue(x[key], unit, precision)));
      const memory = el(
        "div",
        undefined,
        "socket-memory" + (supplementalFresh ? "" : " stale"),
      );
      memory.append(
        el("span", "内存使用 · 补充读数"),
        el(
          "strong",
          monitorValue(x.memory_used_gb, "", 1) +
            " / " +
            monitorValue(x.memory_total_gb, "GB") +
            " · " +
            monitorValue(x.memory_used_pct, "%", 1),
        ),
      );
      card.append(
        head,
        metrics,
        primary,
        el(
          "p",
          supplementalFresh
            ? "补充读数 · " + ago(sample.extra_observed_at)
            : "补充读数已过期 / 未提供",
          "socket-extra-label",
        ),
        extra,
        memory,
      );
      socketSection.append(card);
    }
    items.push(socketSection);
    const section = el("section", undefined, "panel core-section"),
      heading = el("div", undefined, "section-title"),
      info = el("div"),
      views = el("div", undefined, "segmented");
    info.append(
      el("h2", "逐核心状态"),
      el(
        "span",
        hardware.cores.length + " 个 CPU 编号 · 按插槽、编号排序",
        "muted",
      ),
    );
    for (const [mode, label] of [
      ["map", "核心矩阵"],
      ["table", "详细表格"],
    ]) {
      const tab = button(
        label,
        () => {
          coreMode = mode;
          renderMonitor();
        },
        mode === coreMode ? "selected" : "",
        "core-mode-" + mode,
      );
      tab.setAttribute("aria-pressed", String(mode === coreMode));
      views.append(tab);
    }
    heading.append(info, views);
    section.append(heading);
    const filters = el("div", undefined, "core-filters");
    filters.dataset.scrollKey = "core-filters";
    if (
      coreSocket !== "all" &&
      !sockets.some((s) => String(s.id) === coreSocket)
    )
      coreSocket = "all";
    for (const [id, label] of [
      ["all", "全部插槽"],
      ...sockets.map((s) => [String(s.id), "S" + s.id]),
    ]) {
      const tab = button(
        label,
        () => {
          coreSocket = id;
          corePage = 0;
          renderMonitor();
        },
        coreSocket === id ? "selected" : "",
        "socket-" + id,
      );
      tab.setAttribute("aria-pressed", String(coreSocket === id));
      filters.append(tab);
    }
    section.append(filters);
    const cores = [...hardware.cores]
        .filter((c) => coreSocket === "all" || String(c.socket) === coreSocket)
        .sort((a, z) => a.socket - z.socket || a.cpu - z.cpu),
      size = 64;
    corePage = Math.min(
      corePage,
      Math.max(0, Math.ceil(cores.length / size) - 1),
    );
    const shown = cores.slice(corePage * size, (corePage + 1) * size),
      grid = el("div", undefined, "core-grid" + (fresh ? "" : " is-stale"));
    if (!cores.length)
      section.append(empty("没有逐核心读数", "当前接口未提供此部分数据。"));
    else if (coreMode === "map") {
      for (const c of shown) {
        const tile = el("article", undefined, "core-tile"),
          head = el("div", undefined, "core-tile-top"),
          load = el("div", undefined, "core-load"),
          fill = el("i");
        tile.dataset.cpu = String(c.cpu);
        tile.title =
          "S" +
          c.socket +
          " / CPU " +
          c.cpu +
          " · VID " +
          monitorValue(c.vid_v, "V", 4) +
          " · C0 " +
          monitorValue(c.c0_pct, "%") +
          " · C6 " +
          monitorValue(c.c6_pct, "%");
        head.append(el("strong", "CPU " + c.cpu), el("small", "S" + c.socket));
        tile.append(
          head,
          el("div", monitorValue(c.temp_c, "°C"), "core-temperature"),
          el("span", monitorValue(c.mhz, "MHz"), "core-frequency"),
        );
        if (typeof c.c0_pct === "number") {
          fill.style.width = Math.min(100, Math.max(0, c.c0_pct)) + "%";
          load.append(fill);
        }
        tile.append(load, el("small", "C0 " + monitorValue(c.c0_pct, "%")));
        grid.append(tile);
      }
      section.append(grid);
    } else {
      const wrap = el(
          "div",
          undefined,
          "core-table-scroll" + (fresh ? "" : " is-stale"),
        ),
        table = el("table", undefined, "core-table"),
        thead = el("thead"),
        row = el("tr"),
        body = el("tbody");
      table.setAttribute("aria-label", "逐核心实时读数");
      wrap.dataset.scrollKey = "core-table";
      wrap.dataset.focusKey = "core-table-region";
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", "逐核心表格，可滚动查看");
      for (const text of [
        "CPU 编号",
        "插槽",
        "频率 MHz",
        "温度 °C",
        "VID V",
        "C0 %",
        "C6 %",
        "IRQ",
      ]) {
        const th = el("th", text);
        th.scope = "col";
        row.append(th);
      }
      thead.append(row);
      for (const c of shown) {
        const tr = el("tr");
        tr.dataset.cpu = String(c.cpu);
        for (const value of [
          c.cpu,
          "S" + c.socket,
          number(c.mhz),
          number(c.temp_c),
          number(c.vid_v, 4),
          number(c.c0_pct),
          number(c.c6_pct),
          "未提供",
        ])
          tr.append(el("td", value));
        body.append(tr);
      }
      table.append(thead, body);
      wrap.append(table);
      section.append(wrap);
    }
    if (cores.length > size) {
      const nav = el("div", undefined, "pagination"),
        prev = button(
          "上一页",
          () => {
            corePage--;
            renderMonitor();
          },
          "",
          "cores-prev",
        ),
        next = button(
          "下一页",
          () => {
            corePage++;
            renderMonitor();
          },
          "",
          "cores-next",
        );
      prev.disabled = corePage === 0;
      next.disabled = (corePage + 1) * size >= cores.length;
      nav.append(
        prev,
        el("span", corePage + 1 + " / " + Math.ceil(cores.length / size)),
        next,
      );
      section.append(nav);
    }
    section.append(
      el("p", "条形：C0 活跃比例 · IRQ 未提供", "field-help core-legend"),
    );
    items.push(section);
  }
  const trends = el("section", undefined, "monitor-trends"),
    rows = f?.history || (b && history.get(b.id)) || [];
  for (const [field, label, unit] of [
    ["temp_c", "CPU 最高温度", "°C"],
    ["package_w", "CPU Pkg 总功耗", "W"],
  ]) {
    const chart = el("article", undefined, "panel"),
      head = el("div", undefined, "section-title");
    head.append(
      el("h3", label),
      el("span", "最近 " + rows.length + " 次上报", "muted"),
    );
    chart.append(
      head,
      trend(rows, field),
      el(
        "p",
        timeText(rows[0]?.observed_at) +
          " — " +
          timeText(rows.at(-1)?.observed_at) +
          " · " +
          unit,
        "field-help",
      ),
    );
    trends.append(chart);
  }
  items.push(trends, el("p", "— 未提供 · 读数有效性未验证", "monitor-quality"));
  replace(root, ...items);
}
function render() {
  $("create-label").textContent = ["dispatch", "group"].includes(page)
    ? "新建任务组"
    : "新建批次";
  $("version").textContent =
    "BITS " + (snapshot.version || "") + " · 测试控制中心";
  $("nav-node-count").textContent = snapshot.nodes.length;
  connection();
  if (page === "overview") renderOverview();
  if (page === "nodes") renderNodes();
  if (page === "batches") renderBatches();
  if (page === "reports") renderReports();
  if (page === "detail") renderDetail();
  if (page === "monitor") renderMonitor();
  if (page === "info") renderHardwareInfo();
  if (page === "dispatch" || page === "group") renderDispatch();
  if (
    restorePosition !== null &&
    (!selected || detailData?.id === selected) &&
    (!monitorNode || nodeMonitorData?.node === monitorNode)
  ) {
    window.scrollTo({ top: restorePosition, behavior: "instant" });
    restorePosition = null;
  }
  $("back-to-top").hidden = window.scrollY < 400;
}
function route() {
  notice("");
  const hash = location.hash.slice(1),
    detail = hash.match(/^(batch|monitor|group)\/([a-f0-9]{32})$/),
    nodeRoute = hash.match(/^monitor-node\/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})$/),
    infoRoute = hash.match(
      /^hardware-info\/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})$/,
    );
  hardwareInfoRoute(infoRoute ? infoRoute[1] : null);
  page = infoRoute
    ? "info"
    : detail
      ? detail[1] === "group"
        ? "group"
        : detail[1] === "monitor"
          ? "monitor"
          : "detail"
      : nodeRoute
        ? "monitor"
        : Object.hasOwn(pageInfo, hash) &&
            !["detail", "monitor", "group", "info"].includes(hash)
          ? hash
          : "overview";
  const nextRouteKey = infoRoute
    ? infoRoute[0]
    : detail
      ? detail[0]
      : nodeRoute
        ? nodeRoute[0]
        : page;
  if (routeKey !== nextRouteKey) {
    if (routeKey) routePositions.set(routeKey, window.scrollY);
    // Retain useful back-navigation positions without an unbounded session cache.
    if (routePositions.size > 80)
      routePositions.delete(routePositions.keys().next().value);
    restorePosition = routePositions.get(nextRouteKey) || 0;
    routeKey = nextRouteKey;
  }
  selected = detail && page !== "group" ? detail[2] : null;
  monitorNode = nodeRoute ? nodeRoute[1] : null;
  if (monitorNode && nodeMonitorData?.node !== monitorNode) {
    nodeMonitorData = null;
    coreSocket = "all";
    corePage = 0;
    $("monitor-body").replaceChildren(
      empty("正在读取节点实况", "系统在线时持续采集，无需创建压测批次。"),
    );
  }
  dispatchRoute(page === "group" ? detail[2] : null);
  if (selected && detailData?.id !== selected) {
    detailData = null;
    detailEvents = [];
    detailFrame = null;
    lastDetail = 0;
    coreSocket = "all";
    corePage = 0;
    $("monitor-body").replaceChildren(
      empty("正在读取硬件实况", "等待当前批次已采集的数据。"),
    );
    $("detail-body").replaceChildren(
      empty("正在读取批次", "正在载入步骤、实时数据和结果文件。"),
    );
  }
  for (const [key] of Object.entries(pageInfo))
    $("view-" + key).hidden = key !== page;
  const info = pageInfo[page];
  $("page-title").textContent = info[0];
  $("crumb").textContent = info[0];
  $("page-kicker").textContent = info[1];
  document.title = "BITS · " + info[0];
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const match =
      a.dataset.nav ===
      (page === "group"
        ? "dispatch"
        : page === "detail"
          ? "batches"
          : ["monitor", "info"].includes(page)
            ? "nodes"
            : page);
    a.classList.toggle("selected", match);
    if (match) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  render();
  if (authenticated) sync(true);
}
async function sync(force = false) {
  if (syncing) {
    syncRequested ||= force;
    return;
  }
  if (document.hidden || (!authenticated && $("login-dialog").open)) return;
  syncing = true;
  $("refresh").disabled = true;
  $("refresh").setAttribute("aria-busy", "true");
  try {
    const refreshOverview = force || Date.now() - lastOverview >= 5000,
      requested = selected,
      requestedNode = monitorNode;
    const [live, overview] = await Promise.all([
      api("live"),
      refreshOverview ? api("overview") : Promise.resolve(null),
    ]);
    authenticated = true;
    connected = true;
    lastNetwork = Date.now();
    serverOffset = Date.parse(live.time) - Date.now();
    if (overview) {
      snapshot = overview;
      lastOverview = Date.now();
    }
    if (refreshOverview && (page === "dispatch" || page === "group"))
      await syncDispatch();
    frames = new Map(live.frames.map((v) => [v.node, v]));
    nodeFrames = new Map((live.monitors || []).map((v) => [v.node, v]));
    for (const f of live.frames) {
      if (!f.sample) continue;
      let rows = history.get(f.batch) || [];
      if (!rows.length || rows.at(-1).sequence < f.sample.sequence)
        rows = [...rows, f.sample].slice(-180);
      history.set(f.batch, rows);
    }
    const keep = new Set(live.frames.map((v) => v.batch));
    for (const id of history.keys())
      if (!keep.has(id) && id !== selected) history.delete(id);
    if (requested) {
      const needsDetail =
        force ||
        detailData?.id !== requested ||
        Date.now() - lastDetail >= 5000;
      const [lf, b, events] = await Promise.all([
        api("batches/" + requested + "/live"),
        needsDetail ? api("batches/" + requested) : Promise.resolve(detailData),
        needsDetail
          ? api("batches/" + requested + "/events")
          : Promise.resolve(detailEvents),
      ]);
      if (selected === requested) {
        detailFrame = lf.frames[0] || null;
        detailData = b;
        detailEvents = events;
        if (needsDetail) lastDetail = Date.now();
      }
    }
    if (requestedNode) {
      const monitored = await api(
        "nodes/" + encodeURIComponent(requestedNode) + "/live",
      );
      if (monitorNode === requestedNode) nodeMonitorData = monitored;
    }
    await syncHardwareInfo(force);
    render();
  } catch (e) {
    connected = false;
    connection();
    render();
    if (force && !$("login-dialog").open) notice(e.message, true);
  } finally {
    syncing = false;
    $("refresh").disabled = false;
    $("refresh").removeAttribute("aria-busy");
    if (syncRequested) {
      syncRequested = false;
      queueMicrotask(() => sync(true));
    }
  }
}
function confirmAction(title, text, reason, fn, danger = false) {
  $("confirm-title").textContent = title;
  $("confirm-description").textContent = text;
  $("reason-label").hidden = !reason;
  $("confirm-reason").required = reason;
  $("confirm-reason").value = "";
  $("confirm-error").textContent = "";
  $("confirm-submit").textContent = title;
  $("confirm-submit").className = danger ? "danger" : "primary";
  nextConfirm = fn;
  $("confirm-dialog").showModal();
}
function stepsValue(root = "steps") {
  return [...$(root).children].map((row) => ({
    tool: row.querySelector("select").value,
    seconds: Number(row.querySelector("input").value),
  }));
}
function addStep(tool = "stress", seconds = 60, root = "steps") {
  if ($(root).children.length >= 128) return;
  const row = el("div", undefined, "step-editor"),
    n = el("span", ""),
    label = el("label", "压测项目"),
    durationLabel = el("label", "时长（秒）"),
    s = el("select"),
    v = el("input");
  for (const t of snapshot.tools) {
    const o = el("option", t);
    o.value = t;
    s.append(o);
  }
  s.value = snapshot.tools.includes(tool) ? tool : snapshot.tools[0];
  v.type = "number";
  v.min = "1";
  v.max = "2678400";
  v.step = "1";
  v.value = String(seconds);
  v.required = true;
  label.append(s);
  durationLabel.append(v);
  const remove = button(
    "×",
    () => {
      row.remove();
      renumber(root);
    },
    "icon-button",
  );
  remove.setAttribute("aria-label", "移除此步骤");
  row.append(n, label, durationLabel, remove);
  $(root).append(row);
  renumber(root);
}
function renumber(root = "steps") {
  [...$(root).children].forEach(
    (row, i) => (row.firstChild.textContent = String(i + 1).padStart(2, "0")),
  );
}
function selectTemplate(name) {
  const templates = {
    short: [
      ["stress", 30],
      ["stress-ng", 30],
    ],
    cpu: [
      ["stress-ng", 1800],
      ["p95-fma3_m2", 1800],
    ],
    memory: [
      ["mlc", 60],
      ["mbw", 300],
    ],
  };
  $("steps").replaceChildren();
  for (const [tool, seconds] of templates[name]) addStep(tool, seconds);
  document
    .querySelectorAll("[data-template]")
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.template === name),
    );
}
function newBatch(node, copy) {
  if (!snapshot.nodes.length) {
    notice("请先添加并连接测试节点，再创建批次。", true);
    return;
  }
  $("batch-node").replaceChildren(
    ...snapshot.nodes.map((n) => {
      const o = el("option", n.id + (online(n) ? " · 在线" : " · 未连接"));
      o.value = n.id;
      return o;
    }),
  );
  if (node) $("batch-node").value = node;
  $("batch-label").value = copy ? copy.plan.label.slice(0, 80) + "-COPY" : "";
  $("batch-error").textContent = "";
  selectTemplate("short");
  if (copy) {
    $("steps").replaceChildren();
    for (const s of copy.plan.steps) addStep(s.tool, s.seconds);
  }
  wizardStep = 0;
  renderWizard();
  $("batch-dialog").showModal();
}
function renderWizard() {
  document
    .querySelectorAll("[data-wizard-panel]")
    .forEach((p) => (p.hidden = Number(p.dataset.wizardPanel) !== wizardStep));
  $("batch-back").hidden = wizardStep === 0;
  $("batch-next").hidden = wizardStep === 2;
  $("batch-submit").hidden = wizardStep !== 2;
  $("batch-wizard").replaceChildren(
    ...["选择节点", "编排步骤", "核对计划"].map((v, i) => {
      const s = el("span", undefined, i === wizardStep ? "active" : "");
      s.append(el("i", i + 1), el("span", v));
      return s;
    }),
  );
  if (wizardStep === 2) {
    const plan = stepsValue(),
      root = $("batch-review"),
      head = el("div", undefined, "review-title");
    head.append(
      el("strong", $("batch-label").value),
      el("small", "节点 " + $("batch-node").value),
    );
    root.replaceChildren(head);
    plan.forEach((v, i) => {
      const r = el("div", undefined, "review-step");
      r.append(
        el("strong", String(i + 1).padStart(2, "0") + "　" + v.tool),
        el("span", duration(v.seconds)),
      );
      root.append(r);
    });
    root.append(
      el(
        "p",
        "共 " +
          plan.length +
          " 步 · 总预算 " +
          duration(plan.reduce((n, s) => n + s.seconds, 0)),
        "review-total",
      ),
    );
  }
}
function validateWizard() {
  const panel = document.querySelector(`[data-wizard-panel="${wizardStep}"]`);
  for (const input of panel.querySelectorAll("input,select"))
    if (!input.reportValidity()) return false;
  if (wizardStep === 1) {
    const plan = stepsValue();
    if (!plan.length) {
      $("batch-error").textContent = "请至少添加一个压测步骤。";
      return false;
    }
    if (plan.reduce((n, s) => n + s.seconds, 0) > 2678400) {
      $("batch-error").textContent = "单批次总预算最多 31 天，请拆分更长任务。";
      return false;
    }
  }
  $("batch-error").textContent = "";
  return true;
}
$("batch-next").onclick = () => {
  if (validateWizard()) {
    wizardStep++;
    renderWizard();
  }
};
$("batch-back").onclick = () => {
  wizardStep--;
  $("batch-error").textContent = "";
  renderWizard();
};
$("add-step").onclick = () => addStep();
$("create").onclick = () =>
  ["dispatch", "group"].includes(page)
    ? newGroup().catch((e) => notice(e.message, true))
    : newBatch();
$("guide-create").onclick = () => newBatch();
let nodeBMCProfiles = [],
  nodeBMCProfilesReady = false,
  nodeBMCProfilesRequest = 0;
function renderNodeBMCProfile() {
  const selected = $("node-bmc-profile").value,
    p = nodeBMCProfiles.find((p) => p.name === selected && p.enabled),
    root = $("node-profile-summary");
  $("node-submit").disabled = !nodeBMCProfilesReady || Boolean(selected && !p);
  if (!nodeBMCProfilesReady) return;
  if (!selected) {
    replace(
      root,
      el(
        "p",
        nodeBMCProfiles.some((p) => p.enabled)
          ? "按节点名前缀、主板型号和管理网段自动匹配；只有唯一匹配时才会绑定。"
          : "尚无启用的模板。可以先新建模板，也可以添加节点后再配置。",
      ),
    );
    return;
  }
  if (!p) {
    replace(root, el("p", "所选模板已停用或不存在，请重新选择。"));
    return;
  }
  const facts = el("dl", undefined, "node-profile-facts");
  for (const [name, value] of [
    ["IPMI 用户名", p.username],
    ["加密套件", "LANplus · Cipher " + p.cipher],
    ["管理网段", p.networks.join("、")],
    [
      "适用范围",
      [
        p.node_prefix && "节点名以 " + p.node_prefix + " 开头",
        p.model && "主板 " + p.model,
      ]
        .filter(Boolean)
        .join("；") || "所有节点名和主板型号",
    ],
  ]) {
    const row = el("div");
    row.append(el("dt", name), el("dd", value));
    facts.append(row);
  }
  replace(root, facts, el("p", "修改模板不覆盖已有绑定。"));
}
async function loadNodeBMCProfiles(selected = $("node-bmc-profile").value) {
  const request = ++nodeBMCProfilesRequest;
  nodeBMCProfilesReady = false;
  $("node-submit").disabled = true;
  $("node-bmc-profile").disabled = true;
  $("node-profile-create").disabled = true;
  $("node-profile-reload").disabled = true;
  $("node-profile-error").textContent = "";
  $("node-profile-summary").textContent = "正在加载中心模板…";
  try {
    const profiles = await api("dispatch/bmc-profiles");
    if (request !== nodeBMCProfilesRequest) return;
    nodeBMCProfiles = profiles;
    const options = [
      new Option("自动匹配模板", ""),
      ...profiles
        .filter((p) => p.enabled)
        .map((p) => new Option(p.name, p.name)),
    ];
    if (selected && !profiles.some((p) => p.name === selected && p.enabled)) {
      const missing = new Option(selected + " · 不可用", selected);
      missing.disabled = true;
      options.push(missing);
    }
    replace($("node-bmc-profile"), ...options);
    $("node-bmc-profile").value = selected;
    nodeBMCProfilesReady = true;
    $("node-bmc-profile").disabled = false;
    renderNodeBMCProfile();
  } catch (err) {
    if (request !== nodeBMCProfilesRequest) return;
    $("node-profile-summary").textContent = "模板列表尚未加载。";
    $("node-profile-error").textContent =
      err.message + "；请刷新模板列表后再创建节点。";
  } finally {
    if (request === nodeBMCProfilesRequest) {
      $("node-profile-create").disabled = false;
      $("node-profile-reload").disabled = false;
    }
  }
}
$("node-bmc-profile").onchange = renderNodeBMCProfile;
$("node-profile-reload").onclick = () => loadNodeBMCProfiles();
$("node-profile-create").onclick = () => openBMCProfiles(true);
$("enroll").onclick = () => {
  $("node-form").reset();
  $("node-error").textContent = "";
  $("node-dialog").showModal();
  loadNodeBMCProfiles("");
};
$("guide-enroll").onclick = $("enroll").onclick;
$("close-detail").onclick = () => (location.hash = "batches");
$("close-monitor").onclick = () => (location.hash = "nodes");
$("node-search").oninput = () => {
  nodePage = 0;
  renderNodes();
};
$("batch-search").oninput = () => {
  batchPage = 0;
  renderBatches();
};
$("batch-filter").onchange = () => {
  batchPage = 0;
  renderBatches();
};
$("report-search").oninput = () => {
  reportPage = 0;
  renderReports();
};
$("node-filters")
  .querySelectorAll("button")
  .forEach(
    (b) =>
      (b.onclick = () => {
        nodeFilter = b.dataset.filter;
        nodePage = 0;
        $("node-filters")
          .querySelectorAll("button")
          .forEach((v) => {
            v.classList.toggle("selected", v === b);
            v.setAttribute("aria-pressed", String(v === b));
          });
        renderNodes();
      }),
  );
for (const b of document.querySelectorAll("[data-template]"))
  b.onclick = () => selectTemplate(b.dataset.template);
for (const b of document.querySelectorAll("[data-close]"))
  b.onclick = () => $(b.dataset.close).close();
$("login-dialog").addEventListener("cancel", (e) => e.preventDefault());
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  if (formStates.has($("login-form"))) return;
  formBusy($("login-form"), true);
  try {
    await api("login", "POST", { token: $("login-token").value });
    $("login-token").value = "";
    $("login-error").textContent = "";
    authenticated = true;
    $("login-dialog").close();
    notice("");
    await sync(true);
  } catch (err) {
    $("login-error").textContent = err.message;
  } finally {
    formBusy($("login-form"), false);
  }
};
$("logout").onclick = async () => {
  try {
    await api("logout", "POST", {});
    location.reload();
  } catch (e) {
    notice(e.message, true);
  }
};
$("confirm-form").onsubmit = async (e) => {
  e.preventDefault();
  if (formStates.has($("confirm-form"))) return;
  formBusy($("confirm-form"), true);
  try {
    const message = await nextConfirm($("confirm-reason").value.trim());
    $("confirm-dialog").close();
    notice(
      typeof message === "string" ? message : "操作已保存，正在同步节点状态。",
    );
    lastDetail = 0;
    lastOverview = 0;
    await sync(true);
  } catch (err) {
    $("confirm-error").textContent = err.message;
  } finally {
    formBusy($("confirm-form"), false);
  }
};
$("batch-form").onsubmit = async (e) => {
  e.preventDefault();
  if (formStates.has($("batch-form"))) return;
  if (wizardStep < 2) {
    if (validateWizard()) {
      wizardStep++;
      renderWizard();
    }
    return;
  }
  formBusy($("batch-form"), true);
  try {
    const batch = await api("batches", "POST", {
      node: $("batch-node").value,
      label: $("batch-label").value,
      steps: stepsValue(),
    });
    $("batch-dialog").close();
    notice("批次草稿已保存。确认就绪后，点击“开始压测”。");
    lastOverview = 0;
    openBatch(batch.id);
    await sync(true);
  } catch (err) {
    $("batch-error").textContent = err.message;
  } finally {
    formBusy($("batch-form"), false);
  }
};
$("node-form").onsubmit = async (e) => {
  e.preventDefault();
  if (formStates.has($("node-form"))) return;
  if (!nodeBMCProfilesReady || $("node-submit").disabled) return;
  formBusy($("node-form"), true);
  try {
    const cfg = await api("nodes", "POST", {
        id: $("node-id").value,
        serial: $("node-serial").value,
        keep_on: $("node-keep-on").checked,
        bmc_profile: $("node-bmc-profile").value,
      }),
      url = URL.createObjectURL(
        new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" }),
      ),
      a = el("a");
    a.href = url;
    a.download = cfg.node + ".bits.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("node-dialog").close();
    $("enroll-example").textContent =
      "bits-node enroll --file /root/" +
      cfg.node +
      ".bits.json";
    notice(
      "节点 " +
        cfg.node +
        " 已登记，专属连接文件已下载。" +
        ($("node-bmc-profile").value
          ? "已选择 BMC 模板 " +
            $("node-bmc-profile").value +
            "，接入后自动核对绑定。"
          : "") +
        "请安全保存并按下方指引接入。",
    );
    location.hash = "guide";
    lastOverview = 0;
    await sync(true);
  } catch (err) {
    $("node-error").textContent = err.message;
  } finally {
    formBusy($("node-form"), false);
  }
};
window.addEventListener("hashchange", route);
if ("scrollRestoration" in window.history)
  window.history.scrollRestoration = "manual";
$("refresh").onclick = () => sync(true);
document.querySelector(".skip-link").onclick = (e) => {
  e.preventDefault();
  $("workspace").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
};
$("back-to-top").onclick = () => {
  window.scrollTo({ top: 0, behavior: "instant" });
  $("workspace").focus({ preventScroll: true });
};
$("navigation-toggle").onclick = () => {
  if (mobileNavigation.matches) $("navigation-dialog").showModal();
  else {
    compactNavigation = !compactNavigation;
    try {
      localStorage.setItem(
        "bits.navigation.compact",
        String(compactNavigation),
      );
    } catch {}
  }
  navigation();
};
$("navigation-close").onclick = () => $("navigation-dialog").close();
$("navigation-dialog").addEventListener("close", navigation);
$("navigation-dialog").addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const items = Array.from(
    e.currentTarget.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((item) => item.getClientRects().length);
  const first = items[0],
    last = items[items.length - 1];
  if (!first) return;
  if (
    !items.includes(document.activeElement) ||
    (e.shiftKey && document.activeElement === first) ||
    (!e.shiftKey && document.activeElement === last)
  ) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  }
});
$("navigation-dialog").addEventListener("click", (e) => {
  if (e.target === $("navigation-dialog")) $("navigation-dialog").close();
});
document
  .querySelectorAll("[data-nav], .brand")
  .forEach((a) =>
    a.addEventListener("click", () => $("navigation-dialog").close()),
  );
mobileNavigation.addEventListener("change", navigation);
window.addEventListener(
  "scroll",
  () => {
    $("back-to-top").hidden = window.scrollY < 400;
  },
  { passive: true },
);
for (const d of document.querySelectorAll("dialog")) {
  d.addEventListener("cancel", (e) => {
    if (d.dataset.submitting) e.preventDefault();
  });
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) sync(true);
});
window.addEventListener("online", () => sync(true));
window.addEventListener("offline", () => {
  connected = false;
  render();
});
decorate();
initializeDispatch();
navigation();
route();
sync(true);
setInterval(() => sync(), 2000);
