// Package bits implements the independent BITS protocol. It has no OCRUN queue
// keys, shell dispatch, Redis client, or rsync dependency.
package bits

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"time"
)

const Version = "0.4.0-alpha.6"

var nameRE = regexp.MustCompile("^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
var idRE = regexp.MustCompile("^[0-9a-f]{32}$")
var digestRE = regexp.MustCompile("^[0-9a-f]{64}$")
var toolRE = regexp.MustCompile("^p95-(no|avx|fma3|avx512)_m[124]$")
var artifactRE = regexp.MustCompile("^(telemetry-[0-9]{5}[.]jsonl|step-[0-9]{3}[.]log|monitor[.](mon|xlsx)|report[.](json|html))$")

var Tools = []string{"stress", "stress-ng", "mlc", "mbw", "cyclictest", "unixbench", "cpu2017",
	"p95-no_m1", "p95-no_m2", "p95-no_m4", "p95-avx_m1", "p95-avx_m2", "p95-avx_m4",
	"p95-fma3_m1", "p95-fma3_m2", "p95-fma3_m4", "p95-avx512_m1", "p95-avx512_m2", "p95-avx512_m4"}

type Step struct {
	ID      string "json:\"id\""
	Tool    string "json:\"tool\""
	Seconds int    "json:\"seconds\""
}
type Plan struct {
	Node  string "json:\"node\""
	Label string "json:\"label\""
	Steps []Step "json:\"steps\""
}
type Artifact struct {
	SHA256 string "json:\"sha256\""
	Bytes  int64  "json:\"bytes\""
}
type Result struct {
	Execution string            "json:\"execution\""
	Quality   string            "json:\"quality\""
	Report    string            "json:\"report\""
	Error     string            "json:\"error,omitempty\""
	Steps     []json.RawMessage "json:\"steps,omitempty\""
}
type Batch struct {
	ID         string              "json:\"id\""
	Plan       Plan                "json:\"plan\""
	State      string              "json:\"state\""
	Attempt    string              "json:\"attempt,omitempty\""
	Created    string              "json:\"created_at\""
	Expires    string              "json:\"start_expires_at,omitempty\""
	Result     Result              "json:\"result\""
	Sequence   int64               "json:\"sequence\""
	Cancel     bool                "json:\"cancel_requested\""
	Artifacts  map[string]Artifact "json:\"artifacts,omitempty\""
	ReceiptSHA string              "json:\"receipt_sha256,omitempty\""
	Power      *PowerAttempt       `json:"power,omitempty"`
	GroupID    string              `json:"group_id,omitempty"`
}
type Node struct {
	ID       string       "json:\"id\""
	LastSeen string       "json:\"last_seen,omitempty\""
	Agent    string       "json:\"agent_version,omitempty\""
	Disabled bool         "json:\"disabled\""
	Power    *PowerStatus `json:"power,omitempty"`
}
type Event struct {
	At     string "json:\"at\""
	Kind   string "json:\"kind\""
	Detail string "json:\"detail\""
}

func UTC() string { return time.Now().UTC().Format(time.RFC3339Nano) }
func Random(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func Digest(b []byte) string  { d := sha256.Sum256(b); return hex.EncodeToString(d[:]) }
func ValidName(s string) bool { return nameRE.MatchString(s) && s != "." && s != ".." }
func ValidatePlan(p *Plan) error {
	if !ValidName(p.Node) || !ValidName(p.Label) {
		return errors.New("节点和批次名称须为 1–96 位字母、数字、点、下划线或连字符")
	}
	if len(p.Steps) == 0 || len(p.Steps) > 128 {
		return errors.New("每批次需包含 1–128 个步骤")
	}
	total := 0
	for i := range p.Steps {
		s := &p.Steps[i]
		allowed := toolRE.MatchString(s.Tool)
		for _, tool := range Tools {
			allowed = allowed || s.Tool == tool
		}
		if !allowed {
			return fmt.Errorf("步骤 %d：未知压测项目 %q；批次未开始，请修正名称", i+1, s.Tool)
		}
		if s.Seconds < 1 || s.Seconds > 31*86400 {
			return fmt.Errorf("步骤 %d：时长必须为 1–2678400 秒", i+1)
		}
		total += s.Seconds
		s.ID = fmt.Sprintf("step-%03d", i+1)
	}
	if total > 31*86400 {
		return errors.New("单批次总预算最多 31 天，请明确拆分更长任务")
	}
	return nil
}
func ValidateArtifacts(m map[string]Artifact) error {
	if len(m) < 1 || len(m) > 4096 {
		return errors.New("invalid artifact count")
	}
	var total int64
	for name, v := range m {
		if !artifactRE.MatchString(name) || !digestRE.MatchString(v.SHA256) || v.Bytes < 0 || v.Bytes > 16<<30 {
			return errors.New("invalid artifact name, size or SHA-256")
		}
		total += v.Bytes
	}
	if total > 64<<30 {
		return errors.New("batch artifacts exceed 64 GiB; split future batches")
	}
	return nil
}
func IsTerminal(state string) bool {
	return state == "delivered" || state == "closed_incomplete" || state == "cancelled"
}
