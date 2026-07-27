// Package collect reads what this node is actually doing, straight from the
// kernel. Nothing here mutates anything: the agent observes, the hub decides.
package collect

import (
	"os"
	"strconv"
	"strings"
)

// Ppp is one live ppp interface with ABSOLUTE counters since it came up.
type Ppp struct {
	Ifname  string
	RxBytes uint64 // from the client  (the user's upload)
	TxBytes uint64 // toward the client (the user's download)
	PeerIP  string
	Pid     int32
}

const netDir = "/sys/class/net"

// PppInterfaces lists every live ppp* interface with its counters.
//
// The bool reports whether the enumeration itself succeeded. It is not a
// nicety: an empty list because /sys could not be read looks identical to an
// empty list because everybody disconnected, and the hub finalizes sessions
// based on that difference. When it is false the caller must send no session
// list at all rather than an empty one.
func PppInterfaces() ([]Ppp, bool) {
	entries, err := os.ReadDir(netDir)
	if err != nil {
		return nil, false
	}
	out := make([]Ppp, 0, 16)
	for _, e := range entries {
		name := e.Name()
		if !strings.HasPrefix(name, "ppp") {
			continue
		}
		rx, okRx := readUint(netDir + "/" + name + "/statistics/rx_bytes")
		tx, okTx := readUint(netDir + "/" + name + "/statistics/tx_bytes")
		if !okRx || !okTx {
			// The interface vanished between the listing and the read. Skipping
			// it is right: it is genuinely gone, and reporting zeros would look
			// like a counter reset.
			continue
		}
		out = append(out, Ppp{
			Ifname:  name,
			RxBytes: rx,
			TxBytes: tx,
			PeerIP:  peerIP(name),
			Pid:     pppPid(name),
		})
	}
	return out, true
}

// peerIP is the address handed to the client, read from the interface's
// point-to-point peer. It is what the hub uses to tell the L2TP engines apart
// (each hands out from its own pool).
func peerIP(ifname string) string {
	// /proc/net/route and ip(8) both work, but the peer address is exposed
	// directly by the kernel for point-to-point links.
	b, err := os.ReadFile("/sys/class/net/" + ifname + "/ifindex")
	if err != nil {
		return ""
	}
	_ = b
	return peerFromProcRoute(ifname)
}

// peerFromProcRoute finds the /32 route the kernel installs for a ppp peer.
func peerFromProcRoute(ifname string) string {
	data, err := os.ReadFile("/proc/net/route")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n")[1:] {
		f := strings.Fields(line)
		if len(f) < 8 || f[0] != ifname {
			continue
		}
		// Host route (mask 0xFFFFFFFF) => the peer address.
		if strings.ToUpper(f[7]) != "FFFFFFFF" {
			continue
		}
		if ip := hexLEToIP(f[1]); ip != "" {
			return ip
		}
	}
	return ""
}

// hexLEToIP converts /proc/net/route's little-endian hex address to dotted quad.
func hexLEToIP(h string) string {
	v, err := strconv.ParseUint(h, 16, 32)
	if err != nil {
		return ""
	}
	return strconv.FormatUint(v&0xFF, 10) + "." +
		strconv.FormatUint((v>>8)&0xFF, 10) + "." +
		strconv.FormatUint((v>>16)&0xFF, 10) + "." +
		strconv.FormatUint((v>>24)&0xFF, 10)
}

// pppPid recovers the pppd pid from the file pppd writes per interface.
// Only meaningful on this machine — the hub must never act on it remotely.
func pppPid(ifname string) int32 {
	b, err := os.ReadFile("/var/run/" + ifname + ".pid")
	if err != nil {
		return 0
	}
	n, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 32)
	if err != nil {
		return 0
	}
	return int32(n)
}

func readUint(path string) (uint64, bool) {
	b, err := os.ReadFile(path)
	if err != nil {
		return 0, false
	}
	v, err := strconv.ParseUint(strings.TrimSpace(string(b)), 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}
