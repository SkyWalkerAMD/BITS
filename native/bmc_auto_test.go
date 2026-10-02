package bits

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

const autoGUID = "12345678-1234-5678-9abc-0123456789ab"

type autoPowerFixture struct {
	mu                                  sync.Mutex
	guid                                string
	fail                                bool
	identityCalls, statusCalls, onCalls int
	duringIdentity                      func()
}

func (f *autoPowerFixture) Identity(context.Context, BMCBinding) (string, error) {
	f.mu.Lock()
	f.identityCalls++
	hook := f.duringIdentity
	f.mu.Unlock()
	if hook != nil {
		hook()
	}
	if f.fail {
		return "", errors.New("fixture")
	}
	return f.guid, nil
}
func (f *autoPowerFixture) Status(context.Context, BMCBinding) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.statusCalls++
	return "off", nil
}
func (f *autoPowerFixture) On(context.Context, BMCBinding) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.onCalls++
	return nil
}
func autoProfileFixture() BMCProfile {
	return BMCProfile{Name: "default", Enabled: true, Networks: []string{"192.168.40.0/24"}, NodePrefix: "N", Username: "operator", Password: "AUTO-FIXTURE-SECRET", Cipher: 17}
}
func autoDiscoveryFixture() BMCDiscovery {
	return BMCDiscovery{Status: "ready", GUID: autoGUID, Model: "FIXTURE-BOARD", Candidates: []BMCCandidate{{Address: "192.168.40.21", MAC: "02:00:00:00:00:01", Channel: 1}}}
}
func configureAutoFixture(t *testing.T, s *Server) *autoPowerFixture {
	t.Helper()
	f := &autoPowerFixture{guid: autoGUID}
	s.power.driver = f
	if e := s.autoBMC.profile(autoProfileFixture()); e != nil {
		t.Fatal(e)
	}
	if e := s.autoBMC.discovery("N1", autoDiscoveryFixture()); e != nil {
		t.Fatal(e)
	}
	return f
}
func TestAutoBMCReadOnlyBindingPersistenceAndOverview(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	p := autoProfileFixture()
	d := autoDiscoveryFixture()
	f := &autoPowerFixture{guid: autoGUID}
	s.power.driver = f
	if e := admin.JSON(ctx, "POST", "/api/v1/dispatch/bmc-profiles", "", p, nil); e != nil {
		t.Fatal(e)
	}
	if e := node.JSON(ctx, "POST", "/node/v1/bmc-discovery", "", d, nil); e != nil {
		t.Fatal(e)
	}
	s.autoBindBMC(ctx, "N1")
	s.autoBindBMC(ctx, "N1")
	if f.identityCalls != 1 || f.statusCalls != 1 || f.onCalls != 0 {
		t.Fatal("binding sent extra command", f)
	}
	b := s.power.bindings["N1"]
	if b.ExpectedGUID != autoGUID || b.Profile != p.Name || b.Address != d.Candidates[0].Address {
		t.Fatal("binding lost identity")
	}
	s.power.probe(ctx, b)
	var overview struct{ Nodes []Node }
	if e := admin.JSON(ctx, "GET", "/api/v1/overview", "", nil, &overview); e != nil {
		t.Fatal(e)
	}
	if len(overview.Nodes) != 1 || overview.Nodes[0].Power == nil || !overview.Nodes[0].Power.Configured || overview.Nodes[0].Power.State != "off" {
		t.Fatal("overview omitted power state", overview)
	}
	var profiles []BMCProfile
	if e := admin.JSON(ctx, "GET", "/api/v1/dispatch/bmc-profiles", "", nil, &profiles); e != nil {
		t.Fatal(e)
	}
	raw, _ := json.Marshal(profiles)
	if strings.Contains(string(raw), p.Password) || len(profiles) != 1 || profiles[0].Password != "" {
		t.Fatal("profile disclosed credential")
	}
	info, e := os.Stat(s.autoBMC.path)
	if e != nil || info.Mode().Perm() != 0600 {
		t.Fatal("auto configuration is not private", e)
	}
	restarted := NewServer(s.Store, s.Config)
	if e = restarted.LoadPower(); e != nil {
		t.Fatal(e)
	}
	restarted.power.driver = f
	restarted.autoBindBMC(ctx, "N1")
	if f.identityCalls != 1 || restarted.power.bindings["N1"].ExpectedGUID != autoGUID {
		t.Fatal("restart rebound existing BMC")
	}
}
func TestAutoBMCFailedIdentityIsNotRetriedByPollingOrRestart(t *testing.T) {
	for _, kind := range []string{"mismatch", "unreachable"} {
		t.Run(kind, func(t *testing.T) {
			s, _, _ := testServer(t)
			f := configureAutoFixture(t, s)
			if kind == "mismatch" {
				f.guid = "22345678-1234-5678-9abc-0123456789ab"
			} else {
				f.fail = true
			}
			s.autoBindBMC(context.Background(), "N1")
			for i := 0; i < 3; i++ {
				_ = s.autoBMC.discovery("N1", autoDiscoveryFixture())
				s.autoBindBMC(context.Background(), "N1")
			}
			r := NewServer(s.Store, s.Config)
			if e := r.LoadPower(); e != nil {
				t.Fatal(e)
			}
			r.power.driver = f
			r.autoBindBMC(context.Background(), "N1")
			if f.identityCalls != 1 || f.statusCalls != 0 || f.onCalls != 0 || r.power.Snapshot()["N1"].Configured {
				t.Fatal("failed identity retried or bound")
			}
			if r.autoBMC.view("N1").State != "failed" {
				t.Fatal("failure reason lost")
			}
		})
	}
}
func TestAutoBMCMatchingIsUniqueAndExplicit(t *testing.T) {
	for _, kind := range []string{"disabled", "wrong-network", "wrong-model", "wrong-prefix", "overlap", "two-addresses", "stale", "shared-address", "already-bound-address", "missing-guid"} {
		t.Run(kind, func(t *testing.T) {
			s, _, _ := testServer(t)
			f := configureAutoFixture(t, s)
			p := autoProfileFixture()
			d := autoDiscoveryFixture()
			switch kind {
			case "disabled":
				p.Enabled = false
			case "wrong-network":
				p.Networks = []string{"192.168.41.0/24"}
			case "wrong-model":
				p.Model = "DIFFERENT"
			case "wrong-prefix":
				p.NodePrefix = "OTHER"
			case "overlap":
				p.Name = "second"
			case "two-addresses":
				d.Candidates = append(d.Candidates, BMCCandidate{Address: "192.168.40.22", MAC: "02:00:00:00:00:02", Channel: 8})
			case "shared-address":
				readyNode(t, s.Store, "N2")
				if e := s.autoBMC.discovery("N2", d); e != nil {
					t.Fatal(e)
				}
			case "already-bound-address":
				readyNode(t, s.Store, "N2")
				if e := s.power.Bind(BMCBinding{Node: "N2", Address: d.Candidates[0].Address, Username: p.Username, Password: p.Password, Cipher: p.Cipher}); e != nil {
					t.Fatal(e)
				}
			case "missing-guid":
				d.Status = "identity_missing"
				d.GUID = ""
			}
			if e := s.autoBMC.profile(p); e != nil {
				t.Fatal(e)
			}
			if e := s.autoBMC.discovery("N1", d); e != nil {
				t.Fatal(e)
			}
			if kind == "stale" {
				v := s.autoBMC.data.Discoveries["N1"]
				v.Received = time.Now().Add(-25 * time.Hour).Format(time.RFC3339Nano)
				s.autoBMC.data.Discoveries["N1"] = v
			}
			s.autoBindBMC(context.Background(), "N1")
			if f.identityCalls != 0 || s.power.Snapshot()["N1"].Configured {
				t.Fatal("ambiguous or out-of-scope discovery used credentials", kind)
			}
		})
	}
}
func TestAutoBMCDoesNotOverrideManualOrActiveNode(t *testing.T) {
	for _, kind := range []string{"manual", "active", "disabled", "profile-changed-during-query", "manual-during-query"} {
		t.Run(kind, func(t *testing.T) {
			s, _, _ := testServer(t)
			f := configureAutoFixture(t, s)
			manual := BMCBinding{Node: "N1", Address: "192.168.42.9", Username: "operator", Password: "MANUAL-FIXTURE", Cipher: 17}
			switch kind {
			case "manual":
				if e := s.power.Bind(manual); e != nil {
					t.Fatal(e)
				}
			case "active":
				s.Store.Heartbeat("N1", Version)
				b, e := s.Store.Create(testPlan("N1"))
				if e != nil {
					t.Fatal(e)
				}
				if _, e = s.Store.Arm(b.ID); e != nil {
					t.Fatal(e)
				}
			case "disabled":
				s.Store.db.Exec("UPDATE nodes SET disabled=1 WHERE id='N1'")
			case "profile-changed-during-query":
				f.duringIdentity = func() {
					p := autoProfileFixture()
					p.Enabled = false
					if e := s.autoBMC.profile(p); e != nil {
						t.Fatal(e)
					}
				}
			case "manual-during-query":
				f.duringIdentity = func() {
					s.Store.mu.Lock()
					defer s.Store.mu.Unlock()
					if e := s.power.Bind(manual); e != nil {
						t.Fatal(e)
					}
				}
			}
			s.autoBindBMC(context.Background(), "N1")
			b := s.power.bindings["N1"]
			if b.Profile != "" || f.onCalls != 0 {
				t.Fatal("overrode existing or in-flight mapping")
			}
			if strings.HasPrefix(kind, "manual") && b.Address != manual.Address {
				t.Fatal("manual mapping changed")
			}
		})
	}
}
func TestAutoBMCNodeScopeAndPrivateTemplateBoundary(t *testing.T) {
	s, admin, node := testServer(t)
	ctx := context.Background()
	for _, path := range []string{"bmc-profiles", "bmc-discoveries", "bmc-retry"} {
		if e := node.JSON(ctx, "GET", "/api/v1/dispatch/"+path, "", nil, new(any)); e == nil {
			t.Fatal("node accessed admin BMC API")
		}
	}
	for _, network := range []string{"0.0.0.0/0", "127.0.0.0/8", "8.8.8.0/24", "169.254.0.0/16", "192.168.40.21/24", "::/0"} {
		p := autoProfileFixture()
		p.Networks = []string{network}
		if validateProfile(p) == nil {
			t.Fatal("unsafe profile network", network)
		}
	}
	bad := map[string]any{"status": "ready", "node": "N2", "guid": autoGUID, "command": "rmal"}
	if e := node.JSON(ctx, "POST", "/node/v1/bmc-discovery", "", bad, nil); e == nil {
		t.Fatal("unknown discovery fields accepted")
	}
	if e := admin.JSON(ctx, "POST", "/api/v1/dispatch/bmc-profiles", "", autoProfileFixture(), nil); e != nil {
		t.Fatal(e)
	}
	if e := node.JSON(ctx, "POST", "/node/v1/bmc-discovery", "", autoDiscoveryFixture(), nil); e != nil {
		t.Fatal(e)
	}
	if _, ok := s.autoBMC.data.Discoveries["N1"]; !ok {
		t.Fatal("authenticated node report missing")
	}
	if len(s.autoBMC.data.Discoveries) != 1 {
		t.Fatal("cross-node record created")
	}
	p := autoProfileFixture()
	p.Password = ""
	p.Model = "FIXTURE-BOARD"
	if e := s.autoBMC.profile(p); e != nil {
		t.Fatal(e)
	}
	if s.autoBMC.data.Profiles[p.Name].Password != autoProfileFixture().Password {
		t.Fatal("blank edit erased secret")
	}
}
func TestLocalBMCParsersAndAgentDiscoveryCache(t *testing.T) {
	raw := "System GUID : " + strings.ToUpper(autoGUID) + "\nSystem GUID Timestamp : fixture\n"
	if parseGUID(raw) != autoGUID {
		t.Fatal("GUID parsing")
	}
	for _, g := range []string{"00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff", "../../fake"} {
		if parseGUID("System GUID : "+g) != "" {
			t.Fatal("invalid GUID")
		}
	}
	fields := ipmiFields("IP Address Source : DHCP Address\nIP Address : 192.168.40.21\nMAC Address : 02:00:00:00:00:01\n")
	if fields["IP Address"] != "192.168.40.21" {
		t.Fatal("read address source instead of address")
	}
	s, _, node := testServer(t)
	calls := 0
	a := &Agent{Client: node, DiscoverBMC: func(context.Context) BMCDiscovery { calls++; return autoDiscoveryFixture() }}
	a.reportBMC(context.Background())
	a.reportBMC(context.Background())
	if calls != 1 || s.autoBMC.view("N1").State != "no_profile" {
		t.Fatal("discovery was not cached independently of credentials")
	}
}

