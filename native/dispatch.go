package bits

import (
 "database/sql"
 "encoding/json"
 "errors"
 "fmt"
 "os"
 "sort"
 "time"
)

// Schema 2 adds explicit multi-node groups and reserves nodes while booting.
// Version 1 binaries reject it. The consistent pre-migration backup is retained.
func migrateDispatch(db *sql.DB, path string) error {
 var schema string
 if err := db.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&schema); err != nil { return err }
 if schema == "2" { return nil }
 var count int
 if err := db.QueryRow("SELECT (SELECT count(*) FROM batches)+(SELECT count(*) FROM nodes)").Scan(&count); err != nil { return err }
 if count > 0 {
  backup := path + ".before-dispatch-v1"
  if _, err := os.Lstat(backup); !os.IsNotExist(err) { return errors.New("pre-migration backup already exists; retain it and inspect database before retrying") }
  if _, err := db.Exec("VACUUM INTO ?", backup); err != nil { return err }
  if err := os.Chmod(backup, 0600); err != nil { return err }
 }
 tx, err := db.Begin(); if err != nil { return err }; defer tx.Rollback()
 for _, q := range []string{
  "CREATE TABLE dispatch_groups(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)",
  "CREATE TABLE group_members(group_id TEXT NOT NULL REFERENCES dispatch_groups(id), batch_id TEXT NOT NULL UNIQUE REFERENCES batches(id), PRIMARY KEY(group_id,batch_id))",
  "CREATE TABLE dispatch_operations(id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES dispatch_groups(id), fingerprint TEXT NOT NULL, body TEXT NOT NULL)",
  "CREATE TABLE task_templates(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)",
  "DROP INDEX one_active_batch",
  "CREATE UNIQUE INDEX one_active_batch ON batches(node) WHERE state IN ('waiting_boot','armed','running','finishing','needs_attention')",
  "UPDATE metadata SET value='2' WHERE key='schema'",
 } { if _, err = tx.Exec(q); err != nil { return err } }
 return tx.Commit()
}

