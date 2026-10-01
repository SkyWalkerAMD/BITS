package bits

import (
 "context"
 "math"
 "sync"
 "testing"
)

func TestLiveBoundedHistoryFreshnessAndValidation(t *testing.T) {
 b:=Batch{ID:Random(16),State:"running",Plan:testPlan("N1")}
 ValidatePlan(&b.Plan)
 cache:=NewLiveCache()
 temp:=42.0
 for i:=int64(1);i<=220;i++ {
  v:=LiveUpdate{Phase:"executing",StepID:"step-001",Sample:&LiveSample{Sequence:i,Observed:UTC(),Available:true,TempC:&temp}}
  if e:=cache.Put(b,v);e!=nil{t.Fatal(e)}
 }
 frame:=cache.Snapshot(b.ID)[0]
 if len(frame.History)!=180||frame.History[0].Sequence!=41||frame.StepTool!="stress"{t.Fatal("history or step mapping differs")}
 if len(cache.Snapshot("")[0].History)!=0{t.Fatal("fleet summary includes large histories")}
 before:=frame.SampleReceived
 if e:=cache.Put(b,frame.LiveUpdate);e!=nil{t.Fatal(e)}
 if cache.Snapshot(b.ID)[0].SampleReceived!=before{t.Fatal("replayed sample became fresh")}
 if e:=cache.Put(b,LiveUpdate{Phase:"executing"});e!=nil{t.Fatal(e)}
 if cache.Snapshot(b.ID)[0].Sample.Sequence!=220{t.Fatal("status pulse erased sample identity")}
 old:=*frame.Sample;old.Sequence=2
 if cache.Put(b,LiveUpdate{Phase:"executing",Sample:&old})==nil{t.Fatal("regression accepted after a status-only pulse")}
 changed:=*frame.Sample;different:=43.0;changed.TempC=&different
 if cache.Put(b,LiveUpdate{Phase:"executing",Sample:&changed})==nil{t.Fatal("conflicting replay accepted")}
 bad:=*frame.Sample;bad.Sequence++;bad.Available=false
 if validSample(&bad)==nil{t.Fatal("unavailable metric accepted")}
 bad.Available=true;nan:=math.NaN();bad.TempC=&nan
 if validSample(&bad)==nil{t.Fatal("non-finite metric accepted")}
 if cache.Put(b,LiveUpdate{Phase:"executing",StepID:"unknown"})==nil{t.Fatal("foreign step accepted")}
 b.State="delivered"
 if cache.Put(b,LiveUpdate{Phase:"executing"})==nil{t.Fatal("completed batch changed live state")}
 // Snapshot copies history; readers cannot alter the cache's sequence/order.
 frame.History[0].Sequence=0
 if cache.Snapshot(b.ID)[0].History[0].Sequence!=41{t.Fatal("reader altered cache")}
}

func TestLiveEndpointAuthorizationAndHeartbeat(t *testing.T) {
 s,admin,node:=testServer(t);ctx:=context.Background()
 b,_:=s.Store.Create(testPlan("N1"));b,_=s.Store.Arm(b.ID)
 var out any
 if e:=node.JSON(ctx,"GET","/node/v1/heartbeat","",nil,&out);e!=nil{t.Fatal(e)}
 unclaimed,_:=s.Store.Batch(b.ID)
 if unclaimed.State!="armed"{t.Fatal("heartbeat consumed start command")}
 base:="/node/v1/batches/"+b.ID+"/live"
 v:=LiveUpdate{Phase:"executing",StepID:"step-001"}
 if node.JSON(ctx,"POST",base,b.Attempt,v,nil)==nil{t.Fatal("unclaimed execution accepted")}
 s.Store.Claim(b.ID,"N1",b.Attempt)
 if node.JSON(ctx,"POST",base,"wrong",v,nil)==nil{t.Fatal("foreign attempt accepted")}
 if e:=node.JSON(ctx,"POST",base,b.Attempt,v,nil);e!=nil{t.Fatal(e)}
 for _,field:=range []string{"rmal","license","command","raw_info","timings"}{
  evil:=map[string]any{"phase":"executing",field:"hidden"}
  if node.JSON(ctx,"POST",base,b.Attempt,evil,nil)==nil{t.Fatal("unapproved field accepted",field)}
 }
 if node.JSON(ctx,"GET","/api/v1/live","",nil,&out)==nil{t.Fatal("node read whole fleet")}
 if e:=admin.JSON(ctx,"GET","/api/v1/batches/"+b.ID+"/live","",nil,&out);e!=nil{t.Fatal(e)}
 s.Store.AddNode("N2",Random(32));other,_:=s.Store.Create(testPlan("N2"));other,_=s.Store.Arm(other.ID)
 if node.JSON(ctx,"POST","/node/v1/batches/"+other.ID+"/live",other.Attempt,v,nil)==nil{t.Fatal("cross-node live update accepted")}
}

func TestLiveParallelFleetSnapshot(t *testing.T) {
 cache:=NewLiveCache();var wg sync.WaitGroup
 for i:=0;i<200;i++{wg.Add(1);go func(){defer wg.Done();id:=Random(16);b:=Batch{ID:id,State:"running",Plan:testPlan(id)};ValidatePlan(&b.Plan)
  for seq:=int64(1);seq<=4;seq++{if e:=cache.Put(b,LiveUpdate{Phase:"executing",Sample:&LiveSample{Sequence:seq,Observed:UTC(),Available:false}});e!=nil{t.Error(e)};cache.Snapshot("")}
 }()};wg.Wait()
 if len(cache.Snapshot(""))!=200{t.Fatal("fleet update lost")}
}