func TestAutoBMCInterruptedIntentNeedsExplicitRetry(t *testing.T) {
	s, _, _ := testServer(t)
	f := configureAutoFixture(t, s)
	_, choice := s.autoBMC.choose("N1")
	data := s.autoBMC.copyData()
	data.Attempts["N1"] = autoAttempt{Fingerprint: choice.Fingerprint, At: time.Now().Add(-2 * time.Minute).Format(time.RFC3339Nano), State: "checking"}
	if err := s.autoBMC.save(data); err != nil {
		t.Fatal(err)
	}
	restarted := NewServer(s.Store, s.Config)
	if err := restarted.LoadPower(); err != nil {
		t.Fatal(err)
	}
	restarted.power.driver = f
	restarted.autoBindBMC(context.Background(), "N1")
	if f.identityCalls != 0 || restarted.autoBMC.view("N1").State != "interrupted" {
		t.Fatal("restart retried an unconfirmed identity request")
	}
	if err := restarted.autoBMC.retry("N1"); err != nil {
		t.Fatal(err)
	}
	restarted.autoBindBMC(context.Background(), "N1")
	if f.identityCalls != 1 || f.onCalls != 0 || !restarted.power.Snapshot()["N1"].Configured {
		t.Fatal("explicit retry failed or powered on the node")
	}
	if restarted.autoBMC.retry("N1") == nil {
		t.Fatal("retry rate limit was bypassed")
	}
}
