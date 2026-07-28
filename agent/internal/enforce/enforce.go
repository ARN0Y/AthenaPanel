// Package enforce carries out what the ledger decides: dropping a user's
// sessions and keeping the node's account list in step with the hub.
//
// Everything here is deliberately the same mechanism the panel already uses on
// the master — SIGTERM to pppd, an atomically replaced chap-secrets — so a node
// behaves identically to node 1 rather than being a second implementation with
// its own bugs.
package enforce

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

// Disconnect drops the given pppd processes.
//
// SIGTERM, not SIGKILL: pppd runs the ip-down script on a clean shutdown, which
// is what reports the session's final bytes. Killing it outright would strand
// that traffic and lose the very accounting this exists to protect.
func Disconnect(pids []int32) (killed int, err error) {
	var firstErr error
	for _, pid := range pids {
		if pid <= 0 {
			continue
		}
		if e := syscall.Kill(int(pid), syscall.SIGTERM); e != nil {
			// ESRCH just means it already exited, which is the outcome we wanted.
			if e != syscall.ESRCH && firstErr == nil {
				firstErr = e
			}
			continue
		}
		killed++
	}
	return killed, firstErr
}

// DisconnectAccel drops a user's SSTP sessions, which accel-ppp owns rather
// than pppd, so a signal to a pid would not reach them.
func DisconnectAccel(username string) error {
	if _, err := exec.LookPath("accel-cmd"); err != nil {
		return nil // no accel-ppp on this node; nothing to do
	}
	return exec.Command("accel-cmd", "terminate", "username", username).Run()
}

// User is one account as the hub described it.
type User struct {
	Username     string
	Password     string
	Enabled      bool
	RateDownKbps uint32
	RateUpKbps   uint32
	L2tpMode     string
	Outbound     string
}

// WriteChapSecrets replaces the node's account file.
//
// Only enabled accounts are written, so a disabled or over-quota user cannot
// authenticate at all — the same rule the panel applies on the master. The file
// is replaced atomically because pppd re-reads it on every authentication, and
// a half-written file would refuse everyone for as long as it took to finish.
func WriteChapSecrets(path, serverField string, users []User) (written int, err error) {
	if serverField == "" {
		serverField = "*"
	}
	sorted := make([]User, len(users))
	copy(sorted, users)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Username < sorted[j].Username })

	var b strings.Builder
	b.WriteString("# Managed by athena-agent. DO NOT EDIT BY HAND.\n")
	b.WriteString("# client\tserver\tsecret\tIP\n")
	for _, u := range sorted {
		if !u.Enabled {
			continue
		}
		fmt.Fprintf(&b, "%s\t%s\t%s\t*\n", quote(u.Username), serverField, quote(u.Password))
		written++
	}

	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 0, err
	}
	tmp, err := os.CreateTemp(dir, ".chap-secrets.*.tmp")
	if err != nil {
		return 0, err
	}
	defer os.Remove(tmp.Name())

	if _, err := tmp.WriteString(b.String()); err != nil {
		tmp.Close()
		return 0, err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return 0, err
	}
	if err := tmp.Close(); err != nil {
		return 0, err
	}
	if err := os.Chmod(tmp.Name(), 0o600); err != nil {
		return 0, err
	}
	// Rename is atomic within a filesystem: readers see the old file or the new
	// one, never a partial one.
	if err := os.Rename(tmp.Name(), path); err != nil {
		return 0, err
	}
	return written, nil
}

func quote(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	return `"` + v + `"`
}

// ReloadAccel makes accel-ppp pick up a rewritten chap-secrets. pppd re-reads
// it per authentication and needs nothing.
func ReloadAccel() {
	if _, err := exec.LookPath("accel-cmd"); err != nil {
		return
	}
	_ = exec.Command("accel-cmd", "reload").Run()
}
