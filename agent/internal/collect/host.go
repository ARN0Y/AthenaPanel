package collect

import (
	"os"
	"os/exec"
	"strconv"
	"strings"
)

// Host is the node's own health. Reported so that "the node is up but its VPN
// engines are dead" is a distinguishable state from "the node is silent" —
// the two need very different responses.
type Host struct {
	UptimeSeconds     uint64
	Load1             float64
	MemTotalBytes     uint64
	MemAvailableBytes uint64
	Xl2tpdOK          bool
	IpsecOK           bool
	AccelPppOK        bool
	WireguardOK       bool
}

func Health(wgIface string) Host {
	h := Host{
		UptimeSeconds:     uptime(),
		Load1:             load1(),
		MemTotalBytes:     meminfo("MemTotal:"),
		MemAvailableBytes: meminfo("MemAvailable:"),
		Xl2tpdOK:          unitActive("xl2tpd"),
		IpsecOK:           unitActive("libreswan") || unitActive("ipsec"),
		AccelPppOK:        unitActive("accel-ppp-sstp"),
	}
	if wgIface != "" {
		_, ok := WgPeers(wgIface)
		h.WireguardOK = ok
	}
	return h
}

func uptime() uint64 {
	b, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return 0
	}
	v, _ := strconv.ParseFloat(f[0], 64)
	return uint64(v)
}

func load1() float64 {
	b, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return 0
	}
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return 0
	}
	v, _ := strconv.ParseFloat(f[0], 64)
	return v
}

func meminfo(key string) uint64 {
	b, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0
	}
	for _, line := range strings.Split(string(b), "\n") {
		if !strings.HasPrefix(line, key) {
			continue
		}
		f := strings.Fields(line)
		if len(f) < 2 {
			return 0
		}
		kb, _ := strconv.ParseUint(f[1], 10, 64)
		return kb * 1024
	}
	return 0
}

// unitActive asks systemd. A missing unit is simply "not active" — a node that
// does not run SSTP is not unhealthy, it just does not offer SSTP.
func unitActive(unit string) bool {
	out, _ := exec.Command("systemctl", "is-active", unit).Output()
	return strings.TrimSpace(string(out)) == "active"
}

func Hostname() string {
	n, err := os.Hostname()
	if err != nil {
		return ""
	}
	return n
}

func Kernel() string {
	b, err := os.ReadFile("/proc/sys/kernel/osrelease")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func OSName() string {
	b, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "PRETTY_NAME=") {
			return strings.Trim(strings.TrimPrefix(line, "PRETTY_NAME="), `"`)
		}
	}
	return ""
}
