package bits

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func Addresses() ([]string, error) {
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil, err
	}
	out := []string{}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			return nil, err
		}
		for _, address := range addresses {
			ip, _, err := net.ParseCIDR(address.String())
			if err == nil && ip.To4() != nil && ip.IsPrivate() {
				out = append(out, iface.Name+" "+address.String())
			}
		}
	}
	return out, nil
}
func InitializeCenter(configDir, data, address, cidr string, port int, apply bool) (map[string]any, error) {
	if os.Geteuid() != 0 {
		return nil, errors.New("center initialization requires root")
	}
	ip := net.ParseIP(address)
	_, network, err := net.ParseCIDR(cidr)
	if err != nil || ip == nil || (!ip.IsPrivate() && !ip.IsLoopback()) || port < 1 || port > 65535 {
		return nil, errors.New("select an existing private/loopback address and bounded management network")
	}
	if !network.Contains(ip) {
		return nil, errors.New("management network does not include the selected address")
	}
	local, _ := net.InterfaceAddrs()
	found := false
	for _, a := range local {
		v, _, e := net.ParseCIDR(a.String())
		if e == nil && v.Equal(ip) {
			found = true
		}
	}
	if !found {
		return nil, errors.New("selected address is not configured on this server")
	}
	for _, tree := range []string{configDir, data} {
		if !filepath.IsAbs(tree) || filepath.Clean(tree) != tree {
			return nil, errors.New("canonical absolute deployment directory required")
		}
		entries, e := os.ReadDir(tree)
		if e == nil && len(entries) > 0 {
			return nil, errors.New("nonempty deployment retained: " + tree)
		}
		if e != nil && !os.IsNotExist(e) {
			return nil, e
		}
	}
	adminDir := "/root/.bits"
	adminPath := filepath.Join(adminDir, "admin.json")
	if _, e := os.Lstat(adminPath); e == nil || !os.IsNotExist(e) {
		return nil, errors.New("existing administrator credential retained")
	}
	for _, path := range []string{filepath.Join(configDir, "config.json"), filepath.Join(configDir, "manifest.json"), filepath.Join(data, "center.sqlite")} {
		if _, e := os.Lstat(path); e == nil || !os.IsNotExist(e) {
			return nil, errors.New("existing deployment retained: " + path)
		}
	}
	serverURL := "https://" + net.JoinHostPort(address, strconv.Itoa(port))
	if port == 443 {
		serverURL = strings.TrimSuffix(serverURL, ":443")
	}
	plan := map[string]any{"address": address, "url": serverURL, "network": cidr, "data": data, "check": !apply,
		"node_inbound_ports": []int{}, "center_tcp_ports": []int{port}, "database": "local SQLite",
		"sckocp_activation_database": false, "existing_network_settings_modified": false}
	if !apply {
		return plan, nil
	}
	account, err := user.Lookup("bits")
	if err != nil {
		return nil, errors.New("install bits-center package first (bits service account missing)")
	}
	uid, _ := strconv.Atoi(account.Uid)
	gid, _ := strconv.Atoi(account.Gid)
	for _, p := range []string{filepath.Dir(configDir), configDir, filepath.Dir(data), data} {
		if err = os.MkdirAll(p, 0700); err != nil {
			return nil, err
		}
		if err = TrustedDirectory(p); err != nil {
			return nil, err
		}
	}
	if err = PrivateDir(configDir); err != nil {
		return nil, err
	}
	if err = PrivateDir(data); err != nil {
		return nil, err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, err
	}
	now := time.Now()
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "BITS center " + address},
		NotBefore: now.Add(-5 * time.Minute), NotAfter: now.AddDate(1, 0, 0), IPAddresses: []net.IP{ip},
		KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true, IsCA: true}
	cert, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	keyBytes, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyBytes})
	token := Random(32)
	cfg := CenterConfig{Address: net.JoinHostPort(address, strconv.Itoa(port)), URL: serverURL, Networks: []string{cidr},
		Data: data, Cert: filepath.Join(configDir, "center.crt"), Key: filepath.Join(configDir, "center.key"), AdminSHA: Digest([]byte(token))}
	if err = Atomic(cfg.Cert, certPEM); err != nil {
		return nil, err
	}
	if err = Atomic(cfg.Key, keyPEM); err != nil {
		return nil, err
	}
	if err = AtomicJSON(filepath.Join(configDir, "config.json"), cfg); err != nil {
		return nil, err
	}
	// The administrator credential stays in a separate root-only location.
	if err = os.MkdirAll(adminDir, 0700); err != nil {
		return nil, err
	}
	if err = AtomicJSON(adminPath, AdminConfig{URL: serverURL, Token: token, CA: string(certPEM)}); err != nil {
		return nil, err
	}
	if err = os.Mkdir(filepath.Join(data, "artifacts"), 0700); err != nil {
		return nil, err
	}
	store, err := OpenStore(data)
	if err != nil {
		return nil, err
	}
	if err = store.Close(); err != nil {
		return nil, err
	}
	// Only these newly-created deployment trees are transferred to the
	// unprivileged service user. Never traverse another system data tree.
	for _, root := range []string{configDir, data} {
		err = filepath.Walk(root, func(p string, info os.FileInfo, e error) error {
			if e != nil {
				return e
			}
			if info.Mode()&os.ModeSymlink != 0 {
				return errors.New("unexpected deployment symlink")
			}
			return os.Chown(p, uid, gid)
		})
		if err != nil {
			return nil, err
		}
	}
	// Parent traversal without disclosing the private child directories.
	for _, p := range []string{filepath.Dir(configDir), filepath.Dir(data)} {
		if err = os.Chmod(p, 0755); err != nil {
			return nil, err
		}
	}
	plan["admin_connection_file"] = adminPath
	plan["next"] = "systemctl enable --now bits-center.service"
	return plan, nil
}
