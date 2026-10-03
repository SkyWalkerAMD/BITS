package bits

import (
 "context"
 "encoding/json"
 "os"
 "path/filepath"
 "strings"
 "testing"
 "time"
)

func testHardwareInfo() HardwareInfo {
 return HardwareInfo{Schema:"bits-hardware-info-v1",Observed:time.Now().Add(-time.Minute).UTC().Format(time.RFC3339Nano),Status:"ok",
 Sections:[]InfoSection{{Name:"Platform",Lines:[]string{"Secure Boot Disabled"}},{Name:"CPU",Lines:[]string{"S0 Example 24C/24T"}},{Name:"Memory",Lines:[]string{"DIMM   Size"}},{Name:"Memory Timings",Lines:[]string{"S0 Primary 32-32-31-65 tCWL 30"}}},
 CPUs:[]InfoCPU{{ID:0,Model:"Example",Cores:24,Threads:24,Microcode:"0x1"}},
 DIMMs:[]InfoDIMM{{Slot:"CPU0_DIMM_A1",Fields:map[string]string{"DIMM":"CPU0_DIMM_A1","Size":"64 GB"}}}}
}
func TestHardwareInfoAuthenticationReplayAndHistoricalSnapshot(t *testing.T) {
 s,admin,node:=testServer(t);ctx:=context.Background();v:=testHardwareInfo()
 var got NodeHardwareInfo
 get:=func(){t.Helper();if err:=admin.JSON(ctx,"GET","/api/v1/nodes/N1/hardware-info","",nil,&got);err!=nil{t.Fatal(err)}}
 get();if got.Status!="not_collected" || got.Snapshot!=nil {t.Fatal(got)}
 if err:=node.JSON(ctx,"POST","/node/v1/hardware-info","",v,nil);err!=nil{t.Fatal(err)}
 get();first:=got.Received
 if got.Snapshot==nil || got.Snapshot.CPUs[0].Model!="Example"{t.Fatal(got)}
 if err:=node.JSON(ctx,"POST","/node/v1/hardware-info","",v,nil);err!=nil{t.Fatal(err)}
 get();if got.Received!=first{t.Fatal("replay refreshed snapshot")}
 if node.JSON(ctx,"GET","/api/v1/nodes/N1/hardware-info","",nil,nil)==nil {t.Fatal("node accessed operator information")}
 if admin.JSON(ctx,"POST","/node/v1/hardware-info","",v,nil)==nil {t.Fatal("operator impersonated node")}
 raw,_:=json.Marshal(v);var spoof map[string]any;json.Unmarshal(raw,&spoof);spoof["node"]="N2"
 if node.JSON(ctx,"POST","/node/v1/hardware-info","",spoof,nil)==nil{t.Fatal("spoofed node accepted")}
 if admin.JSON(ctx,"GET","/api/v1/nodes/absent/hardware-info","",nil,nil)==nil{t.Fatal("unknown node accepted")}
 denied:=HardwareInfo{Schema:v.Schema,Observed:UTC(),Status:"unavailable"}
 if err:=node.JSON(ctx,"POST","/node/v1/hardware-info","",denied,nil);err!=nil{t.Fatal(err)}
 get();if got.Status!="unavailable" || got.Snapshot.Observed!=v.Observed {t.Fatal("previous data made current after denial",got)}
 if node.JSON(ctx,"POST","/node/v1/hardware-info","",v,nil)==nil{t.Fatal("older snapshot accepted")}
 all,_:=s.Store.Batches("");if len(all)!=0{t.Fatal("info created a batch")}
 s.Store.db.Exec("UPDATE nodes SET last_seen='' WHERE id='N1'")
 get();if got.Snapshot.CPUs[0].Model!="Example"{t.Fatal("offline snapshot lost")}
}
func TestHardwareInfoBoundsAndPrimaryBoundary(t *testing.T) {
 for _,change:=range []func(*HardwareInfo){
  func(v *HardwareInfo){v.Sections=append(v.Sections,InfoSection{Name:"License",Lines:[]string{"bad"}})},
  func(v *HardwareInfo){v.Sections[3].Lines=[]string{"Refresh tRFC 123"}},
  func(v *HardwareInfo){v.Sections[3].Lines=[]string{"S0 Primary 32-32-31-65 tRFC 123"}},
  func(v *HardwareInfo){v.Sections[0].Lines=[]string{"bad\x1b"}},
  func(v *HardwareInfo){v.Sections[0].Lines=[]string{strings.Repeat("x",2049)}},
  func(v *HardwareInfo){v.Sections=append(v.Sections,v.Sections[0])},
  func(v *HardwareInfo){v.CPUs=append(v.CPUs,v.CPUs[0])},
  func(v *HardwareInfo){v.DIMMs[0].Fields["password"]="bad"},
  func(v *HardwareInfo){v.Status="unavailable"},
  func(v *HardwareInfo){v.Observed=time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano)},
 } {v:=testHardwareInfo();change(&v);if validHardwareInfo(v)==nil{t.Fatal("unsafe info accepted",v)}}
 if err:=validHardwareInfo(testHardwareInfo());err!=nil{t.Fatal(err)}
}
func TestHardwareInfoSurvivesCenterRestartAndAgentRetries(t *testing.T) {
 dir:=t.TempDir();os.Chmod(dir,0700);s,err:=OpenStore(dir);if err!=nil{t.Fatal(err)}
 s.AddNode("N1",Random(32));v:=testHardwareInfo()
 if err=s.PutHardwareInfo("N1",v);err!=nil{t.Fatal(err)}
 s.Close();s,err=OpenStore(dir);if err!=nil{t.Fatal(err)};defer s.Close()
 got,err:=s.HardwareInfo("N1");if err!=nil || got.Snapshot==nil || got.Snapshot.Observed!=v.Observed{t.Fatal("snapshot lost after restart",got,err)}
 server,_,client:=testServer(t);local:=t.TempDir();os.Chmod(local,0700)
 a:=&Agent{Client:client,Data:local,Config:NodeConfig{Node:"N1"}}
 if err=AtomicJSON(filepath.Join(local,"info.json"),v);err!=nil{t.Fatal(err)}
 canceled,cancel:=context.WithCancel(context.Background());cancel()
 a.publishHardwareInfo(canceled,local)
 if a.infoDigest!=""{t.Fatal("failed publication acknowledged")}
 a.publishHardwareInfo(context.Background(),local)
 received,err:=server.Store.HardwareInfo("N1")
 if err!=nil || received.Snapshot==nil || a.infoDigest==""{t.Fatal("agent did not publish info",err)}
 a.publishHardwareInfo(context.Background(),local)
 again,_:=server.Store.HardwareInfo("N1");if again.Received!=received.Received{t.Fatal("same local info uploaded again")}
}
