package bits

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Only the agent's control loop starts/stops this worker. Joining it before
// preflight/recovery prevents concurrent idle and batch hardware collection.
func (a *Agent) startMonitor(ctx context.Context) error {
	if !a.monitorEnabled || ctx.Err() != nil {
		return nil
	}
	if a.monitorDone != nil {
		select {
		case err := <-a.monitorDone:
			a.monitorCancel()
			a.monitorDone, a.monitorCancel = nil, nil
			a.monitorRetry = time.Now().Add(30 * time.Second)
			if err != nil && !errors.Is(err, context.Canceled) {
				fmt.Fprintln(os.Stderr, "BITS node monitoring stopped:", err)
			}
		default:
			return nil
		}
	}
	if time.Now().Before(a.monitorRetry) {
		return nil
	}
	dir := filepath.Join(a.Data, "monitor")
	if err := os.Mkdir(dir, 0700); err != nil && !os.IsExist(err) {
		return err
	}
	if err := PrivateDir(dir); err != nil {
		return err
	}
	path := filepath.Join(dir, "live.json")
	if err := CheckFileIfExists(path); err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	infoPath:=filepath.Join(dir,"info.json")
	if err:=CheckFileIfExists(infoPath);err!=nil { return err }
	if err:=os.Remove(infoPath);err!=nil && !os.IsNotExist(err) { return err }
	monitorCtx, cancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	a.monitorCancel, a.monitorDone = cancel, done
	session := Random(16)
	go func() {
		var sent int64
		done <- a.Worker(monitorCtx, "monitor", dir, func() {
			a.publishHardwareInfo(monitorCtx,dir)
			var sample LiveSample
			if monitorCtx.Err() != nil || ReadJSON(path, &sample) != nil || validSample(&sample) != nil || sample.Sequence <= sent {
				return
			}
			callCtx, stop := context.WithTimeout(monitorCtx, 2*time.Second)
			defer stop()
			if a.Client.JSON(callCtx, "POST", "/node/v1/monitor", "", MonitorUpdate{Session: session, Sample: &sample}, nil) == nil {
				sent = sample.Sequence
			}
		})
	}()
	return nil
}

func (a *Agent) stopMonitor() error {
	if a.monitorDone == nil {
		return nil
	}
	a.monitorCancel()
	err := <-a.monitorDone
	a.monitorCancel, a.monitorDone = nil, nil
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}
