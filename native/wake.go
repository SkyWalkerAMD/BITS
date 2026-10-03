package bits

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"time"
)

// Schema 4 reserves nodes for standalone wake operations and records permanent
// deletions before removing evidence. Old centers must not ignore either intent.
func migrateNodeOperations(db *sql.DB, path string) error {
	var schema string
	if err := db.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&schema); err != nil {
		return err
	}
	if schema == "4" {
		return nil
	}
	if schema != "3" {
		return errors.New("unsupported schema for node operations")
	}
	var count int
	if err := db.QueryRow("SELECT count(*) FROM nodes").Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		backup := path + ".before-node-operations-v3"
		if _, err := os.Lstat(backup); !os.IsNotExist(err) {
			return errors.New("node operations migration backup already exists; retain it and inspect database before retrying")
		}
		if _, err := db.Exec("VACUUM INTO ?", backup); err != nil {
			return err
		}
		if err := os.Chmod(backup, 0600); err != nil {
			return err
		}
	}
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, q := range []string{
		"CREATE TABLE wake_operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)",
		"CREATE TABLE wake_members(operation TEXT NOT NULL REFERENCES wake_operations(id), node TEXT NOT NULL REFERENCES nodes(id), state TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(operation,node))",
		"CREATE UNIQUE INDEX one_active_wake ON wake_members(node) WHERE state IN ('pending','command_requested','waiting_agent')",
		"CREATE TABLE wake_holds(node TEXT PRIMARY KEY REFERENCES nodes(id), operation TEXT NOT NULL REFERENCES wake_operations(id))",
		"CREATE TABLE deletion_operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)",
		"CREATE TABLE node_hardware_info(node TEXT PRIMARY KEY REFERENCES nodes(id), body TEXT NOT NULL)",
		"CREATE TABLE deleted_batches(id TEXT PRIMARY KEY, node TEXT NOT NULL REFERENCES nodes(id), operation TEXT NOT NULL REFERENCES deletion_operations(id), state TEXT NOT NULL, deleted_at TEXT NOT NULL)",
		"UPDATE metadata SET value='4' WHERE key='schema'",
	} {
		if _, err = tx.Exec(q); err != nil {
			return err
		}
	}
	return tx.Commit()
}

type WakeRequest struct {
	RequestID string   `json:"request_id"`
	Nodes     []string `json:"nodes"`
}
type WakeMember struct {
	Node      string `json:"node"`
	State     string `json:"state"`
	Requested string `json:"requested_at"`
	Deadline  string `json:"deadline"`
	Binding   string `json:"binding,omitempty"`
	CommandAt string `json:"command_at,omitempty"`
	Finished  string `json:"finished_at,omitempty"`
	Error     string `json:"error,omitempty"`
}
type WakeOperation struct {
	ID      string         `json:"id"`
	Created string         `json:"created_at"`
	Members []WakeMember   `json:"members"`
	Counts  map[string]int `json:"counts"`
}

