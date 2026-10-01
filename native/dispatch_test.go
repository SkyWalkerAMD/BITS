package bits

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func groupPlan(nodes ...string) GroupPlan {
	return GroupPlan{RequestID: Random(16), Label: "GROUP-CHECK", Nodes: nodes, Steps: []Step{{Tool: "stress", Seconds: 1}, {Tool: "stress", Seconds: 2}}}
}
func readyNode(t *testing.T, s *Store, id string) {
	t.Helper()
	if err := s.AddNode(id, Random(32)); err != nil {
		t.Fatal(err)
	}
	if err := s.Heartbeat(id, Version); err != nil {
		t.Fatal(err)
	}
}
func groupIDs(t *testing.T, s *Store, g TaskGroup) []string {
	t.Helper()
	v, e := s.Group(g.ID)
	if e != nil {
		t.Fatal(e)
	}
	ids := []string{}
	for _, m := range v.Members {
		ids = append(ids, m.ID)
	}
	return ids
}
func TestGroupAtomicStartReplayAndIndependentResults(t *testing.T) {
	s := testStore(t)
	readyNode(t, s, "N1")
	readyNode(t, s, "N2")
	p := groupPlan("N1", "N2")
	g, e := s.CreateGroup(p, nil)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.CreateGroup(p, nil)
	if e != nil || g.ID != again.ID {
		t.Fatal("create replay", e)
	}
	p.Label = "OTHER"
	if _, e = s.CreateGroup(p, nil); e == nil {
		t.Fatal("id collision accepted")
	}
	ids := groupIDs(t, s, g)
	if b, _ := s.Pending("N1"); b != nil {
		t.Fatal("draft dispatched")
	}
	if _, e = s.Arm(ids[0]); e == nil {
		t.Fatal("group preflight bypassed")
	}
	busy, _ := s.Create(testPlan("N2"))
	s.Arm(busy.ID)
	in := GroupAction{RequestID: Random(16), Batches: ids}
	if _, e = s.GroupAction(g.ID, "start", in, nil); e == nil {
		t.Fatal("busy group accepted")
	}
	for _, id := range ids {
		b, _ := s.Batch(id)
		if b.State != "draft" {
			t.Fatal("partial commit")
		}
	}
	s.Cancel(busy.ID, "finished fixture")
	if _, e = s.GroupAction(g.ID, "start", in, nil); e != nil {
		t.Fatal(e)
	}
	first, _ := s.Batch(ids[0])
	s.Claim(first.ID, first.Plan.Node, first.Attempt)
	s.Update(first.ID, first.Plan.Node, first.Attempt, 1, Result{Execution: "failed", Quality: "partial", Report: "failed"})
	if _, e = s.GroupAction(g.ID, "start", in, nil); e != nil {
		t.Fatal("lost start response cannot replay", e)
	}
	final, _ := s.Batch(first.ID)
	if final.Attempt != first.Attempt || final.State == "armed" {
		t.Fatal("replayed workload")
	}
	second, _ := s.Batch(ids[1])
	if second.State != "armed" {
		t.Fatal("failure cancelled peer")
	}
	in.Batches = in.Batches[:1]
	if _, e = s.GroupAction(g.ID, "start", in, nil); e == nil {
		t.Fatal("changed replay accepted")
	}
	ops, _ := s.GroupOperations(g.ID)
	if len(ops) != 1 {
		t.Fatal("duplicate start audit", len(ops))
	}
}
func TestGroupValidationOfflineAndTemplates(t *testing.T) {
	s := testStore(t)
	readyNode(t, s, "N1")
	s.AddNode("OFF", Random(32))
	for _, p := range []GroupPlan{groupPlan("OFF"), groupPlan("missing"), groupPlan("N1", "N1"), groupPlan()} {
		if _, e := s.CreateGroup(p, nil); e == nil {
			t.Fatal("invalid group accepted", p)
		}
	}
	p := groupPlan("N1")
	p.Steps[0].Tool = "strss"
	if _, e := s.CreateGroup(p, nil); e == nil {
		t.Fatal("invalid tool accepted")
	}
	p = groupPlan("N1")
	g, e := s.CreateGroup(p, nil)
	if e != nil {
		t.Fatal(e)
	}
	ids := groupIDs(t, s, g)
	s.db.Exec("UPDATE nodes SET last_seen='' WHERE id='N1'")
	if _, e = s.GroupAction(g.ID, "start", GroupAction{RequestID: Random(16), Batches: ids}, nil); e == nil {
		t.Fatal("offline after preview started")
	}
	template := TaskTemplate{ID: Random(16), Label: "PLAN", Steps: p.Steps}
	saved, e := s.SaveTemplate(template)
	if e != nil {
		t.Fatal(e)
	}
	if saved.Steps[0].ID == saved.Steps[1].ID {
		t.Fatal("repeated tool identity lost")
	}
	template.Steps[0].Seconds = 100
	if _, e = s.SaveTemplate(template); e == nil {
		t.Fatal("template overwritten")
	}
	child, _ := s.Batch(ids[0])
	if child.Plan.Steps[0].Seconds != 1 {
		t.Fatal("saved plan changed")
	}
	s.Heartbeat("N1", Version)
	foreign, _ := s.Create(testPlan("N1"))
	if _, e = s.GroupAction(g.ID, "start", GroupAction{RequestID: Random(16), Batches: []string{foreign.ID}}, nil); e == nil {
		t.Fatal("foreign group member")
	}
}
func TestGroupTwoHundredNodesConcurrentStart(t *testing.T) {
	s := testStore(t)
	nodes := []string{}
	for i := 0; i < 200; i++ {
		n := fmt.Sprintf("N%03d", i)
		readyNode(t, s, n)
		nodes = append(nodes, n)
	}
	g, e := s.CreateGroup(groupPlan(nodes...), nil)
	if e != nil {
		t.Fatal(e)
	}
	ids := groupIDs(t, s, g)
	in := GroupAction{RequestID: Random(16), Batches: ids}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			copyIn := in
			copyIn.Batches = append([]string{}, in.Batches...)
			if _, err := s.GroupAction(g.ID, "start", copyIn, nil); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	v, e := s.Group(g.ID)
	if e != nil || v.Counts["armed"] != 200 {
		t.Fatal(v.Counts, e)
	}
	op, e := s.GroupOperations(g.ID)
	if e != nil || len(op) != 1 {
		t.Fatal(len(op), e)
	}
	cancel := GroupAction{RequestID: Random(16), Batches: ids[:100], Reason: "selected cancellation"}
	if _, e = s.GroupAction(g.ID, "cancel", cancel, nil); e != nil {
		t.Fatal(e)
	}
	if _, e = s.GroupAction(g.ID, "cancel", cancel, nil); e != nil {
		t.Fatal("cancel replay", e)
	}
	v, _ = s.Group(g.ID)
	if v.Counts["cancelled"] != 100 || v.Counts["armed"] != 100 {
		t.Fatal(v.Counts)
	}
}

