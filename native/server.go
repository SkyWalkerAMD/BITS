package bits

import (
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

//go:embed web/*
var webFiles embed.FS

type CenterConfig struct {
	Address  string   "json:\"address\""
	URL      string   "json:\"url\""
	Networks []string "json:\"networks\""
	Data     string   "json:\"data\""
	Cert     string   "json:\"certificate\""
	Key      string   "json:\"private_key\""
	AdminSHA string   "json:\"admin_sha256\""
}
type NodeConfig struct {
	URL    string "json:\"url\""
	Node   string "json:\"node\""
	Token  string "json:\"token\""
	CA     string "json:\"ca_pem\""
	Serial string "json:\"serial\""
	KeepOn bool   "json:\"keep_on\""
}
type AdminConfig struct {
	URL   string "json:\"url\""
	Token string "json:\"token\""
	CA    string "json:\"ca_pem\""
}
type Server struct {
	Store    *Store
	Config   CenterConfig
	mu       sync.Mutex
	sessions map[string]time.Time
	logins   map[string][]time.Time
	uploads  sync.Map
	slots    chan struct{}
	live     *LiveCache
	power    *PowerManager
	autoBMC  *AutoBMC
}

func NewServer(store *Store, cfg CenterConfig) *Server {
	return &Server{Store: store, Config: cfg, sessions: map[string]time.Time{}, logins: map[string][]time.Time{}, slots: make(chan struct{}, 64), live: NewLiveCache(), power: newPowerManager(cfg.Data), autoBMC: newAutoBMC(cfg.Data)}
}
func respond(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	json.NewEncoder(w).Encode(v)
}
func decode(r *http.Request, v any) error {
	return decodeBounded(r, v, 1<<20)
}
func decodeBounded(r *http.Request, v any, limit int64) error {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		return errors.New("application/json required")
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return errors.New("request exceeds size limit")
	}
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		return errors.New("invalid or unknown request field")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("exactly one bounded JSON request required")
	}
	return nil
}
func (s *Server) admin(r *http.Request) bool {
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if token != "" && subtle.ConstantTimeCompare([]byte(Digest([]byte(token))), []byte(s.Config.AdminSHA)) == 1 {
		return true
	}
	cookie, err := r.Cookie("bits_session")
	if err != nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	exp, ok := s.sessions[Digest([]byte(cookie.Value))]
	return ok && time.Now().Before(exp)
}
func (s *Server) login(w http.ResponseWriter, r *http.Request) error {
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	s.mu.Lock()
	now := time.Now()
	recent := []time.Time{}
	for ip, times := range s.logins {
		if len(times) == 0 || now.Sub(times[len(times)-1]) > time.Minute {
			delete(s.logins, ip)
		}
	}
	for _, at := range s.logins[host] {
		if now.Sub(at) < time.Minute {
			recent = append(recent, at)
		}
	}
	if len(recent) >= 6 || len(s.logins) >= 4096 {
		s.mu.Unlock()
		return errors.New("login rate limited; wait one minute")
	}
	s.logins[host] = append(recent, now)
	s.mu.Unlock()
	var in struct {
		Token string "json:\"token\""
	}
	if err := decode(r, &in); err != nil {
		return err
	}
	if len(in.Token) != 64 || subtle.ConstantTimeCompare([]byte(Digest([]byte(in.Token))), []byte(s.Config.AdminSHA)) != 1 {
		return errors.New("invalid login")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, exp := range s.sessions {
		if now.After(exp) {
			delete(s.sessions, k)
		}
	}
	if len(s.sessions) >= 128 {
		return errors.New("session limit reached")
	}
	token := Random(32)
	s.sessions[Digest([]byte(token))] = now.Add(8 * time.Hour)
	http.SetCookie(w, &http.Cookie{Name: "bits_session", Value: token, Path: "/", HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode, MaxAge: 8 * 3600})
	respond(w, map[string]string{"status": "authenticated"})
	return nil
}
func (s *Server) allowed(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	for _, cidr := range s.Config.Networks {
		_, n, err := net.ParseCIDR(cidr)
		if err == nil && n.Contains(ip) {
			return true
		}
	}
	return false
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
	if !s.allowed(r) {
		http.Error(w, "source network is not allowed", http.StatusForbidden)
		return
	}
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	default:
		http.Error(w, "busy", 503)
		return
	}
	if r.Method != "GET" && r.Method != "HEAD" {
		if r.Header.Get("X-BITS-Request") != "1" {
			http.Error(w, "explicit BITS request required", 403)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != s.Config.URL {
			http.Error(w, "origin rejected", 403)
			return
		}
	}
	var err error
	if r.URL.Path == "/api/v1/login" && r.Method == "POST" {
		err = s.login(w, r)
	} else if strings.HasPrefix(r.URL.Path, "/api/v1/") {
		if !s.admin(r) {
			http.Error(w, "authentication required", 401)
			return
		}
		err = s.operator(w, r)
	} else if strings.HasPrefix(r.URL.Path, "/node/v1/") {
		node := r.Header.Get("X-BITS-Node")
		token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !s.Store.AuthNode(node, token) {
			http.Error(w, "node authentication required", 401)
			return
		}
		err = s.node(w, r, node)
	} else if r.Method == "GET" && (r.URL.Path == "/" || r.URL.Path == "/app.js" || r.URL.Path == "/dispatch.js" || r.URL.Path == "/style.css") {
		sub, _ := fs.Sub(webFiles, "web")
		http.FileServer(http.FS(sub)).ServeHTTP(w, r)
		return
	} else {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
	}
}
func (s *Server) operator(w http.ResponseWriter, r *http.Request) error {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	if strings.HasPrefix(path, "dispatch/") {
		return s.dispatchAPI(w, r, strings.TrimPrefix(path, "dispatch/"))
	}
	if path == "live" && r.Method == "GET" {
		respond(w, map[string]any{"frames": s.live.Snapshot(""), "monitors": s.live.MonitorSnapshot(""), "time": UTC()})
		return nil
	}
	if path == "logout" && r.Method == "POST" {
		if c, e := r.Cookie("bits_session"); e == nil {
			s.mu.Lock()
			delete(s.sessions, Digest([]byte(c.Value)))
			s.mu.Unlock()
		}
		http.SetCookie(w, &http.Cookie{Name: "bits_session", Value: "", Path: "/", HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode, MaxAge: -1})
		respond(w, map[string]bool{"ok": true})
		return nil
	}
	if path == "overview" && r.Method == "GET" {
		nodes, err := s.Store.Nodes()
		if err != nil {
			return err
		}
		power := s.power.Snapshot()
		for i := range nodes {
			p := power[nodes[i].ID]
			nodes[i].Power = &p
		}
		batches, err := s.Store.Batches("")
		if err != nil {
			return err
		}
		summary := []map[string]any{}
		for _, b := range batches {
			result := b.Result
			result.Steps = nil
			summary = append(summary, map[string]any{"id": b.ID, "state": b.State, "created_at": b.Created,
				"plan": map[string]string{"node": b.Plan.Node, "label": b.Plan.Label}, "step_count": len(b.Plan.Steps), "result": result,
				"budget_s": planSeconds(b.Plan), "cancel_requested": b.Cancel, "receipt_sha256": b.ReceiptSHA, "group_id": b.GroupID})
		}
		respond(w, map[string]any{"nodes": nodes, "batches": summary, "tools": Tools, "version": Version, "time": UTC()})
		return nil
	}
	if path == "nodes" && r.Method == "POST" {
		var in struct {
			ID         string "json:\"id\""
			Serial     string "json:\"serial\""
			KeepOn     bool   "json:\"keep_on\""
			BMCProfile string `json:"bmc_profile"`
		}
		if err := decode(r, &in); err != nil {
			return err
		}
		if !ValidName(in.Serial) {
			return errors.New("stable serial is required")
		}
		cert, err := os.ReadFile(s.Config.Cert)
		if err != nil {
			return err
		}
		token := Random(32)
		if err := s.addNodeWithBMCProfile(in.ID, token, in.BMCProfile); err != nil {
			return err
		}
		respond(w, NodeConfig{URL: s.Config.URL, Node: in.ID, Token: token, CA: string(cert), Serial: in.Serial, KeepOn: in.KeepOn})
		return nil
	}
	if path == "batches" && r.Method == "POST" {
		var p Plan
		if err := decode(r, &p); err != nil {
			return err
		}
		b, err := s.Store.Create(p)
		if err == nil {
			respond(w, b)
		}
		return err
	}
	parts := strings.Split(path, "/")
	if len(parts) == 3 && parts[0] == "nodes" && parts[2] == "live" && r.Method == "GET" {
		value, err := s.nodeMonitoring(parts[1])
		if err == nil {
			respond(w, value)
		}
		return err
	}
	if len(parts) < 2 || parts[0] != "batches" || !idRE.MatchString(parts[1]) {
		return errors.New("unknown operation")
	}
	id := parts[1]
	if len(parts) == 3 && parts[2] == "live" && r.Method == "GET" {
		if _, err := s.Store.Batch(id); err != nil {
			return err
		}
		respond(w, map[string]any{"frames": s.live.Snapshot(id), "time": UTC()})
		return nil
	}
	if len(parts) == 3 && parts[2] == "receipt" && r.Method == "GET" {
		b, err := s.Store.Batch(id)
		if err != nil {
			return err
		}
		return s.receipt(w, b)
	}
	if len(parts) == 2 && r.Method == "GET" {
		b, err := s.Store.Batch(id)
		if err == nil {
			respond(w, b)
		}
		return err
	}
	if len(parts) == 3 && r.Method == "POST" {
		var b Batch
		var err error
		if parts[2] == "start" {
			b, err = s.Store.Arm(id)
		} else {
			var in struct {
				Reason string "json:\"reason\""
			}
			if err = decode(r, &in); err != nil {
				return err
			}
			switch parts[2] {
			case "cancel":
				b, err = s.Store.Cancel(id, in.Reason)
			case "close-incomplete":
				b, err = s.Store.CloseIncomplete(id, in.Reason)
			default:
				return errors.New("unknown operation")
			}
		}
		if err == nil {
			respond(w, b)
		}
		return err
	}
	if len(parts) == 3 && parts[2] == "events" && r.Method == "GET" {
		v, err := s.Store.Events(id)
		if err == nil {
			respond(w, v)
		}
		return err
	}
	if len(parts) == 4 && parts[2] == "files" && r.Method == "GET" {
		b, err := s.Store.Batch(id)
		if err != nil {
			return err
		}
		return s.download(w, r, b, parts[3], true)
	}
	return errors.New("unknown operation")
}
func (s *Server) node(w http.ResponseWriter, r *http.Request, node string) error {
	path := strings.TrimPrefix(r.URL.Path, "/node/v1/")
	if path == "monitor" && r.Method == "POST" {
		var in MonitorUpdate
		if err := decodeBounded(r, &in, 2<<20); err != nil {
			return err
		}
		if err := s.live.PutMonitor(node, in); err != nil {
			return err
		}
		respond(w, map[string]bool{"ok": true})
		return nil
	}
	if path == "bmc-discovery" && r.Method == "POST" {
		var in BMCDiscovery
		if err := decodeBounded(r, &in, 8192); err != nil {
			return err
		}
		if err := s.autoBMC.discovery(node, in); err != nil {
			return err
		}
		respond(w, map[string]bool{"recorded": true, "power_command_sent": false})
		return nil
	}
	if path == "heartbeat" && r.Method == "GET" {
		if err := s.Store.Heartbeat(node, r.Header.Get("X-BITS-Version")); err != nil {
			return err
		}
		respond(w, map[string]string{"time": UTC()})
		return nil
	}
	if path == "poll" && r.Method == "GET" {
		if err := s.Store.Heartbeat(node, r.Header.Get("X-BITS-Version")); err != nil {
			return err
		}
		// This checks only explicitly armed commands, never draft task queues.
		b, err := s.Store.Pending(node)
		if err == nil {
			respond(w, map[string]any{"batch": b})
		}
		return err
	}
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[0] != "batches" || !idRE.MatchString(parts[1]) {
		return errors.New("unknown operation")
	}
	b, err := s.Store.Batch(parts[1])
	if err != nil {
		return err
	}
	if b.Plan.Node != node {
		return errors.New("batch belongs to another node")
	}
	if len(parts) == 2 && r.Method == "GET" {
		respond(w, b)
		return nil
	}
	if len(parts) < 3 {
		return errors.New("unknown operation")
	}
	if r.Header.Get("X-BITS-Attempt") != b.Attempt || b.Attempt == "" {
		return errors.New("attempt identity differs")
	}
	switch parts[2] {
	case "live":
		if r.Method != "POST" || len(parts) != 3 {
			break
		}
		var in LiveUpdate
		if err = decodeBounded(r, &in, 2<<20); err != nil {
			return err
		}
		if err = s.live.Put(b, in); err != nil {
			return err
		}
		respond(w, map[string]bool{"ok": true})
		return nil
	case "claim":
		if r.Method != "POST" {
			break
		}
		v, e := s.Store.Claim(b.ID, node, b.Attempt)
		if e == nil {
			respond(w, v)
		}
		return e
	case "result":
		if r.Method != "POST" {
			break
		}
		var in struct {
			Sequence int64  "json:\"sequence\""
			Result   Result "json:\"result\""
		}
		if err = decode(r, &in); err != nil {
			return err
		}
		v, e := s.Store.Update(b.ID, node, b.Attempt, in.Sequence, in.Result)
		if e == nil {
			respond(w, v)
		}
		return e
	case "manifest":
		if r.Method != "POST" {
			break
		}
		var artifacts map[string]Artifact
		if err = decode(r, &artifacts); err != nil {
			return err
		}
		if err = ValidateArtifacts(artifacts); err != nil {
			return err
		}
		v, e := s.Store.Mutate(b.ID, "artifacts_sealed", func(current *Batch) error {
			if (current.State != "finishing" && current.State != "delivered") || current.Result.Report != "generated" {
				return errors.New("execution/report must end before sealing")
			}
			in, _ := json.Marshal(artifacts)
			old, _ := json.Marshal(current.Artifacts)
			if len(current.Artifacts) > 0 && string(in) != string(old) {
				return errors.New("sealed manifest cannot change")
			}
			current.Artifacts = artifacts
			return nil
		})
		if e == nil {
			respond(w, v)
		}
		return e
	case "files":
		if len(parts) != 4 {
			break
		}
		name := parts[3]
		if r.Method == "GET" {
			return s.download(w, r, b, name, false)
		}
		if r.Method == "PUT" || r.Method == "HEAD" {
			return s.upload(w, r, b, name)
		}
	case "commit":
		if r.Method != "POST" {
			break
		}
		return s.commit(w, r, b)
	case "receipt":
		if r.Method != "GET" || len(parts) != 3 || b.State != "delivered" {
			break
		}
		return s.receipt(w, b)
	}
	return errors.New("unknown operation")
}
func planSeconds(p Plan) int {
	total := 0
	for _, step := range p.Steps {
		total += step.Seconds
	}
	return total
}
func (s *Server) receipt(w http.ResponseWriter, b Batch) error {
	if b.State != "delivered" || b.ReceiptSHA == "" {
		return errors.New("delivery receipt is not available")
	}
	path := filepath.Join(s.Config.Data, "artifacts", b.ID, "receipt.json")
	if err := PrivateDir(filepath.Dir(path)); err != nil {
		return err
	}
	if err := CheckFileIfExists(path); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, (2<<20)+1))
	if err != nil {
		return err
	}
	if len(raw) > 2<<20 {
		return errors.New("receipt is too large")
	}
	if Digest(raw) != b.ReceiptSHA {
		return errors.New("receipt content changed")
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition", "attachment; filename=\"receipt.json\"")
	_, err = w.Write(raw)
	return err
}
func (s *Server) batchDir(b Batch) (string, error) {
	dir := filepath.Join(s.Config.Data, "artifacts", b.ID)
	if err := os.Mkdir(dir, 0700); err != nil && !os.IsExist(err) {
		return "", err
	}
	if err := PrivateDir(dir); err != nil {
		return "", err
	}
	return dir, nil
}
func (s *Server) upload(w http.ResponseWriter, r *http.Request, b Batch, name string) error {
	expected, ok := b.Artifacts[name]
	if !ok || !ValidName(name) {
		return errors.New("file is not in sealed manifest")
	}
	lock, _ := s.uploads.LoadOrStore(b.Plan.Node, new(sync.Mutex))
	lock.(*sync.Mutex).Lock()
	defer lock.(*sync.Mutex).Unlock()
	dir, err := s.batchDir(b)
	if err != nil {
		return err
	}
	final, part := filepath.Join(dir, name), filepath.Join(dir, "."+name+".part")
	if err = CheckFileIfExists(final); err != nil {
		return err
	}
	if info, e := os.Stat(final); e == nil {
		w.Header().Set("X-BITS-Offset", strconv.FormatInt(info.Size(), 10))
		w.WriteHeader(200)
		return nil
	}
	if err = CheckFileIfExists(part); err != nil {
		return err
	}
	var offset int64
	if info, e := os.Stat(part); e == nil {
		offset = info.Size()
	} else if !os.IsNotExist(e) {
		return e
	}
	if r.Method == "HEAD" {
		w.Header().Set("X-BITS-Offset", strconv.FormatInt(offset, 10))
		return nil
	}
	if b.State != "finishing" {
		return errors.New("batch is not accepting uploads")
	}
	supplied, err := strconv.ParseInt(r.URL.Query().Get("offset"), 10, 64)
	if err != nil || supplied != offset {
		return errors.New("upload offset differs; query HEAD before retry")
	}
	if r.ContentLength < 0 || r.ContentLength > 4<<20 || offset+r.ContentLength > expected.Bytes {
		return errors.New("invalid upload chunk size")
	}
	var space syscall.Statfs_t
	if err = syscall.Statfs(dir, &space); err != nil {
		return err
	}
	if space.Bavail*uint64(space.Bsize) < uint64(r.ContentLength)+(64<<20) {
		return errors.New("insufficient artifact storage space")
	}
	f, err := os.OpenFile(part, os.O_CREATE|os.O_WRONLY|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	if _, err = f.Seek(offset, io.SeekStart); err == nil {
		var n int64
		n, err = io.CopyN(f, r.Body, r.ContentLength)
		if err == nil && n != r.ContentLength {
			err = io.ErrUnexpectedEOF
		}
	}
	if err != nil {
		f.Truncate(offset)
		f.Sync()
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	offset += r.ContentLength
	if offset == expected.Bytes {
		actual, e := HashFile(part)
		if e != nil {
			return e
		}
		if actual != expected {
			return errors.New("uploaded content SHA-256 differs; partial evidence retained")
		}
		if err = os.Rename(part, final); err != nil {
			return err
		}
		d, e := os.Open(dir)
		if e != nil {
			return e
		}
		err = d.Sync()
		d.Close()
		if err != nil {
			return err
		}
	}
	w.Header().Set("X-BITS-Offset", strconv.FormatInt(offset, 10))
	respond(w, map[string]int64{"offset": offset})
	return nil
}
func HashFile(path string) (Artifact, error) {
	if err := CheckFileIfExists(path); err != nil {
		return Artifact{}, err
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return Artifact{}, err
	}
	defer f.Close()
	before, err := f.Stat()
	if err != nil {
		return Artifact{}, err
	}
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, (16<<30)+1))
	if err != nil {
		return Artifact{}, err
	}
	after, err := f.Stat()
	if err != nil {
		return Artifact{}, err
	}
	if n > 16<<30 || n != before.Size() || n != after.Size() || before.ModTime() != after.ModTime() {
		return Artifact{}, errors.New("artifact changed or exceeded limit")
	}
	return Artifact{SHA256: hex.EncodeToString(h.Sum(nil)), Bytes: n}, nil
}
func (s *Server) download(w http.ResponseWriter, r *http.Request, b Batch, name string, operator bool) error {
	expected, ok := b.Artifacts[name]
	if !ok || !ValidName(name) {
		return errors.New("unknown artifact")
	}
	path := filepath.Join(s.Config.Data, "artifacts", b.ID, name)
	if err := CheckFileIfExists(path); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if info.Size() != expected.Bytes {
		return errors.New("artifact size differs")
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", name))
	if operator && strings.HasSuffix(name, ".html") {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Disposition", "inline")
		// Downloaded report content has a sandboxed unique origin, no script or
		// forms, and no authority to call the management API.
		w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'")
	}
	http.ServeContent(w, r, name, info.ModTime(), f)
	return nil
}
func (s *Server) commit(w http.ResponseWriter, r *http.Request, b Batch) error {
	var hashes map[string]Artifact
	if err := decode(r, &hashes); err != nil {
		return err
	}
	if len(b.Artifacts) == 0 {
		return errors.New("no sealed artifacts")
	}
	a, _ := json.Marshal(hashes)
	e, _ := json.Marshal(b.Artifacts)
	if string(a) != string(e) {
		return errors.New("node readback hashes differ from sealed artifacts")
	}
	lock, _ := s.uploads.LoadOrStore(b.Plan.Node, new(sync.Mutex))
	lock.(*sync.Mutex).Lock()
	defer lock.(*sync.Mutex).Unlock()
	dir, err := s.batchDir(b)
	if err != nil {
		return err
	}
	for name, expected := range b.Artifacts {
		actual, err := HashFile(filepath.Join(dir, name))
		if err != nil {
			return err
		}
		if actual != expected {
			return errors.New("stored artifact changed: " + name)
		}
	}
	v, err := s.Store.Mutate(b.ID, "delivery_verified", func(current *Batch) error {
		if current.State == "delivered" {
			return nil
		}
		if current.State != "finishing" {
			return errors.New("batch cannot complete")
		}
		receipt := map[string]any{"schema": "bits-receipt-v1", "batch": current.ID, "attempt": current.Attempt, "plan": current.Plan,
			"result": current.Result, "artifacts": current.Artifacts, "verified_at": UTC(), "verification": "https-upload-download-sha256", "hardware_result": "not_assessed", "version": Version}
		raw, _ := json.MarshalIndent(receipt, "", "  ")
		raw = append(raw, '\n')
		path := filepath.Join(dir, "receipt.json")
		if old, e := os.ReadFile(path); e == nil {
			// A crash after durable receipt write but before the DB commit is
			// recoverable only when its immutable content belongs to this case.
			var previous map[string]any
			if json.Unmarshal(old, &previous) != nil {
				return errors.New("invalid interrupted receipt")
			}
			delete(previous, "verified_at")
			delete(receipt, "verified_at")
			x, _ := json.Marshal(previous)
			y, _ := json.Marshal(receipt)
			if string(x) != string(y) {
				return errors.New("existing receipt differs")
			}
			raw = old
		} else if !os.IsNotExist(e) {
			return e
		} else if e = Atomic(path, raw); e != nil {
			return e
		}
		current.State = "delivered"
		current.ReceiptSHA = Digest(raw)
		return nil
	})
	if err == nil {
		respond(w, v)
	}
	return err
}
func (s *Server) HTTPServer() *http.Server {
	return &http.Server{Addr: s.Config.Address, Handler: s, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 2 * time.Minute,
		WriteTimeout: 5 * time.Minute, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 << 10,
		TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
}
func ValidateURL(value string) error {
	u, e := url.Parse(value)
	if e != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return errors.New("absolute HTTPS origin required")
	}
	return nil
}
