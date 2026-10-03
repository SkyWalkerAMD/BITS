package bits

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
)

const WorkerRoot = "/opt/bits/native/0.4.1/worker"

type LocalRun struct {
	Batch        Batch               "json:\"batch\""
	Phase        string              "json:\"phase\""
	Sequence     int64               "json:\"sequence\""
	Acknowledged int64               "json:\"acknowledged_sequence\""
	Result       Result              "json:\"result\""
	Artifacts    map[string]Artifact "json:\"artifacts,omitempty\""
}
type Agent struct {
	Config         NodeConfig
	Client         *Client
	Data           string
	idleSince      time.Time
	wakeHold       string
	infoDigest     string
	lastLive       time.Time
	livePhase      string
	monitorEnabled bool
	monitorCancel  context.CancelFunc
	monitorDone    chan error
	monitorRetry   time.Time
	DiscoverBMC    func(context.Context) BMCDiscovery
	bmcDiscovery   *BMCDiscovery
	bmcNextProbe   time.Time
	bmcNextSend    time.Time
	// Production always uses the installed, verified local worker. This seam
	// permits isolated lifecycle tests without running hardware tools.
	Worker func(context.Context, string, string, func()) error
}

func NewAgent(cfg NodeConfig, data string) (*Agent, error) {
	client, err := NewClient(cfg.URL, cfg.Token, cfg.Node, cfg.CA)
	if err != nil {
		return nil, err
	}
	if err = PrivateDir(data); err != nil {
		return nil, err
	}
	a := &Agent{Config: cfg, Client: client, Data: data}
	var hold struct {
		ID string `json:"id"`
	}
	if _, e := os.Lstat(filepath.Join(data, "wake-hold.json")); e == nil {
		if e = ReadJSON(filepath.Join(data, "wake-hold.json"), &hold); e != nil {
			return nil, e
		}
		if hold.ID != "" && !idRE.MatchString(hold.ID) {
			return nil, errors.New("invalid retained wake policy")
		}
		a.wakeHold = hold.ID
	} else if !os.IsNotExist(e) {
		return nil, e
	}
	a.Worker = a.runWorker
	a.DiscoverBMC = discoverBMC
	return a, nil
}
func (a *Agent) lock() (*os.File, error) {
	path := filepath.Join(a.Data, "agent.lock")
	if err := CheckFileIfExists(path); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	if err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		return nil, errors.New("another BITS agent owns this node")
	}
	return f, nil
}
func (a *Agent) runWorker(ctx context.Context, action, dir string, tick func()) error {
	var inventory map[string]string
	if err := readProgramManifest(WorkerRoot, &inventory); err != nil {
		return err
	}
	python := "/usr/libexec/platform-python"
	if _, err := os.Stat(python); err != nil {
		python = "/usr/bin/python3"
	}
	if err := TrustedProgram(python); err != nil {
		return err
	}
	cmd := exec.Command(python, "-I", "-S", "-B", WorkerRoot+"/worker.py", action, dir)
	cmd.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "LANG=C.UTF-8", "LC_ALL=C.UTF-8"}
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGTERM}
	if action == "monitor" {
		// The idle monitor keeps one bounded snapshot, not a growing log.
		cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	} else {
		log, err := os.OpenFile(filepath.Join(dir, "worker.log"), os.O_WRONLY|os.O_APPEND|os.O_CREATE|syscall.O_NOFOLLOW, 0600)
		if err != nil {
			return err
		}
		defer log.Close()
		cmd.Stdout, cmd.Stderr = log, log
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	cancelled := false
	for {
		select {
		case err := <-done:
			a.publishHardwareInfo(ctx,dir)
			return err
		case <-ticker.C:
			tick()
			// Cancel is a private durable marker, not a PID supplied by network.
			if _, err := os.Stat(filepath.Join(dir, "cancel.json")); err == nil && !cancelled {
				cmd.Process.Signal(syscall.SIGTERM)
				cancelled = true
			}
		case <-ctx.Done():
			cmd.Process.Signal(syscall.SIGTERM)
			select {
			case <-done:
				return ctx.Err()
			case <-time.After(40 * time.Second):
				// systemd KillMode=control-group is the final cleanup boundary.
				// Do not claim confirmed cleanup after escalation.
				cmd.Process.Kill()
				<-done
				return errors.New("worker stop deadline exceeded; inspect systemd cgroup before recovery")
			}
		}
	}
}
func (a *Agent) save(dir string, run *LocalRun) error {
	return AtomicJSON(filepath.Join(dir, "run.json"), run)
}
func (a *Agent) update(ctx context.Context, dir string, run *LocalRun) error {
	var result Result
	if err := ReadJSON(filepath.Join(dir, "result.json"), &result); err == nil {
		before, _ := json.Marshal(run.Result)
		after, _ := json.Marshal(result)
		if string(before) != string(after) {
			run.Result = result
			run.Sequence++
			if err = a.save(dir, run); err != nil {
				return err
			}
		}
	}
	if run.Sequence == 0 || run.Acknowledged == run.Sequence {
		return nil
	}
	err := a.Client.JSON(ctx, "POST", "/node/v1/batches/"+run.Batch.ID+"/result", run.Batch.Attempt,
		map[string]any{"sequence": run.Sequence, "result": run.Result}, nil)
	if err == nil {
		run.Acknowledged = run.Sequence
		err = a.save(dir, run)
	}
	return err
}
func (a *Agent) process(ctx context.Context, dir string, run *LocalRun, fresh bool) error {
	if fresh || run.Phase == "prepared" || run.Phase == "executing" {
		if err := a.stopMonitor(); err != nil {
			return err
		}
	} else if err := a.startMonitor(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "BITS node monitoring:", err)
	}
	base := "/node/v1/batches/" + run.Batch.ID
	a.publishLive(ctx, dir, run)
	if fresh {
		// Before any acceptance or subprocess, persist an execution intent. An
		// interrupted intent is never interpreted as permission to rerun.
		if err := a.save(dir, run); err != nil {
			return err
		}
		if err := a.Worker(ctx, "preflight", dir, func() { a.publishLive(ctx, dir, run) }); err != nil {
			run.Result = Result{Execution: "preflight_failed", Quality: "not_collected", Report: "not_generated", Error: "Node preflight failed; inspect worker.log"}
			run.Sequence = 1
			run.Phase = "blocked"
			a.save(dir, run)
			a.Client.JSON(ctx, "POST", base+"/result", run.Batch.Attempt, map[string]any{"sequence": run.Sequence, "result": run.Result}, nil)
			return err
		}
		if err := a.Client.JSON(ctx, "POST", base+"/claim", run.Batch.Attempt, nil, nil); err != nil {
			return err
		}
		run.Phase = "executing"
		if err := a.save(dir, run); err != nil {
			return err
		}
		err := a.Worker(ctx, "execute", dir, func() {
			var remote Batch
			callCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			defer cancel()
			if a.Client.JSON(callCtx, "GET", base, "", nil, &remote) == nil && remote.Cancel {
				AtomicJSON(filepath.Join(dir, "cancel.json"), map[string]bool{"cancel": true})
			}
			a.update(callCtx, dir, run)
			a.publishLive(callCtx, dir, run)
		})
		if ctx.Err() != nil {
			return ctx.Err()
		}
		// Even execution failure proceeds to evidence finalization, never to
		// another execution attempt. The worker supplies the separate outcome.
		if err != nil {
			fmt.Fprintln(os.Stderr, "BITS workload ended with an error; finalizing available evidence:", err)
		}
		run.Phase = "finalizing"
		if err = a.save(dir, run); err != nil {
			return err
		}
	} else if run.Phase == "prepared" || run.Phase == "executing" {
		if err := a.Worker(ctx, "recover", dir, func() {}); err != nil {
			var recovered Result
			if e := ReadJSON(filepath.Join(dir, "result.json"), &recovered); e == nil && recovered.Execution == "preflight_failed" {
				run.Phase = "blocked"
				if e = a.save(dir, run); e != nil {
					return e
				}
				return a.update(ctx, dir, run)
			}
			// Keep the recovery phase durable until process cleanup is confirmed.
			return err
		}
		run.Phase = "finalizing"
		if err := a.save(dir, run); err != nil {
			return err
		}
	}
	if run.Phase == "blocked" {
		if err := a.startMonitor(ctx); err != nil {
			fmt.Fprintln(os.Stderr, "BITS node monitoring:", err)
		}
		// Retry only publication of the existing failure. No implicit task retry.
		return a.update(ctx, dir, run)
	}
	if run.Phase == "finalizing" {
		if err := a.startMonitor(ctx); err != nil {
			fmt.Fprintln(os.Stderr, "BITS node monitoring:", err)
		}
		a.publishLive(ctx, dir, run)
		if err := a.Worker(ctx, "report", dir, func() { a.update(ctx, dir, run); a.publishLive(ctx, dir, run) }); err != nil {
			a.update(ctx, dir, run)
			return err
		}
		if err := a.update(ctx, dir, run); err != nil {
			return err
		}
		if run.Result.Report != "generated" {
			return errors.New("reports not generated; execution will not be repeated")
		}
		if err := ReadJSON(filepath.Join(dir, "artifacts.json"), &run.Artifacts); err != nil {
			return err
		}
		if err := ValidateArtifacts(run.Artifacts); err != nil {
			return err
		}
		run.Phase = "delivering"
		if err := a.save(dir, run); err != nil {
			return err
		}
	}
	if run.Phase == "delivering" {
		a.publishLive(ctx, dir, run)
		if err := a.update(ctx, dir, run); err != nil {
			return err
		}
		if err := a.Client.JSON(ctx, "POST", base+"/manifest", run.Batch.Attempt, run.Artifacts, nil); err != nil {
			return err
		}
		for name, meta := range run.Artifacts {
			a.publishLive(ctx, dir, run)
			file := filepath.Join(dir, "evidence", name)
			actual, err := HashFile(file)
			if err != nil {
				return err
			}
			if actual != meta {
				return errors.New("sealed local artifact changed: " + name)
			}
			path := base + "/files/" + name
			resp, err := a.Client.Request(ctx, "HEAD", path, run.Batch.Attempt, nil, 0)
			if err != nil {
				return err
			}
			offset, err := strconv.ParseInt(resp.Header.Get("X-BITS-Offset"), 10, 64)
			resp.Body.Close()
			if err != nil || offset < 0 || offset > meta.Bytes {
				return errors.New("invalid remote offset")
			}
			f, err := os.OpenFile(file, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
			if err != nil {
				return err
			}
			// Empty files also need an explicit zero-byte upload.
			for offset < meta.Bytes || meta.Bytes == 0 {
				a.publishLive(ctx, dir, run)
				size := meta.Bytes - offset
				if size > 4<<20 {
					size = 4 << 20
				}
				section := io.NewSectionReader(f, offset, size)
				resp, e := a.Client.Request(ctx, "PUT", path+"?offset="+strconv.FormatInt(offset, 10), run.Batch.Attempt, section, size)
				if e != nil {
					f.Close()
					return e
				}
				io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
				resp.Body.Close()
				offset += size
				if meta.Bytes == 0 {
					break
				}
			}
			f.Close()
			h := sha256.New()
			if err = a.Client.Readback(ctx, path, run.Batch.Attempt, meta.Bytes, h); err != nil {
				return err
			}
			if hex.EncodeToString(h.Sum(nil)) != meta.SHA256 {
				return errors.New("remote readback hash differs: " + name)
			}
		}
		var completed Batch
		if err := a.Client.JSON(ctx, "POST", base+"/commit", run.Batch.Attempt, run.Artifacts, &completed); err != nil {
			return err
		}
		var receiptBytes limitedBuffer
		if err := a.Client.Download(ctx, base+"/receipt", run.Batch.Attempt, &receiptBytes); err != nil {
			return err
		}
		if completed.State != "delivered" || Digest(receiptBytes.Bytes) != completed.ReceiptSHA {
			return errors.New("completion receipt verification failed")
		}
		if err := Atomic(filepath.Join(dir, "receipt.json"), receiptBytes.Bytes); err != nil {
			return err
		}
		run.Phase = "done"
		run.Batch = completed
		return a.save(dir, run)
	}
	return nil
}