type fakePower struct {
	mu    sync.Mutex
	state string
	calls int
	fail  bool
}

func (f *fakePower) Status(context.Context, BMCBinding) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail {
		return "unknown", errors.New("fixture failure")
	}
	return f.state, nil
}
func (f *fakePower) On(context.Context, BMCBinding) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.fail {
		return errors.New("fixture failure")
	}
	return nil
}
func bootFixture(t *testing.T) (*Server, *fakePower, Batch) {
	t.Helper()
	server, _, _ := testServer(t)
	f := &fakePower{state: "off"}
	server.power.driver = f
	binding := BMCBinding{Node: "N1", Address: "192.168.20.21", Username: "operator", Password: "fixture-secret", Cipher: 17}
	if e := server.power.Bind(binding); e != nil {
		t.Fatal(e)
	}
	binding = server.power.bindings["N1"]
	server.power.probe(context.Background(), binding)
	g, e := server.Store.CreateGroup(groupPlan("N1"), server.power.Snapshot())
	if e != nil {
		t.Fatal(e)
	}
	in := GroupAction{RequestID: Random(16), Batches: groupIDs(t, server.Store, g)}
	if _, e = server.Store.GroupAction(g.ID, "start", in, server.power.Snapshot()); e == nil {
		t.Fatal("wake lacked explicit confirmation")
	}
	in.Wake = true
	if _, e = server.Store.GroupAction(g.ID, "start", in, server.power.Snapshot()); e != nil {
		t.Fatal(e)
	}
	b, _ := server.Store.Batch(in.Batches[0])
	return server, f, b
}
func TestPowerOffWakeAgentAndCancellation(t *testing.T) {
	server, f, b := bootFixture(t)
	ctx := context.Background()
	if b.State != "waiting_boot" {
		t.Fatal(b.State)
	}
	if pending, _ := server.Store.Pending("N1"); pending != nil {
		t.Fatal("boot alone dispatched plan")
	}
	other, _ := server.Store.Create(testPlan("N1"))
	if _, e := server.Store.Arm(other.ID); e == nil {
		t.Fatal("boot did not reserve node")
	}
	server.advanceBoot(ctx, b)
	b, _ = server.Store.Batch(b.ID)
	if f.calls != 1 || b.Power.Stage != "waiting_agent" {
		t.Fatal("wake command not journaled")
	}
	server.advanceBoot(ctx, b)
	if f.calls != 1 {
		t.Fatal("repeated power command")
	}
	server.Store.Heartbeat("N1", Version)
	server.advanceBoot(ctx, b)
	b, _ = server.Store.Batch(b.ID)
	if b.State != "armed" || b.Power.Stage != "agent_connected" {
		t.Fatal("heartbeat did not authorize specific batch")
	}
	server2, f2, b2 := bootFixture(t)
	server2.Store.Cancel(b2.ID, "cancel before wake")
	server2.advanceBoot(ctx, b2)
	if f2.calls != 0 {
		t.Fatal("cancelled boot powered node")
	}
}
func TestPowerTimeoutUnreachableAndAmbiguousRestart(t *testing.T) {
	for _, mode := range []string{"timeout", "unreachable", "ambiguous"} {
		t.Run(mode, func(t *testing.T) {
			server, f, b := bootFixture(t)
			switch mode {
			case "timeout":
				server.Store.Mutate(b.ID, "fixture", func(v *Batch) error {
					v.Power.Deadline = time.Now().Add(-time.Second).Format(time.RFC3339Nano)
					return nil
				})
			case "unreachable":
				f.fail = true
			case "ambiguous":
				server.Store.Mutate(b.ID, "fixture", func(v *Batch) error { v.Power.Stage = "command_requested"; return nil })
			}
			b, _ = server.Store.Batch(b.ID)
			restarted := NewServer(server.Store, server.Config)
			if e := restarted.LoadPower(); e != nil {
				t.Fatal(e)
			}
			restarted.power.driver = f
			restarted.advanceBoot(context.Background(), b)
			b, _ = server.Store.Batch(b.ID)
			if b.State != "needs_attention" || b.Result.Execution != "not_started" || f.calls != 0 {
				t.Fatal("unsafe recovery", b.State, f.calls)
			}
			pending, _ := server.Store.Pending("N1")
			if pending != nil {
				t.Fatal("failed boot started workload")
			}
		})
	}
}
func TestDispatchAPIAndSecretBoundary(t *testing.T) {
	server, admin, node := testServer(t)
	ctx := context.Background()
	var out any
	for _, path := range []string{"nodes", "groups", "templates"} {
		if e := node.JSON(ctx, "GET", "/api/v1/dispatch/"+path, "", nil, &out); e == nil {
			t.Fatal("node accessed operator dispatch")
		}
	}
	b := BMCBinding{Node: "N1", Address: "192.168.1.21", Username: "operator", Password: "BMC-SECRET-fixture", Cipher: 17}
	if e := admin.JSON(ctx, "POST", "/api/v1/dispatch/bmc", "", b, &out); e != nil {
		t.Fatal(e)
	}
	if e := admin.JSON(ctx, "GET", "/api/v1/dispatch/nodes", "", nil, &out); e != nil {
		t.Fatal(e)
	}
	raw, _ := json.Marshal(out)
	if strings.Contains(string(raw), b.Password) || strings.Contains(string(raw), b.Username) {
		t.Fatal("credential disclosed")
	}
	info, e := os.Stat(server.power.path)
	if e != nil || info.Mode().Perm() != 0600 {
		t.Fatal("credentials not private", e)
	}
	for _, addr := range []string{"127.0.0.1", "8.8.8.8", "169.254.169.254", "host;reboot", "-H", "192.168.1.21/24"} {
		bad := b
		bad.Address = addr
		if e = validateBinding(bad); e == nil {
			t.Fatal("invalid BMC endpoint", addr)
		}
	}
	f := &fakePower{state: "off"}
	server.power.driver = f
	bound := server.power.bindings["N1"]
	server.power.probe(ctx, bound)
	g, e := server.Store.CreateGroup(groupPlan("N1"), server.power.Snapshot())
	if e != nil {
		t.Fatal(e)
	}
	server.Store.GroupAction(g.ID, "start", GroupAction{RequestID: Random(16), Batches: groupIDs(t, server.Store, g), Wake: true}, server.power.Snapshot())
	if e = admin.JSON(ctx, "POST", "/api/v1/dispatch/bmc", "", b, &out); e == nil {
		t.Fatal("in-flight BMC remapped")
	}
	if _, e = runIPMI(ctx, b, "off"); e == nil {
		t.Fatal("power-off allowed")
	}
	if e = admin.JSON(ctx, "POST", "/api/v1/dispatch/groups", "", map[string]any{"command": "rmal"}, &out); e == nil {
		t.Fatal("unknown dispatch fields")
	}
}
func TestDispatchMigrationBackupAndPersistence(t *testing.T) {
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	s, e := OpenStore(dir)
	if e != nil {
		t.Fatal(e)
	}
	readyNode(t, s, "N1")
	b, _ := s.Create(testPlan("N1"))
	for _, q := range []string{"DROP TABLE group_members", "DROP TABLE dispatch_operations", "DROP TABLE dispatch_groups", "DROP TABLE task_templates", "UPDATE metadata SET value='1' WHERE key='schema'"} {
		if _, e = s.db.Exec(q); e != nil {
			t.Fatal(e)
		}
	}
	s.Close()
	s, e = OpenStore(dir)
	if e != nil {
		t.Fatal(e)
	}
	backup := filepath.Join(dir, "center.sqlite.before-dispatch-v1")
	if st, e := os.Stat(backup); e != nil || st.Mode().Perm() != 0600 {
		t.Fatal("no private schema backup", e)
	}
	old, e := s.Batch(b.ID)
	if e != nil || old.State != "draft" {
		t.Fatal("old evidence lost", e)
	}
	g, e := s.CreateGroup(groupPlan("N1"), nil)
	if e != nil {
		t.Fatal(e)
	}
	s.Close()
	s, e = OpenStore(dir)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	view, e := s.Group(g.ID)
	if e != nil || len(view.Members) != 1 {
		t.Fatal("group not durable", e)
	}
}
