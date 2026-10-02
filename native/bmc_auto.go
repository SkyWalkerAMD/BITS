package bits

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type BMCProfile struct {
	Name string `json:"name"`
	Enabled bool `json:"enabled"`
	Networks []string `json:"networks"`
	NodePrefix string `json:"node_prefix"`
	Model string `json:"model"`
	Username string `json:"username"`
	Password string `json:"password,omitempty"`
	Cipher int `json:"cipher"`
	Revision string `json:"revision,omitempty"`
}
type discoveredBMC struct { Discovery BMCDiscovery `json:"discovery"`; Received string `json:"received_at"` }
type autoAttempt struct { Fingerprint string `json:"fingerprint"`; At string `json:"at"`; State string `json:"state"` }
type autoBMCData struct {
	Profiles map[string]BMCProfile `json:"profiles"`
	Discoveries map[string]discoveredBMC `json:"discoveries"`
	Attempts map[string]autoAttempt `json:"attempts"`
}
type AutoBMC struct { mu sync.Mutex; path string; data autoBMCData }
type AutoBMCView struct {
	State string `json:"state"`
	Reason string `json:"reason"`
	Model string `json:"model,omitempty"`
	Candidates []BMCCandidate `json:"candidates,omitempty"`
	Received string `json:"received_at,omitempty"`
}
var autoReasons = map[string]string{
	"waiting":"等待节点首次上报管理口", "missing_tool":"节点未安装 ipmitool，可手工绑定",
	"unavailable":"本机 IPMI 接口不可用，可手工绑定", "incomplete":"本机管理口查询未完成，可手工绑定",
	"identity_missing":"BMC 未提供可核对的 GUID，请手工确认绑定", "no_address":"未发现已配置的内网管理口",
	"no_profile":"已发现管理口，等待匹配的自动绑定模板", "ambiguous":"匹配多个地址或模板，请缩小模板范围或手工绑定",
	"stale":"管理口发现记录已过期，等待节点重新上报", "ready":"等待自动核对管理口身份",
	"checking":"正在核对 BMC 身份与电源状态", "failed":"自动核对未通过；检查管理网络、凭据、Cipher 与 GUID 后再重试",
	"bound":"已自动绑定并核对 BMC 身份", "interrupted":"上次核对未完成，请检查后重试",
	"conflict":"管理口地址或 BMC 身份被多个节点上报，请手工核对对应关系",
}
func newAutoBMC(data string) *AutoBMC {
	return &AutoBMC{path:filepath.Join(data,"bmc-auto.json"),data:autoBMCData{Profiles:map[string]BMCProfile{},Discoveries:map[string]discoveredBMC{},Attempts:map[string]autoAttempt{}}}
}
func validateProfile(p BMCProfile) error {
	if !ValidName(p.Name) || len(p.Networks)<1 || len(p.Networks)>16 || len(p.Model)>128 || strings.ContainsAny(p.Model,"\x00\r\n") || (p.NodePrefix!="" && !ValidName(p.NodePrefix)) { return errors.New("模板名称、匹配条件或管理网段无效") }
	for _, cidr := range p.Networks {
		ip, n, e := net.ParseCIDR(cidr); if e!=nil || ip.To4()==nil || n.String()!=cidr { return errors.New("请填写规范的内网 IPv4 CIDR 管理网段") }
		last := append(net.IP(nil), n.IP...); for j:=range last { last[j] |= ^n.Mask[j] }
		if !ip.IsPrivate() || !last.IsPrivate() { return errors.New("自动绑定只允许明确的内网管理网段") }
	}
	return validateBinding(BMCBinding{Node:p.Name,Address:"192.168.0.1",Username:p.Username,Password:p.Password,Cipher:p.Cipher})
}
func validDiscovery(d BMCDiscovery) bool {
	if len(d.Model)>128 || strings.ContainsAny(d.Model,"\x00\r\n") || len(d.Candidates)>12 || (d.GUID!="" && !validGUID(d.GUID)) { return false }
	switch d.Status { case "ready","missing_tool","unavailable","incomplete","identity_missing","no_address": default: return false }
	if d.Status=="ready" && (!validGUID(d.GUID) || len(d.Candidates)==0) { return false }
	seen:=map[string]bool{}; for _, c:=range d.Candidates { if !validCandidate(c) || seen[c.Address] { return false }; seen[c.Address]=true }; return true
}
func (a *AutoBMC) load() error {
	a.mu.Lock(); defer a.mu.Unlock()
	if _,e:=os.Lstat(a.path); os.IsNotExist(e) { return nil }
	var d autoBMCData; if e:=ReadJSON(a.path,&d); e!=nil { return errors.New("自动 BMC 配置不可读或权限异常") }
	if len(d.Profiles)>16 || len(d.Discoveries)>200 || len(d.Attempts)>200 || d.Profiles==nil || d.Discoveries==nil || d.Attempts==nil { return errors.New("自动 BMC 配置数量或结构异常") }
	for id,p:=range d.Profiles { if id!=p.Name || !idRE.MatchString(p.Revision) || validateProfile(p)!=nil { return errors.New("自动 BMC 模板无效") } }
	for id,v:=range d.Discoveries { if !ValidName(id) || !validDiscovery(v.Discovery) { return errors.New("自动 BMC 发现记录无效") } }
	for id,v:=range d.Attempts { if !ValidName(id) || !digestRE.MatchString(v.Fingerprint) { return errors.New("自动 BMC 核对记录无效") }; if v.State=="checking" { v.State="interrupted"; d.Attempts[id]=v } }
	a.data=d; return nil
}
// All updates are written before becoming visible to the monitoring loop.
func (a *AutoBMC) save(d autoBMCData) error { if e:=AtomicJSON(a.path,d); e!=nil { return e }; a.data=d; return nil }
func (a *AutoBMC) copyData() autoBMCData {
	d:=autoBMCData{Profiles:map[string]BMCProfile{},Discoveries:map[string]discoveredBMC{},Attempts:map[string]autoAttempt{}}
	for k,v:=range a.data.Profiles { d.Profiles[k]=v }; for k,v:=range a.data.Discoveries { d.Discoveries[k]=v }; for k,v:=range a.data.Attempts { d.Attempts[k]=v }; return d
}
func (a *AutoBMC) profile(p BMCProfile) error {
	a.mu.Lock(); defer a.mu.Unlock(); old,exists:=a.data.Profiles[p.Name]
	if p.Password=="" && exists { p.Password=old.Password }
	if e:=validateProfile(p); e!=nil { return e }
	if !exists && len(a.data.Profiles)>=16 { return errors.New("最多 16 个凭据模板") }
	p.Revision=Random(16); d:=a.copyData(); d.Profiles[p.Name]=p; return a.save(d)
}
func (a *AutoBMC) discovery(node string, v BMCDiscovery) error {
	if !validDiscovery(v) { return errors.New("管理口发现数据无效") }
	a.mu.Lock(); defer a.mu.Unlock()
	if _,ok:=a.data.Discoveries[node]; !ok && len(a.data.Discoveries)>=200 { return errors.New("最多自动发现 200 台节点") }
	d:=a.copyData(); d.Discoveries[node]=discoveredBMC{Discovery:v,Received:UTC()}; return a.save(d)
}
func (a *AutoBMC) retry(node string) error {
	a.mu.Lock(); defer a.mu.Unlock()
	v,ok:=a.data.Attempts[node]; if !ok { return errors.New("没有需要重试的自动绑定记录") }
	t,_:=time.Parse(time.RFC3339Nano,v.At)
	if v.State=="checking" || time.Since(t)<time.Minute { return errors.New("请等待本次检查结束，重试间隔至少一分钟") }
	d:=a.copyData(); delete(d.Attempts,node); return a.save(d)
}
func (a *AutoBMC) profiles() []BMCProfile {
	a.mu.Lock(); defer a.mu.Unlock(); out:=[]BMCProfile{}
	for _,p:=range a.data.Profiles { p.Password=""; p.Revision=""; out=append(out,p) }
	sort.Slice(out,func(i,j int)bool{return out[i].Name<out[j].Name}); return out
}
type autoChoice struct { Binding BMCBinding; Profile BMCProfile; Fingerprint string }
func (a *AutoBMC) choose(node string) (AutoBMCView, autoChoice) {
	v:=AutoBMCView{State:"waiting"}; choice:=autoChoice{}
	d,ok:=a.data.Discoveries[node]; if !ok { v.Reason=autoReasons[v.State]; return v,choice }
	v.Model=d.Discovery.Model; v.Candidates=d.Discovery.Candidates; v.Received=d.Received; v.State=d.Discovery.Status
	if v.State=="ready" {
		t,e:=time.Parse(time.RFC3339Nano,d.Received)
		if e!=nil || time.Since(t)>24*time.Hour || time.Until(t)>5*time.Second { v.State="stale" } else {
			count:=0
			for _,c:=range d.Discovery.Candidates { for _,p:=range a.data.Profiles {
				if !p.Enabled || !strings.HasPrefix(node,p.NodePrefix) || (p.Model!="" && p.Model!=d.Discovery.Model) { continue }
				matches:=false; for _,cidr:=range p.Networks { _,n,_:=net.ParseCIDR(cidr); if n!=nil && n.Contains(net.ParseIP(c.Address)) { matches=true } }
				if !matches { continue }; count++
				b:=BMCBinding{Node:node,Address:c.Address,Username:p.Username,Password:p.Password,Cipher:p.Cipher,ExpectedGUID:d.Discovery.GUID,Profile:p.Name}
				raw,_:=json.Marshal([]any{node,c,d.Discovery.GUID,p.Revision}); choice=autoChoice{Binding:b,Profile:p,Fingerprint:Digest(raw)}
			} }
			if count==0 { v.State="no_profile" } else if count>1 { v.State="ambiguous" } else {
				for other,found:=range a.data.Discoveries { if other==node {continue}; at,e:=time.Parse(time.RFC3339Nano,found.Received); if e!=nil || time.Since(at)>24*time.Hour {continue}; for _,c:=range found.Discovery.Candidates { if c.Address==choice.Binding.Address || found.Discovery.GUID==choice.Binding.ExpectedGUID {v.State="conflict"} } }
				if v.State=="ready" { if attempt,ok:=a.data.Attempts[node]; ok && attempt.Fingerprint==choice.Fingerprint {v.State=attempt.State} }
			}
		}
	}
	v.Reason=autoReasons[v.State]; return v,choice
}
func (a *AutoBMC) view(node string) AutoBMCView { a.mu.Lock(); defer a.mu.Unlock(); v,_:=a.choose(node); return v }
func (s *Server) autoBindBMC(ctx context.Context, node string) {
	// Serialize registration/start checks and recheck after network I/O.
	s.Store.mu.Lock(); a:=s.autoBMC; a.mu.Lock()
	v,c:=a.choose(node)
	if v.State!="ready" || !s.autoBindingAllowed(node) { a.mu.Unlock(); s.Store.mu.Unlock(); return }
	d:=a.copyData(); d.Attempts[node]=autoAttempt{Fingerprint:c.Fingerprint,At:UTC(),State:"checking"}
	for other,power:=range s.power.Snapshot() { if other!=node && power.Configured && power.Address==c.Binding.Address {d.Attempts[node]=autoAttempt{Fingerprint:c.Fingerprint,At:UTC(),State:"conflict"}; _=a.save(d);a.mu.Unlock();s.Store.mu.Unlock();return} }
	err:=a.save(d); a.mu.Unlock(); s.Store.mu.Unlock(); if err!=nil { return }
	state:="failed"
	verifier,ok:=s.power.driver.(interface{ Identity(context.Context,BMCBinding)(string,error) })
	if ok {
		select { case s.power.slots<-struct{}{}: defer func(){<-s.power.slots}(); case <-ctx.Done(): return }
		guid,e:=verifier.Identity(ctx,c.Binding)
		if e==nil && guid==c.Binding.ExpectedGUID {
			power,e:=s.power.driver.Status(ctx,c.Binding); if e==nil && (power=="on" || power=="off") { state="bound" }
		}
	}
	s.Store.mu.Lock(); defer s.Store.mu.Unlock(); a.mu.Lock(); defer a.mu.Unlock()
	_,current:=a.choose(node)
	if current.Fingerprint!=c.Fingerprint { return }
	currentView,_:=a.choose(node); if currentView.State!="checking" { return }
	// Disabled/edited templates, existing manual bindings and active batches win.
	p,exists:=a.data.Profiles[c.Profile.Name]
	if !exists || !p.Enabled || p.Revision!=c.Profile.Revision { return }
	if state=="bound" {
		if !s.autoBindingAllowed(node) { state="interrupted" } else if s.power.Bind(c.Binding)!=nil { state="failed" }
	}
	d=a.copyData(); d.Attempts[node]=autoAttempt{Fingerprint:c.Fingerprint,At:UTC(),State:state}; _=a.save(d)
}
// Caller holds Store.mu, then optionally AutoBMC.mu; lock ordering is fixed.
func (s *Server) autoBindingAllowed(node string) bool {
	if s.power.Snapshot()[node].Configured { return false }
	var count int
	if s.Store.db.QueryRow("SELECT count(*) FROM nodes WHERE id=? AND disabled=0",node).Scan(&count)!=nil || count!=1 { return false }
	return s.Store.db.QueryRow("SELECT count(*) FROM batches WHERE node=? AND state IN ('waiting_boot','armed','running','finishing','needs_attention')",node).Scan(&count)==nil && count==0
}
func (s *Server) runAutoBMC(ctx context.Context) {
	for {
		nodes,err:=s.Store.Nodes()
		if err==nil { var wg sync.WaitGroup; slots:=make(chan struct{},16)
			for _,n:=range nodes { if ctx.Err()!=nil { break }; slots<-struct{}{}; wg.Add(1); go func(id string){defer wg.Done();defer func(){<-slots}();s.autoBindBMC(ctx,id)}(n.ID) }; wg.Wait()
		}
		select { case <-ctx.Done():return; case <-time.After(10*time.Second): }
	}
}
