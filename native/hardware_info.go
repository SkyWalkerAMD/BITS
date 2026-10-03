package bits

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"path/filepath"
	"regexp"
	"time"
	"unicode"
)

// A separate, bounded projection of the licensed info interface. It never
// accepts commands, executable paths, native diagnostics or non-Primary timings.
type InfoSection struct {
	Name  string   `json:"name"`
	Lines []string `json:"lines"`
}
type InfoCPU struct {
	ID        int    `json:"id"`
	Model     string `json:"model"`
	Cores     int    `json:"cores"`
	Threads   int    `json:"threads"`
	Family    int    `json:"family"`
	ModelID   int    `json:"model_id"`
	Stepping  int    `json:"stepping"`
	Microcode string `json:"microcode"`
}
type InfoDIMM struct {
	Slot   string            `json:"slot"`
	Fields map[string]string `json:"fields"`
}
type HardwareInfo struct {
	Schema   string        `json:"schema"`
	Observed string        `json:"observed_at"`
	Status   string        `json:"status"`
	Sections []InfoSection `json:"sections,omitempty"`
	CPUs     []InfoCPU     `json:"cpus,omitempty"`
	DIMMs    []InfoDIMM    `json:"dimms,omitempty"`
}
type NodeHardwareInfo struct {
	Node     string        `json:"node"`
	Checked  string        `json:"checked_at"`
	Received string        `json:"received_at"`
	Status   string        `json:"status"`
	Snapshot *HardwareInfo `json:"snapshot"`
}

var infoSections = map[string]bool{
	"Platform": true, "CPU": true, "Turbo Ratio Limits": true, "Thermal": true,
	"Power Limits": true, "Power Supplies": true, "Memory": true,
	"Memory Timings": true, "Cache": true, "Per-CCD Temperature": true, "SVI Rails": true,
}
var primaryInfoLine = regexp.MustCompile(`^\s*S[0-9]+\s+Primary\s+[0-9?]+-[0-9?]+-[0-9?]+-[0-9?]+(\s+(tCWL|tRC)\s+[0-9?]+)*\s*$`)

func infoText(s string, limit int) bool {
	if len(s) > limit {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}
func validHardwareInfo(v HardwareInfo) error {
	bad := errors.New("invalid hardware information")
	at, err := time.Parse(time.RFC3339Nano, v.Observed)
	if v.Schema != "bits-hardware-info-v1" || err != nil || at.After(time.Now().Add(5*time.Minute)) {
		return bad
	}
	if v.Status == "unavailable" {
		if len(v.Sections)+len(v.CPUs)+len(v.DIMMs) != 0 {
			return bad
		}
		return nil
	}
	if v.Status != "ok" || len(v.Sections) > len(infoSections) || len(v.CPUs) > 32 || len(v.DIMMs) > 512 {
		return bad
	}
	seen, total := map[string]bool{}, 0
	for _, section := range v.Sections {
		if !infoSections[section.Name] || seen[section.Name] || section.Lines == nil || len(section.Lines) > 512 {
			return bad
		}
		seen[section.Name] = true
		for _, line := range section.Lines {
			total += len(line)
			if !infoText(line, 2048) || total > 256<<10 {
				return bad
			}
			if section.Name == "Memory Timings" && !primaryInfoLine.MatchString(line) {
				return bad
			}
		}
	}
	if !seen["Platform"] || !seen["CPU"] {
		return bad
	}
	cpuIDs := map[int]bool{}
	for _, c := range v.CPUs {
		if c.ID < 0 || c.ID > 4095 || cpuIDs[c.ID] || c.Cores < 1 || c.Cores > 8192 || c.Threads < c.Cores || c.Threads > 16384 || c.Family < 0 || c.Family > 65535 || c.ModelID < 0 || c.ModelID > 65535 || c.Stepping < 0 || c.Stepping > 65535 || c.Model == "" || !infoText(c.Model, 512) || !infoText(c.Microcode, 128) {
			return bad
		}
		cpuIDs[c.ID] = true
	}
	slots := map[string]bool{}
	fields := map[string]bool{"DIMM": true, "Part Number": true, "Speed": true, "JEDEC": true, "VDDQ": true, "Size": true, "Temp": true}
	for _, d := range v.DIMMs {
		if !seen["Memory"] || d.Slot == "" || !infoText(d.Slot, 256) || slots[d.Slot] || len(d.Fields) > len(fields) || d.Fields["DIMM"] != d.Slot {
			return bad
		}
		slots[d.Slot] = true
		for key, val := range d.Fields {
			if !fields[key] || !infoText(val, 512) {
				return bad
			}
		}
	}
	encoded, err := json.Marshal(v)
	if err != nil || len(encoded) > 512<<10 {
		return bad
	}
	return nil
}

func (s *Store) HardwareInfo(node string) (NodeHardwareInfo, error) {
	v := NodeHardwareInfo{Node: node, Status: "not_collected"}
	if !ValidName(node) {
		return v, errors.New("invalid node")
	}
	var body sql.NullString
	err := s.db.QueryRow("SELECT h.body FROM nodes n LEFT JOIN node_hardware_info h ON n.id=h.node WHERE n.id=?", node).Scan(&body)
	if err != nil {
		return v, err
	}
	if body.Valid {
		err = json.Unmarshal([]byte(body.String), &v)
	}
	return v, err
}
func (s *Store) PutHardwareInfo(node string, in HardwareInfo) error {
	if err := validHardwareInfo(in); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	old, err := s.HardwareInfo(node)
	if err != nil {
		return err
	}
	if old.Checked != "" {
		before, _ := time.Parse(time.RFC3339Nano, old.Checked)
		after, _ := time.Parse(time.RFC3339Nano, in.Observed)
		if after.Before(before) {
			return errors.New("older hardware information")
		}
		if after.Equal(before) {
			if old.Status != in.Status {
				return errors.New("hardware information replay differs")
			}
			if in.Status == "ok" {
				a, _ := json.Marshal(old.Snapshot)
				b, _ := json.Marshal(in)
				if string(a) != string(b) {
					return errors.New("hardware information replay differs")
				}
			}
			return nil // A replay never refreshes the receipt or observation time.
		}
	}
	old.Checked, old.Received, old.Status = in.Observed, UTC(), in.Status
	if in.Status == "ok" {
		old.Snapshot = &in
	}
	body, err := json.Marshal(old)
	if err != nil {
		return err
	}
	_, err = s.db.Exec("INSERT INTO node_hardware_info(node,body) VALUES(?,?) ON CONFLICT(node) DO UPDATE SET body=excluded.body", node, string(body))
	return err
}

func (a *Agent) publishHardwareInfo(ctx context.Context, dir string) {
	var in HardwareInfo
	if ctx.Err() != nil || ReadJSON(filepath.Join(dir, "info.json"), &in) != nil || validHardwareInfo(in) != nil {
		return
	}
	body, _ := json.Marshal(in)
	digest := Digest(body)
	if digest == a.infoDigest {
		return
	}
	callCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	if a.Client.JSON(callCtx, "POST", "/node/v1/hardware-info", "", in, nil) == nil {
		a.infoDigest = digest
	}
}