func wakeActive(state string) bool {
	return state == "pending" || state == "command_requested" || state == "waiting_agent"
}
func activeWake(tx *sql.Tx, node string) (bool, error) {
	var count int
	err := tx.QueryRow("SELECT count(*) FROM wake_members WHERE node=? AND state IN ('pending','command_requested','waiting_agent')", node).Scan(&count)
	return count != 0, err
}
func (s *Store) wakeHold(node string) (string, error) {
	var id string
	err := s.db.QueryRow("SELECT operation FROM wake_holds WHERE node=?", node).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return id, err
}
func (s *Store) latestWakes() (map[string]WakeMember, error) {
	rows, err := s.db.Query("SELECT body FROM wake_members WHERE rowid IN (SELECT max(rowid) FROM wake_members GROUP BY node)")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]WakeMember{}
	for rows.Next() {
		var raw string
		var m WakeMember
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(raw), &m); err != nil {
			return nil, err
		}
		m.Binding = ""
		out[m.Node] = m
	}
	return out, rows.Err()
}
func (s *Store) WakeOperation(id string) (WakeOperation, error) {
	v := WakeOperation{ID: id, Members: []WakeMember{}, Counts: map[string]int{}}
	if err := s.db.QueryRow("SELECT created_at FROM wake_operations WHERE id=?", id).Scan(&v.Created); err != nil {
		return v, err
	}
	rows, err := s.db.Query("SELECT body FROM wake_members WHERE operation=? ORDER BY node", id)
	if err != nil {
		return v, err
	}
	defer rows.Close()
	for rows.Next() {
		var body string
		var m WakeMember
		if err = rows.Scan(&body); err != nil {
			return v, err
		}
		if err = json.Unmarshal([]byte(body), &m); err != nil {
			return v, err
		}
		m.Binding = "" // An operator view never needs the internal binding revision.
		v.Members = append(v.Members, m)
		v.Counts[m.State]++
	}
	return v, rows.Err()
}
func (s *Store) WakeOperations(offset int) ([]WakeOperation, error) {
	if offset < 0 || offset > 1000000 {
		return nil, errors.New("invalid page offset")
	}
	rows, err := s.db.Query("SELECT id FROM wake_operations ORDER BY rowid DESC LIMIT 25 OFFSET ?", offset)
	if err != nil {
		return nil, err
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return nil, err
		}
		ids = append(ids, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	out := []WakeOperation{}
	for _, id := range ids {
		v, e := s.WakeOperation(id)
		if e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, nil
}
func (s *Store) CreateWake(in WakeRequest, power map[string]PowerStatus) (WakeOperation, error) {
	if !idRE.MatchString(in.RequestID) || len(in.Nodes) < 1 || len(in.Nodes) > 200 {
		return WakeOperation{}, errors.New("请选择 1–200 台节点，并提供请求标识")
	}
	in.Nodes = append([]string{}, in.Nodes...)
	sort.Strings(in.Nodes)
	for i, node := range in.Nodes {
		if !ValidName(node) || (i > 0 && node == in.Nodes[i-1]) {
			return WakeOperation{}, errors.New("节点名称无效或重复")
		}
	}
	raw, _ := json.Marshal(in)
	fingerprint := Digest(raw)
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return WakeOperation{}, err
	}
	defer tx.Rollback()
	var old string
	err = tx.QueryRow("SELECT fingerprint FROM wake_operations WHERE id=?", in.RequestID).Scan(&old)
	if err == nil {
		if old != fingerprint {
			return WakeOperation{}, errors.New("请求标识已用于另一组唤醒操作")
		}
		tx.Rollback()
		return s.WakeOperation(in.RequestID)
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return WakeOperation{}, err
	}
	v := WakeOperation{ID: in.RequestID, Created: UTC()}
	for _, node := range in.Nodes {
		a, e := txAvailability(tx, node, power)
		if e != nil {
			return v, e
		}
		if !a.CanSelect {
			return v, fmt.Errorf("%s：%s；本次唤醒尚未提交", node, a.Reason)
		}
		m := WakeMember{Node: node, Requested: v.Created, State: "pending", Deadline: time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339Nano), Binding: a.Power.Binding}
		if a.AgentOnline {
			m.State = "already_online"
			m.Finished = UTC()
			m.Binding = ""
		}
		v.Members = append(v.Members, m)
	}
	if _, err = tx.Exec("INSERT INTO wake_operations VALUES(?,?,?)", v.ID, fingerprint, v.Created); err != nil {
		return v, err
	}
	for _, m := range v.Members {
		raw, _ = json.Marshal(m)
		if _, err = tx.Exec("INSERT INTO wake_members VALUES(?,?,?,?)", v.ID, m.Node, m.State, string(raw)); err != nil {
			return v, err
		}
		if m.State != "already_online" {
			if _, err = tx.Exec("INSERT INTO wake_holds VALUES(?,?) ON CONFLICT(node) DO UPDATE SET operation=excluded.operation", m.Node, v.ID); err != nil {
				return v, err
			}
		}
	}
	if err = tx.Commit(); err != nil {
		return v, err
	}
	return s.WakeOperation(v.ID)
}
func (s *Store) mutateWake(id, node string, fn func(*WakeMember) error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var raw string
	if err := s.db.QueryRow("SELECT body FROM wake_members WHERE operation=? AND node=?", id, node).Scan(&raw); err != nil {
		return err
	}
	var m WakeMember
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		return err
	}
	if err := fn(&m); err != nil {
		return err
	}
	body, _ := json.Marshal(m)
	_, err := s.db.Exec("UPDATE wake_members SET state=?,body=? WHERE operation=? AND node=?", m.State, string(body), id, node)
	return err
}
func (s *Store) pendingWakes() (map[string][]WakeMember, error) {
	rows, err := s.db.Query("SELECT operation,body FROM wake_members WHERE state IN ('pending','command_requested','waiting_agent')")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string][]WakeMember{}
	for rows.Next() {
		var id, raw string
		var m WakeMember
		if err = rows.Scan(&id, &raw); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(raw), &m); err != nil {
			return nil, err
		}
		out[id] = append(out[id], m)
	}
	return out, rows.Err()
}
func (s *Server) finishWake(id, node, state, message string) {
	s.Store.mutateWake(id, node, func(m *WakeMember) error {
		if wakeActive(m.State) {
			m.State = state
			m.Error = message
			m.Finished = UTC()
		}
		return nil
	})
}
func (s *Server) advanceWake(ctx context.Context, id string, m WakeMember) {
	deadline, err := time.Parse(time.RFC3339Nano, m.Deadline)
	if err != nil || time.Now().After(deadline) {
		s.finishWake(id, m.Node, "timed_out", "等待系统连接超过 10 分钟，请检查机器启动和节点服务")
		return
	}
	var seen string
	var disabled bool
	if err = s.Store.db.QueryRow("SELECT last_seen,disabled FROM nodes WHERE id=?", m.Node).Scan(&seen, &disabled); err != nil || disabled {
		s.finishWake(id, m.Node, "failed", "节点不存在或已禁用")
		return
	}
	heartbeat, _ := time.Parse(time.RFC3339Nano, seen)
	requested, _ := time.Parse(time.RFC3339Nano, m.Requested)
	a := availability(Node{ID: m.Node, LastSeen: seen}, "", s.power.Snapshot()[m.Node])
	if a.AgentOnline && heartbeat.After(requested) {
		s.finishWake(id, m.Node, "online", "")
		return
	}
	if m.State == "waiting_agent" {
		return
	}
	s.power.mu.Lock()
	binding, ok := s.power.bindings[m.Node]
	s.power.mu.Unlock()
	if !ok || bindingID(binding) != m.Binding {
		s.finishWake(id, m.Node, "failed", "BMC 绑定已变化，请核对后重新唤醒")
		return
	}
	status := s.power.probe(ctx, binding)
	if ctx.Err() != nil {
		return
	}
	if status.State == "on" {
		s.Store.mutateWake(id, m.Node, func(v *WakeMember) error {
			if wakeActive(v.State) {
				v.State = "waiting_agent"
			}
			return nil
		})
		return
	}
	if status.State != "off" {
		s.finishWake(id, m.Node, "failed", "BMC 状态未确认，请检查管理网络和绑定")
		return
	}
	if m.State != "pending" {
		s.finishWake(id, m.Node, "failed", "上次开机请求结果未确认；没有重复发送，请核对机器状态")
		return
	}
	claimed := false
	err = s.Store.mutateWake(id, m.Node, func(v *WakeMember) error {
		if v.State == "pending" {
			v.State = "command_requested"
			v.CommandAt = UTC()
			claimed = true
		}
		return nil
	})
	if err != nil || !claimed {
		return
	}
	// Persist before I/O: a restart may observe power but must never blindly retry.
	select {
	case s.power.slots <- struct{}{}:
	case <-ctx.Done():
		return
	}
	err = s.power.driver.On(ctx, binding)
	<-s.power.slots
	if ctx.Err() != nil {
		return
	}
	if err != nil {
		s.finishWake(id, m.Node, "failed", "开机请求未确认成功，可能已发出；请核对状态后再操作")
		return
	}
	s.Store.mutateWake(id, m.Node, func(v *WakeMember) error {
		if v.State == "command_requested" {
			v.State = "waiting_agent"
		}
		return nil
	})
}
