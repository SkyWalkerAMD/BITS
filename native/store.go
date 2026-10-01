package bits

import (
	"crypto/subtle"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// One local SQLite database owns identities, immutable plans, explicit start
// authorizations and events. WAL must be on a local filesystem, not an NFS share.
type Store struct {
	db *sql.DB
	mu sync.Mutex
}

func OpenStore(dir string) (*Store, error) {
	if err := PrivateDir(dir); err != nil {
		return nil, err
	}
	dbpath := filepath.Join(dir, "center.sqlite")
	if err := CheckFileIfExists(dbpath); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", dbpath)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	var tables int
	if err = db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").Scan(&tables); err != nil {
		db.Close()
		return nil, err
	}
	if tables > 0 {
		var version string
		if err = db.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&version); err != nil || (version != "1" && version != "2") {
			db.Close()
			return nil, errors.New("unsupported existing database; no schema changes were made")
		}
	}
	for _, statement := range []string{
		"PRAGMA busy_timeout=10000", "PRAGMA journal_mode=WAL", "PRAGMA synchronous=FULL", "PRAGMA foreign_keys=ON",
		"CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
		"CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, token_sha TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, last_seen TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '')",
		"CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, node TEXT NOT NULL REFERENCES nodes(id), state TEXT NOT NULL, body TEXT NOT NULL)",
		"CREATE UNIQUE INDEX IF NOT EXISTS one_active_batch ON batches(node) WHERE state IN ('armed','running','finishing','needs_attention')",
		"CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL REFERENCES batches(id), at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)",
		"INSERT OR IGNORE INTO metadata(key,value) VALUES('schema','1')",
	} {
		if _, err = db.Exec(statement); err != nil {
			db.Close()
			return nil, err
		}
	}
	var schema string
	err = db.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&schema)
	if err != nil || (schema != "1" && schema != "2") {
		db.Close()
		return nil, errors.New("unsupported database schema; retain database and use its matching binary")
	}
	if err = os.Chmod(dbpath, 0600); err != nil {
		db.Close()
		return nil, err
	}
	if err = migrateDispatch(db, dbpath); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}
