"use strict";
const $ = id => document.getElementById(id);
let snapshot = {nodes: [], batches: [], tools: []};
let selected = null;
const labels = {draft:"待开始",armed:"已授权，等待节点",running:"执行中",finishing:"整理 / 交付中",needs_attention:"需要处理",delivered:"文件已核验交付",closed_incomplete:"已关闭，未完成",cancelled:"已取消",
  not_started:"未开始",completed:"按计划结束",failed:"执行失败",interrupted:"已中断",preflight_failed:"预检未通过",readings_reported_validity_unknown:"读数已报告 · 有效性未知",not_collected:"尚未采集",partial:"数据不完整",unavailable:"不可用",generated:"已生成",not_generated:"尚未生成"};
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function badge(value){return el("span",labels[value]||value,"badge "+(["delivered","completed","generated"].includes(value)?"good":["needs_attention","failed","interrupted"].includes(value)?"bad":["armed","partial"].includes(value)?"warn":""));}
async function api(path,method="GET",data){
  const r=await fetch("/api/v1/"+path,{method,credentials:"same-origin",headers:{"Content-Type":"application/json","X-BITS-Request":"1"},body:data===undefined?undefined:JSON.stringify(data)});
  if(r.status===401){if(!$("login-dialog").open)$("login-dialog").showModal();throw new Error("请登录工作台");}
  const text=await r.text();let value;try{value=JSON.parse(text);}catch{value={error:text};}
  if(!r.ok)throw new Error(value.error||"请求未完成");
  return value;
}
async function action(button,fn){button.disabled=true;$("notice").textContent="";try{await fn();await refresh();}catch(e){$("notice").textContent=e.message;}finally{button.disabled=false;}}
function button(text,fn){const b=el("button",text);b.onclick=()=>action(b,fn);return b;}
function render(){
  $("version").textContent="BITS "+snapshot.version+" · 独立架构预览";
  $("connection").textContent="已同步 "+new Date().toLocaleTimeString();
  const online=snapshot.nodes.filter(n=>n.last_seen&&Date.now()-Date.parse(n.last_seen)<20000).length;
  const running=snapshot.batches.filter(b=>["armed","running","finishing"].includes(b.state)).length;
  const attention=snapshot.batches.filter(b=>b.state==="needs_attention").length;
  $("summary").replaceChildren(...[[online+"/"+snapshot.nodes.length,"在线 / 已登记节点"],[running,"进行中的批次"],[attention,"待处理批次"],[snapshot.batches.filter(b=>b.state==="delivered").length,"近期已交付批次"]].map(([n,t])=>{const d=el("div",undefined,"stat");d.append(el("strong",String(n)),el("span",t));return d;}));
  $("node-list").replaceChildren(...snapshot.nodes.map(n=>{const d=el("div",undefined,"node-card");d.append(el("strong",n.id));const seen=n.last_seen&&Date.now()-Date.parse(n.last_seen)<20000;d.append(el("span",seen?"连接在线":"未连接 / 状态过期","badge "+(seen?"good":"")),el("p",n.last_seen?"最后联系："+new Date(n.last_seen).toLocaleString():"下载连接文件后，在节点登记并启动服务","muted"));return d;}));
  if(!snapshot.nodes.length)$("node-list").append(el("p","还没有节点。先添加节点并下载专属连接文件。","empty"));
  $("batch-list").replaceChildren(...snapshot.batches.map(b=>{
    const tr=el("tr"),title=el("td");title.append(el("strong",b.plan.label),el("small",b.plan.node+" · "+b.step_count+" 个步骤"));tr.append(title);
    for(const value of [b.state,b.result.execution,b.result.quality,b.result.report]){const td=el("td");td.append(badge(value));tr.append(td);}
    const ops=el("td");
    ops.append(button("详情",async()=>{selected=b.id;await detail(b.id);}));
    if(b.state==="draft")ops.append(button("开始",async()=>{if(confirm("开始节点 "+b.plan.node+" 上的批次 "+b.plan.label+"？仅执行本批次。"))await api("batches/"+b.id+"/start","POST",{});}));
    if(["draft","armed","running"].includes(b.state))ops.append(button("取消",async()=>{const reason=prompt("请输入取消原因。执行中的压测会请求停止。");if(reason)await api("batches/"+b.id+"/cancel","POST",{reason});}));
    if(b.state==="needs_attention")ops.append(button("关闭未完成",async()=>{const reason=prompt("确认已检查节点及失败证据后，填写关闭原因。不会生成成功记录。");if(reason)await api("batches/"+b.id+"/close-incomplete","POST",{reason});}));
    tr.append(ops);return tr;
  }));
}
async function detail(id){
  const b=await api("batches/"+id),events=await api("batches/"+id+"/events");
  $("detail").hidden=false;$("detail-title").textContent=b.plan.label+" · "+b.plan.node;
  const root=$("detail-body");root.replaceChildren(el("p","批次编号："+b.id,"muted"),el("p","尝试编号："+(b.attempt||"尚未授权"),"muted"));
  if(b.result.error)root.append(el("p",b.result.error,"error"));
  const table=el("table");const head=el("tr");["步骤","压测项目","时长预算"].forEach(v=>head.append(el("th",v)));table.append(head);
  b.plan.steps.forEach(s=>{const tr=el("tr");[s.id,s.tool,s.seconds+" 秒"].forEach(v=>tr.append(el("td",v)));table.append(tr);});root.append(table);
  if(b.artifacts){root.append(el("h3","结果文件"));for(const [name,m] of Object.entries(b.artifacts)){const a=el("a",name+" · "+m.bytes.toLocaleString()+" 字节");a.href="/api/v1/batches/"+b.id+"/files/"+encodeURIComponent(name);a.target="_blank";a.rel="noopener";root.append(a);}root.append(el("p","完成回执 SHA-256："+(b.receipt_sha256||"交付尚未确认"),"muted"));}
  root.append(el("h3","操作与状态记录"));for(const e of events.slice(0,30))root.append(el("p",new Date(e.at).toLocaleString()+" · "+e.kind+" · "+e.detail,"muted"));
}
async function refresh(){try{snapshot=await api("overview");render();if(selected)await detail(selected);}catch(e){$("connection").textContent="连接未确认";if(!$("login-dialog").open)$("notice").textContent=e.message;}}
function addStep(){const row=el("div",undefined,"step"),label=el("label","压测项目"),seconds=el("label","秒");
  const select=el("select");snapshot.tools.forEach(t=>{const o=el("option",t);o.value=t;select.append(o);});
  const input=el("input");input.type="number";input.min="1";input.max="2678400";input.value="60";input.required=true;
  label.append(select);seconds.append(input);const remove=el("button","移除");remove.type="button";remove.onclick=()=>row.remove();row.append(label,seconds,remove);$("steps").append(row);
}
$("create").onclick=()=>{$("batch-node").replaceChildren(...snapshot.nodes.map(n=>{const o=el("option",n.id);o.value=n.id;return o;}));$("steps").replaceChildren();addStep();$("batch-dialog").showModal();};
$("add-step").onclick=addStep;
$("enroll").onclick=()=>$("node-dialog").showModal();
$("close-detail").onclick=()=>{selected=null;$("detail").hidden=true;};
document.querySelectorAll("[data-close]").forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$("login-form").onsubmit=async e=>{e.preventDefault();try{await api("login","POST",{token:$("login-token").value});$("login-token").value="";$("login-error").textContent="";$("login-dialog").close();await refresh();}catch(err){$("login-error").textContent=err.message;}};
$("logout").onclick=async()=>{await api("logout","POST",{});snapshot={nodes:[],batches:[],tools:[]};location.reload();};
$("batch-form").onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const steps=[...$("steps").children].map(row=>({tool:row.querySelector("select").value,seconds:Number(row.querySelector("input").value)}));await api("batches","POST",{node:$("batch-node").value,label:$("batch-label").value,steps});$("batch-dialog").close();});};
$("node-form").onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const cfg=await api("nodes","POST",{id:$("node-id").value,serial:$("node-serial").value,keep_on:$("node-keep-on").checked});const url=URL.createObjectURL(new Blob([JSON.stringify(cfg,null,2)],{type:"application/json"}));const a=el("a");a.href=url;a.download=cfg.node+".bits.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$("node-dialog").close();});};
refresh();setInterval(()=>{if(!$("login-dialog").open)refresh();},5000);
