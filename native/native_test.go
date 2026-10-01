package bits

import (
	"bytes"
	"context"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}
func testPlan(node string) Plan {
	return Plan{Node: node, Label: "CHECK", Steps: []Step{{Tool: "stress", Seconds: 1}, {Tool: "stress", Seconds: 2}}}
}
func completedResult() Result {
	return Result{Execution: "completed", Quality: "readings_reported_validity_unknown", Report: "generated"}
}
func TestBatchIdentityAndExplicitStart(t *testing.T) {
	s := testStore(t)
	if err := s.AddNode("N1", Random(32)); err != nil {
		t.Fatal(err)
	}
	b, err := s.Create(testPlan("N1"))
	if err != nil {
		t.Fatal(err)
	}
	if b.Plan.Steps[0].ID == b.Plan.Steps[1].ID || b.Plan.Steps[1].Seconds != 2 {
		t.Fatal("repeat steps lost their identity")
	}
	pending, err := s.Pending("N1")
	if err != nil || pending != nil {
		t.Fatal("draft was automatically dispatched")
	}
	b, err = s.Arm(b.ID)
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.Arm(b.ID)
	if err != nil || again.Attempt != b.Attempt {
		t.Fatal("double start changed attempt")
	}
	if _, err = s.Claim(b.ID, "N2", b.Attempt); err == nil {
		t.Fatal("cross-node claim allowed")
	}
	if _, err = s.Claim(b.ID, "N1", b.Attempt); err != nil {
		t.Fatal(err)
	}
	second, _ := s.Create(testPlan("N1"))
	if _, err = s.Arm(second.ID); err == nil {
		t.Fatal("two active batches on one node")
	}
	if _, err = s.Update(b.ID, "N1", b.Attempt, 1, completedResult()); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Update(b.ID, "N1", b.Attempt, 1, completedResult()); err != nil {
		t.Fatal("lost response not idempotent", err)
	}
	if _, err = s.Update(b.ID, "N1", b.Attempt, 1, Result{Execution: "failed", Quality: "partial", Report: "failed"}); err == nil {
		t.Fatal("different replay accepted")
	}
}
func TestValidationAndExpiredStart(t *testing.T) {
	s := testStore(t)
	s.AddNode("N1", Random(32))
	for _, tool := range []string{"strss", "/bin/sh", "sckocp", "rmal", "activate", "stress;reboot"} {
		p := testPlan("N1")
		p.Steps[0].Tool = tool
		if _, err := s.Create(p); err == nil {
			t.Fatal("invalid workload accepted", tool)
		}
	}
	b, _ := s.Create(testPlan("N1"))
	b, _ = s.Arm(b.ID)
	s.Mutate(b.ID, "test clock", func(v *Batch) error {
		v.Expires = time.Now().Add(-time.Minute).UTC().Format(time.RFC3339Nano)
		return nil
	})
	if _, err := s.Claim(b.ID, "N1", b.Attempt); err == nil {
		t.Fatal("expired command executed")
	}
	p, err := s.Pending("N1")
	if err != nil || p != nil {
		t.Fatal("expired command dispatched")
	}
	b, _ = s.Batch(b.ID)
	if b.State != "cancelled" {
		t.Fatal(b.State)
	}
}
func TestTwoHundredIndependentNodes(t *testing.T) {
	s := testStore(t)
	for i := 0; i < 200; i++ {
		if err := s.AddNode(fmt.Sprintf("N%03d", i), Random(32)); err != nil {
			t.Fatal(err)
		}
	}
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			node := fmt.Sprintf("N%03d", n)
			b, e := s.Create(testPlan(node))
			if e == nil {
				b, e = s.Arm(b.ID)
			}
			if e == nil {
				_, e = s.Claim(b.ID, node, b.Attempt)
			}
			if e != nil {
				t.Error(e)
			}
		}(i)
	}
	wg.Wait()
	batches, err := s.Batches("")
	if err != nil || len(batches) != 200 {
		t.Fatal(len(batches), err)
	}
}
func testServer(t *testing.T) (*Server, *Client, *Client) {
	t.Helper()
	s := testStore(t)
	data := t.TempDir()
	os.Chmod(data, 0700)
	os.Mkdir(filepath.Join(data, "artifacts"), 0700)
	adminToken, nodeToken := Random(32), Random(32)
	s.AddNode("N1", nodeToken)
	server := NewServer(s, CenterConfig{Data: data, AdminSHA: Digest([]byte(adminToken)), Networks: []string{"127.0.0.0/8"}})
	h := httptest.NewTLSServer(server)
	t.Cleanup(h.Close)
	server.Config.URL = h.URL
	ca := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: h.Certificate().Raw})
	admin, err := NewClient(h.URL, adminToken, "", string(ca))
	if err != nil {
		t.Fatal(err)
	}
	node, err := NewClient(h.URL, nodeToken, "N1", string(ca))
	if err != nil {
		t.Fatal(err)
	}
	return server, admin, node
}
func TestHTTPSAuthenticationAndStrictRequests(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	var out any
	if err := admin.JSON(ctx, "GET", "/api/v1/overview", "", nil, &out); err != nil {
		t.Fatal(err)
	}
	if err := node.JSON(ctx, "GET", "/api/v1/overview", "", nil, &out); err == nil {
		t.Fatal("node gained operator privileges")
	}
	evil := map[string]any{"node": "N1", "label": "CHECK", "steps": []map[string]any{{"tool": "stress", "seconds": 1, "command": "rmal"}}}
	if err := admin.JSON(ctx, "POST", "/api/v1/batches", "", evil, &out); err == nil {
		t.Fatal("command injection field accepted")
	}
	s.Store.AddNode("N2", Random(32))
	b, _ := s.Store.Create(testPlan("N2"))
	if err := node.JSON(ctx, "GET", "/node/v1/batches/"+b.ID, "", nil, &out); err == nil {
		t.Fatal("cross-node read allowed")
	}
	req, _ := http.NewRequest("POST", admin.URL+"/api/v1/batches", strings.NewReader("{}"))
	req.Header.Set("Authorization", "Bearer "+admin.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := admin.HTTP.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatal("CSRF safeguard missing", resp.StatusCode)
	}
	req, _ = http.NewRequest("POST", admin.URL+"/api/v1/batches", strings.NewReader("{}"))
	req.Header.Set("Authorization", "Bearer "+admin.Token)
	req.Header.Set("X-BITS-Request", "1")
	req.Header.Set("Origin", "https://attacker.invalid")
	resp, err = admin.HTTP.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatal("cross origin allowed")
	}
}
func TestResumableVerifiedDeliveryAndTamper(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	b, _ := s.Store.Create(testPlan("N1"))
	b, _ = s.Store.Arm(b.ID)
	s.Store.Claim(b.ID, "N1", b.Attempt)
	s.Store.Update(b.ID, "N1", b.Attempt, 1, completedResult())
	data := []byte("<html>sealed evidence</html>")
	manifest := map[string]Artifact{"report.html": {SHA256: Digest(data), Bytes: int64(len(data))}}
	base := "/node/v1/batches/" + b.ID
	if e := node.JSON(ctx, "POST", base+"/manifest", b.Attempt, manifest, nil); e != nil {
		t.Fatal(e)
	}
	put := func(off int, p []byte) error {
		r, e := node.Request(ctx, "PUT", base+"/files/report.html?offset="+fmt.Sprint(off), b.Attempt, bytes.NewReader(p), int64(len(p)))
		if e == nil {
			io.Copy(io.Discard, r.Body)
			r.Body.Close()
		}
		return e
	}
	if e := put(0, data[:7]); e != nil {
		t.Fatal(e)
	}
	if e := node.JSON(ctx, "POST", base+"/commit", b.Attempt, manifest, nil); e == nil {
		t.Fatal("partial upload became complete")
	}
	if e := put(0, data); e == nil {
		t.Fatal("stale offset accepted")
	}
	if e := put(7, data[7:]); e != nil {
		t.Fatal(e)
	}
	var downloaded bytes.Buffer
	if e := node.Download(ctx, base+"/files/report.html", b.Attempt, &downloaded); e != nil {
		t.Fatal(e)
	}
	if Digest(downloaded.Bytes()) != Digest(data) {
		t.Fatal("readback differs")
	}
	var final Batch
	if e := node.JSON(ctx, "POST", base+"/commit", b.Attempt, manifest, &final); e != nil {
		t.Fatal(e)
	}
	before := final.ReceiptSHA
	if e := node.JSON(ctx, "POST", base+"/commit", b.Attempt, manifest, &final); e != nil || final.ReceiptSHA != before {
		t.Fatal("recovery changed receipt", e)
	}
	res, e := admin.Request(ctx, "GET", "/api/v1/batches/"+b.ID+"/files/report.html", "", nil, 0)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if !strings.Contains(res.Header.Get("Content-Security-Policy"), "sandbox") {
		t.Fatal("HTML report has operator-origin capabilities")
	}
	file := filepath.Join(s.Config.Data, "artifacts", b.ID, "report.html")
	os.WriteFile(file, []byte("tampered"), 0600)
	if e := node.JSON(ctx, "POST", base+"/commit", b.Attempt, manifest, nil); e == nil {
		t.Fatal("tampered result verified")
	}
}
func TestPrivateFilesRejectSymlinksAndPermissions(t *testing.T) {
	dir := t.TempDir()
	os.Chmod(dir, 0700)
	path := filepath.Join(dir, "file")
	os.WriteFile(path, []byte("{}"), 0644)
	if e := ReadJSON(path, new(any)); e == nil {
		t.Fatal("public credentials allowed")
	}
	os.Remove(path)
	os.Symlink("/etc/passwd", path)
	if e := ReadJSON(path, new(any)); e == nil {
		t.Fatal("credential symlink allowed")
	}
	if e := ValidateArtifacts(map[string]Artifact{"../escape": {SHA256: Random(32)}}); e == nil {
		t.Fatal("path escape allowed")
	}
}
func TestAgentNeverRepeatsExecution(t *testing.T) {
	server, _, client := testServer(t)
	ctx := context.Background()
	cfg := NodeConfig{Node: "N1", Serial: "SERIAL", KeepOn: true}
	data := t.TempDir()
	os.Chmod(data, 0700)
	agent := &Agent{Config: cfg, Client: client, Data: data}
	executed := 0
	agent.Worker = func(ctx context.Context, action, dir string, tick func()) error {
		switch action {
		case "preflight":
			return nil
		case "execute":
			executed++
			return AtomicJSON(filepath.Join(dir, "result.json"), completedResult())
		case "report":
			os.Mkdir(filepath.Join(dir, "evidence"), 0700)
			file := filepath.Join(dir, "evidence", "report.html")
			if err := Atomic(file, []byte("safe report")); err != nil {
				return err
			}
			meta, err := HashFile(file)
			if err != nil {
				return err
			}
			return AtomicJSON(filepath.Join(dir, "artifacts.json"), map[string]Artifact{"report.html": meta})
		default:
			return fmt.Errorf("unexpected worker action %s", action)
		}
	}
	b, _ := server.Store.Create(testPlan("N1"))
	if e := agent.Run(ctx, true); e != nil {
		t.Fatal(e)
	}
	if executed != 0 {
		t.Fatal("draft executed")
	}
	server.Store.Arm(b.ID)
	if e := agent.Run(ctx, true); e != nil {
		t.Fatal(e)
	}
	if e := agent.Run(ctx, true); e != nil {
		t.Fatal(e)
	}
	if executed != 1 {
		t.Fatal("execution duplicated", executed)
	}
	b, _ = server.Store.Batch(b.ID)
	if b.State != "delivered" {
		t.Fatal(b.State)
	}
}
func TestInterruptedIntentPublishesFailureWithoutRestart(t *testing.T) {
	server, _, client := testServer(t)
	ctx := context.Background()
	b, _ := server.Store.Create(testPlan("N1"))
	b, _ = server.Store.Arm(b.ID)
	data := t.TempDir()
	os.Chmod(data, 0700)
	os.Mkdir(filepath.Join(data, "runs"), 0700)
	dir := filepath.Join(data, "runs", b.ID)
	os.Mkdir(dir, 0700)
	run := LocalRun{Batch: b, Phase: "prepared", Result: b.Result}
	AtomicJSON(filepath.Join(dir, "run.json"), run)
	agent := &Agent{Config: NodeConfig{Node: "N1", KeepOn: true}, Client: client, Data: data}
	calls := []string{}
	agent.Worker = func(ctx context.Context, action, dir string, tick func()) error {
		calls = append(calls, action)
		if action != "recover" {
			t.Fatal("interrupted intent tried to launch", action)
		}
		AtomicJSON(filepath.Join(dir, "result.json"), Result{Execution: "preflight_failed", Quality: "not_collected", Report: "not_generated", Error: "interrupted before execution"})
		return fmt.Errorf("no execution evidence")
	}
	if err := agent.Run(ctx, true); err != nil {
		t.Fatal(err)
	}
	if err := agent.Run(ctx, true); err != nil {
		t.Fatal(err)
	}
	if len(calls) != 1 {
		t.Fatal("recovery repeated work", calls)
	}
	b, _ = server.Store.Batch(b.ID)
	if b.State != "needs_attention" || b.Result.Execution != "preflight_failed" {
		t.Fatal(b)
	}
	if _, err := server.Store.Arm(b.ID); err == nil {
		t.Fatal("failed attempt could be restarted")
	}
}
func TestPendingSurvivesDashboardHistoryAndNoopDoesNotGrowEvents(t *testing.T) {
	s := testStore(t)
	s.AddNode("N1", Random(32))
	b, _ := s.Create(testPlan("N1"))
	b, _ = s.Arm(b.ID)
	before, _ := s.Events(b.ID)
	for i := 0; i < 8; i++ {
		s.Arm(b.ID)
	}
	after, _ := s.Events(b.ID)
	if len(before) != len(after) {
		t.Fatal("repeated click added duplicate events")
	}
	for i := 0; i < 505; i++ {
		if _, e := s.Create(testPlan("N1")); e != nil {
			t.Fatal(e)
		}
	}
	p, e := s.Pending("N1")
	if e != nil || p == nil || p.ID != b.ID {
		t.Fatal("active batch hidden behind history limit", e)
	}
}
func TestReservedReceiptAndCredentialFieldsRejected(t *testing.T) {
	for _, name := range []string{"receipt.json", "license.json", "activation.db", "arbitrary.txt"} {
		if e := ValidateArtifacts(map[string]Artifact{name: {SHA256: Random(32)}}); e == nil {
			t.Fatal("unexpected artifact accepted", name)
		}
	}
	_, admin, _ := testServer(t)
	for _, field := range []string{"activation_code", "license", "rmal", "sckocp_binary", "environment", "sckocp_args"} {
		request := map[string]any{"node": "N1", "label": "GUARD", "steps": []map[string]any{{"tool": "stress", "seconds": 1}}, field: "forbidden"}
		if e := admin.JSON(context.Background(), "POST", "/api/v1/batches", "", request, nil); e == nil {
			t.Fatal("permission-related request field accepted", field)
		}
	}
}
