package bits

import (
 "context"
 "encoding/json"
 "errors"
 "io"
 "net"
 "os"
 "os/exec"
 "path/filepath"
 "strconv"
 "strings"
 "sync"
 "time"
)

// BMC credentials belong only to the center. They never enter batch plans,
// node connections, reports, command-line passwords or API GET responses.
type BMCBinding struct {
 Node string `json:"node"`
 Address string `json:"address"`
 Username string `json:"username"`
 Password string `json:"password"`
 Cipher int `json:"cipher"`
 Revision string `json:"revision,omitempty"`
}
type PowerStatus struct {
 Configured bool `json:"configured"`
 Address string `json:"address,omitempty"`
 State string `json:"state"`
 Checked string `json:"checked_at,omitempty"`
 Error string `json:"error,omitempty"`
 Binding string `json:"-"`
}
func (p PowerStatus) Fresh()bool{
 t,err:=time.Parse(time.RFC3339Nano,p.Checked);age:=time.Since(t)
 return p.Configured&&p.Binding!=""&&err==nil&&age>=-5*time.Second&&age<90*time.Second
}
type PowerAttempt struct {
 RequestedAt string `json:"requested_at"`
 Deadline string `json:"deadline"`
 Stage string `json:"stage"`
 Binding string `json:"binding"`
 CommandAt string `json:"command_at,omitempty"`
}
func validateBinding(b BMCBinding)error{
 ip:=net.ParseIP(b.Address)
 if !ValidName(b.Node)||ip==nil||ip.To4()==nil||!ip.IsPrivate()||ip.IsLoopback()||b.Address!=ip.String(){return errors.New("BMC 需要注册节点名和规范的内网 IPv4 地址")}
 if len(b.Username)<1||len(b.Username)>16||strings.HasPrefix(b.Username,"-")||strings.ContainsAny(b.Username,"\x00\r\n"){return errors.New("BMC 用户名须为 1–16 字节")}
 if len(b.Password)<1||len(b.Password)>20||strings.ContainsAny(b.Password,"\x00\r\n"){return errors.New("IPMI 2.0 密码须为 1–20 字节，不含换行")}
 if b.Cipher!=17&&b.Cipher!=3{return errors.New("仅支持 IPMI LANplus cipher 17 或明确选择的 cipher 3")};return nil
}
func bindingID(b BMCBinding)string{b.Password="";raw,_:=json.Marshal(b);return Digest(raw)}
type powerDriver interface{
 Status(context.Context,BMCBinding)(string,error)
 On(context.Context,BMCBinding)error
}
type ipmiDriver struct{}
type limitedOutput struct{data []byte}
func (o *limitedOutput)Write(b []byte)(int,error){n:=len(b);space:=16384-len(o.data);if space>0{if len(b)>space{b=b[:space]};o.data=append(o.data,b...)};return n,nil}
func runIPMI(ctx context.Context,b BMCBinding,action string)(string,error){
 if action!="status"&&action!="on"{return "",errors.New("power operation rejected")}
 if err:=validateBinding(b);err!=nil{return "",err}
 const program="/usr/bin/ipmitool"
 if err:=TrustedProgram(program);err!=nil{return "",errors.New("未找到受信任的 /usr/bin/ipmitool；请安装发行版 ipmitool 包")}
 ctx,cancel:=context.WithTimeout(ctx,6*time.Second);defer cancel()
 cmd:=exec.CommandContext(ctx,program,"-I","lanplus","-C",strconv.Itoa(b.Cipher),"-H",b.Address,"-U",b.Username,"-E","-L","OPERATOR","-N","1","-R","1","chassis","power",action)
 cmd.Env=[]string{"PATH=/usr/bin:/bin","LC_ALL=C","HOME=/nonexistent","IPMI_PASSWORD="+b.Password}
 out:=&limitedOutput{};cmd.Stdout=out;cmd.Stderr=io.Discard
 if err:=cmd.Run();err!=nil{return "",errors.New("BMC 请求未确认成功：检查管理网络、账号权限和 cipher 设置")}
 return strings.TrimSpace(string(out.data)),nil
}
func (ipmiDriver)Status(ctx context.Context,b BMCBinding)(string,error){
 raw,err:=runIPMI(ctx,b,"status");if err!=nil{return "unknown",err}
 switch raw{case "Chassis Power is on":return "on",nil;case "Chassis Power is off":return "off",nil}
 return "unknown",errors.New("BMC 电源状态无法识别")
}
func (ipmiDriver)On(ctx context.Context,b BMCBinding)error{
 _,err:=runIPMI(ctx,b,"on");return err // Exit 0 acknowledges a command, not a booted OS.
}
type PowerManager struct{
 mu sync.Mutex
 bindings map[string]BMCBinding
 status map[string]PowerStatus
 path string
 driver powerDriver
 slots chan struct{}
 refresh chan struct{}
}
func newPowerManager(data string)*PowerManager{return &PowerManager{bindings:map[string]BMCBinding{},status:map[string]PowerStatus{},path:filepath.Join(data,"bmc.json"),driver:ipmiDriver{},slots:make(chan struct{},8),refresh:make(chan struct{},1)}}
func (p *PowerManager)Load()error{
 p.mu.Lock();defer p.mu.Unlock()
 if _,err:=os.Lstat(p.path);os.IsNotExist(err){return nil}
 var bindings []BMCBinding
 if err:=ReadJSON(p.path,&bindings);err!=nil{return errors.New("BMC 配置不可信或不可读；保留原文件并检查权限")}
 if len(bindings)>200{return errors.New("BMC 配置超过 200 台")}
 hosts:=map[string]bool{};nodes:=map[string]BMCBinding{}
 for _,b:=range bindings{if err:=validateBinding(b);err!=nil{return err};if _,ok:=nodes[b.Node];ok||hosts[b.Address]{return errors.New("BMC 节点或管理地址重复")};nodes[b.Node]=b;hosts[b.Address]=true}
 p.bindings=nodes;return nil
}
func (p *PowerManager)Bind(b BMCBinding)error{
 if err:=validateBinding(b);err!=nil{return err}
 b.Revision=Random(16)
 p.mu.Lock();defer p.mu.Unlock()
 all:=[]BMCBinding{b}
 for node,old:=range p.bindings{if node!=b.Node{if old.Address==b.Address{return errors.New("该 BMC 地址已绑定另一节点")};all=append(all,old)}}
 if len(all)>200{return errors.New("最多绑定 200 个 BMC")}
 if err:=AtomicJSON(p.path,all);err!=nil{return err}
 p.bindings[b.Node]=b;delete(p.status,b.Node)
 select{case p.refresh<-struct{}{}:default:};return nil
}
func (p *PowerManager)Snapshot()map[string]PowerStatus{
 p.mu.Lock();defer p.mu.Unlock();out:=map[string]PowerStatus{}
 for node,b:=range p.bindings{
  v,ok:=p.status[node];if !ok{v=PowerStatus{Configured:true,Address:b.Address,State:"checking",Binding:bindingID(b)}}
  out[node]=v
 };return out
}
func (p *PowerManager)probe(ctx context.Context,b BMCBinding)PowerStatus{
 v:=PowerStatus{Configured:true,Address:b.Address,State:"unknown",Binding:bindingID(b)}
 select{case p.slots<-struct{}{}:defer func(){<-p.slots}();case <-ctx.Done():return v}
 state,err:=p.driver.Status(ctx,b);v.Checked=UTC();v.State=state;if err!=nil{v.Error="BMC 未确认可达；请检查管理网络、账号权限或协议设置";v.State="unknown"}
 p.mu.Lock();if current,ok:=p.bindings[b.Node];ok&&bindingID(current)==v.Binding{p.status[b.Node]=v};p.mu.Unlock();return v
}
func (p *PowerManager)Monitor(ctx context.Context){
 for{
  p.mu.Lock();all:=[]BMCBinding{};for _,b:=range p.bindings{all=append(all,b)};p.mu.Unlock()
  var wg sync.WaitGroup
  for _,b:=range all{wg.Add(1);go func(b BMCBinding){defer wg.Done();p.probe(ctx,b)}(b)};wg.Wait()
  select{case <-ctx.Done():return;case <-time.After(30*time.Second):case <-p.refresh:}
 }
}
func (s *Store) bootBatches()([]Batch,error){
 rows,err:=s.db.Query("SELECT body FROM batches WHERE state IN ('waiting_boot','armed')");if err!=nil{return nil,err};defer rows.Close();out:=[]Batch{}
 for rows.Next(){var body string;var b Batch;if err=rows.Scan(&body);err!=nil{return nil,err};if err=json.Unmarshal([]byte(body),&b);err!=nil{return nil,err};out=append(out,b)};return out,rows.Err()
}
func (s *Server) advanceBoot(ctx context.Context,b Batch){
 if b.State=="armed"{
  exp,err:=time.Parse(time.RFC3339Nano,b.Expires);if err==nil&&time.Now().Before(exp){return}
  s.Store.Mutate(b.ID,"start_expired",func(v *Batch)error{if v.State=="armed"{v.State="cancelled";v.Result.Error="开始授权超时；未自动重排"};return nil});return
 }
 if b.Power==nil{return}
 deadline,err:=time.Parse(time.RFC3339Nano,b.Power.Deadline)
 if err!=nil||time.Now().After(deadline){s.bootFailure(b.ID,"等待节点连接超过 10 分钟；未执行压测，请处理后创建新批次");return}
 // The authenticated agent heartbeat, not a BMC command response, permits arming.
 var seen string;var disabled bool
 if err=s.Store.db.QueryRow("SELECT last_seen,disabled FROM nodes WHERE id=?",b.Plan.Node).Scan(&seen,&disabled);err!=nil||disabled{s.bootFailure(b.ID,"节点不存在或已禁用");return}
 if freshHeartbeat(seen){
  s.Store.Mutate(b.ID,"boot_agent_connected",func(v *Batch)error{if v.State=="waiting_boot"&&!v.Cancel{v.State="armed";v.Expires=time.Now().UTC().Add(5*time.Minute).Format(time.RFC3339Nano);v.Power.Stage="agent_connected"};return nil});return
 }
 if b.Power.Stage=="waiting_agent"{return}
 s.power.mu.Lock();binding,ok:=s.power.bindings[b.Plan.Node];s.power.mu.Unlock()
 if !ok||bindingID(binding)!=b.Power.Binding{s.bootFailure(b.ID,"BMC 绑定已变化；压测未开始");return}
 status:=s.power.probe(ctx,binding);if ctx.Err()!=nil{return}
 if status.State=="on"{
  s.Store.Mutate(b.ID,"power_on_observed",func(v *Batch)error{if v.State=="waiting_boot"{v.Power.Stage="waiting_agent"};return nil});return
 }
 if status.State!="off"{s.bootFailure(b.ID,"BMC 不可达或状态不明；未发送开机指令");return}
 if b.Power.Stage!="pending"{s.bootFailure(b.ID,"上次开机请求结果未确认，BMC 仍报告关机；请人工检查，未自动重发");return}
 authorized:=false
 _,err=s.Store.Mutate(b.ID,"power_on_intent",func(v *Batch)error{
  if v.State=="waiting_boot"&&!v.Cancel&&v.Power.Stage=="pending"{v.Power.Stage="command_requested";v.Power.CommandAt=UTC();authorized=true};return nil
 })
 if err!=nil||!authorized{return}
 // Intent is durable before I/O. A restart never blindly repeats power commands.
 // Cancellation after this point cannot retract a command already sent to BMC.
 select{case s.power.slots<-struct{}{}:case <-ctx.Done():return}
 err=s.power.driver.On(ctx,binding);<-s.power.slots
 if ctx.Err()!=nil{return}
 if err!=nil{s.bootFailure(b.ID,"开机指令未确认成功；可能已经发出，请核对 BMC，未自动重发");return}
 s.Store.Mutate(b.ID,"power_command_acknowledged",func(v *Batch)error{if v.State=="waiting_boot"{v.Power.Stage="waiting_agent"};return nil})
}
func (s *Server)bootFailure(id,message string){
 s.Store.Mutate(id,"boot_failed",func(v *Batch)error{if v.State=="waiting_boot"{v.State="needs_attention";v.Result.Error=message;v.Power.Stage="failed"};return nil})
}
func (s *Server)RunDispatch(ctx context.Context){
 monitorDone:=make(chan struct{});go func(){defer close(monitorDone);s.power.Monitor(ctx)}();defer func(){<-monitorDone}()
 for{
  batches,err:=s.Store.bootBatches()
  if err==nil{var wg sync.WaitGroup;for _,b:=range batches{wg.Add(1);go func(b Batch){defer wg.Done();s.advanceBoot(ctx,b)}(b)};wg.Wait()}
  select{case <-ctx.Done():return;case <-time.After(2*time.Second):}
 }
}
func (s *Server)LoadPower()error{return s.power.Load()}