type limitedBuffer struct{ Bytes []byte }

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if len(b.Bytes)+len(p) > 2<<20 {
		return 0, errors.New("receipt too large")
	}
	b.Bytes = append(b.Bytes, p...)
	return len(p), nil
}
func (a *Agent) once(ctx context.Context) error {
	runs := filepath.Join(a.Data, "runs")
	if err := os.Mkdir(runs, 0700); err != nil && !os.IsExist(err) {
		return err
	}
	if err := PrivateDir(runs); err != nil {
		return err
	}
	entries, err := os.ReadDir(runs)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !idRE.MatchString(entry.Name()) {
			return errors.New("unexpected local run directory")
		}
		dir := filepath.Join(runs, entry.Name())
		var run LocalRun
		if err = ReadJSON(filepath.Join(dir, "run.json"), &run); err != nil {
			return err
		}
		if run.Batch.ID != entry.Name() || run.Batch.Plan.Node != a.Config.Node {
			return errors.New("local run ownership differs")
		}
		if run.Phase != "done" {
			// Explicit operator closure frees the node while keeping all local
			// evidence and the recorded failure.
			var remote Batch
			if e := a.Client.JSON(ctx, "GET", "/node/v1/batches/"+run.Batch.ID, "", nil, &remote); e == nil && (remote.State == "deleted" || remote.State == "closed_incomplete" || (remote.State == "cancelled" && (run.Phase == "prepared" || run.Phase == "blocked"))) {
				if _, e = os.Stat(filepath.Join(dir, "execution.json")); e == nil {
					if e = a.stopMonitor(); e != nil {
						return e
					}
					if e = a.Worker(ctx, "recover", dir, func() {}); e != nil {
						return e
					}
				}
				run.Phase = "done"
				if remote.State == "deleted" {
					run.Batch.State = "deleted"
				} else {
					run.Batch = remote
				}
				if err = a.save(dir, &run); err != nil {
					return err
				}
				continue
			}
			return a.process(ctx, dir, &run, false)
		}
	}
	var pending struct {
		Batch    *Batch "json:\"batch\""
		WakeHold string `json:"wake_hold"`
	}
	if err = a.Client.JSON(ctx, "GET", "/node/v1/poll", "", nil, &pending); err != nil {
		return err
	}
	if err = a.applyWakeHold(pending.WakeHold); err != nil {
		return err
	}
	if pending.Batch == nil {
		// Discover only while idle, never competing with stress telemetry.
		a.reportBMC(ctx)
		return a.startMonitor(ctx)
	}
	if err = ValidatePlan(&pending.Batch.Plan); err != nil {
		return err
	}
	if pending.Batch.Plan.Node != a.Config.Node || !idRE.MatchString(pending.Batch.ID) || !idRE.MatchString(pending.Batch.Attempt) {
		return errors.New("invalid explicit assignment")
	}
	dir := filepath.Join(runs, pending.Batch.ID)
	if err = os.Mkdir(dir, 0700); err != nil {
		return errors.New("batch already has local evidence; it will not be run again")
	}
	run := LocalRun{Batch: *pending.Batch, Phase: "prepared", Result: pending.Batch.Result}
	a.idleSince = time.Time{}
	if err = AtomicJSON(filepath.Join(dir, "request.json"), map[string]any{"batch": pending.Batch, "serial": a.Config.Serial}); err != nil {
		return err
	}
	return a.process(ctx, dir, &run, true)
}
func (a *Agent) Run(ctx context.Context, once bool) error {
	lock, err := a.lock()
	if err != nil {
		return err
	}
	defer lock.Close()
	a.monitorEnabled = !once
	defer func() { a.stopMonitor(); a.monitorEnabled = false }()
	heartbeatCtx, stopHeartbeat := context.WithCancel(ctx)
	heartbeatDone := make(chan struct{})
	go func() { defer close(heartbeatDone); a.heartbeat(heartbeatCtx) }()
	defer func() { stopHeartbeat(); <-heartbeatDone }()
	for {
		err = a.once(ctx)
		if once {
			return err
		}
		pause := 5 * time.Second
		if err != nil {
			fmt.Fprintln(os.Stderr, "BITS:", err)
			a.idleSince = time.Time{}
			pause = 30 * time.Second
		} else if err = a.shutdownIfIdle(ctx); err != nil {
			fmt.Fprintln(os.Stderr, "BITS shutdown:", err)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(pause):
		}
	}
}
func (a *Agent) shutdownIfIdle(ctx context.Context) error {
	if a.Config.KeepOn || a.wakeHold != "" {
		a.idleSince = time.Time{}
		return nil
	}
	entries, err := os.ReadDir(filepath.Join(a.Data, "runs"))
	if err != nil {
		return err
	}
	found := false
	for _, entry := range entries {
		var run LocalRun
		if err = ReadJSON(filepath.Join(a.Data, "runs", entry.Name(), "run.json"), &run); err != nil {
			return err
		}
		if run.Phase != "done" {
			a.idleSince = time.Time{}
			return nil
		}
		if run.Batch.State == "delivered" && run.Result.Execution == "completed" {
			found = true
		}
		if run.Batch.State != "closed_incomplete" && run.Result.Execution != "completed" {
			a.idleSince = time.Time{}
			return nil
		}
	}
	if !found {
		return nil
	}
	if err = TrustedProgram("/usr/bin/who"); err != nil {
		return err
	}
	whoCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	users, err := exec.CommandContext(whoCtx, "/usr/bin/who").Output()
	if err != nil || len(users) > 0 {
		a.idleSince = time.Time{}
		return err
	}
	if a.idleSince.IsZero() {
		a.idleSince = time.Now()
		return nil
	}
	if time.Since(a.idleSince) < 30*time.Minute {
		return nil
	}
	if err = TrustedProgram("/usr/bin/systemctl"); err != nil {
		return err
	}
	// No pending evidence and no login session. Never power off due only to
	// connectivity loss, a failed batch or time since machine boot.
	return exec.CommandContext(ctx, "/usr/bin/systemctl", "poweroff").Run()
}
func (a *Agent) applyWakeHold(id string) error {
	if id != "" && !idRE.MatchString(id) {
		return errors.New("invalid center wake policy")
	}
	if id == a.wakeHold {
		return nil
	}
	if err := AtomicJSON(filepath.Join(a.Data, "wake-hold.json"), map[string]string{"id": id}); err != nil {
		return err
	}
	a.wakeHold = id
	a.idleSince = time.Time{}
	return nil
}
func readProgramManifest(root string, inventory *map[string]string) error {
	if err := TrustedDirectory(root); err != nil {
		return err
	}
	if err := TrustedProgramFile(filepath.Join(root, "MANIFEST.json")); err != nil {
		return err
	}
	raw, err := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	if err != nil {
		return err
	}
	if err = json.Unmarshal(raw, inventory); err != nil {
		return err
	}
	for _, required := range []string{"worker.py", "data_api.py", "workload.py", "sckocp_api/interface.py", "sckocp_api/provider.py", "sckocp_api/security.py"} {
		if !digestRE.MatchString((*inventory)[required]) {
			return errors.New("required worker file missing from inventory")
		}
	}
	for name, expected := range *inventory {
		if filepath.IsAbs(name) || filepath.Clean(name) != name || stringsUnsafe(name) {
			return errors.New("invalid program manifest entry")
		}
		path := filepath.Join(root, name)
		if err = TrustedProgramFile(path); err != nil {
			return err
		}
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if Digest(b) != expected {
			return errors.New("installed worker changed: " + name)
		}
	}
	return nil
}
func VerifyWorker() error {
	var inventory map[string]string
	return readProgramManifest(WorkerRoot, &inventory)
}
func stringsUnsafe(s string) bool {
	for _, part := range filepath.SplitList(s) {
		if part == ".." {
			return true
		}
	}
	return s == ".." || len(s) > 512 || filepath.IsAbs(s) || len(s) >= 3 && s[:3] == "../"
}
func TrustedDirectory(path string) error {
	path = filepath.Clean(path)
	for {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		st, ok := info.Sys().(*syscall.Stat_t)
		if !ok || !info.IsDir() || st.Uid != 0 || info.Mode().Perm()&0022 != 0 {
			return errors.New("untrusted program directory: " + path)
		}
		next := filepath.Dir(path)
		if next == path {
			return nil
		}
		path = next
	}
}
func TrustedProgramFile(path string) error {
	if err := TrustedDirectory(filepath.Dir(path)); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || st.Uid != 0 || info.Mode().Perm()&0022 != 0 || info.Mode()&(os.ModeSetuid|os.ModeSetgid) != 0 {
		return errors.New("untrusted program: " + path)
	}
	return nil
}
func TrustedProgram(path string) error {
	// Trusted distro interpreter symlinks/hardlinks are allowed; writable data
	// and Python modules use the stricter non-symlink checks above.
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return err
	}
	if err = TrustedDirectory(filepath.Dir(path)); err != nil {
		return err
	}
	return TrustedProgramFile(resolved)
}
