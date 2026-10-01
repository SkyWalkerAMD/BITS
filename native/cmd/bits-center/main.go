package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	bits "github.com/SkyWalkerAMD/BITS/native"
)

func show(v any) { b, _ := json.MarshalIndent(v, "", "  "); fmt.Println(string(b)) }
func run() error {
	if len(os.Args) < 2 {
		return errors.New("use bits-center setup | serve | network | status | node-add | batch-add | start | cancel")
	}
	action := os.Args[1]
	if action == "version" || action == "--version" {
		fmt.Println("BITS " + bits.Version)
		return nil
	}
	if action == "network" {
		v, e := bits.Addresses()
		if e == nil {
			show(v)
		}
		return e
	}
	f := flag.NewFlagSet(action, flag.ContinueOnError)
	config := f.String("config", "/etc/bits/center/config.json", "center configuration")
	connection := f.String("connection", "/root/.bits/admin.json", "private administrator connection")
	address := f.String("address", "", "existing center LAN address")
	network := f.String("network", "", "allowed LAN CIDR")
	port := f.Int("port", 443, "HTTPS port")
	check := f.Bool("check", false, "read-only deployment plan")
	apply := f.Bool("apply", false, "create deployment")
	node := f.String("node", "", "node hostname")
	serial := f.String("serial", "", "stable asset serial")
	keepOn := f.Bool("keep-on", false, "keep accepted node powered on after delivery")
	label := f.String("label", "", "batch label")
	batch := f.String("batch", "", "batch ID")
	tasks := f.String("steps", "", "comma-separated tool=seconds, repeats allowed")
	output := f.String("output", "", "private node enrollment file")
	reason := f.String("reason", "", "cancellation/closure reason")
	if err := f.Parse(os.Args[2:]); err != nil {
		return err
	}
	if action == "setup" {
		if *check == *apply {
			return errors.New("choose exactly one of --check or --apply")
		}
		v, e := bits.InitializeCenter(filepath.Dir(*config), "/var/lib/bits/center", *address, *network, *port, *apply)
		if e == nil {
			show(v)
		}
		return e
	}
	if action == "serve" {
		var cfg bits.CenterConfig
		if e := bits.ReadJSON(*config, &cfg); e != nil {
			return e
		}
		if e := bits.ValidateURL(cfg.URL); e != nil {
			return e
		}
		store, e := bits.OpenStore(cfg.Data)
		if e != nil {
			return e
		}
		defer store.Close()
		server := bits.NewServer(store, cfg).HTTPServer()
		stop := make(chan os.Signal, 1)
		signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
		go func() {
			<-stop
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			server.Shutdown(ctx)
		}()
		e = server.ListenAndServeTLS(cfg.Cert, cfg.Key)
		if errors.Is(e, http.ErrServerClosed) {
			return nil
		}
		return e
	}
	var admin bits.AdminConfig
	if err := bits.ReadJSON(*connection, &admin); err != nil {
		return err
	}
	client, err := bits.NewClient(admin.URL, admin.Token, "", admin.CA)
	if err != nil {
		return err
	}
	path, method := "/api/v1/overview", "GET"
	var input any
	switch action {
	case "status":
		if *batch != "" {
			path = "/api/v1/batches/" + *batch
		}
	case "node-add":
		if *output == "" {
			return errors.New("--output is required; node credentials are never printed")
		}
		if _, e := os.Lstat(*output); e == nil {
			return errors.New("existing output retained")
		}
		if e := bits.TrustedDirectory(filepath.Dir(*output)); e != nil {
			return e
		}
		var cfg bits.NodeConfig
		err = client.JSON(context.Background(), "POST", "/api/v1/nodes", "", map[string]any{"id": *node, "serial": *serial, "keep_on": *keepOn}, &cfg)
		if err == nil {
			err = bits.WriteCredential(*output, cfg)
		}
		if err == nil {
			show(map[string]string{"node": cfg.Node, "enrollment_file": *output})
		}
		return err
	case "batch-add":
		p := bits.Plan{Node: *node, Label: *label}
		for _, item := range strings.Split(*tasks, ",") {
			parts := strings.Split(item, "=")
			if len(parts) != 2 {
				return errors.New("use --steps stress=60,stress-ng=60")
			}
			n, e := strconv.Atoi(parts[1])
			if e != nil {
				return e
			}
			p.Steps = append(p.Steps, bits.Step{Tool: parts[0], Seconds: n})
		}
		if err = bits.ValidatePlan(&p); err != nil {
			return err
		}
		path, method, input = "/api/v1/batches", "POST", p
	case "start", "cancel", "close-incomplete":
		path, method = "/api/v1/batches/"+*batch+"/"+action, "POST"
		input = map[string]string{"reason": *reason}
	default:
		return errors.New("unknown command")
	}
	var result any
	err = client.JSON(context.Background(), method, path, "", input, &result)
	if err == nil {
		show(result)
	}
	return err
}
func main() {
	syscall.Umask(0077)
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "BITS center:", err)
		os.Exit(1)
	}
}
