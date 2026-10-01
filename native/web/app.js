"use strict";
const $ = (id) => document.getElementById(id);
const activeStates = ["armed", "running", "finishing"];
const labels = {
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
  overview: [
    "运行总览",
    "OPERATIONS OVERVIEW",
    "掌握节点实况，跟进每一批测试。",
  ],
  nodes: ["测试节点", "NODE FLEET", "查看每台节点的连接、负载与当前进度。"],
  batches: ["压测批次", "TEST BATCHES", "编排有序步骤，明确开始，完整记录。"],
  reports: [
    "结果与报告",
    "RESULTS & EVIDENCE",
    "从执行过程，到经过核验的结果文件。",
  ],
  guide: [
    "部署与操作",
    "GETTING STARTED",
    "把接入、测试与交付，整理成清晰的流程。",
  ],
  detail: ["批次详情", "BATCH WORKSPACE", "追踪运行实况、处理过程与结果交付。"],
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
};
let snapshot = { nodes: [], batches: [], tools: [], version: "" };
let frames = new Map(),
  history = new Map(),
  detailData = null,
  detailEvents = [],
  detailFrame = null;
let page = "overview",
  selected = null,
  selectedNode = null,
  nodeFilter = "all",
  nodePage = 0;
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
  root.replaceChildren(...children);
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
  return fresh;
}
function online(n) {
  return connectionFresh() && age(n.last_seen) < 20;
}
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
  if (!online(n)) return "offline";
  if (b?.state === "needs_attention") return "attention";
  return b ? "running" : "idle";
}
function frameFor(b) {
  const f = b ? frames.get(b.plan.node) : null;
  return f?.batch === b?.id ? f : null;
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
  const w = mini ? 90 : 280,
    h = mini ? 30 : 108,
    pad = mini ? 2 : 7,
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
  if (!values.length) return s;
  const lo = Math.min(...values),
    hi = Math.max(...values),
    range = Math.max(hi - lo, 1),
    span = Math.max(1, rows.length - 1);
  if (!mini)
    for (const y of [pad, h / 2, h - pad])
      s.append(
        svgEl("line", {
          x1: 0,
          y1: y,
          x2: w,
          y2: y,
          stroke: "#e7eef0",
          "stroke-width": 1,
          "stroke-dasharray": "3 4",
        }),
      );
  let path = "",
    last = false;
  rows.forEach((v, i) => {
    if (!v.available || typeof v[field] !== "number") {
      last = false;
      return;
    }
    const x = pad + ((w - 2 * pad) * i) / span,
      y =
        h -
        pad -
        ((v[field] - lo + range * 0.1) / (range * 1.2)) * (h - 2 * pad);
    path += (last ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    last = true;
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
  return s;
}
function nodeCard(n) {
  const b = activeBatch(n.id),
    last = latestBatch(n.id),
    f = frameFor(b),
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
        : badge(
            kind === "idle" ? "completed" : "offline",
            kind === "idle" ? "空闲" : "未连接",
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
    const metrics = el(
        "div",
        undefined,
        "node-metrics" + (freshSample(f) ? "" : " stale"),
      ),
      sample = f?.sample || {};
    metrics.append(
      metric("CPU 温度", sample.temp_c, "°C"),
      metric("CPU Pkg", sample.package_w, "W", 1),
      metric("核心频率", sample.mhz, "MHz"),
    );
    card.append(metrics);
  } else {
    const idle = el("div", undefined, "node-idle");
    idle.append(
      el(
        "strong",
        b?.state === "needs_attention"
          ? "测试需要人工处理"
          : kind === "offline"
            ? "等待节点连接"
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
  }
  const foot = el("div", undefined, "node-card-foot"),
    info =
      b && activeStates.includes(b.state)
        ? ["finalizing", "delivering"].includes(f?.phase)
          ? "采集已结束 · 正在交付"
          : freshSample(f)
            ? "采样接收 " + ago(f.sample_received_at)
            : f?.sample
              ? "采样已过期 · " + ago(f.sample_received_at)
              : "等待采样"
        : n.last_seen
          ? "最后联系 " + ago(n.last_seen)
          : "尚未连接";
  foot.append(el("span", info));
  if (b && freshSample(f))
    foot.append(trend(history.get(b.id) || [], "temp_c", true));
  foot.append(
    button(
      b ? "查看批次 ↗" : "节点详情 ↗",
      () => (b ? openBatch(b.id) : inspectNode(n.id)),
      "quiet",
      "node-" + n.id,
    ),
  );
  card.append(foot);
  return card;
}
function renderOverview() {
  const on = snapshot.nodes.filter(online).length,
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
        "在线节点",
        on,
        snapshot.nodes.length,
        "节点心跳每 5 秒更新",
        "nodes",
        "",
      ],
      [
        "进行中批次",
        running,
        null,
        "仅执行已明确开始的批次",
        "activity",
        "accent",
      ],
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
  const priority = { running: 0, attention: 1, idle: 2, offline: 3 },
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
    nodes.length + " 台节点 · 在线 " + nodes.filter(online).length + " 台";
  replace(
    $("node-list"),
    ...nodes.slice(nodePage * count, (nodePage + 1) * count).map(nodeCard),
  );
  if (!nodes.length)
    $("node-list").append(
      empty("没有符合条件的节点", "调整名称或状态筛选，或添加新的节点。"),
    );
  const pager = $("node-pagination");
  pager.replaceChildren();
  if (nodes.length > count) {
    const prev = button("← 上一页", () => {
        nodePage--;
        renderNodes();
      }),
      next = button("下一页 →", () => {
        nodePage++;
        renderNodes();
      });
    prev.disabled = nodePage === 0;
    next.disabled = (nodePage + 1) * count >= nodes.length;
    pager.append(
      prev,
      el("span", nodePage + 1 + " / " + Math.ceil(nodes.length / count)),
      next,
    );
  }
  if (selectedNode) renderNodeDetail();
}
function inspectNode(id) {
  selectedNode = id;
  location.hash = "nodes";
  renderNodes();
  $("node-detail").hidden = false;
  $("node-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
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
      "版本：" +
        (n.agent_version || "尚未连接") +
        "　最后联系：" +
        dateText(n.last_seen),
      "muted",
    ),
  ];
  for (const b of list.slice(0, 8)) {
    const row = el("div", undefined, "file-row");
    row.append(
      button(b.plan.label, () => openBatch(b.id), "quiet", "nodebatch-" + b.id),
      badge(b.state),
    );
    items.push(row);
  }
  if (!list.length) items.push(el("p", "此节点尚无批次。", "muted"));
  items.push(button("为此节点新建批次", () => newBatch(n.id), "", "node-new"));
  replace(root, ...items);
  root.hidden = false;
}
function openBatch(id) {
  location.hash = "batch/" + id;
}
function operationButtons(b) {
  const ops = el("div", undefined, "actions");
  if (b.state === "draft")
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
  if (["draft", "armed", "running"].includes(b.state) && !b.cancel_requested)
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
  replace(
    $("batch-list"),
    ...items.map((b) => {
      const tr = el("tr"),
        title = el("td");
      title.append(
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
      if (b.state === "draft")
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
  replace(
    $("report-list"),
    ...items.map((b) => {
      const d = el("article", undefined, "report-card"),
        head = el("div", undefined, "report-top"),
        ops = el("div", undefined, "actions");
      head.append(icon("reports"), badge("delivered"));
      ops.append(
        fileLink(b, "report.html", "查看 HTML ↗"),
        fileLink(b, "monitor.xlsx", "Excel ↓"),
        button("全部文件", () => openBatch(b.id), "quiet", "report-" + b.id),
      );
      d.append(
        head,
        el("h3", b.plan.label),
        el("p", b.plan.node + " · " + dateText(b.created_at)),
        badge(b.result.execution),
        el("p", "传感器有效性未知 · 硬件合格性未判定"),
        ops,
      );
      return d;
    }),
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
  banner.append(title, operationButtons(b));
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
  const ended = ["delivered", "cancelled", "closed_incomplete"].includes(b.state);
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
          ? "实时缓存已释放或本批次未采集。完整数据与统计请查看报告。"
          : "等待采集。趋势只保留最近 180 个样本，完整数据见报告。"
        : running
          ? freshSample(frame)
            ? "采样接收 " + ago(frame.sample_received_at)
            : "数据已过期 / 不可用 · 最后接收 " + ago(frame.sample_received_at)
          : "采集已结束或已停止。此处是最后收到的读数，完整统计见报告。",
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
        "（约 30 秒更新；非实时 2 秒读数）",
      "field-help",
    ),
    el(
      "p",
      "所有值均为 sckocp 报告值；有效性与传感器读数年龄未知。缺失项显示 —。",
      "field-help",
    ),
    el(
      "p",
      "多插槽汇总：温度、VCCIN、VID 取最高，TjMax 取最低；Pkg / DRAM 功耗求和，PSU 采用程序报告的整机输入。",
      "field-help",
    ),
  );
  layout.append(live);
  items.push(layout);
  const bottom = el("div", undefined, "detail-layout detail-support"),
    files = el("section", undefined, "panel"),
    events = el("section", undefined, "panel");
  files.append(el("h2", "报告与结果文件"));
  if (b.artifacts) {
    const list = el("div", undefined, "file-list");
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
function render() {
  $("version").textContent =
    "BITS " + (snapshot.version || "") + " · 独立架构预览";
  $("nav-node-count").textContent = snapshot.nodes.length;
  connection();
  if (page === "overview") renderOverview();
  if (page === "nodes") renderNodes();
  if (page === "batches") renderBatches();
  if (page === "reports") renderReports();
  if (page === "detail") renderDetail();
}
function route() {
  const hash = location.hash.slice(1),
    detail = hash.match(/^batch\/([a-f0-9]{32})$/);
  page = detail
    ? "detail"
    : Object.hasOwn(pageInfo, hash) && hash !== "detail"
      ? hash
      : "overview";
  selected = detail ? detail[1] : null;
  if (selected && detailData?.id !== selected) {
    detailData = null;
    detailEvents = [];
    detailFrame = null;
    lastDetail = 0;
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
  $("page-description").textContent = info[2];
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const match = a.dataset.nav === (page === "detail" ? "batches" : page);
    a.classList.toggle("selected", match);
    if (match) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  render();
  if (authenticated) sync(true);
}
async function sync(force = false) {
  if (syncing || document.hidden || (!authenticated && $("login-dialog").open))
    return;
  syncing = true;
  try {
    const refreshOverview = force || Date.now() - lastOverview >= 5000,
      requested = selected;
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
    frames = new Map(live.frames.map((v) => [v.node, v]));
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
    render();
  } catch (e) {
    connected = false;
    connection();
    render();
    if (force && !$("login-dialog").open) notice(e.message, true);
  } finally {
    syncing = false;
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
function stepsValue() {
  return [...$("steps").children].map((row) => ({
    tool: row.querySelector("select").value,
    seconds: Number(row.querySelector("input").value),
  }));
}
function addStep(tool = "stress", seconds = 60) {
  if ($("steps").children.length >= 128) return;
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
      renumber();
    },
    "icon-button",
  );
  remove.setAttribute("aria-label", "移除此步骤");
  row.append(n, label, durationLabel, remove);
  $("steps").append(row);
  renumber();
}
function renumber() {
  [...$("steps").children].forEach(
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
$("create").onclick = () => newBatch();
$("guide-create").onclick = () => newBatch();
$("enroll").onclick = () => {
  $("node-error").textContent = "";
  $("node-dialog").showModal();
};
$("guide-enroll").onclick = $("enroll").onclick;
$("close-detail").onclick = () => (location.hash = "batches");
$("node-search").oninput = () => {
  nodePage = 0;
  renderNodes();
};
$("batch-search").oninput = renderBatches;
$("batch-filter").onchange = renderBatches;
$("report-search").oninput = renderReports;
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
  const b = e.submitter;
  b.disabled = true;
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
    b.disabled = false;
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
  const b = e.submitter;
  b.disabled = true;
  try {
    await nextConfirm($("confirm-reason").value.trim());
    $("confirm-dialog").close();
    notice("操作已保存，正在同步节点状态。");
    lastDetail = 0;
    lastOverview = 0;
    await sync(true);
  } catch (err) {
    $("confirm-error").textContent = err.message;
  } finally {
    b.disabled = false;
  }
};
$("batch-form").onsubmit = async (e) => {
  e.preventDefault();
  if (wizardStep < 2) {
    if (validateWizard()) {
      wizardStep++;
      renderWizard();
    }
    return;
  }
  const b = $("batch-submit");
  b.disabled = true;
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
    b.disabled = false;
  }
};
$("node-form").onsubmit = async (e) => {
  e.preventDefault();
  const b = e.submitter;
  b.disabled = true;
  try {
    const cfg = await api("nodes", "POST", {
        id: $("node-id").value,
        serial: $("node-serial").value,
        keep_on: $("node-keep-on").checked,
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
      ".bits.json\nbits-node check\nsystemctl enable --now bits-node.service";
    notice(
      "节点 " +
        cfg.node +
        " 已登记，专属连接文件已下载。请安全保存并按下方指引接入。",
    );
    location.hash = "guide";
    lastOverview = 0;
    await sync(true);
  } catch (err) {
    $("node-error").textContent = err.message;
  } finally {
    b.disabled = false;
  }
};
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) sync(true);
});
window.addEventListener("online", () => sync(true));
window.addEventListener("offline", () => {
  connected = false;
  render();
});
decorate();
route();
sync(true);
setInterval(() => sync(), 2000);
