package bits

import (
 "bytes"
 "context"
 "crypto/ed25519"
 "crypto/rand"
 "errors"
 "io"
 "net"
 "net/http/httptest"
 "os"
 "path/filepath"
 "strings"
 "sync/atomic"
 "testing"
 "time"

 "github.com/pkg/sftp"
 "golang.org/x/crypto/ssh"
)

func sshFixture(t *testing.T)(string,string,*atomic.Int32){
 t.Helper();_,private,e:=ed25519.GenerateKey(rand.Reader);if e!=nil{t.Fatal(e)}
 signer,e:=ssh.NewSignerFromKey(private);if e!=nil{t.Fatal(e)}
 authenticated:=&atomic.Int32{}
 cfg:=&ssh.ServerConfig{PasswordCallback:func(c ssh.ConnMetadata,p []byte)(*ssh.Permissions,error){
  if c.User()!="tester"||string(p)!="fixture-password"{return nil,errors.New("denied")};authenticated.Add(1);return nil,nil
 }};cfg.AddHostKey(signer)
 listener,e:=net.Listen("tcp","127.0.0.1:0");if e!=nil{t.Fatal(e)};t.Cleanup(func(){listener.Close()})
 go func(){for{conn,e:=listener.Accept();if e!=nil{return};go func(){
  defer conn.Close();server,channels,requests,e:=ssh.NewServerConn(conn,cfg);if e!=nil{return};defer server.Close();go ssh.DiscardRequests(requests)
  for incoming:=range channels{if incoming.ChannelType()!="session"{incoming.Reject(ssh.UnknownChannelType,"session required");continue};ch,reqs,e:=incoming.Accept();if e!=nil{continue};go func(){defer ch.Close();for req:=range reqs{switch req.Type{
   case "pty-req","window-change":req.Reply(true,nil)
   case "shell":req.Reply(true,nil);go func(){io.WriteString(ch,"fixture ready\r\n");io.Copy(ch,ch)}()
   case "subsystem":var v struct{Name string};ssh.Unmarshal(req.Payload,&v);if v.Name!="sftp"{req.Reply(false,nil);continue};req.Reply(true,nil);s,e:=sftp.NewServer(ch);if e==nil{s.Serve();s.Close()};return
   default:req.Reply(false,nil)
  }}}()}
 }()}}()
 return listener.Addr().String(),ssh.FingerprintSHA256(signer.PublicKey()),authenticated
}
func remoteFixture(t *testing.T)(*Server,*Client,*Client,RemoteConnect,string,*atomic.Int32){
 s,admin,node:=testServer(t);t.Cleanup(s.CloseRemote)
 s.Store.Heartbeat("N1",Version)
 v:=NodeNetwork{Primary:"192.168.20.30",Addresses:[]SystemAddress{{"eno1","192.168.20.30",24}}}
 if e:=s.Store.SaveNetwork("N1",v);e!=nil{t.Fatal(e)}
 address,fp,count:=sshFixture(t)
 s.remote.dial=func(ctx context.Context,network,target string)(net.Conn,error){d:=net.Dialer{};return d.DialContext(ctx,network,address)}
 return s,admin,node,RemoteConnect{Node:"N1",Address:v.Primary,Port:22,Username:"tester",Password:"fixture-password",Cols:80,Rows:24},fp,count
}
func TestSystemIPReportOwnershipFreshnessAndLegacyNode(t *testing.T){
 s,admin,node:=testServer(t);ctx:=context.Background()
 report:=NodeNetwork{Primary:"192.168.1.10",Addresses:[]SystemAddress{{"eno1","192.168.1.10",24},{"eno2","fd00::10",64}}}
 if e:=node.JSON(ctx,"POST","/node/v1/network","",report,nil);e!=nil{t.Fatal(e)}
 var overview struct{Nodes []Node `json:"nodes"`}
 if e:=admin.JSON(ctx,"GET","/api/v1/overview","",nil,&overview);e!=nil{t.Fatal(e)}
 if overview.Nodes[0].Network.Primary!=report.Primary||len(overview.Nodes[0].Network.Addresses)!=2{t.Fatal("network missing")}
 if e:=node.JSON(ctx,"GET","/node/v1/heartbeat","",nil,nil);e!=nil{t.Fatal("legacy heartbeat",e)}
 for _,ip:=range []string{"127.0.0.1","169.254.169.254","::1","0.0.0.0","224.1.1.1","example.com"}{
  bad:=NodeNetwork{Primary:ip,Addresses:[]SystemAddress{{"eth0",ip,24}}}
  if e:=node.JSON(ctx,"POST","/node/v1/network","",bad,nil);e==nil{t.Fatal("unsafe address",ip)}
 }
 if e:=s.remoteTarget("N1","192.168.1.11");e==nil{t.Fatal("unreported target allowed")}
 s.Store.db.Exec("UPDATE nodes SET last_seen=? WHERE id='N1'",time.Now().Add(-time.Minute).UTC().Format(time.RFC3339Nano))
 if e:=s.remoteTarget("N1",report.Primary);e==nil{t.Fatal("offline target allowed")}
 collected:=collectSystemNetwork("https://192.0.2.1")
 if e:=collected.validate();e!=nil{t.Fatal("local network report",e)}
}
func TestSSHPrivateTemplatesRotationAndSessionOnlyCredentials(t *testing.T){
 s,admin,node,_,_,_:=remoteFixture(t);ctx:=context.Background()
 p:=SSHProfile{Name:"测试机模板",Username:"tester",Port:22,Password:"private-password"}
 var saved SSHProfile
 if e:=admin.JSON(ctx,"POST","/api/v1/remote/profiles","",p,&saved);e!=nil{t.Fatal(e)}
 if saved.Secret!=""||saved.Password!=""||saved.ID==""{t.Fatal("template exposes secret")}
 raw,_:=os.ReadFile(filepath.Join(s.Config.Data,"ssh.json"));if bytes.Contains(raw,[]byte(p.Password)){t.Fatal("plaintext template on disk")}
 if st,e:=os.Stat(filepath.Join(s.Config.Data,"ssh-key.json"));e!=nil||st.Mode().Perm()!=0600{t.Fatal("key permissions",e)}
 reloaded:=newRemoteManager(s.Config.Data);v,e:=reloaded.config();if e!=nil{t.Fatal(e)};password,e:=reloaded.decrypt(v.Profiles[saved.ID]);if e!=nil||password!=p.Password{t.Fatal("persisted template decryption",e)}
 saved.Password="replacement";var rotated SSHProfile
 if e=admin.JSON(ctx,"POST","/api/v1/remote/profiles","",saved,&rotated);e!=nil{t.Fatal(e)}
 if e=admin.JSON(ctx,"POST","/api/v1/remote/profiles","",saved,nil);e==nil{t.Fatal("stale template overwrote rotation")}
 if e=node.JSON(ctx,"GET","/api/v1/remote/config","",nil,nil);e==nil{t.Fatal("node can read SSH templates")}
 var config map[string]any;admin.JSON(ctx,"GET","/api/v1/remote/config","",nil,&config)
 raw=[]byte(strings.TrimSpace(string(raw)));if strings.Contains(string(raw),"replacement"){t.Fatal("password leaked")}
 if e=admin.JSON(ctx,"DELETE","/api/v1/remote/profiles/"+rotated.ID,"",map[string]string{"revision":rotated.Revision},nil);e!=nil{t.Fatal(e)}
}
func TestSSHHostTrustBeforePasswordAndPTYSessionOwnership(t *testing.T){
 s,_,_,in,fp,count:=remoteFixture(t);ctx:=context.Background();expires:=time.Now().Add(time.Hour)
 session,trust,e:=s.connectRemote(ctx,"owner",expires,in)
 if e!=nil||session!=nil||trust==nil||trust.Fingerprint!=fp||count.Load()!=0{t.Fatal("first host key must stop before password",e,trust)}
 in.Trust=fp
 session,trust,e=s.connectRemote(ctx,"owner",expires,in)
 if e!=nil||session==nil||trust!=nil||count.Load()!=1||session.files==nil{t.Fatal("SSH/SFTP handshake",e,trust)}
 req:=httptest.NewRequest("GET","/api/v1/remote/sessions/"+session.id+"/output?offset=0",nil);req.Header.Set("Authorization","Bearer different-session")
 if e=s.remoteAPI(httptest.NewRecorder(),req,"sessions/"+session.id+"/output");e==nil{t.Fatal("cross operator session access")}
 session.stdin.Write([]byte("copied text\r\n"));var result strings.Builder;offset:=int64(0)
 deadline:=time.Now().Add(3*time.Second)
 for time.Now().Before(deadline)&&!strings.Contains(result.String(),"copied text") {raw,next,_,_,_:=session.output(ctx,offset);offset=next;result.Write(raw)}
 if !strings.Contains(result.String(),"copied text"){t.Fatal("PTY roundtrip",result.String())}
 // Template and temporary credentials must not be emitted into retained output/config.
 raw,_:=os.ReadFile(filepath.Join(s.Config.Data,"ssh.json"));if bytes.Contains(raw,[]byte(in.Password)){t.Fatal("temporary password persisted")}
 changed,_,otherCount:=sshFixture(t);s.remote.dial=func(ctx context.Context,network,target string)(net.Conn,error){return (&net.Dialer{}).DialContext(ctx,network,changed)}
 _,trust,e=s.connectRemote(ctx,"owner2",expires,in)
 if e!=nil||trust==nil||!trust.Changed||otherCount.Load()!=0{t.Fatal("changed host key sent password",e)}
 s.remote.closeOwner("someone-else","logout");session.mu.Lock();closed:=session.ended;session.mu.Unlock();if closed{t.Fatal("unrelated logout closed session")}
 s.remote.closeOwner("owner","logout");session.mu.Lock();closed=session.ended;session.mu.Unlock();if !closed{t.Fatal("logout retained shell")}
}
func TestSFTPUploadDownloadNoClobberAndDirectoryPermissions(t *testing.T){
 s,admin,_,in,fp,_:=remoteFixture(t);ctx:=context.Background();in.Trust=fp
 var response struct{ID string `json:"id"`}
 if e:=admin.JSON(ctx,"POST","/api/v1/remote/connect","",in,&response);e!=nil{t.Fatal(e)}
 directory:=t.TempDir();target:=filepath.Join(directory,"upload.txt")
 base:="/api/v1/remote/sessions/"+response.ID
 payload:=[]byte("SFTP copy and paste file\n")
 res,e:=admin.Request(ctx,"POST",base+"/upload?path="+target,"",bytes.NewReader(payload),int64(len(payload)));if e!=nil{t.Fatal(e)};res.Body.Close()
 raw,e:=os.ReadFile(target);if e!=nil||!bytes.Equal(raw,payload){t.Fatal("upload",e)}
 res,e=admin.Request(ctx,"POST",base+"/upload?path="+target,"",bytes.NewReader([]byte("overwrite")),9)
 if e==nil{res.Body.Close();t.Fatal("upload silently replaced existing file")}
 res,e=admin.Request(ctx,"GET",base+"/download?path="+target,"",nil,0);if e!=nil{t.Fatal(e)};raw,e=io.ReadAll(res.Body);res.Body.Close();if e!=nil||!bytes.Equal(raw,payload){t.Fatal("download differs",e)}
 var files map[string]any;if e=admin.JSON(ctx,"GET",base+"/files?path="+directory,"",nil,&files);e!=nil{t.Fatal(e)}
 // Terminal buffers have an explicit bound and signal discarded output.
 s.remote.mu.Lock();session:=s.remote.sessions[response.ID];s.remote.mu.Unlock();session.Write(bytes.Repeat([]byte("x"),2<<20))
 _,_,lost,_,_:=session.output(ctx,0);if !lost{t.Fatal("truncated output not marked")}
 session.mu.Lock();session.expires=time.Now().Add(-time.Second);session.mu.Unlock()
 if e=admin.JSON(ctx,"POST",base+"/input","",map[string]string{"data":"unexpected"},nil);e==nil{t.Fatal("expired shell accepted input")}
}
