package bits

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode"
)

type SSHProfile struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Username string `json:"username"`
	Port     int    `json:"port"`
	Password string `json:"password,omitempty"`
	Secret   string `json:"secret,omitempty"`
	Revision string `json:"revision"`
}
type remoteConfig struct {
	Profiles map[string]SSHProfile `json:"profiles"`
	Bindings map[string]string     `json:"bindings"`
	Hosts    map[string]string     `json:"hosts"`
}

func (m *RemoteManager) config() (remoteConfig, error) {
	v := remoteConfig{Profiles: map[string]SSHProfile{}, Bindings: map[string]string{}, Hosts: map[string]string{}}
	err := ReadJSON(filepath.Join(m.dir, "ssh.json"), &v)
	if os.IsNotExist(err) {
		err = nil
	}
	if v.Profiles == nil || v.Bindings == nil || v.Hosts == nil {
		return v, errors.New("SSH 配置损坏，请检查中心备份")
	}
	return v, err
}
func (m *RemoteManager) saveConfig(v remoteConfig) error {
	return AtomicJSON(filepath.Join(m.dir, "ssh.json"), v)
}
func (m *RemoteManager) cipher(create bool) (cipher.AEAD, error) {
	var v struct {
		Key []byte `json:"key"`
	}
	file := filepath.Join(m.dir, "ssh-key.json")
	err := ReadJSON(file, &v)
	if os.IsNotExist(err) && create {
		v.Key = make([]byte, 32)
		if _, err = rand.Read(v.Key); err == nil {
			err = AtomicJSON(file, &v)
		}
	}
	if err != nil || len(v.Key) != 32 {
		return nil, errors.New("SSH 模板密钥不可用，请检查中心备份")
	}
	block, err := aes.NewCipher(v.Key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}
func (m *RemoteManager) encrypt(id, password string, create bool) (string, error) {
	a, err := m.cipher(create)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, a.NonceSize())
	if _, err = rand.Read(nonce); err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(a.Seal(nonce, nonce, []byte(password), []byte(id))), nil
}
func (m *RemoteManager) decrypt(p SSHProfile) (string, error) {
	a, err := m.cipher(false)
	if err != nil {
		return "", err
	}
	raw, err := base64.StdEncoding.DecodeString(p.Secret)
	if err != nil || len(raw) < a.NonceSize() {
		return "", errors.New("SSH 模板密码不可用")
	}
	text, err := a.Open(nil, raw[:a.NonceSize()], raw[a.NonceSize():], []byte(p.ID))
	if err != nil {
		return "", errors.New("SSH 模板密码不可用")
	}
	return string(text), nil
}
func validSSHLogin(user, password string) bool {
	if len(user) == 0 || len(user) > 64 || len(password) == 0 || len(password) > 1024 || strings.ContainsRune(password, 0) {
		return false
	}
	for _, c := range user {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("_.@-", c)) {
			return false
		}
	}
	return true
}
func profileViews(v remoteConfig) []SSHProfile {
	out := []SSHProfile{}
	for _, p := range v.Profiles {
		p.Password = ""
		p.Secret = ""
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}
func (m *RemoteManager) saveProfile(p SSHProfile) (SSHProfile, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	v, err := m.config()
	if err != nil {
		return SSHProfile{}, err
	}
	if p.Secret != "" || len([]rune(p.Name)) > 80 || strings.TrimSpace(p.Name) == "" || strings.IndexFunc(p.Name, unicode.IsControl) >= 0 || p.Port < 1 || p.Port > 65535 {
		return SSHProfile{}, errors.New("请填写模板名称、系统账号和有效 SSH 端口")
	}
	old, exists := v.Profiles[p.ID]
	if p.ID == "" {
		if len(v.Profiles) >= 100 {
			return SSHProfile{}, errors.New("最多保存 100 个 SSH 模板")
		}
		p.ID = Random(16)
	} else if !exists || old.Revision != p.Revision {
		return SSHProfile{}, errors.New("模板已变更，请刷新后重试")
	}
	password := p.Password
	if password == "" && exists {
		password, err = m.decrypt(old)
		if err != nil {
			return SSHProfile{}, err
		}
	}
	if !validSSHLogin(p.Username, password) {
		return SSHProfile{}, errors.New("请填写有效系统账号和密码")
	}
	p.Secret, err = m.encrypt(p.ID, password, len(v.Profiles) == 0)
	if err != nil {
		return SSHProfile{}, err
	}
	p.Password = ""
	p.Revision = Random(16)
	v.Profiles[p.ID] = p
	if err = m.saveConfig(v); err != nil {
		return SSHProfile{}, err
	}
	p.Secret = ""
	return p, nil
}
func (m *RemoteManager) deleteProfile(id, revision string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	v, err := m.config()
	if err != nil {
		return err
	}
	p, ok := v.Profiles[id]
	if !ok || p.Revision != revision {
		return errors.New("模板已变更，请刷新后重试")
	}
	delete(v.Profiles, id)
	for node, profile := range v.Bindings {
		if profile == id {
			delete(v.Bindings, node)
		}
	}
	return m.saveConfig(v)
}
