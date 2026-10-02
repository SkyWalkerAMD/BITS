package bits

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestNodeMonitoringWithoutBatchAndProtectedEndpoint(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	v := MonitorUpdate{Session: Random(16), Sample: &LiveSample{Sequence: 1, Observed: UTC(), ExtraObserved: UTC(), Available: true, Hardware: testHardware()}}
	if err := node.JSON(ctx, "POST", "/node/v1/monitor", "", v, nil); err != nil {
		t.Fatal(err)
	}
	var got struct {
		Node   string
		Frames []LiveFrame
		Batch  *Batch
	}
	if err := admin.JSON(ctx, "GET", "/api/v1/nodes/N1/live", "", nil, &got); err != nil {
		t.Fatal(err)
	}
	if got.Node != "N1" || got.Batch != nil || len(got.Frames) != 1 || got.Frames[0].Batch != "" || got.Frames[0].Sample.Hardware == nil {
		t.Fatal("batch-free detail missing", got)
	}
	all, _ := s.Store.Batches("")
	if len(all) != 0 {
		t.Fatal("monitoring created a batch")
	}
	if node.JSON(ctx, "GET", "/api/v1/nodes/N1/live", "", nil, &got) == nil {
		t.Fatal("node read operator view")
	}
	for _, extra := range []string{"node", "batch", "command", "timings", "license"} {
		bad := map[string]any{"session": v.Session, "sample": v.Sample, extra: "forbidden"}
		if node.JSON(ctx, "POST", "/node/v1/monitor", "", bad, nil) == nil {
			t.Fatal("unknown monitor field accepted", extra)
		}
	}
	if admin.JSON(ctx, "GET", "/api/v1/nodes/absent/live", "", nil, &got) == nil {
		t.Fatal("unknown node accepted")
	}
}

func TestNodeMonitorSessionsBoundedHistoryAndDenial(t *testing.T) {
	c := NewLiveCache()
	stamp := time.Now().Add(-time.Minute).UTC()
	v := MonitorUpdate{Session: Random(16), Sample: &LiveSample{Sequence: 1, Observed: stamp.Format(time.RFC3339Nano), ExtraObserved: UTC(), Available: true, Hardware: testHardware()}}
	for i := int64(1); i <= 200; i++ {
		v.Sample.Sequence = i
		if err := c.PutMonitor("N1", v); err != nil {
			t.Fatal(err)
		}
	}
	f := c.MonitorSnapshot("N1")[0]
	if len(f.History) != 180 || c.MonitorSnapshot("")[0].Sample.Hardware != nil {
		t.Fatal("unbounded or excessive fleet projection")
	}
	for _, sample := range f.History {
		if sample.Hardware != nil {
			t.Fatal("core arrays in trend history")
		}
	}
	*f.Sample.Hardware.Cores[0].MHz = 9999
	if *c.MonitorSnapshot("N1")[0].Sample.Hardware.Cores[0].MHz != 3000 {
		t.Fatal("snapshot mutated cache")
	}
	if err := c.PutMonitor("N1", v); err != nil {
		t.Fatal(err)
	}
	if c.MonitorSnapshot("N1")[0].SampleReceived != f.SampleReceived {
		t.Fatal("replay refreshed sample")
	}
	v.Sample.Sequence--
	if c.PutMonitor("N1", v) == nil {
		t.Fatal("old sequence accepted")
	}
	v.Session = Random(16)
	if c.PutMonitor("N1", v) == nil {
		t.Fatal("old session accepted")
	}
	v.Sample = &LiveSample{Sequence: 1, Observed: UTC(), Available: false}
	if err := c.PutMonitor("N1", v); err != nil {
		t.Fatal(err)
	}
	f = c.MonitorSnapshot("N1")[0]
	if len(f.History) != 1 || f.Sample.Hardware != nil || f.Sample.Available {
		t.Fatal("denial reused earlier metrics")
	}
}

