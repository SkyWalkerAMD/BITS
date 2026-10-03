"use strict";
const dispatchState = {
  nodes: [],
  groups: [],
  templates: [],
  group: null,
  id: null,
  operations: [],
  wakes: [],
  wakeOffset: 0,
  offset: 0,
  step: 0,
  selectedNodes: new Set(),
  selectedBatches: new Set(),
  createID: "",
  templateID: "",
  actionIDs: new Map(),
  bmcNode: "",
  discoveries: {},
  profiles: [],
  profileForEnrollment: false,
};
const reachabilityLabels = {
  ready: "系统在线 · 可执行",
  wakeable: "已关机 · 可唤醒",
  awaiting_agent: "已开机 · 等待系统",
  unreachable: "不可达 · 待检查",
  busy: "已有任务",
  disabled: "已禁用",
  waking: "正在唤醒 · 等待系统",
};
function requestID() {
  return crypto.randomUUID().replaceAll("-", "");
}
function dispatchRoute(id) {
  if (dispatchState.id !== id) {
    dispatchState.id = id;
    dispatchState.group = null;
    dispatchState.selectedBatches.clear();
    dispatchState.operations = [];
  }
}
async function syncDispatch() {
  const id = dispatchState.id;
  const [fleet, groups, templates, group, operations, discoveries, wakes] =
    await Promise.all([
      api("dispatch/nodes"),
      api("dispatch/groups?offset=" + dispatchState.offset),
      api("dispatch/templates"),
      id ? api("dispatch/groups/" + id) : null,
      id ? api("dispatch/groups/" + id + "/operations") : null,
      api("dispatch/bmc-discoveries"),
      api("dispatch/wakes?offset=" + dispatchState.wakeOffset),
    ]);
  dispatchState.nodes = fleet.nodes;
  dispatchState.discoveries = discoveries;
  dispatchState.groups = groups;
  dispatchState.templates = templates;
  dispatchState.wakes = wakes;
  if (id === dispatchState.id && group) {
    if (!dispatchState.group) {
      dispatchState.selectedBatches = new Set(
        group.members.filter((m) => m.state === "draft").map((m) => m.id),
      );
    }
    dispatchState.group = group;
    dispatchState.operations = operations;
  }
  if ($("group-dialog").open && !formStates.has($("group-form")))
    renderNodePicker();
}
function powerText(p) {
  if (!p?.configured) return "BMC 未绑定";
  const age = Date.now() + serverOffset - Date.parse(p.checked_at);
  const state =
    !Number.isFinite(age) || age > 90000
      ? "状态待刷新"
      : { on: "电源开", off: "电源关", checking: "检查中" }[p.state] ||
        "不可达 / 未确认";
  return "BMC " + p.address + " · " + state;
}
function reachability(n) {
  const badge = el(
    "span",
    reachabilityLabels[n.state] || "检查中",
    "reachability " + n.state,
  );
  badge.title = n.reason;
  return badge;
}
function filteredDispatchNodes(query) {
  query = query.toLowerCase();
  return [...dispatchState.nodes]
    .sort((a, b) => collator.compare(a.node, b.node))
    .filter((n) =>
      (n.node + " " + (n.power?.address || "")).toLowerCase().includes(query),
    );
}
function renderDispatch() {
  if (page === "group") {
    renderGroup();
    return;
  }
  const d = dispatchState;
  replace(
    $("dispatch-list"),
    ...(d.groups.length
      ? d.groups.map((g) => {
          const card = el("article", undefined, "panel group-card"),
            count = Object.values(g.counts).reduce((n, v) => n + v, 0);
          card.append(
            el("p", dateText(g.created_at), "eyebrow"),
            link(g.label, "#group/" + g.id, "group-name"),
            el(
              "p",
              `${count} 台节点 · ${g.steps.length} 个步骤 · 每台 ${duration(g.steps.reduce((n, s) => n + s.seconds, 0))}`,
            ),
          );
          const stats = el("div", undefined, "group-counts");
          for (const [state, n] of Object.entries(g.counts))
            stats.append(el("span", `${labels[state] || state} ${n}`));
          card.append(stats);
          return card;
        })
      : [
          empty("还没有任务组", "将多台节点编入同一计划，核对后一次确认开始。"),
        ]),
  );
  const prev = button("← 较新任务组", async () => {
      d.offset = Math.max(0, d.offset - 25);
      await syncDispatch();
      renderDispatch();
    }),
    next = button("较早任务组 →", async () => {
      d.offset += 25;
      await syncDispatch();
      renderDispatch();
    });
  prev.disabled = d.offset === 0;
  next.disabled = d.groups.length < 25;
  replace(
    $("dispatch-pagination"),
    prev,
    el("span", `第 ${d.offset / 25 + 1} 页`),
    next,
  );
  const nodes = filteredDispatchNodes($("dispatch-search").value);
  renderWakeToolbar();
  renderWakeHistory();
  replace(
    $("dispatch-nodes"),
    ...nodes.map((n) => {
      const row = el("article", undefined, "dispatch-node"),
        identity = el("div"),
        status = el("div");
      identity.append(wakeCheckbox(n), el("small", powerText(n.power)));
      const found = dispatchState.discoveries[n.node];
      if (found?.profile)
        identity.append(el("small", "接入模板：" + found.profile));
      if (!n.power?.configured && found) {
        identity.append(
          el(
            "small",
            found.model
              ? "主板型号：" + found.model
              : found.candidates?.map((c) => c.address).join("、") ||
                  "等待自动发现管理口",
          ),
        );
        status.append(el("small", found.reason));
      }
      status.append(reachability(n), el("small", n.reason));
      const configure = button(
        n.power?.configured ? "更新 BMC" : "绑定 BMC",
        () => openBMC(n),
        "",
        "bmc-" + n.node,
      );
      configure.disabled = Boolean(n.active_batch || n.waking);
      const actions = el("div", undefined, "dispatch-actions");
      const wake = wakeButton(snapshot.nodes.find(v => v.id === n.node));
      if (wake) actions.append(wake);
      actions.append(configure);
      if (
        !n.active_batch &&
        !n.power?.configured &&
        ["failed", "interrupted"].includes(found?.state)
      )
        actions.append(
          button("重试自动绑定", async () => {
            await api("dispatch/bmc-retry", "POST", { node: n.node });
            await syncDispatch();
            renderDispatch();
          }),
        );
      row.append(identity, status, actions);
      return row;
    }),
  );
}
function renderNodePicker() {
  const d = dispatchState,
    nodes = filteredDispatchNodes($("group-node-search").value);
  const children = nodes.map((n) => {
    const row = el("label", undefined, "node-choice"),
      box = el("input"),
      identity = el("div");
    box.type = "checkbox";
    box.value = n.node;
    box.dataset.focusKey = "choose-" + n.node;
    box.setAttribute("aria-label", "选择 " + n.node);
    box.checked = d.selectedNodes.has(n.node);
    box.disabled = !n.can_select && !box.checked;
    box.onchange = () => {
      if (box.checked) d.selectedNodes.add(n.node);
      else d.selectedNodes.delete(n.node);
      renderNodePicker();
    };
    identity.append(el("strong", n.node), el("small", powerText(n.power)));
    row.append(box, identity, reachability(n));
    return row;
  });
  replace($("group-node-picker"), ...children);
  const selected = d.nodes.filter((n) => d.selectedNodes.has(n.node));
  $("group-selection-count").textContent =
    `已选 ${selected.length} / 200 台 · 系统在线 ${selected.filter((n) => n.agent_online).length} · 开机或等待系统 ${selected.filter((n) => !n.agent_online && n.can_select).length} · 当前不可用 ${selected.filter((n) => !n.can_select).length}`;
}
async function newGroup() {
  await syncDispatch();
  const d = dispatchState;
  d.step = 0;
  d.selectedNodes.clear();
  d.createID = requestID();
  d.templateID = requestID();
  $("group-form").reset();
  $("group-error").textContent = "";
  $("group-steps").replaceChildren();
  addStep("stress", 60, "group-steps");
  addStep("stress-ng", 60, "group-steps");
  replace(
    $("group-template"),
    el("option", "自定义计划"),
    ...d.templates.map((t) => {
      const o = el("option", t.label + " · " + dateText(t.created_at));
      o.value = t.id;
      return o;
    }),
  );
  $("group-template").firstChild.value = "";
  renderGroupWizard();
  $("group-dialog").showModal();
}
function renderGroupWizard() {
  const d = dispatchState;
  document
    .querySelectorAll("[data-group-panel]")
    .forEach((p) => (p.hidden = Number(p.dataset.groupPanel) !== d.step));
  replace(
    $("group-wizard"),
    ...["选择可达节点", "编排共用计划", "核对并保存"].map((name, i) => {
      const e = el("span", undefined, i === d.step ? "active" : "");
      e.append(el("i", i + 1), el("span", name));
      return e;
    }),
  );
  $("group-back").hidden = d.step === 0;
  $("group-next").hidden = d.step === 2;
  $("group-submit").hidden = d.step !== 2;
  renderNodePicker();
  if (d.step === 2) {
    const plan = stepsValue("group-steps"),
      root = $("group-review");
    replace(
      root,
      el("h3", $("group-label").value),
      el(
        "p",
        `${d.selectedNodes.size} 台节点 · 每台预算 ${duration(plan.reduce((n, s) => n + s.seconds, 0))}`,
      ),
      el(
        "p",
        [...d.selectedNodes].sort(collator.compare).join("、"),
        "selected-node-names",
      ),
    );
    plan.forEach((s, i) => {
      const row = el("div", undefined, "review-step");
      row.append(
        el("strong", `${i + 1}. ${s.tool}`),
        el("span", duration(s.seconds)),
      );
      root.append(row);
    });
  }
}
function validateGroupWizard() {
  const d = dispatchState,
    panel = document.querySelector(`[data-group-panel="${d.step}"]`);
  for (const input of panel.querySelectorAll("input,select"))
    if (!input.reportValidity()) return false;
  let message = "";
  if (d.step === 0) {
    if (d.selectedNodes.size < 1 || d.selectedNodes.size > 200)
      message = "请选择 1–200 台可用节点。";
    const bad = d.nodes.filter(
      (n) => d.selectedNodes.has(n.node) && !n.can_select,
    );
    if (bad.length)
      message =
        "以下节点状态已变化，请取消选择后重试：" +
        bad.map((n) => n.node).join("、");
  }
  if (d.step === 1) {
    const plan = stepsValue("group-steps");
    if (!plan.length || plan.reduce((n, s) => n + s.seconds, 0) > 2678400)
      message = "每台需有 1–128 个步骤，总预算最多 31 天。";
  }
  $("group-error").textContent = message;
  return !message;
}
function actionID(kind, ids, reason) {
  const d = dispatchState,
    key = JSON.stringify([d.id, kind, [...ids].sort(), reason]);
  if (!d.actionIDs.has(key)) d.actionIDs.set(key, requestID());
  if (d.actionIDs.size > 300)
    d.actionIDs.delete(d.actionIDs.keys().next().value);
  return d.actionIDs.get(key);
}
async function groupStart() {
  await syncDispatch();
  renderGroup();
  const d = dispatchState,
    g = d.group;
  const selected = g.members.filter((m) => d.selectedBatches.has(m.id));
  if (!selected.length) throw new Error("请勾选要开始的节点。");
  const checks = selected.map((m) => ({
    m,
    n: d.nodes.find((n) => n.node === m.node),
  }));
  const bad = checks.filter(
    ({ m, n }) => m.state !== "draft" || !n?.can_select,
  );
  if (bad.length)
    throw new Error(
      "所选节点暂不可开始，请查看原因并调整选择：" +
        bad.map((v) => v.m.node).join("、"),
    );
  const wake = checks.filter((v) => !v.n.agent_online).length,
    ids = selected.map((m) => m.id),
    gid = g.id;
  confirmAction(
    "确认分发并开始",
    `任务组 ${g.label}，本次 ${ids.length} 台，其中 ${wake} 台需要开机或等待系统。其他节点保持原状态。关机节点将收到开机指令；等待连接最多 10 分钟。`,
    false,
    async () => {
      await api("dispatch/groups/" + gid + "/start", "POST", {
        request_id: actionID("start", ids, ""),
        batches: ids,
        wake: wake > 0,
      });
      d.selectedBatches.clear();
    },
  );
}
function groupCancel() {
  const d = dispatchState,
    g = d.group,
    ids = g.members
      .filter(
        (m) =>
          d.selectedBatches.has(m.id) &&
          !["cancelled", "closed_incomplete", "delivered"].includes(m.state),
      )
      .map((m) => m.id),
    gid = g.id;
  if (!ids.length) throw new Error("请勾选要取消的未结束批次。");
  confirmAction(
    "取消所选节点任务",
    `将请求取消 ${ids.length} 台节点的本次任务并保留证据。已经发给 BMC 的开机指令无法撤回；不会强制关机。正在运行的压测以节点确认停止为准。`,
    true,
    async (reason) => {
      await api("dispatch/groups/" + gid + "/cancel", "POST", {
        request_id: actionID("cancel", ids, reason),
        batches: ids,
        wake: false,
        reason,
      });
      d.selectedBatches.clear();
    },
    true,
  );
}
function renderGroup() {
  const d = dispatchState,
    g = d.group;
  if (!g) {
    replace(
      $("group-body"),
      empty("正在读取任务组", "载入节点结果和分发记录。"),
    );
    return;
  }
  const head = el("div", undefined, "dispatch-intro"),
    title = el("div"),
    actions = el("div", undefined, "actions");
  title.append(
    link("← 返回任务分发", "#dispatch"),
    el("h2", g.label),
    el(
      "p",
      `${g.members.length} 台节点 · ${g.steps.length} 个步骤 · 每台 ${duration(g.steps.reduce((n, s) => n + s.seconds, 0))}`,
    ),
  );
  actions.append(
    button("确认分发并开始", groupStart, "primary", "group-start"),
    button("取消所选任务", groupCancel, "", "group-cancel"),
  );
  head.append(title, actions);
  const counts = el("div", undefined, "group-counts");
  for (const [state, count] of Object.entries(g.counts))
    counts.append(el("span", `${labels[state] || state} ${count}`));
  const plan = el("div", undefined, "group-plan");
  g.steps.forEach((s, i) =>
    plan.append(el("span", `${i + 1}. ${s.tool} · ${duration(s.seconds)}`)),
  );
  const select = el("div", undefined, "toolbar");
  select.append(
    button(
      "选择可开始节点",
      () => {
        d.selectedBatches = new Set(
          g.members
            .filter(
              (m) =>
                m.state === "draft" &&
                d.nodes.some((n) => n.node === m.node && n.can_select),
            )
            .map((m) => m.id),
        );
        renderGroup();
      },
      "",
      "group-select-ready",
    ),
    button(
      "清空选择",
      () => {
        d.selectedBatches.clear();
        renderGroup();
      },
      "",
      "group-deselect",
    ),
    el(
      "span",
      `已选 ${g.members.filter((m) => d.selectedBatches.has(m.id)).length} 台`,
      "muted",
    ),
  );
  const table = el("table", undefined, "group-table"),
    thead = el("thead"),
    tr = el("tr");
  [
    "选择 / 节点",
    "当前状态",
    "执行",
    "数据质量",
    "报告",
    "交付 / 详情",
  ].forEach((t) => tr.append(el("th", t)));
  thead.append(tr);
  table.append(thead);
  const tbody = el("tbody");
  g.members.forEach((m) => {
    const row = el("tr"),
      identity = el("td"),
      label = el("label", undefined, "checkbox-label"),
      box = el("input");
    box.type = "checkbox";
    box.dataset.focusKey = "member-" + m.id;
    box.setAttribute("aria-label", "选择任务 " + m.node);
    box.checked = d.selectedBatches.has(m.id);
    box.disabled = ["cancelled", "closed_incomplete", "delivered"].includes(
      m.state,
    );
    box.onchange = () => {
      if (box.checked) d.selectedBatches.add(m.id);
      else d.selectedBatches.delete(m.id);
      renderGroup();
    };
    label.append(box, el("strong", m.node));
    identity.append(label);
    const state = el("td"),
      n = d.nodes.find((n) => n.node === m.node);
    state.append(el("strong", labels[m.state] || m.state));
    if (m.state === "draft" && n) state.append(reachability(n));
    if (m.power?.deadline && m.state === "waiting_boot")
      state.append(el("small", "连接截止 " + dateText(m.power.deadline)));
    if (m.result.error) state.append(el("small", m.result.error, "error"));
    if (m.cancel_requested) state.append(el("small", "已请求取消"));
    const files = el("td");
    files.append(
      el("span", m.state === "delivered" ? "已核验" : "尚未完成"),
      link("查看详情", "#batch/" + m.id),
    );
    if (["running", "finishing"].includes(m.state))
      files.append(link("实时硬件", "#monitor/" + m.id));
    row.append(
      identity,
      state,
      el("td", labels[m.result.execution] || m.result.execution),
      el("td", labels[m.result.quality] || m.result.quality),
      el("td", labels[m.result.report] || m.result.report),
      files,
    );
    tbody.append(row);
  });
  table.append(tbody);
  const wrapper = el("div", undefined, "table-scroll");
  wrapper.dataset.scrollKey = "group-table";
  wrapper.append(table);
  const events = el("section", undefined, "panel group-audit");
  events.append(el("h3", "分发操作记录"));
  for (const op of d.operations) {
    events.append(
      el(
        "p",
        `${dateText(op.at)} · ${op.kind === "start" ? "确认开始" : "请求取消"} · ${op.batches.length} 台${op.reason ? " · " + op.reason : ""}`,
      ),
    );
  }
  if (!d.operations.length)
    events.append(el("p", "仅保存草稿，尚未授权开机或压测。", "muted"));
  replace(
    $("group-body"),
    head,
    counts,
    plan,
    select,
    wrapper,
    el(
      "p",
      "一台节点失败不会停止其他节点。每台执行前仍检查工具、sckocp 采集和报告条件；文件交付与硬件是否合格分别记录。",
      "form-note",
    ),
    events,
  );
}
function openBMC(n) {
  dispatchState.bmcNode = n.node;
  $("bmc-form").reset();
  $("bmc-node").textContent = "节点 " + n.node;
  const candidates = dispatchState.discoveries[n.node]?.candidates || [];
  $("bmc-address").value =
    n.power?.address || (candidates.length === 1 ? candidates[0].address : "");
  $("bmc-error").textContent = "";
  $("bmc-dialog").showModal();
}
async function openBMCProfiles(forEnrollment = false) {
  try {
    dispatchState.profiles = await api("dispatch/bmc-profiles");
    replace(
      $("bmc-profile-select"),
      ...[{ name: "" }, ...dispatchState.profiles].map((p) => {
        const option = el(
          "option",
          p.name
            ? p.name + (p.enabled ? " · 已启用" : " · 已停用")
            : "新增模板",
        );
        option.value = p.name;
        return option;
      }),
    );
    $("bmc-profiles-form").reset();
    dispatchState.profileForEnrollment = forEnrollment;
    fillBMCProfile();
    $("bmc-profiles-dialog").showModal();
  } catch (err) {
    if (forEnrollment) $("node-profile-error").textContent = err.message;
    else notice(err.message, true);
  }
}
function initializeDispatch() {
  $("bmc-profiles-open").onclick = () => openBMCProfiles();
  $("bmc-profile-select").onchange = fillBMCProfile;
  $("bmc-profiles-dialog").addEventListener("close", () => {
    $("bmc-profile-password").value = "";
  });
  $("bmc-profiles-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = $("bmc-profiles-form");
    if (formStates.has(form)) return;
    const value = {
      name: $("bmc-profile-name").value.trim(),
      enabled: $("bmc-profile-enabled").checked,
      networks: $("bmc-profile-networks")
        .value.trim()
        .split(/[\s,]+/),
      node_prefix: $("bmc-profile-prefix").value.trim(),
      model: $("bmc-profile-model").value.trim(),
      username: $("bmc-profile-user").value,
      password: $("bmc-profile-password").value,
      cipher: Number($("bmc-profile-cipher").value),
    };
    formBusy(form, true);
    try {
      await api("dispatch/bmc-profiles", "POST", value);
      $("bmc-profiles-dialog").close();
      if (dispatchState.profileForEnrollment && $("node-dialog").open) {
        await loadNodeBMCProfiles(value.name);
        $("node-bmc-profile").focus();
      }
      notice(
        "模板已保存，符合条件的节点将自动核对并绑定 BMC；没有发送开机指令。",
      );
      await syncDispatch();
      if (["dispatch", "group"].includes(page)) renderDispatch();
    } catch (err) {
      $("bmc-profile-error").textContent = err.message;
    } finally {
      value.password = "";
      formBusy(form, false);
    }
  };
  $("group-create").onclick = () =>
    newGroup().catch((e) => notice(e.message, true));
  $("dispatch-search").oninput = renderDispatch;
  $("group-node-search").oninput = renderNodePicker;
  $("group-select-visible").onclick = () => {
    for (const n of filteredDispatchNodes($("group-node-search").value))
      if (n.can_select && dispatchState.selectedNodes.size < 200)
        dispatchState.selectedNodes.add(n.node);
    renderNodePicker();
  };
  $("group-clear").onclick = () => {
    dispatchState.selectedNodes.clear();
    renderNodePicker();
  };
  $("group-next").onclick = () => {
    if (validateGroupWizard()) {
      dispatchState.step++;
      renderGroupWizard();
    }
  };
  $("group-back").onclick = () => {
    dispatchState.step--;
    renderGroupWizard();
  };
  $("group-add-step").onclick = () => addStep("stress", 60, "group-steps");
  $("group-template").onchange = () => {
    const t = dispatchState.templates.find(
      (t) => t.id === $("group-template").value,
    );
    if (t) {
      $("group-steps").replaceChildren();
      for (const s of t.steps) addStep(s.tool, s.seconds, "group-steps");
    }
  };
  $("group-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = $("group-form"),
      d = dispatchState;
    if (formStates.has(form)) return;
    if (d.step < 2) {
      $("group-next").click();
      return;
    }
    const label = $("group-label").value,
      steps = stepsValue("group-steps"),
      save = $("group-save-template").checked;
    formBusy(form, true);
    try {
      const g = await api("dispatch/groups", "POST", {
        request_id: d.createID,
        label,
        nodes: [...d.selectedNodes],
        steps,
      });
      if (save)
        await api("dispatch/templates", "POST", {
          id: d.templateID,
          label,
          steps,
        });
      $("group-dialog").close();
      location.hash = "group/" + g.id;
      await sync(true);
    } catch (e) {
      $("group-error").textContent = e.message;
    } finally {
      formBusy(form, false);
    }
  };
  $("bmc-dialog").addEventListener("close", () => {
    $("bmc-password").value = "";
  });
  $("bmc-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = $("bmc-form");
    if (formStates.has(form)) return;
    const value = {
      node: dispatchState.bmcNode,
      address: $("bmc-address").value.trim(),
      username: $("bmc-user").value,
      password: $("bmc-password").value,
      cipher: Number($("bmc-cipher").value),
    };
    formBusy(form, true);
    try {
      await api("dispatch/bmc", "POST", value);
      $("bmc-dialog").close();
      await syncDispatch();
      renderDispatch();
      notice("BMC 绑定已保存，后台正在查询状态；没有发送开机指令。");
    } catch (e) {
      $("bmc-error").textContent = e.message;
    } finally {
      value.password = "";
      formBusy(form, false);
    }
  };
}
function fillBMCProfile() {
  const p = dispatchState.profiles.find(
    (p) => p.name === $("bmc-profile-select").value,
  );
  $("bmc-profile-name").value = p?.name || "";
  $("bmc-profile-name").readOnly = Boolean(p);
  $("bmc-profile-networks").value = p?.networks.join("\n") || "";
  $("bmc-profile-prefix").value = p?.node_prefix || "";
  $("bmc-profile-model").value = p?.model || "";
  $("bmc-profile-user").value = p?.username || "";
  $("bmc-profile-password").value = "";
  $("bmc-profile-password").required = !p;
  $("bmc-profile-cipher").value = String(p?.cipher || 17);
  $("bmc-profile-enabled").checked = p?.enabled ?? true;
  $("bmc-profile-error").textContent = "";
}
