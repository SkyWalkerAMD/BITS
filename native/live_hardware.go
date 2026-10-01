package bits

import (
	"encoding/json"
	"errors"
	"unicode"
	"unicode/utf8"
)

// Fixed display-only fields. No console text, info sections, timing registers,
// command paths or licensing material can cross this wire format.
type LiveHardware struct {
	Schema  string       `json:"schema"`
	Vendor  string       `json:"vendor"`
	Family  int          `json:"family"`
	Sockets []LiveSocket `json:"sockets"`
	Cores   []LiveCore   `json:"cores"`
}
type LiveSocket struct {
	ID       int              `json:"id"`
	TempC    *float64         `json:"temp_c"`
	TjMax    *float64         `json:"tjmax_c"`
	VID      *float64         `json:"vid_v"`
	MHz      *float64         `json:"core_mhz"`
	BaseMHz  *float64         `json:"base_mhz"`
	PackageW *float64         `json:"package_w"`
	Extra    *LiveSocketExtra `json:"extra,omitempty"`
}
type LiveSocketExtra struct {
	Model         string   `json:"model,omitempty"`
	PhysicalCores *float64 `json:"physical_cores"`
	Threads       *float64 `json:"threads"`
	VCCIN         *float64 `json:"vccin_v"`
	MeshMHz       *float64 `json:"mesh_mhz"`
	MemoryMTs     *float64 `json:"memory_mts"`
	DIMMs         *float64 `json:"dimms"`
	MemoryUsedGB  *float64 `json:"memory_used_gb"`
	MemoryTotalGB *float64 `json:"memory_total_gb"`
	MemoryUsedPct *float64 `json:"memory_used_pct"`
	MemoryTempC   *float64 `json:"memory_temp_c"`
	DRAMW         *float64 `json:"dram_w"`
	PC2           *float64 `json:"pc2_pct"`
	PC6           *float64 `json:"pc6_pct"`
}
type LiveCore struct {
	CPU    int      `json:"cpu"`
	Socket int      `json:"socket"`
	MHz    *float64 `json:"mhz"`
	TempC  *float64 `json:"temp_c"`
	VID    *float64 `json:"vid_v"`
	C0     *float64 `json:"c0_pct"`
	C6     *float64 `json:"c6_pct"`
}

func validHardware(h *LiveHardware, extraObserved string) error {
	if h == nil {
		return nil
	} // Older agents still provide summary-only data.
	bad := errors.New("invalid live hardware projection")
	if h.Schema != "bits-live-hardware-v1" || (h.Vendor != "GenuineIntel" && h.Vendor != "AuthenticAMD") || h.Family < 0 || h.Family > 65535 || len(h.Sockets) < 1 || len(h.Sockets) > 256 || len(h.Cores) > 8192 {
		return bad
	}
	metric := func(p *float64, low, high float64) bool { return p == nil || finite(*p, low, high) }
	text := func(s string) bool {
		if len(s) > 256 || !utf8.ValidString(s) {
			return false
		}
		for _, c := range s {
			if unicode.IsControl(c) || unicode.In(c, unicode.Cf) {
				return false
			}
		}
		return true
	}
	sockets := map[int]bool{}
	for _, s := range h.Sockets {
		if s.ID < 0 || s.ID > 65535 || sockets[s.ID] {
			return bad
		}
		sockets[s.ID] = true
		if !metric(s.TempC, -273.15, 300) || !metric(s.TjMax, 0, 300) || !metric(s.VID, 0, 10) || !metric(s.MHz, 0, 100000) || !metric(s.BaseMHz, 0, 100000) || !metric(s.PackageW, 0, 1e9) {
			return bad
		}
		if x := s.Extra; x != nil {
			if extraObserved == "" || !text(x.Model) || !metric(x.PhysicalCores, 1, 8192) || !metric(x.Threads, 1, 8192) || !metric(x.VCCIN, 0, 10) || !metric(x.MeshMHz, 0, 100000) || !metric(x.MemoryMTs, 0, 1e6) || !metric(x.DIMMs, 0, 4096) || !metric(x.MemoryUsedGB, 0, 1e9) || !metric(x.MemoryTotalGB, 0, 1e9) || !metric(x.MemoryUsedPct, 0, 100) || !metric(x.MemoryTempC, -273.15, 300) || !metric(x.DRAMW, 0, 1e9) || !metric(x.PC2, 0, 100) || !metric(x.PC6, 0, 100) {
				return bad
			}
		}
	}
	cpus := map[int]bool{}
	for _, c := range h.Cores {
		if c.CPU < 0 || c.CPU > 1048575 || cpus[c.CPU] || !sockets[c.Socket] || !metric(c.MHz, 0, 100000) || !metric(c.TempC, -273.15, 300) || !metric(c.VID, 0, 10) || !metric(c.C0, 0, 100) || !metric(c.C6, 0, 100) {
			return bad
		}
		cpus[c.CPU] = true
	}
	return nil
}

func copyLiveSample(v *LiveSample, hardware bool) *LiveSample {
	if v == nil {
		return nil
	}
	copy := *v
	if !hardware {
		copy.Hardware = nil
	}
	raw, _ := json.Marshal(copy)
	var out LiveSample
	json.Unmarshal(raw, &out)
	return &out
}
