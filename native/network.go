package bits

import (
 "database/sql"
 "encoding/json"
 "errors"
 "net"
 "net/url"
 "os"
 "sort"
 "time"
)

type SystemAddress struct {
 Interface string `json:"interface"`
 Address string `json:"address"`
 Prefix int `json:"prefix"`
}
type NodeNetwork struct {
 Primary string `json:"primary"`
 Addresses []SystemAddress `json:"addresses"`
 Received string `json:"received_at,omitempty"`
}

func systemIP(s string) bool {
 ip := net.ParseIP(s)
 return ip != nil && ip.String() == s && ip.IsGlobalUnicast() && !ip.IsLoopback() && !ip.IsLinkLocalUnicast()
}
func (v *NodeNetwork) validate() error {
 if len(v.Addresses) > 64 { return errors.New("too many system addresses") }
 found := v.Primary == "" && len(v.Addresses) == 0
 seen := map[string]bool{}
 for _, a := range v.Addresses {
  bits := 128
  if net.ParseIP(a.Address).To4() != nil { bits = 32 }
  if !systemIP(a.Address) || len(a.Interface) == 0 || len(a.Interface) > 64 || a.Prefix < 0 || a.Prefix > bits || seen[a.Interface+"/"+a.Address] { return errors.New("invalid system network report") }
  for _, c := range a.Interface { if c < 33 || c > 126 { return errors.New("invalid interface name") } }
  seen[a.Interface+"/"+a.Address] = true
  found = found || a.Address == v.Primary
 }
 if !found { return errors.New("primary address must belong to an interface") }
 return nil
}
func collectSystemNetwork(center string) NodeNetwork {
 v := NodeNetwork{Addresses: []SystemAddress{}}
 preferred := ""
 if u, err := url.Parse(center); err == nil && net.ParseIP(u.Hostname()) != nil {
  if c, e := net.DialTimeout("udp", net.JoinHostPort(u.Hostname(), "443"), time.Second); e == nil {
   if a, ok := c.LocalAddr().(*net.UDPAddr); ok { preferred = a.IP.String() }
   c.Close()
  }
 }
 interfaces, _ := net.Interfaces()
 for _, iface := range interfaces {
  if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 { continue }
  addresses, _ := iface.Addrs()
  for _, addr := range addresses {
   ip, network, err := net.ParseCIDR(addr.String())
   if err != nil || !systemIP(ip.String()) { continue }
   prefix, _ := network.Mask.Size()
   v.Addresses = append(v.Addresses, SystemAddress{iface.Name, ip.String(), prefix})
  }
 }
 sort.Slice(v.Addresses, func(i,j int) bool {
  a,b := v.Addresses[i], v.Addresses[j]
  if (a.Address == preferred) != (b.Address == preferred) { return a.Address == preferred }
  if (net.ParseIP(a.Address).To4() != nil) != (net.ParseIP(b.Address).To4() != nil) { return net.ParseIP(a.Address).To4() != nil }
  if a.Interface != b.Interface { return a.Interface < b.Interface }
  return a.Address < b.Address
 })
 if len(v.Addresses) > 64 { v.Addresses = v.Addresses[:64] }
 if len(v.Addresses) > 0 { v.Primary = v.Addresses[0].Address }
 return v
}
func migrateSystemAccess(db *sql.DB, path string) error {
 var schema string
 if err := db.QueryRow("SELECT value FROM metadata WHERE key='schema'").Scan(&schema); err != nil { return err }
 if schema == "5" { return nil }
 if schema != "4" { return errors.New("unsupported system access schema") }
 var count int
 if err := db.QueryRow("SELECT count(*) FROM nodes").Scan(&count); err != nil { return err }
 if count > 0 {
  backup := path+".before-system-access-v4"
  if _, err := os.Lstat(backup); !os.IsNotExist(err) { return errors.New("system access migration backup already exists; inspect before retrying") }
  if _, err := db.Exec("VACUUM INTO ?", backup); err != nil { return err }
  if err := os.Chmod(backup,0600); err != nil { return err }
 }
 tx, err := db.Begin(); if err != nil { return err }; defer tx.Rollback()
 for _, q := range []string{
  "CREATE TABLE node_network(node TEXT PRIMARY KEY REFERENCES nodes(id), body TEXT NOT NULL)",
  "UPDATE metadata SET value='5' WHERE key='schema'",
 } { if _, err = tx.Exec(q); err != nil { return err } }
 return tx.Commit()
}
func (s *Store) SaveNetwork(node string, v NodeNetwork) error {
 if err := v.validate(); err != nil { return err }
 v.Received = UTC()
 body, _ := json.Marshal(v)
 _, err := s.db.Exec("INSERT INTO node_network(node,body) VALUES(?,?) ON CONFLICT(node) DO UPDATE SET body=excluded.body",node,string(body))
 return err
}
func (s *Store) SystemNetwork(node string) (*NodeNetwork,error) {
 var body string
 if err := s.db.QueryRow("SELECT body FROM node_network WHERE node=?",node).Scan(&body); err != nil { return nil,err }
 var v NodeNetwork
 if err := json.Unmarshal([]byte(body),&v); err != nil { return nil,err }
 return &v,nil
}
