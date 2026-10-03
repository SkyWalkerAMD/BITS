package bits

import (
	"context"
	"encoding/json"
	"path/filepath"
	"time"
)

func (a *Agent) publishLive(ctx context.Context, dir string, run *LocalRun) {
	a.publishHardwareInfo(ctx, dir)
	now := time.Now()
	if now.Sub(a.lastLive) < 2*time.Second && a.livePhase == run.Phase {
		return
	}
	a.lastLive = now
	a.livePhase = run.Phase
	v := LiveUpdate{Phase: run.Phase}
	var sample LiveSample
	if ReadJSON(filepath.Join(dir, "live.json"), &sample) == nil && validSample(&sample) == nil {
		v.Sample = &sample
	}
	for _, raw := range run.Result.Steps {
		var step struct {
			ID      string  "json:\"id\""
			Started string  "json:\"started_at\""
			Ended   string  "json:\"ended_at\""
			Elapsed float64 "json:\"elapsed_s\""
		}
		if json.Unmarshal(raw, &step) != nil {
			continue
		}
		if step.Ended != "" {
			v.Completed++
			if finite(step.Elapsed, 0, 40*86400) {
				v.Elapsed += step.Elapsed
			}
		} else if start, e := time.Parse(time.RFC3339Nano, step.Started); e == nil {
			v.StepID = step.ID
			v.StepElapsed = now.Sub(start).Seconds()
			if v.StepElapsed < 0 {
				v.StepElapsed = 0
			}
			v.Elapsed += v.StepElapsed
		}
	}
	callCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	// Live publication failure never changes sealed evidence or grants an
	// execution attempt. Last-received times make lost updates visible.
	a.Client.JSON(callCtx, "POST", "/node/v1/batches/"+run.Batch.ID+"/live", run.Batch.Attempt, v, nil)
}
func (a *Agent) heartbeat(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	var lastNetwork time.Time
	for {
		callCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		a.Client.JSON(callCtx, "GET", "/node/v1/heartbeat", "", nil, nil)
		cancel()
		if time.Since(lastNetwork) >= 30*time.Second {
			callCtx, cancel = context.WithTimeout(ctx, 3*time.Second)
			if a.Client.JSON(callCtx, "POST", "/node/v1/network", "", collectSystemNetwork(a.Config.URL), nil) == nil {
				lastNetwork = time.Now()
			}
			cancel()
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