func (s *Store) Close() error { return s.db.Close() }
func (s *Store) AddNode(id, token string) error {
	if !ValidName(id) || !digestRE.MatchString(token) {
		return errors.New("invalid node identity or credential")
	}
	_, err := s.db.Exec("INSERT INTO nodes(id,token_sha) VALUES(?,?)", id, Digest([]byte(token)))
	return err
}
func (s *Store) AuthNode(id, token string) bool {
	var expected string
	err := s.db.QueryRow("SELECT token_sha FROM nodes WHERE id=? AND disabled=0", id).Scan(&expected)
	return err == nil && subtle.ConstantTimeCompare([]byte(expected), []byte(Digest([]byte(token)))) == 1
}
func (s *Store) Nodes() ([]Node, error) {
	rows, err := s.db.Query("SELECT id,last_seen,agent,disabled FROM nodes ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Node{}
	for rows.Next() {
		var n Node
		if err = rows.Scan(&n.ID, &n.LastSeen, &n.Agent, &n.Disabled); err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err()
}
func (s *Store) Heartbeat(id, version string) error {
	if len(version) > 64 {
		return errors.New("invalid agent version")
	}
	_, err := s.db.Exec("UPDATE nodes SET last_seen=?,agent=? WHERE id=?", UTC(), version, id)
	return err
}
func (s *Store) Batch(id string) (Batch, error) {
	var body string
	var b Batch
	err := s.db.QueryRow("SELECT body FROM batches WHERE id=?", id).Scan(&body)
	if err == nil {
		err = json.Unmarshal([]byte(body), &b)
	}
	return b, err
}
func (s *Store) Batches(node string) ([]Batch, error) {
	query, args := "SELECT body FROM batches WHERE state IN ('waiting_boot','armed','running','finishing','needs_attention') OR id IN (SELECT id FROM batches ORDER BY rowid DESC LIMIT 500) ORDER BY rowid DESC", []any{}
	if node != "" {
		query = "SELECT body FROM batches WHERE node=? ORDER BY rowid DESC LIMIT 500"
		args = append(args, node)
	}
	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Batch{}
	for rows.Next() {
		var body string
		var b Batch
		if err = rows.Scan(&body); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(body), &b); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}
func (s *Store) Events(id string) ([]Event, error) {
	rows, err := s.db.Query("SELECT at,kind,detail FROM events WHERE batch=? ORDER BY seq DESC LIMIT 512", id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Event{}
	for rows.Next() {
		var e Event
		if err = rows.Scan(&e.At, &e.Kind, &e.Detail); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}
func event(tx *sql.Tx, id, kind, detail string) error {
	_, err := tx.Exec("INSERT INTO events(batch,at,kind,detail) VALUES(?,?,?,?)", id, UTC(), kind, detail)
	return err
}
func (s *Store) Create(p Plan) (Batch, error) {
	if err := ValidatePlan(&p); err != nil {
		return Batch{}, err
	}
	b := Batch{ID: Random(16), Plan: p, State: "draft", Created: UTC(), Result: Result{Execution: "not_started", Quality: "not_collected", Report: "not_generated"}}
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return b, err
	}
	defer tx.Rollback()
	body, _ := json.Marshal(b)
	_, err = tx.Exec("INSERT INTO batches(id,node,state,body) VALUES(?,?,?,?)", b.ID, p.Node, b.State, string(body))
	if err == nil {
		err = event(tx, b.ID, "created", "explicit start required")
	}
	if err == nil {
		err = tx.Commit()
	}
	return b, err
}
func (s *Store) Mutate(id, kind string, fn func(*Batch) error) (Batch, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, err := s.Batch(id)
	if err != nil {
		return b, err
	}
	before, _ := json.Marshal(b)
	if err = fn(&b); err != nil {
		return b, err
	}
	body, err := json.Marshal(b)
	if err != nil {
		return b, err
	}
	if string(before) == string(body) {
		return b, nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return b, err
	}
	defer tx.Rollback()
	_, err = tx.Exec("UPDATE batches SET state=?,body=? WHERE id=?", b.State, string(body), id)
	if err == nil {
		err = event(tx, id, kind, b.State)
	}
	if err == nil {
		err = tx.Commit()
	}
	return b, err
}
func (s *Store) Arm(id string) (Batch, error) {
	return s.Mutate(id, "start_authorized", func(b *Batch) error {
		if b.GroupID != "" {
			return errors.New("此批次属于任务组，请从任务组检查并明确开始")
		}
		if b.State == "armed" {
			return nil
		} // Repeated click authorizes no second run.
		if b.State != "draft" {
			return errors.New("only a draft can start; execution recovery never reruns a batch")
		}
		b.State = "armed"
		b.Attempt = Random(16)
		b.Expires = time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339Nano)
		return nil
	})
}
func (s *Store) Claim(id, node, attempt string) (Batch, error) {
	return s.Mutate(id, "node_accepted", func(b *Batch) error {
		if b.Plan.Node != node || b.Attempt != attempt {
			return errors.New("batch ownership differs")
		}
		if b.State == "running" {
			return nil
		}
		if b.State != "armed" || b.Cancel {
			return errors.New("batch is not authorized to start")
		}
		expires, err := time.Parse(time.RFC3339Nano, b.Expires)
		if err != nil || time.Now().After(expires) {
			return errors.New("start authorization expired; create a new explicit batch")
		}
		b.State = "running"
		b.Result.Execution = "running"
		return nil
	})
}
func (s *Store) Pending(node string) (*Batch, error) {
	// An armed batch must not disappear behind the dashboard's history limit.
	var body string
	err := s.db.QueryRow("SELECT body FROM batches WHERE node=? AND state='armed'", node).Scan(&body)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var selected Batch
	if err = json.Unmarshal([]byte(body), &selected); err != nil {
		return nil, err
	}
	all := []Batch{selected}
	for _, b := range all {
		if b.State == "armed" {
			exp, _ := time.Parse(time.RFC3339Nano, b.Expires)
			if time.Now().After(exp) {
				_, err = s.Mutate(b.ID, "start_expired", func(v *Batch) error {
					if v.State == "armed" {
						v.State = "cancelled"
						v.Result.Error = "开始授权超过 5 分钟未被节点接受；压测未自动重排"
					}
					return nil
				})
				if err != nil {
					return nil, err
				}
				continue
			}
			return &b, nil
		}
	}
	return nil, nil
}
func (s *Store) Update(id, node, attempt string, seq int64, r Result) (Batch, error) {
	if len(r.Error) > 2048 || len(r.Steps) > 128 {
		return Batch{}, errors.New("oversized result")
	}
	switch r.Execution {
	case "running", "completed", "failed", "interrupted", "preflight_failed":
	default:
		return Batch{}, errors.New("invalid execution result")
	}
	switch r.Quality {
	case "not_collected", "readings_reported_validity_unknown", "unavailable", "partial":
	default:
		return Batch{}, errors.New("invalid quality")
	}
	switch r.Report {
	case "not_generated", "generated", "failed":
	default:
		return Batch{}, errors.New("invalid report result")
	}
	return s.Mutate(id, "node_result", func(b *Batch) error {
		if b.Plan.Node != node || b.Attempt != attempt {
			return errors.New("batch ownership differs")
		}
		if seq == b.Sequence {
			old, _ := json.Marshal(b.Result)
			incoming, _ := json.Marshal(r)
			if string(old) != string(incoming) {
				return errors.New("sequence replay contains different result")
			}
			return nil
		}
		if seq < b.Sequence || IsTerminal(b.State) || len(b.Artifacts) > 0 {
			return errors.New("stale result or sealed batch")
		}
		if b.State != "running" && b.State != "finishing" && !(b.State == "armed" && r.Execution == "preflight_failed") && b.State != "needs_attention" {
			return errors.New("batch not accepted")
		}
		if b.Result.Execution != "running" && b.Result.Execution != "not_started" && r.Execution == "running" {
			return errors.New("execution cannot resume")
		}
		b.Sequence = seq
		b.Result = r
		if r.Execution == "running" {
			b.State = "running"
		} else if r.Report == "generated" || (r.Report == "not_generated" && r.Execution != "preflight_failed") {
			// Stopped execution may still be generating its evidence. Report
			// failure is an explicit result, not a transient not-yet-generated
			// snapshot received while the worker changes phase.
			b.State = "finishing"
		} else {
			b.State = "needs_attention"
		}
		return nil
	})
}
func (s *Store) Cancel(id, reason string) (Batch, error) {
	if len(reason) < 1 || len(reason) > 512 {
		return Batch{}, errors.New("provide a bounded reason")
	}
	return s.Mutate(id, "cancel_requested: "+reason, func(b *Batch) error {
		if IsTerminal(b.State) {
			return errors.New("batch is already terminal")
		}
		b.Cancel = true
		if b.State == "draft" || b.State == "armed" || b.State == "waiting_boot" {
			b.State = "cancelled"
			b.Result.Error = reason
		}
		return nil
	})
}
func (s *Store) CloseIncomplete(id, reason string) (Batch, error) {
	if len(reason) < 1 || len(reason) > 512 {
		return Batch{}, errors.New("provide a reason")
	}
	return s.Mutate(id, "closed_incomplete: "+reason, func(b *Batch) error {
		if b.State != "needs_attention" {
			return fmt.Errorf("cannot close %s; stop and confirm node execution first", b.State)
		}
		b.State = "closed_incomplete"
		return nil
	})
}