func TestMonitorSwitchPreservesBatchEvidence(t *testing.T) {
	s, _, _ := testServer(t)
	c := s.live
	idle := MonitorUpdate{Session: Random(16), Sample: &LiveSample{Sequence: 1, Observed: UTC(), Available: false}}
	if err := c.PutMonitor("N1", idle); err != nil {
		t.Fatal(err)
	}
	b, _ := s.Store.Create(testPlan("N1"))
	b, _ = s.Store.Arm(b.ID)
	view, err := s.nodeMonitoring("N1")
	if err != nil || len(view["frames"].([]LiveFrame)) != 0 {
		t.Fatal("old idle reading used while starting", view, err)
	}
	b, _ = s.Store.Claim(b.ID, "N1", b.Attempt)
	active := LiveUpdate{Phase: "executing", Sample: &LiveSample{Sequence: 1, Observed: UTC(), Available: true}}
	if err := c.Put(b, active); err != nil {
		t.Fatal(err)
	}
	idle.Sample.Sequence++
	if err := c.PutMonitor("N1", idle); err != nil {
		t.Fatal(err)
	}
	view, _ = s.nodeMonitoring("N1")
	if view["frames"].([]LiveFrame)[0].Batch != b.ID {
		t.Fatal("idle update replaced executing batch")
	}
	_, err = s.Store.Update(b.ID, "N1", b.Attempt, 1, Result{Execution: "completed", Quality: "readings_reported_validity_unknown", Report: "not_generated"})
	if err != nil {
		t.Fatal(err)
	}
	view, _ = s.nodeMonitoring("N1")
	if view["frames"].([]LiveFrame)[0].Phase != "monitoring" {
		t.Fatal("node did not return to monitoring")
	}
	if c.Snapshot(b.ID)[0].Sample.Sequence != 1 || !c.Snapshot(b.ID)[0].Sample.Available {
		t.Fatal("idle reading changed batch cache")
	}
}

func TestAgentJoinsIdleCollectorBeforePreflightAndResumesWhenBlocked(t *testing.T) {
	s, _, client := testServer(t)
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	a := &Agent{Client: client, Data: dir, Config: NodeConfig{Node: "N1"}, monitorEnabled: true}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	defer a.stopMonitor()
	started := make(chan struct{}, 4)
	var active, executions atomic.Int32
	a.Worker = func(ctx context.Context, action, directory string, tick func()) error {
		if action == "monitor" {
			if active.Add(1) != 1 {
				t.Error("multiple collectors")
			}
			defer active.Add(-1)
			if err := AtomicJSON(filepath.Join(directory, "live.json"), &LiveSample{Sequence: 1, Observed: UTC(), Available: false}); err != nil {
				return err
			}
			tick()
			started <- struct{}{}
			<-ctx.Done()
			return ctx.Err()
		}
		if action == "preflight" {
			if active.Load() != 0 {
				t.Error("preflight overlaps monitor")
			}
			return errors.New("synthetic preflight failure")
		}
		executions.Add(1)
		return nil
	}
	wait := func() {
		t.Helper()
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("monitor did not start")
		}
	}
	if err := a.startMonitor(ctx); err != nil {
		t.Fatal(err)
	}
	wait()
	b, _ := s.Store.Create(testPlan("N1"))
	b, _ = s.Store.Arm(b.ID)
	run := LocalRun{Batch: b, Phase: "prepared", Result: b.Result}
	if err := a.process(ctx, dir, &run, true); err == nil {
		t.Fatal("preflight failure ignored")
	}
	if active.Load() != 0 || run.Phase != "blocked" {
		t.Fatal("idle collector not joined")
	}
	if err := a.process(ctx, dir, &run, false); err != nil {
		t.Fatal(err)
	}
	wait()
	if err := a.stopMonitor(); err != nil {
		t.Fatal(err)
	}
	if active.Load() != 0 || executions.Load() != 0 {
		t.Fatal("monitor created workload or survived stop")
	}
}
