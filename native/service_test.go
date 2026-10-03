package bits

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestPackageMaintenanceExcludesNewClaimsAndActiveWork(t *testing.T) {
	server, _, client := testServer(t)
	b, err := server.Store.Create(testPlan("N1"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = server.Store.Arm(b.ID); err != nil {
		t.Fatal(err)
	}
	data := t.TempDir()
	os.Chmod(data, 0700)
	gate, err := maintenanceGate(data)
	if err != nil {
		t.Fatal(err)
	}
	defer gate.Close()
	if err = syscall.Flock(int(gate.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{})
	agent := &Agent{Config: NodeConfig{Node: "N1", KeepOn: true}, Client: client, Data: data}
	agent.Worker = func(ctx context.Context, action, dir string, tick func()) error {
		if action != "preflight" {
			return errors.New("unexpected worker action")
		}
		close(started)
		<-ctx.Done()
		return ctx.Err()
	}
	done := make(chan error, 1)
	go func() { done <- agent.Run(ctx, true) }()
	select {
	case <-started:
		t.Fatal("package maintenance allowed a new worker")
	case <-time.After(200 * time.Millisecond):
	}
	if _, err = os.Stat(filepath.Join(data, "runs", b.ID)); !os.IsNotExist(err) {
		t.Fatal("execution intent was created while maintenance owned the node")
	}
	syscall.Flock(int(gate.Fd()), syscall.LOCK_UN)
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("node did not resume after maintenance")
	}
	if err = syscall.Flock(int(gate.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); !errors.Is(err, syscall.EWOULDBLOCK) {
		t.Fatal("maintenance could interrupt an active worker", err)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("agent did not stop")
	}
	if err = syscall.Flock(int(gate.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal("maintenance lock leaked after service stop", err)
	}
}
