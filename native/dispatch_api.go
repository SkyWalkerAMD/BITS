package bits

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
)

func (s *Server) dispatchAPI(w http.ResponseWriter, r *http.Request, path string) error {
	switch path {
	case "bmc-profiles":
		if r.Method=="GET" { respond(w,s.autoBMC.profiles()); return nil }
		if r.Method=="POST" {
			var in BMCProfile; if err:=decodeBounded(r,&in,8192);err!=nil{return err}
			if in.Revision!="" { return errors.New("模板版本由中心生成") }
			if err:=s.autoBMC.profile(in);err!=nil{return err}
			respond(w,map[string]bool{"saved":true,"power_command_sent":false});return nil
		}
	case "bmc-discoveries":
		if r.Method=="GET" {
			nodes,err:=s.Store.Nodes();if err!=nil{return err};out:=map[string]AutoBMCView{}
			for _,n:=range nodes {out[n.ID]=s.autoBMC.view(n.ID)};respond(w,out);return nil
		}
	case "bmc-retry":
		if r.Method=="POST" {
			var in struct{Node string `json:"node"`};if err:=decodeBounded(r,&in,1024);err!=nil{return err}
			if err:=s.autoBMC.retry(in.Node);err!=nil{return err};respond(w,map[string]bool{"queued":true,"power_command_sent":false});return nil
		}
	case "nodes":
		if r.Method != "GET" {
			break
		}
		v, err := s.Store.Availability(s.power.Snapshot())
		if err == nil {
			respond(w, map[string]any{"nodes": v, "time": UTC()})
		}
		return err
	case "bmc":
		if r.Method != "POST" {
			break
		}
		var in struct {
			Node     string `json:"node"`
			Address  string `json:"address"`
			Username string `json:"username"`
			Password string `json:"password"`
			Cipher   int    `json:"cipher"`
		}
		if err := decode(r, &in); err != nil {
			return err
		}
		// Serialize with every batch start; never change a power mapping in use.
		s.Store.mu.Lock()
		defer s.Store.mu.Unlock()
		var count int
		if err := s.Store.db.QueryRow("SELECT count(*) FROM nodes WHERE id=?", in.Node).Scan(&count); err != nil {
			return err
		}
		if count != 1 {
			return errors.New("请先注册该节点")
		}
		if err := s.Store.db.QueryRow("SELECT count(*) FROM batches WHERE node=? AND state IN ('waiting_boot','armed','running','finishing','needs_attention')", in.Node).Scan(&count); err != nil {
			return err
		}
		if count != 0 {
			return errors.New("节点仍有未完成批次，BMC 绑定保持原样")
		}
		if err := s.power.Bind(BMCBinding{Node: in.Node, Address: in.Address, Username: in.Username, Password: in.Password, Cipher: in.Cipher}); err != nil {
			return err
		}
		respond(w, map[string]any{"node": in.Node, "status": "configured", "power_command_sent": false})
		return nil
	case "templates":
		if r.Method == "GET" {
			v, err := s.Store.Templates()
			if err == nil {
				respond(w, v)
			}
			return err
		}
		if r.Method == "POST" {
			var in TaskTemplate
			if err := decode(r, &in); err != nil {
				return err
			}
			v, err := s.Store.SaveTemplate(in)
			if err == nil {
				respond(w, v)
			}
			return err
		}
	case "groups":
		if r.Method == "GET" {
			offset := 0
			var err error
			if r.URL.Query().Get("offset") != "" {
				offset, err = strconv.Atoi(r.URL.Query().Get("offset"))
				if err != nil {
					return errors.New("invalid offset")
				}
			}
			v, err := s.Store.Groups(offset)
			if err == nil {
				respond(w, v)
			}
			return err
		}
		if r.Method == "POST" {
			var in GroupPlan
			if err := decode(r, &in); err != nil {
				return err
			}
			v, err := s.Store.CreateGroup(in, s.power.Snapshot())
			if err == nil {
				respond(w, v)
			}
			return err
		}
	}
	parts := strings.Split(path, "/")
	if len(parts) >= 2 && parts[0] == "groups" && idRE.MatchString(parts[1]) {
		if len(parts) == 2 && r.Method == "GET" {
			v, err := s.Store.Group(parts[1])
			if err == nil {
				respond(w, v)
			}
			return err
		}
		if len(parts) == 3 && parts[2] == "operations" && r.Method == "GET" {
			v, err := s.Store.GroupOperations(parts[1])
			if err == nil {
				respond(w, v)
			}
			return err
		}
		if len(parts) == 3 && r.Method == "POST" && (parts[2] == "start" || parts[2] == "cancel") {
			var in GroupAction
			if err := decode(r, &in); err != nil {
				return err
			}
			v, err := s.Store.GroupAction(parts[1], parts[2], in, s.power.Snapshot())
			if err == nil {
				respond(w, v)
			}
			return err
		}
	}
	return errors.New("unknown dispatch operation")
}
