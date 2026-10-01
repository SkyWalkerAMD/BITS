package bits

import (
	"encoding/json"
	"errors"
	"math"
	"sync"
	"time"
)

// Live data is a small, typed projection of already collected evidence. It
// never accepts native output, commands, timing tables or licensing material.
type LiveSample struct {
	Sequence      int64    "json:\"sequence\""
	Observed      string   "json:\"observed_at\""
	ExtraObserved string   "json:\"extra_observed_at,omitempty\""
	Available     bool     "json:\"available\""
	TempC         *float64 "json:\"temp_c\""
	MHz           *float64 "json:\"mhz\""
	PackageW      *float64 "json:\"package_w\""
	Load          *float64 "json:\"load\""
	MemoryTempC   *float64 "json:\"memory_temp_c\""
	DRAMW         *float64 "json:\"dram_w\""
	PSUW          *float64 "json:\"psu_w\""
	VCCIN         *float64 "json:\"vccin_v\""
	VID           *float64 "json:\"vid_v\""
	TjMax         *float64 "json:\"tjmax_c\""
	Hardware      *LiveHardware "json:\"hardware,omitempty\""
}
type LiveUpdate struct {
	Phase       string      "json:\"phase\""
	StepID      string      "json:\"step_id,omitempty\""
	Completed   int         "json:\"completed_steps\""
	Elapsed     float64     "json:\"elapsed_s\""
	StepElapsed float64     "json:\"step_elapsed_s\""
	Sample      *LiveSample "json:\"sample,omitempty\""
}
type LiveFrame struct {
	LiveUpdate
	Node           string       "json:\"node\""
	StepTool       string       "json:\"step_tool,omitempty\""
	Batch          string       "json:\"batch\""
	Received       string       "json:\"received_at\""
	SampleReceived string       "json:\"sample_received_at,omitempty\""
	History        []LiveSample "json:\"history,omitempty\""
}
type LiveCache struct {
	mu    sync.Mutex
	nodes map[string]LiveFrame
}

func NewLiveCache() *LiveCache { return &LiveCache{nodes: map[string]LiveFrame{}} }
func finite(v float64, low, high float64) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0) && v >= low && v <= high
}
func validSample(v *LiveSample) error {
	if v == nil {
		return nil
	}
	if v.Sequence < 1 {
		return errors.New("invalid live sample sequence")
	}
	for index, stamp := range []string{v.Observed, v.ExtraObserved} {
		if index == 1 && stamp == "" {
			continue
		}
		t, err := time.Parse(time.RFC3339Nano, stamp)
		if err != nil || t.After(time.Now().Add(5*time.Minute)) {
			return errors.New("invalid live observation time")
		}
	}
	for _, item := range []struct {
		p         *float64
		low, high float64
	}{
		{v.TempC, -100, 300}, {v.MemoryTempC, -100, 300}, {v.TjMax, 0, 300},
		{v.MHz, 0, 100000}, {v.PackageW, 0, 1e9}, {v.DRAMW, 0, 1e9},
		{v.PSUW, 0, 1e9}, {v.Load, 0, 1e6}, {v.VCCIN, 0, 10}, {v.VID, 0, 10},
	} {
		if item.p != nil && (!v.Available || !finite(*item.p, item.low, item.high)) {
			return errors.New("invalid or unavailable live metric")
		}
	}
	if !v.Available && v.Hardware != nil {
		return errors.New("unavailable sample contains hardware readings")
	}
	return validHardware(v.Hardware, v.ExtraObserved)
}
func (c *LiveCache) Put(b Batch, v LiveUpdate) error {
	if IsTerminal(b.State) || b.State == "draft" {
		return errors.New("batch is no longer active")
	}
	if b.State == "armed" && v.Phase != "prepared" && v.Phase != "blocked" {
		return errors.New("execution is not accepted")
	}
	switch v.Phase {
	case "prepared", "executing", "finalizing", "delivering", "blocked":
	default:
		return errors.New("unknown live phase")
	}
	if v.Completed < 0 || v.Completed > len(b.Plan.Steps) || !finite(v.Elapsed, 0, 40*86400) || !finite(v.StepElapsed, 0, 40*86400) {
		return errors.New("invalid live progress")
	}
	found := v.StepID == ""
	for _, step := range b.Plan.Steps {
		found = found || step.ID == v.StepID
	}
	if !found {
		return errors.New("live step does not belong to this plan")
	}
	if err := validSample(v.Sample); err != nil {
		return err
	}
	// Own the values, including nested pointer fields; neither publishers nor
	// snapshot readers may mutate an accepted sample after validation.
	v.Sample = copyLiveSample(v.Sample, true)
	c.mu.Lock()
	defer c.mu.Unlock()
	old := c.nodes[b.Plan.Node]
	frame := LiveFrame{LiveUpdate: v, Node: b.Plan.Node, Batch: b.ID, Received: UTC()}
	for _, step := range b.Plan.Steps {
		if step.ID == v.StepID {
			frame.StepTool = step.Tool
		}
	}
	if old.Batch == b.ID {
		frame.History = old.History
		frame.SampleReceived = old.SampleReceived
		if v.Sample == nil {
			frame.Sample = old.Sample
		}
		if old.Sample != nil && v.Sample != nil && v.Sample.Sequence < old.Sample.Sequence {
			return errors.New("stale live sample")
		}
		if old.Sample != nil && v.Sample != nil && old.Sample.Sequence == v.Sample.Sequence {
			a, _ := json.Marshal(old.Sample)
			z, _ := json.Marshal(v.Sample)
			if string(a) != string(z) {
				return errors.New("sample replay differs")
			}
		}
	}
	if v.Sample != nil && (old.Batch != b.ID || old.Sample == nil || v.Sample.Sequence > old.Sample.Sequence) {
		frame.SampleReceived = UTC()
		frame.History = append(frame.History, *copyLiveSample(v.Sample, false))
		if len(frame.History) > 180 {
			frame.History = append([]LiveSample(nil), frame.History[len(frame.History)-180:]...)
		}
	}
	c.nodes[b.Plan.Node] = frame
	return nil
}
func (c *LiveCache) Snapshot(batch string) []LiveFrame {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := []LiveFrame{}
	for _, value := range c.nodes {
		if batch != "" && value.Batch != batch {
			continue
		}
		if batch == "" {
			value.History = nil
		} else {
			value.History = make([]LiveSample, len(value.History))
			for i, sample := range c.nodes[value.Node].History {
				value.History[i] = *copyLiveSample(&sample, false)
			}
		}
		value.Sample = copyLiveSample(value.Sample, batch != "")
		out = append(out, value)
	}
	return out
}
