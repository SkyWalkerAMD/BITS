package bits

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"strconv"
)

// The interpreter keeps the remover in memory after the package deletes its
// own binary. A private resume copy is retained only after an interrupted purge.
//
//go:embed package_lifecycle.py
var packageLifecycle string

//go:embed package_uninstall.py
var packageUninstall string

func Uninstall(role string, args []string) error {
	if os.Geteuid() != 0 {
		return fmt.Errorf("uninstall requires root")
	}
	if role != "center" && role != "node" {
		return fmt.Errorf("unknown package role")
	}
	source := "LIFECYCLE = " + strconv.Quote(packageLifecycle) + "\nUNINSTALL = " + strconv.Quote(packageUninstall) + "\nexec(UNINSTALL)\n"
	arguments := append([]string{"-I", "-B", "-c", source, role, Version}, args...)
	cmd := exec.Command("/usr/bin/python3", arguments...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("uninstall did not complete: %w", err)
	}
	return nil
}
