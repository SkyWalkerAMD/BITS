package bits

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func operationTestCA(t *testing.T, client *Client) string {
	t.Helper()
	response, err := client.HTTP.Get(client.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: response.TLS.PeerCertificates[0].Raw}))
}

func manualWakeFixture(t *testing.T) (*Server, *Client, *Client, *fakePower) {
	t.Helper()
	s, admin, node := testServer(t)
	f := &fakePower{state: "off"}
	s.power.driver = f
	if err := s.power.Bind(BMCBinding{Node: "N1", Address: "192.168.20.21", Username: "operator", Password: "fixture-only", Cipher: 17}); err != nil {
		t.Fatal(err)
	}
	s.power.probe(context.Background(), s.power.bindings["N1"])
	return s, admin, node, f
}
func storedWake(t *testing.T, s *Store, id, node string) WakeMember {
	t.Helper()
	var raw string
	var m WakeMember
	if err := s.db.QueryRow("SELECT body FROM wake_members WHERE operation=? AND node=?", id, node).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatal(err)
	}
	return m
}
func TestManualWakeIsIdempotentAndNeverStartsBatch(t *testing.T) {
	s, admin, node, driver := manualWakeFixture(t)
	ctx := context.Background()
	draft, err := s.Store.Create(testPlan("N1"))
	if err != nil {
		t.Fatal(err)
	}
	req := WakeRequest{RequestID: Random(16), Nodes: []string{"N1"}}
	var op WakeOperation
	if err = admin.JSON(ctx, "POST", "/api/v1/dispatch/wakes", "", req, &op); err != nil {
		t.Fatal(err)
	}
	if op.Members[0].Binding != "" {
		t.Fatal("internal binding exposed")
	}
	if _, err = s.Store.Arm(draft.ID); err == nil {
		t.Fatal("active wake allowed a batch start")
	}
	if err = node.JSON(ctx, "POST", "/api/v1/dispatch/wakes", "", req, nil); err == nil {
		t.Fatal("node gained wake authority")
	}
	bind := BMCBinding{Node: "N1", Address: "192.168.20.22", Username: "operator", Password: "new-fixture", Cipher: 17}
	if err = admin.JSON(ctx, "POST", "/api/v1/dispatch/bmc", "", bind, nil); err == nil {
		t.Fatal("active wake allowed BMC reassignment")
	}
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := s.Store.CreateWake(req, s.power.Snapshot()); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	first := storedWake(t, s.Store, op.ID, "N1")
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.advanceWake(ctx, op.ID, first) }()
	}
	wg.Wait()
	if driver.calls != 1 {
		t.Fatalf("sent %d power-on commands", driver.calls)
	}
	s.advanceWake(ctx, op.ID, storedWake(t, s.Store, op.ID, "N1"))
	if storedWake(t, s.Store, op.ID, "N1").State != "waiting_agent" {
		t.Fatal("power command alone claimed system online")
	}
	var poll struct {
		Batch *Batch `json:"batch"`
		Hold  string `json:"wake_hold"`
	}
	if err = node.JSON(ctx, "GET", "/node/v1/poll", "", nil, &poll); err != nil {
		t.Fatal(err)
	}
	if poll.Batch != nil || poll.Hold != op.ID {
		t.Fatal("wake started stress or lost idle hold")
	}
	s.advanceWake(ctx, op.ID, storedWake(t, s.Store, op.ID, "N1"))
	if storedWake(t, s.Store, op.ID, "N1").State != "online" {
		t.Fatal("fresh heartbeat did not complete wake")
	}
	all, _ := s.Store.Batches("")
	if len(all) != 1 || all[0].State != "draft" {
		t.Fatal("manual wake changed batch records")
	}
	if _, err = s.Store.Arm(draft.ID); err != nil {
		t.Fatal(err)
	}
	if hold, err := s.Store.wakeHold("N1"); err != nil || hold != "" {
		t.Fatal("explicit batch start did not release hold", err)
	}
	if _, err = s.Store.CreateWake(WakeRequest{RequestID: req.RequestID, Nodes: []string{"N2"}}, s.power.Snapshot()); err == nil {
		t.Fatal("request id reused for other nodes")
	}
}

