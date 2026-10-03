package bits

import (
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"os"
	"path"
	"sort"
	"strconv"
	"strings"
	"time"
)

const remoteFileLimit int64 = 1 << 30

func (s *Server) remoteAPI(w http.ResponseWriter, req *http.Request, route string) error {
	m := s.remote
	owner := remoteOwner(req)
	if owner == "" {
		return errors.New("登录后才能访问系统终端")
	}
	if route == "config" && req.Method == "GET" {
		m.mu.Lock()
		v, e := m.config()
		m.mu.Unlock()
		if e != nil {
			return e
		}
		node := req.URL.Query().Get("node")
		respond(w, map[string]any{"profiles": profileViews(v), "profile": v.Bindings[node]})
		return nil
	}
	if route == "profiles" && req.Method == "POST" {
		var in SSHProfile
		if e := decodeBounded(req, &in, 8192); e != nil {
			return e
		}
		p, e := m.saveProfile(in)
		if e == nil {
			respond(w, p)
		}
		return e
	}
	if strings.HasPrefix(route, "profiles/") && req.Method == "DELETE" {
		var in struct {
			Revision string `json:"revision"`
		}
		if e := decodeBounded(req, &in, 1024); e != nil {
			return e
		}
		e := m.deleteProfile(strings.TrimPrefix(route, "profiles/"), in.Revision)
		if e == nil {
			respond(w, map[string]bool{"ok": true})
		}
		return e
	}
	if route == "connect" && req.Method == "POST" {
		var in RemoteConnect
		if e := decodeBounded(req, &in, 8192); e != nil {
			return e
		}
		expires := time.Now().Add(8 * time.Hour)
		s.mu.Lock()
		if t, ok := s.sessions[strings.TrimPrefix(owner, "session:")]; ok {
			expires = t
		}
		s.mu.Unlock()
		r, trust, e := s.connectRemote(req.Context(), owner, expires, in)
		if e != nil {
			return e
		}
		if trust != nil {
			respond(w, map[string]any{"state": "host_key", "host_key": trust})
			return nil
		}
		respond(w, map[string]any{"state": "connected", "id": r.id, "username": r.username, "address": r.address, "port": r.port, "sftp": r.files != nil})
		return nil
	}
	if route == "host-key" && req.Method == "DELETE" {
		var in struct {
			Node     string `json:"node"`
			Address  string `json:"address"`
			Port     int    `json:"port"`
			Previous string `json:"previous"`
		}
		if e := decodeBounded(req, &in, 2048); e != nil {
			return e
		}
		if e := s.remoteTarget(in.Node, in.Address); e != nil {
			return e
		}
		key := in.Node + "/" + net.JoinHostPort(in.Address, strconv.Itoa(in.Port))
		m.mu.Lock()
		defer m.mu.Unlock()
		v, e := m.config()
		if e != nil {
			return e
		}
		if in.Previous == "" || v.Hosts[key] != in.Previous {
			return errors.New("主机指纹已变更，请重新检查")
		}
		delete(v.Hosts, key)
		if e = m.saveConfig(v); e != nil {
			return e
		}
		respond(w, map[string]bool{"ok": true})
		return nil
	}
	parts := strings.Split(route, "/")
	if len(parts) != 3 || parts[0] != "sessions" || !idRE.MatchString(parts[1]) {
		return errors.New("unknown SSH operation")
	}
	m.mu.Lock()
	r := m.sessions[parts[1]]
	m.mu.Unlock()
	if r == nil || r.owner != owner {
		return errors.New("SSH 会话不存在或已关闭")
	}
	if parts[2] == "close" && req.Method == "POST" {
		r.close("已断开")
		respond(w, map[string]bool{"ok": true})
		return nil
	}
	var disabled bool
	if e := s.Store.db.QueryRow("SELECT disabled FROM nodes WHERE id=?", r.node).Scan(&disabled); e != nil || disabled {
		r.close("节点不可用")
		return errors.New("节点不可用")
	}
	r.mu.Lock()
	ended := r.ended
	expired := time.Now().After(r.expires) || time.Since(r.lastInput) > 30*time.Minute
	r.mu.Unlock()
	if expired {
		r.close("会话超时，请重新连接")
		return errors.New("会话超时，请重新连接")
	}
	if parts[2] == "output" && req.Method == "GET" {
		offset, e := strconv.ParseInt(req.URL.Query().Get("offset"), 10, 64)
		if e != nil || offset < 0 {
			return errors.New("invalid output position")
		}
		raw, next, lost, done, reason := r.output(req.Context(), offset)
		respond(w, map[string]any{"data": raw, "offset": next, "truncated": lost, "closed": done, "reason": reason})
		return nil
	}
	if ended {
		return errors.New("SSH 会话已结束，请重新连接")
	}
	if parts[2] == "input" && req.Method == "POST" {
		var in struct {
			Data string `json:"data"`
			Cols int    `json:"cols"`
			Rows int    `json:"rows"`
		}
		if e := decodeBounded(req, &in, 64<<10); e != nil {
			return e
		}
		if len(in.Data) > 16<<10 || ((in.Cols != 0 || in.Rows != 0) && !validTerminalSize(in.Cols, in.Rows)) {
			return errors.New("terminal input exceeds limits")
		}
		r.inputMu.Lock()
		defer r.inputMu.Unlock()
		timer := time.AfterFunc(15*time.Second, func() { r.close("SSH 输入超时") })
		defer timer.Stop()
		if in.Cols != 0 {
			if e := r.terminal.WindowChange(in.Rows, in.Cols); e != nil {
				return errors.New("终端尺寸更新失败")
			}
		}
		if in.Data != "" {
			if _, e := io.WriteString(r.stdin, in.Data); e != nil {
				return errors.New("终端输入失败")
			}
			r.mu.Lock()
			r.lastInput = time.Now()
			r.mu.Unlock()
		}
		respond(w, map[string]bool{"ok": true})
		return nil
	}
	if r.files == nil {
		return errors.New("服务器未启用 SFTP")
	}
	if !r.fileMu.TryLock() {
		return errors.New("文件操作进行中，请稍后重试")
	}
	defer r.fileMu.Unlock()
	timer := time.AfterFunc(4*time.Minute, func() { r.close("文件传输超时") })
	defer timer.Stop()
	r.mu.Lock()
	r.lastInput = time.Now()
	r.mu.Unlock()
	file := req.URL.Query().Get("path")
	if file == "" && parts[2] == "files" {
		var e error
		file, e = r.files.Getwd()
		if e != nil {
			return errors.New("无法读取登录目录")
		}
	}
	if len(file) > 4096 || !strings.HasPrefix(file, "/") || strings.ContainsAny(file, "\x00\r\n") || path.Clean(file) != file {
		return errors.New("请输入有效的绝对路径")
	}
	if parts[2] == "files" && req.Method == "GET" {
		entries, e := r.files.ReadDirContext(req.Context(), file)
		if e != nil {
			return errors.New("无法读取目录，请检查路径和账号权限")
		}
		sort.Slice(entries, func(i, j int) bool {
			if entries[i].IsDir() != entries[j].IsDir() {
				return entries[i].IsDir()
			}
			return entries[i].Name() < entries[j].Name()
		})
		more := len(entries) > 2000
		if more {
			entries = entries[:2000]
		}
		out := []map[string]any{}
		for _, v := range entries {
			out = append(out, map[string]any{"name": v.Name(), "directory": v.IsDir(), "symlink": v.Mode()&os.ModeSymlink != 0, "bytes": v.Size(), "modified": v.ModTime().UTC().Format(time.RFC3339), "mode": v.Mode().String()})
		}
		respond(w, map[string]any{"path": file, "entries": out, "truncated": more})
		return nil
	}
	if parts[2] == "download" && req.Method == "GET" {
		f, e := r.files.Open(file)
		if e != nil {
			return errors.New("无法打开文件，请检查账号权限")
		}
		defer f.Close()
		stat, e := f.Stat()
		if e != nil || !stat.Mode().IsRegular() || stat.Size() < 0 || stat.Size() > remoteFileLimit {
			return errors.New("仅支持下载 1 GiB 以内的普通文件")
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": path.Base(file)}))
		w.Header().Set("Content-Length", strconv.FormatInt(stat.Size(), 10))
		if _, e = io.CopyN(w, f, stat.Size()); e != nil {
			r.close("文件下载中断")
		}
		return nil
	}
	if parts[2] == "upload" && req.Method == "POST" {
		if req.ContentLength < 0 || req.ContentLength > remoteFileLimit {
			return errors.New("单次上传上限 1 GiB")
		}
		if _, e := r.files.Lstat(file); e == nil {
			return errors.New("同名文件已存在，请修改文件名后上传")
		} else if !os.IsNotExist(e) {
			return errors.New("无法检查目标路径")
		}
		temp := path.Join(path.Dir(file), ".bits-upload-"+Random(16))
		f, e := r.files.OpenFile(temp, os.O_WRONLY|os.O_CREATE|os.O_EXCL)
		if e != nil {
			return errors.New("无法创建文件，请检查目录权限")
		}
		committed := false
		defer func() {
			f.Close()
			if !committed {
				r.files.Remove(temp)
			}
		}()
		if e = f.Chmod(0600); e != nil {
			return errors.New("无法设置上传文件权限")
		}
		n, e := io.Copy(f, io.LimitReader(req.Body, req.ContentLength+1))
		if e != nil || n != req.ContentLength || req.Context().Err() != nil {
			return errors.New("上传中断，目标文件未替换")
		}
		if e = f.Close(); e != nil {
			return errors.New("上传文件保存失败")
		}
		// Standard SFTP rename fails if the target exists; never use overwrite extensions.
		if e = r.files.Rename(temp, file); e != nil {
			return errors.New("无法保存文件，目标可能已存在")
		}
		committed = true
		respond(w, map[string]any{"ok": true, "bytes": n})
		return nil
	}
	return errors.New("unknown SFTP operation")
}
