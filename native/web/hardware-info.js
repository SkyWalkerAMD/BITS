"use strict";
const hardwareInfoState = {
  node: null,
  data: null,
  fetched: 0,
  query: "",
  section: "all",
  composing: false,
};
const hardwareSectionNames = {
  Platform: "平台",
  CPU: "处理器",
  "Turbo Ratio Limits": "睿频倍率",
  Thermal: "散热",
  "Power Limits": "功耗限制",
  "Power Supplies": "电源",
  Memory: "内存",
  "Memory Timings": "内存主时序",
  Cache: "缓存",
  "Per-CCD Temperature": "CCD 温度",
  "SVI Rails": "SVI 电压",
};
function openHardwareInfo(node) {
  location.hash = "hardware-info/" + encodeURIComponent(node);
}
function hardwareInfoRoute(node) {
  if (hardwareInfoState.node === node) return;
  Object.assign(hardwareInfoState, {
    node,
    data: null,
    fetched: 0,
    query: "",
    section: "all",
    composing: false,
  });
}
async function syncHardwareInfo(force) {
  const s = hardwareInfoState,
    node = s.node;
  if (!node || (!force && Date.now() - s.fetched < 15000)) return;
  const data = await api(
    "nodes/" + encodeURIComponent(node) + "/hardware-info",
  );
  if (s.node === node) {
    s.data = data;
    s.fetched = Date.now();
  }
}
function infoFacts(pairs) {
  const dl = el("dl", undefined, "info-facts");
  for (const [name, value] of pairs) {
    const row = el("div");
    row.append(
      el("dt", name),
      el("dd", value === undefined || value === "" ? "—" : String(value)),
    );
    dl.append(row);
  }
  return dl;
}
function infoLines(lines) {
  const root = el("div", undefined, "info-lines");
  const names = {
    "Secure Boot": "安全启动",
    Lockdown: "内核锁定",
    "OC Lock": "超频锁定",
    HT: "超线程",
    IOMMU: "IOMMU",
    x2APIC: "x2APIC",
    NUMA: "NUMA",
    Base: "基础频率",
    Wall: "整机输入",
    Arrangement: "供电模式",
    Package: "封装",
    Programmable: "可编程参数",
    Readings: "读数年龄",
  };
  for (const line of lines) {
    if (!line.trim()) continue;
    const row = el("div", undefined, "info-line");
    for (const part of line.trim().split(/\s{2,}/)) {
      const match = part.match(
        /^(Secure Boot|Lockdown|OC Lock|HT|IOMMU|x2APIC|NUMA|Base|Wall|Arrangement|Package|Programmable|Readings)[:\s]+(.+)$/,
      );
      if (match) row.append(infoFacts([[names[match[1]], match[2]]]));
      else row.append(el("span", part, "info-value"));
    }
    root.append(row);
  }
  if (!root.childElementCount) root.append(el("p", "未提供", "muted"));
  return root;
}
function infoContent(section, snapshot) {
  const root = el("div");
  if (section.name === "CPU" && snapshot.cpus?.length) {
    const grid = el("div", undefined, "info-cpus");
    for (const c of snapshot.cpus) {
      const card = el("article", undefined, "info-cpu");
      card.append(
        el("span", "SOCKET " + String(c.id).padStart(2, "0"), "eyebrow"),
        el("h3", c.model),
        infoFacts([
          ["核心 / 线程", c.cores + " / " + c.threads],
          ["微码", c.microcode],
          ["Family / Model", c.family + " / " + c.model_id],
          ["Stepping", c.stepping],
        ]),
      );
      grid.append(card);
    }
    root.append(
      grid,
      infoLines(
        section.lines.filter(
          (l) => !/^\s*S\d+\s+.+\s+\d+C\/\d+T\s+fam\d+ model/.test(l),
        ),
      ),
    );
  } else if (section.name === "Memory" && snapshot.dimms?.length) {
    const wrap = el("div", undefined, "table-wrap info-memory-table"),
      table = el("table"),
      head = el("thead"),
      tr = el("tr"),
      body = el("tbody");
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "内存插槽，可横向滚动");
    wrap.dataset.scrollKey = "hardware-memory";
    const columns = [
      ["DIMM", "插槽"],
      ["Part Number", "型号"],
      ["Size", "容量"],
      ["Speed", "速率"],
      ["JEDEC", "JEDEC"],
      ["VDDQ", "VDDQ"],
      ["Temp", "温度"],
    ];
    for (const [, label] of columns) {
      const th = el("th", label);
      th.scope = "col";
      tr.append(th);
    }
    head.append(tr);
    for (const d of snapshot.dimms) {
      const row = el("tr");
      for (const [key] of columns) row.append(el("td", d.fields[key] || "—"));
      body.append(row);
    }
    table.append(head, body);
    wrap.append(table);
    root.append(wrap);
  } else if (section.name === "Memory Timings") {
    const grid = el("div", undefined, "info-timings");
    for (const line of section.lines) {
      const m = line.match(
        /^\s*S(\d+)\s+Primary\s+([\d?]+)-([\d?]+)-([\d?]+)-([\d?]+)(.*)$/,
      );
      if (!m) continue;
      const card = el("article", undefined, "info-timing");
      const pairs = [
        ["tCL", m[2]],
        ["tRCD", m[3]],
        ["tRP", m[4]],
        ["tRAS", m[5]],
      ];
      for (const extra of m[6].matchAll(/(tCWL|tRC)\s+([\d?]+)/g))
        pairs.push([extra[1], extra[2]]);
      card.append(el("h3", "Socket " + m[1]), infoFacts(pairs));
      grid.append(card);
    }
    root.append(grid.childElementCount ? grid : el("p", "未提供", "muted"));
  } else root.append(infoLines(section.lines));
  return root;
}
function renderHardwareInfo() {
  const s = hardwareInfoState,
    root = $("hardware-info-body");
  const input = $("hardware-info-search"),
    focus =
      input === document.activeElement
        ? [input.selectionStart, input.selectionEnd]
        : null;
  if (!s.node) return;
  if (!s.data) {
    root.dataset.renderKey = "";
    replace(root, empty("正在读取硬件信息", ""));
    return;
  }
  const node = snapshot.nodes.find((n) => n.id === s.node),
    data = s.data,
    v = data.snapshot;
  const fresh =
    !!v &&
    connectionFresh() &&
    node &&
    online(node) &&
    data.status === "ok" &&
    now() - Date.parse(v.observed_at) < 90000;
  let status = !connectionFresh()
    ? "连接中断 · 上次信息"
    : node && !online(node)
      ? "系统离线 · 上次信息"
      : data.status === "unavailable"
        ? "采集不可用 · 上次信息"
        : fresh
          ? "已更新"
          : v
            ? "等待更新 · 上次信息"
            : "等待首次采集";
  if (!v)
    status = data.status === "unavailable" ? "采集不可用" : "等待首次采集";
  if (s.composing) return;
  const renderKey = JSON.stringify([
    s.node,
    data.checked_at,
    status,
    s.section,
    s.query,
    node?.wake,
    node?.power?.state,
  ]);
  if (root.dataset.renderKey === renderKey) return;
  root.dataset.renderKey = renderKey;
  const hero = el("section", undefined, "monitor-hero info-hero"),
    title = el("div"),
    actions = el("div", undefined, "monitor-hero-actions");
  const h = el("h2", s.node);
  h.id = "hardware-info-title";
  title.append(el("p", "HARDWARE PROFILE", "eyebrow"), h);
  if (v)
    title.append(
      el("p", "采集于 " + dateText(v.observed_at), "monitor-subtitle"),
    );
  const badge = el(
    "span",
    status,
    "monitor-status" + (fresh ? " is-live" : " is-stale"),
  );
  badge.id = "hardware-info-status";
  actions.append(
    badge,
    button("实时监控 ↗", () => openNodeMonitor(s.node), "", "info-monitor"),
  );
  const wake = wakeButton(node);
  if (wake) actions.append(wake);
  hero.append(title, actions);
  if (!v) {
    replace(
      root,
      hero,
      empty(
        "暂无硬件信息",
        node && online(node)
          ? "等待节点采集；需要节点版本 0.4.1 或更高。"
          : "节点上线后自动采集。",
      ),
    );
    return;
  }
  const summary = el("div", undefined, "info-summary");
  const cpus = v.cpus || [],
    dimms = v.dimms || [];
  let capacity = 0,
    capacityKnown = dimms.length > 0;
  for (const d of dimms) {
    const m = (d.fields.Size || "").match(/^(\d+(?:\.\d+)?)\s*(GB|MB|TB)$/i);
    if (!m) capacityKnown = false;
    else
      capacity +=
        Number(m[1]) * { GB: 1, MB: 1 / 1024, TB: 1024 }[m[2].toUpperCase()];
  }
  for (const [label, value] of [
    ["处理器", cpus.length ? cpus.length + " 颗" : "—"],
    [
      "核心 / 线程",
      cpus.length
        ? cpus.reduce((n, c) => n + c.cores, 0) +
          " / " +
          cpus.reduce((n, c) => n + c.threads, 0)
        : "—",
    ],
    ["内存容量", capacityKnown ? number(capacity, 0) + " GB" : "—"],
    ["内存插槽", dimms.length ? dimms.length + " 条" : "—"],
  ]) {
    const card = el("div", undefined, "panel info-summary-card");
    card.append(el("span", label), el("strong", value));
    summary.append(card);
  }
  const toolbar = el("div", undefined, "info-toolbar"),
    search = el("input"),
    nav = el("div", undefined, "info-navigation");
  search.type = "search";
  search.id = "hardware-info-search";
  search.placeholder = "搜索硬件信息";
  search.setAttribute("aria-label", "搜索硬件信息");
  search.value = s.query;
  search.dataset.focusKey = "info-search";
  search.oncompositionstart = () => {
    s.composing = true;
  };
  search.oncompositionend = () => {
    s.composing = false;
    s.query = search.value;
    renderHardwareInfo();
  };
  search.oninput = () => {
    s.query = search.value;
    renderHardwareInfo();
  };
  for (const [name, label] of [
    ["all", "全部"],
    ...Object.entries(hardwareSectionNames).filter(([name]) =>
      v.sections.some((x) => x.name === name),
    ),
  ]) {
    const b = button(
      label,
      () => {
        s.section = name;
        renderHardwareInfo();
      },
      name === s.section ? "selected" : "",
      "info-tab-" + name.replaceAll(" ", "-"),
    );
    b.setAttribute("aria-pressed", String(name === s.section));
    nav.append(b);
  }
  toolbar.append(search, nav);
  const grid = el("div", undefined, "info-section-grid"),
    query = s.query.trim().toLocaleLowerCase();
  for (const section of v.sections) {
    if (s.section !== "all" && s.section !== section.name) continue;
    if (
      query &&
      !(
        hardwareSectionNames[section.name] +
        " " +
        section.name +
        " " +
        section.lines.join(" ")
      )
        .toLocaleLowerCase()
        .includes(query)
    )
      continue;
    const card = el(
      "section",
      undefined,
      "panel info-section" +
        (["CPU", "Memory"].includes(section.name) ? " info-wide" : ""),
    );
    card.dataset.section = section.name;
    card.append(
      el("h2", hardwareSectionNames[section.name] || section.name),
      infoContent(section, v),
    );
    grid.append(card);
  }
  if (!grid.childElementCount) grid.append(empty("没有匹配的信息", ""));
  replace(root, hero, summary, toolbar, grid);
  if (focus) search.setSelectionRange(...focus);
}
