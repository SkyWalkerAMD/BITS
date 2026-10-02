package bits

import (
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

// Node monitoring has no batch, execution attempt, plan or result. Its latest
// reading and bounded in-memory history cannot enter a sealed batch report.
type MonitorUpdate struct {
	Session string      `json:"session"`
	Sample  *LiveSample `json:"sample"`
}

func (c *LiveCache) PutMonitor(node string, v MonitorUpdate) error {
	if !ValidName(node) || !idRE.MatchString(v.Session) || v.Sample == nil {
		return errors.New("invalid node monitoring identity")
	}
	if err := validSample(v.Sample); err != nil {
		return err
	}
	v.Sample = copyLiveSample(v.Sample, true)
	c.mu.Lock()
	defer c.mu.Unlock()
	old := c.monitors[node]
	f := LiveFrame{LiveUpdate: LiveUpdate{Phase: "monitoring", Sample: v.Sample},
		Node: node, Session: v.Session, Received: UTC(), SampleReceived: UTC()}
	if old.Sample != nil {
		if old.Session == v.Session {
			if v.Sample.Sequence < old.Sample.Sequence {
				return errors.New("stale node sample")
			}
			if v.Sample.Sequence == old.Sample.Sequence {
				a, _ := json.Marshal(old.Sample)
				b, _ := json.Marshal(v.Sample)
				if string(a) != string(b) {
					return errors.New("node sample replay differs")
				}
				// Republishing the same reading must not make it fresh again.
				return nil
			}
			f.History = old.History
		} else {
			before, _ := time.Parse(time.RFC3339Nano, old.Sample.Observed)
			after, _ := time.Parse(time.RFC3339Nano, v.Sample.Observed)
			if !after.After(before) {
				return errors.New("older monitoring session")
			}
		}
	}
	f.History = append(f.History, *copyLiveSample(v.Sample, false))
	if len(f.History) > 180 {
		f.History = append([]LiveSample(nil), f.History[len(f.History)-180:]...)
	}
	c.monitors[node] = f
	return nil
}

func (c *LiveCache) MonitorSnapshot(node string) []LiveFrame {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := []LiveFrame{}
	for id, value := range c.monitors {
		if node != "" && id != node {
			continue
		}
		value.Sample = copyLiveSample(value.Sample, node != "")
		if node == "" {
			value.History = nil
		} else {
			value.History = make([]LiveSample, len(c.monitors[id].History))
			for i, sample := range c.monitors[id].History {
				value.History[i] = *copyLiveSample(&sample, false)
			}
		}
		out = append(out, value)
	}
	return out
}

func (s *Server) nodeMonitoring(node string) (map[string]any, error) {
	if !ValidName(node) {
		return nil, errors.New("invalid node")
	}
	var exists int
	if err := s.Store.db.QueryRow("SELECT count(*) FROM nodes WHERE id=?", node).Scan(&exists); err != nil {
		return nil, err
	}
	if exists != 1 {
		return nil, errors.New("unknown node")
	}
	frames := s.live.MonitorSnapshot(node)
	var batch *Batch
	var body string
	err := s.Store.db.QueryRow("SELECT body FROM batches WHERE node=? AND state IN ('armed','running') ORDER BY rowid DESC LIMIT 1", node).Scan(&body)
	if err != nil && err != sql.ErrNoRows {
		return nil, err
	}
	if err == nil {
		var b Batch
		if err = json.Unmarshal([]byte(body), &b); err != nil {
			return nil, err
		}
		active := s.live.Snapshot(b.ID)
		// During preflight/execution use the same samples as the batch. Never
		// substitute an earlier idle reading while waiting for the first sample.
		if len(active) == 0 || active[0].Phase == "prepared" || active[0].Phase == "executing" {
			frames, batch = active, &b
		}
	}
	return map[string]any{"node": node, "frames": frames, "batch": batch, "time": UTC()}, nil
}
