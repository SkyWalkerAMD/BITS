package bits

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
)

// Data directories are owned by the effective user. Ancestors may be root
// owned, but never symlinks or writable by other users.
func PrivateDir(path string) error {
	if !filepath.IsAbs(path) || filepath.Clean(path)!=path {return errors.New("absolute canonical path required")}
	for current:=path;;current=filepath.Dir(current){
		info,err:=os.Lstat(current);if err!=nil{return err}
		st,ok:=info.Sys().(*syscall.Stat_t)
		if !ok || !info.IsDir() || (st.Uid!=0&&int(st.Uid)!=os.Geteuid()) || info.Mode().Perm()&0022!=0 {
			// Root-owned sticky /tmp is allowed as an ancestor, never as the
			// selected private data directory.
			if !(current!=path&&ok&&info.IsDir()&&st.Uid==0&&info.Mode()&os.ModeSticky!=0){return errors.New("untrusted directory: "+current)}
		}
		if current==filepath.Dir(current){break}
	}
	info,err:=os.Stat(path);if err!=nil{return err}
	if info.Mode().Perm()&0077!=0{return errors.New("private data directory must be mode 0700: "+path)}
	return nil
}
func CheckFileIfExists(path string) error {
	info,err:=os.Lstat(path);if os.IsNotExist(err){return nil};if err!=nil{return err}
	st,ok:=info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || st.Nlink!=1 || (st.Uid!=0&&int(st.Uid)!=os.Geteuid()) || info.Mode().Perm()&0077!=0 {return errors.New("untrusted private file: "+path)}
	return nil
}
func ReadJSON(path string,v any) error {
	if err:=PrivateDir(filepath.Dir(path));err!=nil{return err}
	return readPrivateJSON(path,v)
}
// Credentials remain single-link 0600 files. A root-owned distro home may be
// 0550/0750; directory read/traversal cannot make a 0600 secret readable.
// Unlike runtime state, enrollment need not require its parent to be 0700.
func ReadCredential(path string,v any)error{
	if err:=TrustedDirectory(filepath.Dir(path));err!=nil{return err}
	return readPrivateJSON(path,v)
}
func readPrivateJSON(path string,v any)error{
	if err:=CheckFileIfExists(path);err!=nil{return err}
	fd,err:=syscall.Open(path,syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK,0)
	if err!=nil{return err};f:=os.NewFile(uintptr(fd),path);defer f.Close()
	raw,err:=io.ReadAll(io.LimitReader(f,2<<20+1));if err!=nil{return err};if len(raw)>2<<20{return errors.New("configuration too large")}
	return json.Unmarshal(raw,v)
}
func AtomicJSON(path string,v any) error {
	b,err:=json.MarshalIndent(v,"","  ");if err!=nil{return err};return Atomic(path,append(b,'\n'))
}
func Atomic(path string,raw []byte) error {
	if err:=PrivateDir(filepath.Dir(path));err!=nil{return err}
	return atomicChecked(path,raw)
}
func WriteCredential(path string,v any)error{
	if err:=TrustedDirectory(filepath.Dir(path));err!=nil{return err}
	b,err:=json.MarshalIndent(v,"","  ");if err!=nil{return err}
	return atomicChecked(path,append(b,'\n'))
}
func atomicChecked(path string,raw []byte)error{
	if err:=CheckFileIfExists(path);err!=nil{return err}
	f,err:=os.CreateTemp(filepath.Dir(path),".bits-write-");if err!=nil{return err}
	tmp:=f.Name();defer os.Remove(tmp)
	if _,err=f.Write(raw);err==nil{err=f.Sync()};closeErr:=f.Close();if err==nil{err=closeErr};if err!=nil{return err}
	if err=os.Rename(tmp,path);err!=nil{return err}
	d,err:=os.Open(filepath.Dir(path));if err!=nil{return err};defer d.Close();return d.Sync()
}
