package bits

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
)

type DeleteRequest struct {
	RequestID string   `json:"request_id"`
	Batches   []string `json:"batches"`
}
type Deletion struct {
	ID      string         `json:"id"`
	At      string         `json:"at"`
	Batches []string       `json:"batches"`
	Counts  map[string]int `json:"counts"`
}

func (s *Store) Deletions() ([]Deletion, error) {
	rows, err := s.db.Query("SELECT body FROM deletion_operations ORDER BY rowid DESC LIMIT 25")
	if err != nil {
		return nil, err
	}
	out := []Deletion{}
	for rows.Next() {
		var raw string
		var d Deletion
		if err = rows.Scan(&raw); err != nil {
			rows.Close()
			return nil, err
		}
		if err = json.Unmarshal([]byte(raw), &d); err != nil {
			rows.Close()
			return nil, err
		}
		out = append(out, d)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for i := range out {
		out[i].Counts = map[string]int{}
		states, e := s.db.Query("SELECT state,count(*) FROM deleted_batches WHERE operation=? GROUP BY state", out[i].ID)
		if e != nil {
			return nil, e
		}
		for states.Next() {
			var state string
			var count int
			if e = states.Scan(&state, &count); e != nil {
				states.Close()
				return nil, e
			}
			out[i].Counts[state] = count
		}
		e = states.Err()
		states.Close()
		if e != nil {
			return nil, e
		}
	}
	return out, nil
}
func (s *Store) BeginDelete(in DeleteRequest) (Deletion, error) {
	if !idRE.MatchString(in.RequestID) || len(in.Batches) == 0 || len(in.Batches) > 200 {
		return Deletion{}, errors.New("请选择 1–200 个已结束批次，并提供请求标识")
	}
	in.Batches = append([]string{}, in.Batches...)
	sort.Strings(in.Batches)
	for i, id := range in.Batches {
		if !idRE.MatchString(id) || (i > 0 && id == in.Batches[i-1]) {
			return Deletion{}, errors.New("批次标识无效或重复")
		}
	}
	raw, _ := json.Marshal(in)
	fingerprint := Digest(raw)
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return Deletion{}, err
	}
	defer tx.Rollback()
	var old, body string
	err = tx.QueryRow("SELECT fingerprint,body FROM deletion_operations WHERE id=?", in.RequestID).Scan(&old, &body)
	if err == nil {
		if old != fingerprint {
			return Deletion{}, errors.New("请求标识已用于另一组删除操作")
		}
		var d Deletion
		if err = json.Unmarshal([]byte(body), &d); err != nil {
			return d, err
		}
		_, err = tx.Exec("UPDATE deleted_batches SET state='pending' WHERE operation=? AND state='failed'", d.ID)
		if err != nil {
			return d, err
		}
		return d, tx.Commit()
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return Deletion{}, err
	}
	d := Deletion{ID: in.RequestID, At: UTC(), Batches: in.Batches}
	all := []Batch{}
	for _, id := range in.Batches {
		if err = tx.QueryRow("SELECT body FROM batches WHERE id=?", id).Scan(&body); err != nil {
			return d, errors.New("所选批次已删除或不存在，请刷新列表")
		}
		var b Batch
		if err = json.Unmarshal([]byte(body), &b); err != nil {
			return d, err
		}
		if !IsTerminal(b.State) {
			return d, fmt.Errorf("%s 尚未结束，不能删除；本次选择尚未提交", b.Plan.Label)
		}
		all = append(all, b)
	}
	raw, _ = json.Marshal(d)
	if _, err = tx.Exec("INSERT INTO deletion_operations VALUES(?,?,?)", d.ID, fingerprint, string(raw)); err != nil {
		return d, err
	}
	for _, b := range all {
		// Tombstones contain only ownership and deletion progress, never report
		// contents. They prevent delayed agents from resurrecting removed evidence.
		if _, err = tx.Exec("INSERT INTO deleted_batches VALUES(?,?,?,?,?)", b.ID, b.Plan.Node, d.ID, "pending", d.At); err != nil {
			return d, err
		}
		b.State = "deleting"
		raw, _ = json.Marshal(b)
		if _, err = tx.Exec("UPDATE batches SET state=?,body=? WHERE id=?", b.State, string(raw), b.ID); err != nil {
			return d, err
		}
	}
	return d, tx.Commit()
}
func (s *Store) deletedNode(id string) (string, error) {
	var node string
	err := s.db.QueryRow("SELECT node FROM deleted_batches WHERE id=?", id).Scan(&node)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return node, err
}
func (s *Server) deleteEvidence(id, node string) error {
	// Same lock order as commit/upload. An in-flight upload must finish before
	// removal; a request holding a pre-deletion Batch must recheck the tombstone.
	lock, _ := s.uploads.LoadOrStore(node, new(sync.Mutex))
	lock.(*sync.Mutex).Lock()
	defer lock.(*sync.Mutex).Unlock()
	if !idRE.MatchString(id) {
		return errors.New("invalid deletion target")
	}
	if err := PrivateDir(s.Config.Data); err != nil {
		return err
	}
	root := filepath.Join(s.Config.Data, "artifacts")
	if err := PrivateDir(root); err != nil {
		return err
	}
	target := filepath.Join(root, id)
	if filepath.Dir(target) != root {
		return errors.New("deletion target outside artifact root")
	}
	if _, err := os.Lstat(target); err == nil {
		if err = PrivateDir(target); err != nil {
			return err
		}
		// os.Root confines deletion even if a path below this private directory is
		// exchanged for a symlink. No path from a request becomes a filesystem root.
		dir, err := os.OpenRoot(root)
		if err != nil {
			return err
		}
		err = dir.RemoveAll(id)
		dir.Close()
		if err != nil {
			return err
		}
		parent, err := os.Open(root)
		if err != nil {
			return err
		}
		err = parent.Sync()
		parent.Close()
		if err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	s.Store.mu.Lock()
	defer s.Store.mu.Unlock()
	tx, err := s.Store.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, q := range []string{"DELETE FROM events WHERE batch=?", "DELETE FROM group_members WHERE batch_id=?", "DELETE FROM batches WHERE id=?"} {
		if _, err = tx.Exec(q, id); err != nil {
			return err
		}
	}
	for _, q := range []string{
		"DELETE FROM dispatch_operations WHERE group_id IN (SELECT id FROM dispatch_groups WHERE NOT EXISTS(SELECT 1 FROM group_members WHERE group_id=dispatch_groups.id))",
		"DELETE FROM dispatch_groups WHERE NOT EXISTS(SELECT 1 FROM group_members WHERE group_id=dispatch_groups.id)",
	} {
		if _, err = tx.Exec(q); err != nil {
			return err
		}
	}
	if _, err = tx.Exec("UPDATE deleted_batches SET state='done' WHERE id=?", id); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.live.mu.Lock()
	if f, ok := s.live.nodes[node]; ok && f.Batch == id {
		delete(s.live.nodes, node)
	}
	s.live.mu.Unlock()
	return nil
}
func (s *Server) completeDeletions() {
	rows, err := s.Store.db.Query("SELECT id,node FROM deleted_batches WHERE state='pending' LIMIT 200")
	if err != nil {
		return
	}
	pending := [][2]string{}
	for rows.Next() {
		var id, node string
		if err = rows.Scan(&id, &node); err != nil {
			break
		}
		pending = append(pending, [2]string{id, node})
	}
	if err == nil {
		err = rows.Err()
	}
	rows.Close()
	if err != nil {
		return
	}
	for _, item := range pending {
		if err = s.deleteEvidence(item[0], item[1]); err != nil {
			fmt.Fprintln(os.Stderr, "BITS evidence deletion:", item[0], err)
			s.Store.db.Exec("UPDATE deleted_batches SET state='failed' WHERE id=?", item[0])
		}
	}
}