func TestManualWakeTwoHundredAtomicSelection(t *testing.T) {
	s := testStore(t)
	states := map[string]PowerStatus{}
	names := []string{}
	for i := 0; i < 200; i++ {
		name := fmt.Sprintf("WAKE-%03d", i)
		names = append(names, name)
		if err := s.AddNode(name, Random(32)); err != nil {
			t.Fatal(err)
		}
		states[name] = PowerStatus{Configured: true, Binding: "fixture", State: "off", Checked: UTC()}
	}
	req := WakeRequest{RequestID: Random(16), Nodes: names}
	bad := states[names[199]]
	bad.State = "unknown"
	states[names[199]] = bad
	if _, err := s.CreateWake(req, states); err == nil {
		t.Fatal("mixed unreachable selection accepted")
	}
	ops, _ := s.WakeOperations(0)
	if len(ops) != 0 {
		t.Fatal("partial wake persisted before full validation")
	}
	bad.State = "off"
	states[names[199]] = bad
	s.Heartbeat(names[0], Version) // Already-online node is recorded, never powered again.
	op, err := s.CreateWake(req, states)
	if err != nil {
		t.Fatal(err)
	}
	if op.Counts["pending"] != 199 || op.Counts["already_online"] != 1 {
		t.Fatal(op.Counts)
	}
	if hold, _ := s.wakeHold(names[0]); hold != "" {
		t.Fatal("skipped node acquired a hold")
	}
	req.RequestID = Random(16)
	if _, err = s.CreateWake(req, states); err == nil {
		t.Fatal("second active wake accepted")
	}
	if _, err = s.CreateGroup(groupPlan(names[1]), states); err == nil {
		t.Fatal("group reserved a waking node")
	}
	batches, _ := s.Batches("")
	if len(batches) != 0 {
		t.Fatal("bulk wake created stress batches")
	}
}

func TestManualWakeRecoveryTimeoutAndBindingChange(t *testing.T) {
	for _, scenario := range []string{"restart-off", "restart-on", "timeout", "binding-change", "unreachable"} {
		t.Run(scenario, func(t *testing.T) {
			s, _, _, f := manualWakeFixture(t)
			ctx := context.Background()
			op, err := s.Store.CreateWake(WakeRequest{RequestID: Random(16), Nodes: []string{"N1"}}, s.power.Snapshot())
			if err != nil {
				t.Fatal(err)
			}
			if scenario == "timeout" {
				s.Store.mutateWake(op.ID, "N1", func(m *WakeMember) error {
					m.Deadline = time.Now().Add(-time.Second).UTC().Format(time.RFC3339Nano)
					return nil
				})
			}
			if scenario == "binding-change" {
				s.power.Bind(BMCBinding{Node: "N1", Address: "192.168.20.22", Username: "operator", Password: "fixture", Cipher: 17})
			}
			if scenario == "unreachable" {
				f.fail = true
			}
			if scenario == "restart-off" || scenario == "restart-on" {
				s.Store.mutateWake(op.ID, "N1", func(m *WakeMember) error { m.State = "command_requested"; m.CommandAt = UTC(); return nil })
				// A new center instance must reconcile the durable intent, not resend On.
				s = NewServer(s.Store, s.Config)
				s.power.driver = f
				if err = s.power.Load(); err != nil {
					t.Fatal(err)
				}
				if scenario == "restart-on" {
					f.state = "on"
				}
			}
			s.advanceWake(ctx, op.ID, storedWake(t, s.Store, op.ID, "N1"))
			m := storedWake(t, s.Store, op.ID, "N1")
			expected := "failed"
			if scenario == "timeout" {
				expected = "timed_out"
			}
			if scenario == "restart-on" {
				expected = "waiting_agent"
			}
			if m.State != expected || f.calls != 0 {
				t.Fatal(m.State, m.Error, f.calls)
			}
		})
	}
}

