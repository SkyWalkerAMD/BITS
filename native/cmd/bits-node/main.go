package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	bits "github.com/SkyWalkerAMD/BITS/native"
)

func run()error{
	if len(os.Args)<2{return errors.New("use bits-node enroll | agent | check | status")}
	action:=os.Args[1]
	if action=="version"||action=="--version"{fmt.Println("BITS "+bits.Version);return nil}
	f:=flag.NewFlagSet(action,flag.ContinueOnError)
	config:=f.String("config","/etc/bits/node/connection.json","private node connection")
	enrollment:=f.String("file","","enrollment file exported from center")
	once:=f.Bool("once",false,"process pending explicit command or recovery once")
	if err:=f.Parse(os.Args[2:]);err!=nil{return err}
	if os.Geteuid()!=0{return errors.New("node operations require root")}
	data:="/var/lib/bits/node"
	if action=="enroll"{
		if *enrollment==""{return errors.New("--file is required")}
		cfg,err:=bits.LoadNodeConfig(*enrollment);if err!=nil{return err}
		if _,err=os.Lstat(*config);err==nil{return errors.New("existing node configuration retained")}
		for _,old:=range []string{"/root/ocrun/ocb","/etc/bits/node/native.json","/etc/ocrun-node/native.json"}{
			if _,err=os.Lstat(old);err==nil{return errors.New("existing OCRUN/BITS deployment retained; use BITS-o or explicit migration")}
		}
		for _,dir:=range []string{filepath.Dir(*config),data}{
			if err=os.MkdirAll(dir,0700);err!=nil{return err}
			if err=bits.PrivateDir(dir);err!=nil{return err}
		}
		if err=bits.AtomicJSON(*config,cfg);err!=nil{return err}
		fmt.Println("Enrolled. Install/activate original sckocp separately, then: systemctl enable --now bits-node.service")
		return nil
	}
	cfg,err:=bits.LoadNodeConfig(*config);if err!=nil{return err}
	if action=="check"{fmt.Printf("BITS %s; node=%s; center=%s; local sckocp authorization is managed separately\n",bits.Version,cfg.Node,cfg.URL);return nil}
	if action=="status"{
		entries,err:=os.ReadDir(filepath.Join(data,"runs"));if err!=nil{return err}
		for _,e:=range entries{var run bits.LocalRun;if err=bits.ReadJSON(filepath.Join(data,"runs",e.Name(),"run.json"),&run);err!=nil{return err};raw,_:=json.Marshal(run);fmt.Println(string(raw))}
		return nil
	}
	if action!="agent"{return errors.New("unknown command")}
	agent,err:=bits.NewAgent(cfg,data);if err!=nil{return err}
	ctx,cancel:=signal.NotifyContext(context.Background(),syscall.SIGTERM,syscall.SIGINT);defer cancel()
	return agent.Run(ctx,*once)
}
func main(){syscall.Umask(0077);if err:=run();err!=nil{fmt.Fprintln(os.Stderr,"BITS node:",err);os.Exit(1)}}
