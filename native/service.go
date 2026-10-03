package bits

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

// Setup/enrollment is the one-time authorization to start the installed service.
func EnableService(role string) error {
	if role != "center" && role != "node" {
		return fmt.Errorf("unknown service role")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	systemctl := "/usr/bin/systemctl"
	if _, err := os.Stat(systemctl); os.IsNotExist(err) {
		// Debian 11 also supports systems without the merged /usr layout.
		systemctl = "/bin/systemctl"
	}
	cmd := exec.CommandContext(ctx, systemctl, "enable", "--now", "bits-"+role+".service")
	cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("configuration saved; start bits-%s.service after checking systemd: %w", role, err)
	}
	return nil
}

// The package installer takes an exclusive lock. The agent takes a shared lock
// over each polling/execution cycle, including the idle power policy.
func maintenanceGate(data string) (*os.File, error) {
	path := filepath.Join(data, "maintenance.lock")
	if err := CheckFileIfExists(path); err != nil {
		return nil, err
	}
	return os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
}
