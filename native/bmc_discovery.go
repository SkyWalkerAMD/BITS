package bits

import (
	"context"
	"encoding/hex"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type BMCCandidate struct {
	Address string `json:"address"`
	MAC     string `json:"mac"`
	Channel int    `json:"channel"`
}
type BMCDiscovery struct {
	Status     string         `json:"status"`
	GUID       string         `json:"guid"`
	Model      string         `json:"model"`
	Candidates []BMCCandidate `json:"candidates"`
}

func validGUID(g string) bool {
	b, e := hex.DecodeString(strings.ReplaceAll(g, "-", ""))
	return e == nil && len(b) == 16 && len(g) == 36 && g[8] == '-' && g[13] == '-' && g[18] == '-' && g[23] == '-' && g == strings.ToLower(g) && g != "00000000-0000-0000-0000-000000000000" && g != "ffffffff-ffff-ffff-ffff-ffffffffffff"
}
func ipmiFields(raw string) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(raw, "\n") {
		if k, v, ok := strings.Cut(line, ":"); ok {
			out[strings.TrimSpace(k)] = strings.TrimSpace(v)
		}
	}
	return out
}
func parseGUID(raw string) string {
	g := strings.ToLower(ipmiFields(raw)["System GUID"])
	if !validGUID(g) {
		return ""
	}
	return g
}
func validCandidate(c BMCCandidate) bool {
	ip := net.ParseIP(c.Address)
	mac, err := net.ParseMAC(c.MAC)
	return ip != nil && ip.To4() != nil && ip.IsPrivate() && c.Address == ip.String() && c.Channel >= 0 && c.Channel <= 11 && err == nil && len(mac) == 6 && mac[0]&1 == 0 && mac.String() == c.MAC && c.MAC != "00:00:00:00:00:00"
}

// Local discovery uses the kernel IPMI interface only. No network scan,
// credentials, LAN changes, user management or sckocp command is involved.
func localIPMI(ctx context.Context, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/usr/bin/ipmitool", append([]string{"-I", "open"}, args...)...)
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LC_ALL=C", "HOME=/nonexistent"}
	out := &limitedOutput{}
	cmd.Stdout = out
	err := cmd.Run()
	return string(out.data), err
}
func discoverBMC(ctx context.Context) BMCDiscovery {
	d := BMCDiscovery{Status: "unavailable", Candidates: []BMCCandidate{}}
	if model, err := os.ReadFile("/sys/class/dmi/id/board_name"); err == nil {
		d.Model = strings.TrimSpace(string(model))
		if len(d.Model) > 128 {
			d.Model = ""
		}
	}
	if TrustedProgram("/usr/bin/ipmitool") != nil {
		d.Status = "missing_tool"
		return d
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	raw, err := localIPMI(ctx, "mc", "guid")
	if err != nil {
		return d
	}
	d.GUID = parseGUID(raw)
	seen := map[string]bool{}
	for ch := 0; ch <= 11 && ctx.Err() == nil; ch++ {
		raw, err = localIPMI(ctx, "lan", "print", strconv.Itoa(ch))
		if err != nil {
			continue
		}
		fields := ipmiFields(raw)
		c := BMCCandidate{Address: fields["IP Address"], MAC: strings.ToLower(fields["MAC Address"]), Channel: ch}
		if validCandidate(c) && !seen[c.Address] {
			d.Candidates = append(d.Candidates, c)
			seen[c.Address] = true
		}
	}
	if ctx.Err() != nil {
		d.Status = "incomplete"
		return d
	}
	d.Status = "ready"
	if d.GUID == "" {
		d.Status = "identity_missing"
	} else if len(d.Candidates) == 0 {
		d.Status = "no_address"
	}
	return d
}
func (a *Agent) reportBMC(ctx context.Context) {
	if a.DiscoverBMC == nil || time.Now().Before(a.bmcNextSend) {
		return
	}
	if a.bmcDiscovery == nil || time.Now().After(a.bmcNextProbe) {
		d := a.DiscoverBMC(ctx)
		a.bmcDiscovery = &d
		a.bmcNextProbe = time.Now().Add(time.Hour)
	}
	a.bmcNextSend = time.Now().Add(time.Minute)
	call, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if a.Client.JSON(call, "POST", "/node/v1/bmc-discovery", "", a.bmcDiscovery, nil) == nil {
		a.bmcNextSend = a.bmcNextProbe
	}
}
