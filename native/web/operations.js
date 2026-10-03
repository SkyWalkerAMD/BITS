"use strict";
const wakeLabels = {pending:"等待发送", command_requested:"开机请求已记录", waiting_agent:"等待系统连接", online:"已连接", already_online:"已在线 · 已跳过", failed:"唤醒未确认", timed_out:"连接超时"};
const operationSelection = {batches:new Set(), reports:new Set(), wake:new Set()};
const operationRequests = new Map();
function isWaking(n) { return ["pending","command_requested","waiting_agent"].includes(n?.wake?.state); }
function wakeButton(n) {
  if (!n || n.disabled || activeBatch(n.id) || online(n) || !n.power?.configured) return null;
  const b = button(isWaking(n) ? "正在唤醒" : "唤醒机器", () => confirmWake([n.id]), "", "wake-"+n.id);
  b.disabled = isWaking(n) || !powerFresh(n) || !["off","on"].includes(n.power.state);
  b.title = b.disabled ? (isWaking(n) ? "等待节点系统连接" : "等待 BMC 确认电源状态") : "开机后查看日常监控";
  return b;
}
function wakeNote(n) {
  if (isWaking(n)) return wakeLabels[n.wake.state];
  if (n?.wake_hold && online(n)) return "手动唤醒 · 保持开机";
  if (["failed","timed_out"].includes(n?.wake?.state) && !online(n)) return n.wake.error || wakeLabels[n.wake.state];
  return "";
}
function actionRequest(kind, ids) {
  const key = kind+":"+[...ids].sort().join(",");
  if (!operationRequests.has(key)) operationRequests.set(key, requestID());
  return {key,id:operationRequests.get(key)};
}
function confirmWake(ids) {
  ids = [...ids].sort();
  if (!ids.length || ids.length>200) { notice("请选择 1–200 台机器", true); return; }
  const req=actionRequest("wake",ids);
  confirmAction("确认唤醒", `唤醒 ${ids.length} 台机器：\n${ids.join("、")}\n\n仅开机并等待系统连接，不创建或执行压测。已在线机器自动跳过。新版节点本次保持开机，下一次开始压测后恢复原任务关机策略。`, false, async () => {
    const op=await api("dispatch/wakes","POST",{request_id:req.id,nodes:ids});
    operationRequests.delete(req.key);
    operationSelection.wake.clear();
    for (const m of op.members) { const n=snapshot.nodes.find(n=>n.id===m.node); if(n) n.wake=m; }
    return "唤醒已提交，可在任务分发查看每台机器的连接进度。";
  });
}
function renderWakeToolbar() {
  const chosen=operationSelection.wake;
  const eligible=dispatchState.nodes.filter(n=>n.can_select&&!n.agent_online&&["wakeable","awaiting_agent"].includes(n.state));
  const select=button("选择可唤醒机器",()=>{ for(const n of filteredDispatchNodes($("dispatch-search").value)) if(eligible.some(v=>v.node===n.node)) chosen.add(n.node); renderDispatch(); });
  const clear=button("清空选择",()=>{chosen.clear();renderDispatch();});
  const run=button("唤醒所选机器",()=>confirmWake(chosen),"primary");
  run.disabled=!chosen.size||!connectionFresh();
  replace($("wake-toolbar"),select,clear,el("span",`已选 ${chosen.size} 台`),run);
}
function wakeCheckbox(n) {
  const label=el("label",undefined,"operation-choice"), box=el("input");
  box.type="checkbox"; box.setAttribute("aria-label","选择唤醒 "+n.node); box.dataset.focusKey="wake-select-"+n.node;
  box.checked=operationSelection.wake.has(n.node);
  box.disabled=(!n.can_select||n.agent_online)&&!box.checked;
  box.onchange=()=>{if(box.checked) operationSelection.wake.add(n.node); else operationSelection.wake.delete(n.node); renderWakeToolbar();};
  label.append(box,el("span",n.node)); return label;
}
function renderWakeHistory() {
  const root=$("wake-history"), rows=dispatchState.wakes||[];
  root.hidden=!rows.length;
  replace(root,...rows.map(op=>{
    const card=el("article",undefined,"panel operation-record");
    card.append(el("h3",`${dateText(op.created_at)} · 唤醒 ${op.members.length} 台`));
    for(const m of op.members) {
      const row=el("div",undefined,"operation-result"), identity=el("div");
      identity.append(el("strong",m.node),el("small",m.error|| (m.state==="waiting_agent" ? "连接截止 "+dateText(m.deadline):"")));
      row.append(identity,el("span",wakeLabels[m.state]||m.state,"reachability "+(["online","already_online"].includes(m.state)?"ready":"awaiting_agent")));
      const n=snapshot.nodes.find(n=>n.id===m.node);
      if(n && online(n)) row.append(button("实时监控 ↗",()=>openNodeMonitor(m.node),"quiet"));
      card.append(row);
    }
    return card;
  }));
  const previous=button("← 较新唤醒",async()=>{dispatchState.wakeOffset=Math.max(0,dispatchState.wakeOffset-25);await syncDispatch();renderDispatch();});
  const next=button("较早唤醒 →",async()=>{dispatchState.wakeOffset+=25;await syncDispatch();renderDispatch();});
  previous.disabled=dispatchState.wakeOffset===0;next.disabled=rows.length<25;
  replace($("wake-pagination"),previous,el("span",`第 ${dispatchState.wakeOffset/25+1} 页`),next);
  $("wake-pagination").hidden=!rows.length&&dispatchState.wakeOffset===0;
}
function canDelete(b) { return ["delivered","cancelled","closed_incomplete"].includes(b.state); }
function deleteCheckbox(b,kind) {
  const label=el("label",undefined,"operation-choice"), box=el("input");
  box.type="checkbox"; box.setAttribute("aria-label",`选择${kind==="reports"?"报告":"批次"} ${b.plan.label}`); box.dataset.focusKey=kind+"-select-"+b.id;
  box.checked=operationSelection[kind].has(b.id); box.disabled=!canDelete(b);
  box.onchange=()=>{if(box.checked) operationSelection[kind].add(b.id); else operationSelection[kind].delete(b.id); if(kind==="reports") renderReports();else renderBatches();};
  label.append(box,el("span","选择"));return label;
}
function confirmDelete(ids) {
  ids=[...ids].sort();
  if(!ids.length||ids.length>200) {notice("请选择 1–200 个已结束批次",true);return;}
  const all=ids.map(id=>snapshot.batches.find(b=>b.id===id));
  if(all.some(b=>!b||!canDelete(b))) {notice("选择中有未结束或已变化的批次，请刷新后重新选择。",true);return;}
  const req=actionRequest("delete",ids);
  confirmAction("永久删除",`永久删除 ${ids.length} 个批次及其报告：\n${all.map(b=>b.plan.label+"（"+b.plan.node+"）").join("\n")}\n\n将删除中心的批次记录、HTML / Excel 报告、遥测、日志和回执，无法恢复。节点本地副本不在此操作范围。`,false,async()=>{
    await api("deletions","POST",{request_id:req.id,batches:ids});
    operationRequests.delete(req.key);
    for(const kind of ["batches","reports"]) for(const id of ids) operationSelection[kind].delete(id);
    if(ids.includes(selected)) { selected=null;detailData=null;detailFrame=null;location.hash="reports"; }
    return "永久删除已提交，清理进度显示在列表下方。";
  },true);
}
function renderDeleteToolbar(kind,pageItems) {
  const chosen=operationSelection[kind];
  for(const id of chosen) if(!snapshot.batches.some(b=>b.id===id&&canDelete(b))) chosen.delete(id);
  const select=button(kind==="reports"?"选择本页报告":"选择本页已结束批次",()=>{for(const b of pageItems) if(canDelete(b)) chosen.add(b.id); if(kind==="reports") renderReports();else renderBatches();});
  const clear=button("清空选择",()=>{chosen.clear();if(kind==="reports") renderReports();else renderBatches();});
  const remove=button("删除所选",()=>confirmDelete(chosen),"danger");
  remove.disabled=!chosen.size||!connectionFresh();
  const hidden=[...chosen].filter(id=>!pageItems.some(b=>b.id===id)).length;
  replace($(kind+"-delete-toolbar"),select,clear,el("span",`已选 ${chosen.size} 项`+(hidden?`（其他页 ${hidden} 项）`:"")),remove);
  const recent=(snapshot.deletions||[]).slice(0,5);
  replace($(kind+"-deletion-history"),...recent.map(d=>{
    const row=el("div",undefined,"operation-result"),counts=d.counts||{};
    row.append(el("span",`${dateText(d.at)} · 已删除 ${counts.done||0} / ${d.batches.length} 项`));
    if(counts.pending) row.append(el("span",`正在清理 ${counts.pending} 项`));
    if(counts.failed) row.append(el("span",`清理失败 ${counts.failed} 项，请检查中心日志`,"error"),button("重试清理",()=>confirmAction("重试永久删除",`继续清理此前确认删除的 ${d.batches.length} 个批次。`,false,()=>api("deletions","POST",{request_id:d.id,batches:d.batches}),true)));
    return row;
  }));
}