func TestManualWakeHoldSurvivesNodeRestart(t *testing.T) {
	s, _, node, _ := manualWakeFixture(t)
	op, err := s.Store.CreateWake(WakeRequest{RequestID: Random(16), Nodes: []string{"N1"}}, s.power.Snapshot())
	if err != nil {
		t.Fatal(err)
	}
	cfg := NodeConfig{Node: "N1", URL: node.URL, Token: node.Token, CA: operationTestCA(t, node)}
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	a, err := NewAgent(cfg, dir)
	if err != nil {
		t.Fatal(err)
	}
	if err = a.once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if a.wakeHold != op.ID {
		t.Fatal("idle poll lost wake policy")
	}
	a, err = NewAgent(cfg, dir)
	if err != nil {
		t.Fatal(err)
	}
	a.idleSince = time.Now().Add(-time.Hour)
	if err = a.shutdownIfIdle(context.Background()); err != nil || !a.idleSince.IsZero() {
		t.Fatal("manual monitoring allowed automatic shutdown", err)
	}
	if err = a.applyWakeHold("../../bad"); err == nil {
		t.Fatal("invalid hold accepted")
	}
	if err = a.applyWakeHold(""); err != nil {
		t.Fatal(err)
	}
	a, err = NewAgent(cfg, dir)
	if err != nil || a.wakeHold != "" {
		t.Fatal("released policy not durable", err)
	}
}

