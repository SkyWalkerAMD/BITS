package bits

import (
 "context"
 "errors"
 "io"
 "net"
 "net/http"
 "strconv"
 "sync"
 "time"

 "golang.org/x/crypto/ssh"
 "github.com/pkg/sftp"
)

type RemoteManager struct {
 mu sync.Mutex
 dir string
 sessions map[string]*RemoteSession
 pending int
 attempts map[string][]time.Time
 dial func(context.Context,string,string)(net.Conn,error)
}
type RemoteSession struct {
 mu sync.Mutex
 inputMu sync.Mutex
 fileMu sync.Mutex
 id,owner,node,address,username string
 port int
 client *ssh.Client
 terminal *ssh.Session
 stdin io.WriteCloser
 files *sftp.Client
 buffer []byte
 base int64
 ended bool
 reason string
 changed chan struct{}
 lastInput,expires time.Time
}
type RemoteConnect struct {
 Node string `json:"node"`
 Address string `json:"address"`
 Port int `json:"port"`
 Profile string `json:"profile"`
 Username string `json:"username"`
 Password string `json:"password"`
 Trust string `json:"trust_fingerprint"`
 Remember bool `json:"remember_template"`
 Cols int `json:"cols"`
 Rows int `json:"rows"`
}
type remoteTrust struct { Fingerprint string `json:"fingerprint"`; Algorithm string `json:"algorithm"`; Changed bool `json:"changed"`; Previous string `json:"previous,omitempty"` }
func newRemoteManager(dir string)*RemoteManager {
 d:=&net.Dialer{Timeout:10*time.Second}
 return &RemoteManager{dir:dir,sessions:map[string]*RemoteSession{},attempts:map[string][]time.Time{},dial:d.DialContext}
}
func remoteOwner(r *http.Request) string {
 if token:=r.Header.Get("Authorization");token!=""{return Digest([]byte(token))}
 if c,e:=r.Cookie("bits_session");e==nil{return Digest([]byte(c.Value))}
 return ""
}
func (s *Server) remoteTarget(node,address string) error {
 if !ValidName(node)||!systemIP(address){return errors.New("请选择节点上报的系统 IP")}
 var disabled bool
 var last string
 if e:=s.Store.db.QueryRow("SELECT disabled,last_seen FROM nodes WHERE id=?",node).Scan(&disabled,&last);e!=nil||disabled{return errors.New("节点不可用")}
 stamp,e:=time.Parse(time.RFC3339Nano,last)
 if e!=nil||time.Since(stamp)>30*time.Second{return errors.New("节点系统尚未连接")}
 v,e:=s.Store.SystemNetwork(node);if e!=nil{return errors.New("等待节点上报系统 IP；请升级并启动节点服务")}
 stamp,e=time.Parse(time.RFC3339Nano,v.Received)
 if e!=nil||time.Since(stamp)>90*time.Second{return errors.New("系统 IP 已过期，等待节点刷新")}
 for _,a:=range v.Addresses{if a.Address==address{return nil}}
 return errors.New("此 IP 不属于节点最新上报的系统地址")
}
func (m *RemoteManager) reserve(owner string) error {
 m.mu.Lock();defer m.mu.Unlock()
 now:=time.Now();recent:=[]time.Time{}
 for k,list:=range m.attempts{if len(list)==0||now.Sub(list[len(list)-1])>time.Minute{delete(m.attempts,k)}}
 for _,at:=range m.attempts[owner]{if now.Sub(at)<time.Minute{recent=append(recent,at)}}
 if len(recent)>=12||len(m.attempts)>=256{return errors.New("连接尝试过于频繁，请稍后重试")}
 m.attempts[owner]=append(recent,now)
 count:=0
 for id,v:=range m.sessions{
  v.mu.Lock();expired:=v.ended&&now.Sub(v.lastInput)>time.Minute;v.mu.Unlock()
  if expired{delete(m.sessions,id)}else if v.owner==owner{count++}
 }
 if count>=4||len(m.sessions)+m.pending>=16{return errors.New("SSH 会话数已达上限，请先断开不用的会话")}
 m.pending++;return nil
}
func validTerminalSize(cols,rows int)bool{return cols>=20&&cols<=400&&rows>=5&&rows<=150}
func (s *Server) connectRemote(ctx context.Context,owner string,expires time.Time,in RemoteConnect)(*RemoteSession,*remoteTrust,error){
 if e:=s.remoteTarget(in.Node,in.Address);e!=nil{return nil,nil,e}
 if !validTerminalSize(in.Cols,in.Rows){return nil,nil,errors.New("invalid terminal size")}
 m:=s.remote
 if e:=m.reserve(owner);e!=nil{return nil,nil,e}
 defer func(){m.mu.Lock();m.pending--;m.mu.Unlock()}()
 m.mu.Lock()
 v,err:=m.config()
 if err==nil&&in.Profile!=""{
  p,ok:=v.Profiles[in.Profile]
  if !ok{err=errors.New("SSH 模板不存在")}else{in.Username=p.Username;in.Port=p.Port;in.Password,err=m.decrypt(p)}
 }
 key:=in.Node+"/"+net.JoinHostPort(in.Address,strconv.Itoa(in.Port))
 pinned:=v.Hosts[key]
 m.mu.Unlock()
 if err!=nil{return nil,nil,err}
 if !validSSHLogin(in.Username,in.Password)||in.Port<1||in.Port>65535{return nil,nil,errors.New("请填写有效 SSH 账号、密码和端口")}
 var challenge *remoteTrust
 var verified string
 config:=&ssh.ClientConfig{User:in.Username,Auth:[]ssh.AuthMethod{ssh.Password(in.Password)},Timeout:15*time.Second,
  HostKeyCallback:func(_ string,_ net.Addr,pub ssh.PublicKey)error{
   fp:=ssh.FingerprintSHA256(pub)
   if pinned!=""&&pinned!=fp{challenge=&remoteTrust{fp,pub.Type(),true,pinned};return errors.New("host key changed")}
   if pinned==""&&in.Trust!=fp{challenge=&remoteTrust{fp,pub.Type(),false,""};return errors.New("host key confirmation required")}
   verified=fp;return nil
  },
 }
 address:=net.JoinHostPort(in.Address,strconv.Itoa(in.Port))
 conn,err:=m.dial(ctx,"tcp",address)
 if err!=nil{return nil,nil,errors.New("SSH 无法连接，请检查系统 IP、端口和 sshd 服务")}
 stopCancel:=context.AfterFunc(ctx,func(){conn.Close()})
 defer stopCancel()
 conn.SetDeadline(time.Now().Add(15*time.Second))
 c,ch,req,err:=ssh.NewClientConn(conn,address,config)
 in.Password="";config.Auth=nil
 if err!=nil{conn.Close();if challenge!=nil{return nil,challenge,nil};return nil,nil,errors.New("SSH 登录失败，请检查系统账号、密码和服务器认证设置")}
 client:=ssh.NewClient(c,ch,req)
 success:=false
 defer func(){if !success{client.Close()}}()
 terminal,err:=client.NewSession();if err!=nil{return nil,nil,errors.New("SSH 终端不可用")}
 r:=&RemoteSession{id:Random(16),owner:owner,node:in.Node,address:in.Address,port:in.Port,username:in.Username,client:client,terminal:terminal,changed:make(chan struct{}),lastInput:time.Now(),expires:expires}
 r.stdin,err=terminal.StdinPipe();if err!=nil{return nil,nil,errors.New("SSH 输入不可用")}
 terminal.Stdout=r;terminal.Stderr=r
 if err=terminal.RequestPty("xterm-256color",in.Rows,in.Cols,ssh.TerminalModes{ssh.ECHO:1,ssh.TTY_OP_ISPEED:38400,ssh.TTY_OP_OSPEED:38400});err!=nil{return nil,nil,errors.New("服务器不允许交互终端")}
 if err=terminal.Shell();err!=nil{return nil,nil,errors.New("无法启动系统 Shell")}
 // SFTP is optional: a server may allow a shell but disable the subsystem.
 r.files,_=sftp.NewClient(client,sftp.MaxConcurrentRequestsPerFile(16))
 conn.SetDeadline(time.Time{})
 if ctx.Err()!=nil{return nil,nil,ctx.Err()}
 m.mu.Lock()
 v,err=m.config()
 if err==nil{
  if current:=v.Hosts[key];current!=""&&current!=verified{err=errors.New("主机指纹已变更，请重新连接")}else{
   v.Hosts[key]=verified
   if in.Remember&&in.Profile!=""{v.Bindings[in.Node]=in.Profile}
   err=m.saveConfig(v)
  }
 }
 if err==nil{m.sessions[r.id]=r}
 m.mu.Unlock()
 if err!=nil{return nil,nil,err}
 success=true
 go func(){terminal.Wait();r.close("终端已退出")}()
 go r.watch()
 return r,nil,nil
}
func(r *RemoteSession) Write(p []byte)(int,error){
 r.mu.Lock();defer r.mu.Unlock()
 if r.ended{return len(p),nil}
 r.buffer=append(r.buffer,p...)
 if n:=len(r.buffer)-(1<<20);n>0{r.base+=int64(n);copy(r.buffer,r.buffer[n:]);r.buffer=r.buffer[:1<<20]}
 close(r.changed);r.changed=make(chan struct{})
 return len(p),nil
}
func(r *RemoteSession) close(reason string){
 r.mu.Lock()
 if r.ended{r.mu.Unlock();return}
 r.ended=true;r.reason=reason;close(r.changed);r.mu.Unlock()
 r.client.Close()
}
func(r *RemoteSession) watch(){
 tick:=time.NewTicker(15*time.Second);defer tick.Stop()
 for range tick.C{
  r.mu.Lock();end:=r.ended;expired:=time.Now().After(r.expires)||time.Since(r.lastInput)>30*time.Minute;r.mu.Unlock()
  if end{return};if expired{r.close("会话超时，请重新连接");return}
 }
}
func(m *RemoteManager) closeOwner(owner,reason string){
 m.mu.Lock();list:=[]*RemoteSession{};for _,r:=range m.sessions{if r.owner==owner{list=append(list,r)}};m.mu.Unlock()
 for _,r:=range list{r.close(reason)}
}
func(m *RemoteManager) Close(){
 m.mu.Lock();list:=[]*RemoteSession{};for _,r:=range m.sessions{list=append(list,r)};m.mu.Unlock()
 for _,r:=range list{r.close("中心服务停止")}
}
func(r *RemoteSession) output(ctx context.Context,offset int64)([]byte,int64,bool,bool,string){
 deadline:=time.NewTimer(time.Second);defer deadline.Stop()
 for{
  r.mu.Lock()
  if offset!=r.base+int64(len(r.buffer))||r.ended{
   lost:=offset<r.base||offset>r.base+int64(len(r.buffer));if lost{offset=r.base}
   end:=offset+65536;if max:=r.base+int64(len(r.buffer));end>max{end=max}
   raw:=append([]byte{},r.buffer[offset-r.base:end-r.base]...);ended:=r.ended&&end==r.base+int64(len(r.buffer));reason:=r.reason;r.mu.Unlock()
   return raw,end,lost,ended,reason
  }
  changed:=r.changed;r.mu.Unlock()
  select{case<-changed:case<-ctx.Done():return []byte{},offset,false,false,"";case<-deadline.C:return []byte{},offset,false,false,""}
 }
}