type GroupPlan struct {
 RequestID string `json:"request_id"`
 Label string `json:"label"`
 Nodes []string `json:"nodes"`
 Steps []Step `json:"steps"`
}
type TaskGroup struct {
 ID string `json:"id"`
 Label string `json:"label"`
 Created string `json:"created_at"`
 Steps []Step `json:"steps"`
}
type GroupMember struct {
 ID string `json:"id"`
 Node string `json:"node"`
 State string `json:"state"`
 Result Result `json:"result"`
 Cancel bool `json:"cancel_requested"`
 Power *PowerAttempt `json:"power,omitempty"`
}
type GroupView struct {
 TaskGroup
 Members []GroupMember `json:"members"`
 Counts map[string]int `json:"counts"`
}
type GroupAction struct {
 RequestID string `json:"request_id"`
 Batches []string `json:"batches"`
 Wake bool `json:"wake"`
 Reason string `json:"reason,omitempty"`
}
type DispatchOperation struct {
 ID string `json:"id"`
 Group string `json:"group"`
 Kind string `json:"kind"`
 At string `json:"at"`
 Batches []string `json:"batches"`
 Wake bool `json:"wake"`
 Reason string `json:"reason,omitempty"`
}
type TaskTemplate struct {
 ID string `json:"id"`
 Label string `json:"label"`
 Steps []Step `json:"steps"`
 Created string `json:"created_at"`
}
type Availability struct {
 Node string `json:"node"`
 State string `json:"state"`
 CanSelect bool `json:"can_select"`
 AgentOnline bool `json:"agent_online"`
 Power PowerStatus `json:"power"`
 Active string `json:"active_batch,omitempty"`
 Reason string `json:"reason"`
}
func freshHeartbeat(seen string) bool {
 t, err := time.Parse(time.RFC3339Nano, seen)
 age := time.Since(t)
 return err == nil && age >= -5*time.Second && age <= 20*time.Second
}
func availability(n Node, active string, power PowerStatus) Availability {
 a := Availability{Node:n.ID, State:"unreachable", Power:power, AgentOnline:freshHeartbeat(n.LastSeen), Active:active, Reason:"系统未连接；未接电或管理网络故障等原因尚未确认"}
 switch {
 case n.Disabled: a.State="disabled"; a.Reason="节点已禁用"
 case active!="": a.State="busy"; a.Reason="已有执行、开机等待或未处理批次"
 case a.AgentOnline: a.State="ready"; a.CanSelect=true; a.Reason="系统在线；开始后仍需节点执行前检查"
 case power.Fresh() && power.State=="off": a.State="wakeable"; a.CanSelect=true; a.Reason="BMC 可达，已关机；开始时开机并等待节点连接"
 case power.Fresh() && power.State=="on": a.State="awaiting_agent"; a.CanSelect=true; a.Reason="BMC 确认已开机；开始后等待节点程序连接"
 }
 return a
}
func (s *Store) Availability(power map[string]PowerStatus) ([]Availability,error) {
 nodes,err:=s.Nodes(); if err!=nil {return nil,err}
 active:=map[string]string{}
 rows,err:=s.db.Query("SELECT node,id FROM batches WHERE state IN ('waiting_boot','armed','running','finishing','needs_attention')")
 if err!=nil{return nil,err}
 for rows.Next(){var n,id string;if err=rows.Scan(&n,&id);err!=nil{rows.Close();return nil,err};active[n]=id}
 err=rows.Err();rows.Close();if err!=nil{return nil,err}
 out:=[]Availability{}
 for _,n:=range nodes{out=append(out,availability(n,active[n.ID],power[n.ID]))}
 return out,nil
}
func txAvailability(tx *sql.Tx,node string,power map[string]PowerStatus)(Availability,error){
 n:=Node{ID:node};var active string
 if err:=tx.QueryRow("SELECT last_seen,disabled FROM nodes WHERE id=?",node).Scan(&n.LastSeen,&n.Disabled);err!=nil{return Availability{},fmt.Errorf("节点 %s 未注册",node)}
 err:=tx.QueryRow("SELECT id FROM batches WHERE node=? AND state IN ('waiting_boot','armed','running','finishing','needs_attention')",node).Scan(&active)
 if err!=nil && !errors.Is(err,sql.ErrNoRows){return Availability{},err}
 return availability(n,active,power[node]),nil
}
func validateGroup(p *GroupPlan)error{
 if !idRE.MatchString(p.RequestID)||len(p.Nodes)<1||len(p.Nodes)>200{return errors.New("需要请求标识和 1–200 台不同节点")}
 sort.Strings(p.Nodes)
 for i,n:=range p.Nodes{if !ValidName(n)||(i>0&&n==p.Nodes[i-1]){return errors.New("节点名称无效或重复")}}
 plan:=Plan{Node:p.Nodes[0],Label:p.Label,Steps:append([]Step{},p.Steps...)}
 if err:=ValidatePlan(&plan);err!=nil{return err};p.Steps=plan.Steps;return nil
}
func (s *Store) CreateGroup(p GroupPlan,power map[string]PowerStatus)(TaskGroup,error){
 if err:=validateGroup(&p);err!=nil{return TaskGroup{},err}
 raw,_:=json.Marshal(p); fingerprint:=Digest(raw)
 s.mu.Lock();defer s.mu.Unlock()
 tx,err:=s.db.Begin();if err!=nil{return TaskGroup{},err};defer tx.Rollback()
 var old,body string
 err=tx.QueryRow("SELECT fingerprint,body FROM dispatch_groups WHERE id=?",p.RequestID).Scan(&old,&body)
 if err==nil{var g TaskGroup;json.Unmarshal([]byte(body),&g);if old!=fingerprint{return TaskGroup{},errors.New("请求标识已用于另一份计划")};return g,nil}
 if !errors.Is(err,sql.ErrNoRows){return TaskGroup{},err}
 for _,node:=range p.Nodes{a,e:=txAvailability(tx,node,power);if e!=nil{return TaskGroup{},e};if !a.CanSelect{return TaskGroup{},fmt.Errorf("%s：%s；任务组尚未创建",node,a.Reason)}}
 g:=TaskGroup{ID:p.RequestID,Label:p.Label,Created:UTC(),Steps:p.Steps}
 raw,_=json.Marshal(g)
 if _,err=tx.Exec("INSERT INTO dispatch_groups VALUES(?,?,?)",g.ID,fingerprint,string(raw));err!=nil{return TaskGroup{},err}
 for _,node:=range p.Nodes{
  b:=Batch{ID:Random(16),GroupID:g.ID,Plan:Plan{Node:node,Label:p.Label,Steps:p.Steps},State:"draft",Created:g.Created,Result:Result{Execution:"not_started",Quality:"not_collected",Report:"not_generated"}}
  raw,_=json.Marshal(b)
  if _,err=tx.Exec("INSERT INTO batches(id,node,state,body) VALUES(?,?,?,?)",b.ID,node,b.State,string(raw));err!=nil{return TaskGroup{},err}
  if _,err=tx.Exec("INSERT INTO group_members VALUES(?,?)",g.ID,b.ID);err!=nil{return TaskGroup{},err}
  if err=event(tx,b.ID,"group_created",g.ID+"; explicit start required");err!=nil{return TaskGroup{},err}
 }
 return g,tx.Commit()
}
func (s *Store) Group(id string)(GroupView,error){
 var body string;v:=GroupView{Members:[]GroupMember{},Counts:map[string]int{}}
 if err:=s.db.QueryRow("SELECT body FROM dispatch_groups WHERE id=?",id).Scan(&body);err!=nil{return v,err}
 if err:=json.Unmarshal([]byte(body),&v.TaskGroup);err!=nil{return v,err}
 rows,err:=s.db.Query("SELECT b.body FROM group_members m JOIN batches b ON b.id=m.batch_id WHERE m.group_id=? ORDER BY b.node",id)
 if err!=nil{return v,err};defer rows.Close()
 for rows.Next(){var b Batch;if err=rows.Scan(&body);err!=nil{return v,err};if err=json.Unmarshal([]byte(body),&b);err!=nil{return v,err};b.Result.Steps=nil;v.Members=append(v.Members,GroupMember{b.ID,b.Plan.Node,b.State,b.Result,b.Cancel,b.Power});v.Counts[b.State]++}
 return v,rows.Err()
}
func (s *Store) Groups(offset int)([]GroupView,error){
 if offset<0||offset>1000000{return nil,errors.New("invalid page offset")}
 rows,err:=s.db.Query("SELECT id FROM dispatch_groups ORDER BY rowid DESC LIMIT 25 OFFSET ?",offset)
 if err!=nil{return nil,err};ids:=[]string{}
 for rows.Next(){var id string;if err=rows.Scan(&id);err!=nil{rows.Close();return nil,err};ids=append(ids,id)}
 err=rows.Err();rows.Close();if err!=nil{return nil,err}
 out:=[]GroupView{};for _,id:=range ids{v,e:=s.Group(id);if e!=nil{return nil,e};v.Members=nil;out=append(out,v)};return out,nil
}
func (s *Store) GroupAction(id,kind string,in GroupAction,power map[string]PowerStatus)(DispatchOperation,error){
 if !idRE.MatchString(id)||!idRE.MatchString(in.RequestID)||len(in.Batches)<1||len(in.Batches)>200{return DispatchOperation{},errors.New("选择 1–200 个本组批次，并提供请求标识")}
 if kind!="start"&&kind!="cancel"{return DispatchOperation{},errors.New("unknown group action")}
 if kind=="cancel"&&(len(in.Reason)<1||len(in.Reason)>512){return DispatchOperation{},errors.New("请填写取消原因（最多 512 字节）")}
 sort.Strings(in.Batches)
 for i,b:=range in.Batches{if !idRE.MatchString(b)||(i>0&&b==in.Batches[i-1]){return DispatchOperation{},errors.New("批次无效或重复")}}
 raw,_:=json.Marshal(in);fingerprint:=Digest(append([]byte(id+":"+kind+":"),raw...))
 s.mu.Lock();defer s.mu.Unlock();tx,err:=s.db.Begin();if err!=nil{return DispatchOperation{},err};defer tx.Rollback()
 var old,body string
 err=tx.QueryRow("SELECT fingerprint,body FROM dispatch_operations WHERE id=?",in.RequestID).Scan(&old,&body)
 if err==nil{var op DispatchOperation;json.Unmarshal([]byte(body),&op);if old!=fingerprint{return op,errors.New("请求标识已用于其他操作")};return op,nil}
 if !errors.Is(err,sql.ErrNoRows){return DispatchOperation{},err}
 for _,bid:=range in.Batches{
  if err=tx.QueryRow("SELECT b.body FROM group_members m JOIN batches b ON m.batch_id=b.id WHERE m.group_id=? AND b.id=?",id,bid).Scan(&body);err!=nil{return DispatchOperation{},errors.New("选择中包含不属于本组的批次")}
  var b Batch;if err=json.Unmarshal([]byte(body),&b);err!=nil{return DispatchOperation{},err}
  if kind=="start"{
   if b.State!="draft"{return DispatchOperation{},fmt.Errorf("%s 已经开始或结束；整次开始未提交",b.Plan.Node)}
   a,e:=txAvailability(tx,b.Plan.Node,power);if e!=nil{return DispatchOperation{},e}
   if !a.CanSelect{return DispatchOperation{},fmt.Errorf("%s：%s；整次开始未提交",b.Plan.Node,a.Reason)}
   if !a.AgentOnline && !in.Wake{return DispatchOperation{},errors.New("包含系统未连接的节点，请明确确认开机/等待连接")}
   b.Attempt=Random(16)
   if a.AgentOnline{b.State="armed";b.Expires=time.Now().UTC().Add(5*time.Minute).Format(time.RFC3339Nano)}else{
    b.State="waiting_boot";b.Power=&PowerAttempt{RequestedAt:UTC(),Deadline:time.Now().UTC().Add(10*time.Minute).Format(time.RFC3339Nano),Stage:"pending",Binding:a.Power.Binding}
   }
  }else{
   if IsTerminal(b.State){return DispatchOperation{},fmt.Errorf("%s 已结束；请更新选择",b.Plan.Node)}
   b.Cancel=true
   if b.State=="draft"||b.State=="armed"||b.State=="waiting_boot"{b.State="cancelled";b.Result.Error=in.Reason}
  }
  raw,_=json.Marshal(b)
  if _,err=tx.Exec("UPDATE batches SET state=?,body=? WHERE id=?",b.State,string(raw),b.ID);err!=nil{return DispatchOperation{},err}
  if err=event(tx,b.ID,"group_"+kind,id+" / "+in.RequestID+" "+in.Reason);err!=nil{return DispatchOperation{},err}
 }
 op:=DispatchOperation{in.RequestID,id,kind,UTC(),in.Batches,in.Wake,in.Reason};raw,_=json.Marshal(op)
 if _,err=tx.Exec("INSERT INTO dispatch_operations VALUES(?,?,?,?)",op.ID,id,fingerprint,string(raw));err!=nil{return op,err}
 return op,tx.Commit()
}
func (s *Store) GroupOperations(id string)([]DispatchOperation,error){
 rows,err:=s.db.Query("SELECT body FROM dispatch_operations WHERE group_id=? ORDER BY rowid DESC LIMIT 100",id);if err!=nil{return nil,err};defer rows.Close();out:=[]DispatchOperation{}
 for rows.Next(){var body string;var op DispatchOperation;if err=rows.Scan(&body);err!=nil{return nil,err};if err=json.Unmarshal([]byte(body),&op);err!=nil{return nil,err};out=append(out,op)};return out,rows.Err()
}
func (s *Store) SaveTemplate(t TaskTemplate)(TaskTemplate,error){
 if !idRE.MatchString(t.ID){return t,errors.New("invalid template request ID")}
 p:=Plan{Node:"template",Label:t.Label,Steps:append([]Step{},t.Steps...)};if err:=ValidatePlan(&p);err!=nil{return t,err};t.Steps=p.Steps;t.Created="";raw,_:=json.Marshal(t);hash:=Digest(raw)
 s.mu.Lock();defer s.mu.Unlock();var old,body string
 err:=s.db.QueryRow("SELECT fingerprint,body FROM task_templates WHERE id=?",t.ID).Scan(&old,&body)
 if err==nil{if old!=hash{return t,errors.New("模板请求标识已用于其他内容")};err=json.Unmarshal([]byte(body),&t);return t,err};if !errors.Is(err,sql.ErrNoRows){return t,err}
 t.Created=UTC();raw,_=json.Marshal(t);_,err=s.db.Exec("INSERT INTO task_templates VALUES(?,?,?)",t.ID,hash,string(raw));return t,err
}
func (s *Store) Templates()([]TaskTemplate,error){
 rows,err:=s.db.Query("SELECT body FROM task_templates ORDER BY rowid DESC LIMIT 200");if err!=nil{return nil,err};defer rows.Close();out:=[]TaskTemplate{}
 for rows.Next(){var body string;var t TaskTemplate;if err=rows.Scan(&body);err!=nil{return nil,err};if err=json.Unmarshal([]byte(body),&t);err!=nil{return nil,err};out=append(out,t)};return out,rows.Err()
}