func finishedForDelete(t *testing.T, s *Server, node string) Batch {
	t.Helper()
	b, err := s.Store.Create(testPlan(node))
	if err != nil {
		t.Fatal(err)
	}
	b, err = s.Store.Mutate(b.ID, "fixture", func(v *Batch) error {
		v.State = "delivered"
		v.Attempt = Random(16)
		v.Result = completedResult()
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(s.Config.Data, "artifacts", b.ID)
	if err = os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"report.html", "monitor.xlsx", "telemetry-00001.jsonl", "worker.log", "receipt.json"} {
		if err = os.WriteFile(filepath.Join(dir, name), []byte("fixture evidence"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return b
}
func TestPermanentDeleteAtomicValidationAndCompleteCleanup(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	b := finishedForDelete(t, s, "N1")
	keep := finishedForDelete(t, s, "N1")
	draft, _ := s.Store.Create(testPlan("N1"))
	s.Store.Arm(draft.ID)
	req := DeleteRequest{RequestID: Random(16), Batches: []string{b.ID, draft.ID}}
	if _, err := s.Store.BeginDelete(req); err == nil {
		t.Fatal("active batch accepted")
	}
	if owner, _ := s.Store.deletedNode(b.ID); owner != "" {
		t.Fatal("mixed selection partially deleted")
	}
	if _, err := s.Store.BeginDelete(DeleteRequest{RequestID: Random(16), Batches: []string{b.ID, "../outside"}}); err == nil {
		t.Fatal("invalid deletion path accepted")
	}
	req.Batches = []string{b.ID}
	if err := node.JSON(ctx, "POST", "/api/v1/deletions", "", req, nil); err == nil {
		t.Fatal("node deleted operator evidence")
	}
	if err := admin.JSON(ctx, "GET", "/api/v1/deletions", "", nil, nil); err == nil {
		t.Fatal("GET accepted as deletion")
	}
	if err := admin.JSON(ctx, "POST", "/api/v1/deletions", "", req, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Store.BeginDelete(req); err != nil {
		t.Fatal("lost response replay rejected", err)
	}
	if _, err := s.Store.BeginDelete(DeleteRequest{RequestID: req.RequestID, Batches: []string{keep.ID}}); err == nil {
		t.Fatal("deletion identity collision accepted")
	}
	if _, err := s.Store.Cancel(b.ID, "late cancellation"); err == nil {
		t.Fatal("deleting batch was mutable")
	}
	// Exercise stale in-flight handlers with a Batch fetched before deletion.
	b.Artifacts = map[string]Artifact{"report.html": {SHA256: Digest([]byte("fixture evidence")), Bytes: 16}}
	if err := s.upload(httptest.NewRecorder(), httptest.NewRequest("HEAD", "/", nil), b, "report.html"); err == nil || !strings.Contains(err.Error(), "permanently deleted") {
		t.Fatal("stale upload accepted")
	}
	sealed, _ := json.Marshal(b.Artifacts)
	commitRequest := httptest.NewRequest("POST", "/", bytes.NewReader(sealed))
	commitRequest.Header.Set("Content-Type", "application/json")
	if err := s.commit(httptest.NewRecorder(), commitRequest, b); err == nil || !strings.Contains(err.Error(), "permanently deleted") {
		t.Fatal("stale commit accepted")
	}
	s.live.nodes["N1"] = LiveFrame{Batch: b.ID}
	s.completeDeletions()
	if _, err := s.Store.Batch(b.ID); err == nil {
		t.Fatal("batch record survived")
	}
	if _, err := os.Lstat(filepath.Join(s.Config.Data, "artifacts", b.ID)); !os.IsNotExist(err) {
		t.Fatal("evidence survived", err)
	}
	if _, err := os.Stat(filepath.Join(s.Config.Data, "artifacts", keep.ID, "report.html")); err != nil {
		t.Fatal("neighboring report removed", err)
	}
	events, _ := s.Store.Events(b.ID)
	if len(events) != 0 {
		t.Fatal("batch events survived")
	}
	if s.live.nodes["N1"].Batch != "" {
		t.Fatal("deleted live evidence survived")
	}
	var tombstone Batch
	if err := node.JSON(ctx, "GET", "/node/v1/batches/"+b.ID, "", nil, &tombstone); err != nil || tombstone.State != "deleted" || tombstone.Plan.Label != "" {
		t.Fatal("node tombstone missing or exposed report", err)
	}
	if err := node.JSON(ctx, "PUT", "/node/v1/batches/"+b.ID+"/files/report.html", b.Attempt, []byte("late"), nil); err == nil {
		t.Fatal("late delivery resurrected batch")
	}
	if err := admin.JSON(ctx, "GET", "/api/v1/batches/"+b.ID+"/files/report.html", "", nil, nil); err == nil {
		t.Fatal("deleted report downloadable")
	}
	if _, err := s.Store.BeginDelete(req); err != nil {
		t.Fatal("completed retry failed", err)
	}
	history, _ := s.Store.Deletions()
	if len(history) != 1 || history[0].Counts["done"] != 1 {
		t.Fatal(history)
	}
}

func TestDeletionRecoveryAndSymlinkBoundary(t *testing.T) {
	s, _, node := testServer(t)
	s.Store.AddNode("N2", Random(32))
	b := finishedForDelete(t, s, "N2")
	outside := t.TempDir()
	os.Chmod(outside, 0700)
	canary := filepath.Join(outside, "keep")
	os.WriteFile(canary, []byte("do not delete"), 0600)
	dir := filepath.Join(s.Config.Data, "artifacts", b.ID)
	if err := os.Symlink(outside, filepath.Join(dir, "outside-link")); err != nil {
		t.Fatal(err)
	}
	req := DeleteRequest{RequestID: Random(16), Batches: []string{b.ID}}
	if _, err := s.Store.BeginDelete(req); err != nil {
		t.Fatal(err)
	}
	if err := node.JSON(context.Background(), "GET", "/node/v1/batches/"+b.ID, "", nil, nil); err == nil {
		t.Fatal("cross-node tombstone exposed")
	}
	restarted := NewServer(s.Store, s.Config)
	restarted.completeDeletions()
	if _, err := os.Stat(canary); err != nil {
		t.Fatal("symlink escaped artifact root", err)
	}
	retry := finishedForDelete(t, s, "N1")
	req = DeleteRequest{RequestID: Random(16), Batches: []string{retry.ID}}
	s.Store.BeginDelete(req)
	// Simulate a crash after files are gone but before database cleanup commits.
	os.RemoveAll(filepath.Join(s.Config.Data, "artifacts", retry.ID))
	restarted.completeDeletions()
	if _, err := s.Store.Batch(retry.ID); err == nil {
		t.Fatal("post-removal recovery did not finish")
	}
	// A replaced top-level batch directory fails closed and supports explicit retry.
	fail := finishedForDelete(t, s, "N1")
	failDir := filepath.Join(s.Config.Data, "artifacts", fail.ID)
	os.RemoveAll(failDir)
	os.Symlink(outside, failDir)
	req = DeleteRequest{RequestID: Random(16), Batches: []string{fail.ID}}
	s.Store.BeginDelete(req)
	restarted.completeDeletions()
	var state string
	s.Store.db.QueryRow("SELECT state FROM deleted_batches WHERE id=?", fail.ID).Scan(&state)
	if state != "failed" {
		t.Fatal("unsafe deletion not stopped", state)
	}
	os.Remove(failDir)
	s.Store.BeginDelete(req)
	restarted.completeDeletions()
	if _, err := s.Store.Batch(fail.ID); err == nil {
		t.Fatal("explicit retry not completed")
	}
	if _, err := os.Stat(canary); err != nil {
		t.Fatal("outside file removed", err)
	}
}

func TestDeletionRemovesEmptyGroupAndUnblocksLateAgent(t *testing.T) {
	s, _, node := testServer(t)
	s.Store.Heartbeat("N1", Version)
	g, err := s.Store.CreateGroup(groupPlan("N1"), nil)
	if err != nil {
		t.Fatal(err)
	}
	ids := groupIDs(t, s.Store, g)
	b, _ := s.Store.Batch(ids[0])
	s.Store.Mutate(b.ID, "fixture", func(v *Batch) error { v.State = "delivered"; v.Result = completedResult(); return nil })
	s.Store.BeginDelete(DeleteRequest{RequestID: Random(16), Batches: ids})
	s.completeDeletions()
	if _, err = s.Store.Group(g.ID); err == nil {
		t.Fatal("empty group retained orphaned members")
	}
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	a, err := NewAgent(NodeConfig{Node: "N1", URL: node.URL, Token: node.Token, CA: operationTestCA(t, node)}, dir)
	if err != nil {
		t.Fatal(err)
	}
	runDir := filepath.Join(dir, "runs", b.ID)
	os.MkdirAll(runDir, 0700)
	run := LocalRun{Batch: b, Phase: "delivering"}
	a.save(runDir, &run)
	if err = a.once(context.Background()); err != nil {
		t.Fatal("late agent blocked after deletion", err)
	}
	ReadJSON(filepath.Join(runDir, "run.json"), &run)
	if run.Phase != "done" || run.Batch.State != "deleted" || run.Batch.Plan.Label != b.Plan.Label {
		t.Fatal("local evidence identity lost", run)
	}
}

func dropNodeOperations(t *testing.T, s *Store) {
	t.Helper()
	for _, table := range []string{"node_network", "wake_holds", "wake_members", "wake_operations", "deleted_batches", "deletion_operations", "node_hardware_info"} {
		if _, err := s.db.Exec("DROP TABLE " + table); err != nil {
			t.Fatal(err)
		}
	}
}
func TestNodeOperationsMigrationBackupAndDurability(t *testing.T) {
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	s.AddNode("N1", Random(32))
	dropNodeOperations(t, s)
	s.db.Exec("UPDATE metadata SET value='3' WHERE key='schema'")
	s.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	backup := filepath.Join(dir, "center.sqlite.before-node-operations-v3")
	if info, err := os.Stat(backup); err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("private migration backup missing", err)
	}
	before, err := sql.Open("sqlite", backup+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	var schema string
	before.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&schema)
	before.Close()
	if schema != "3" {
		t.Fatal("backup changed original schema")
	}
	req := WakeRequest{RequestID: Random(16), Nodes: []string{"N1"}}
	_, err = s.CreateWake(req, map[string]PowerStatus{"N1": {Configured: true, Binding: "fixture", State: "off", Checked: UTC()}})
	if err != nil {
		t.Fatal(err)
	}
	s.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if hold, err := s.wakeHold("N1"); err != nil || hold != req.RequestID {
		t.Fatal("wake hold lost on restart", err)
	}
	if op, err := s.WakeOperation(req.RequestID); err != nil || op.Counts["pending"] != 1 {
		t.Fatal("wake operation lost", err)
	}
}
