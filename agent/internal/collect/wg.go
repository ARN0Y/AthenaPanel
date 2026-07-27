package collect

import (
	"golang.zx2c4.com/wireguard/wgctrl"
)

// WgPeer is one WireGuard peer with ABSOLUTE counters since it was added.
type WgPeer struct {
	PublicKey     string
	RxBytes       uint64
	TxBytes       uint64
	LastHandshake int64 // unix seconds, 0 = never
	Address       string
}

// WgPeers reads every peer on the given interface via netlink.
//
// Deliberately not shelling out to `wg show`: parsing another process's text
// output means a format change becomes a silent accounting bug, and it costs a
// fork per poll. wgctrl talks the same netlink protocol wg(8) does.
//
// The bool reports whether the interface could be read at all, so "no peers"
// and "no WireGuard here" stay distinguishable.
func WgPeers(iface string) ([]WgPeer, bool) {
	c, err := wgctrl.New()
	if err != nil {
		return nil, false
	}
	defer c.Close()

	dev, err := c.Device(iface)
	if err != nil {
		return nil, false
	}
	out := make([]WgPeer, 0, len(dev.Peers))
	for _, p := range dev.Peers {
		var hs int64
		if !p.LastHandshakeTime.IsZero() {
			hs = p.LastHandshakeTime.Unix()
		}
		addr := ""
		if len(p.AllowedIPs) > 0 {
			addr = p.AllowedIPs[0].IP.String()
		}
		out = append(out, WgPeer{
			PublicKey: p.PublicKey.String(),
			// wgctrl is named from the server's point of view, matching the
			// hub's convention: Receive = from the peer = the user's upload.
			RxBytes:       uint64(max64(p.ReceiveBytes, 0)),
			TxBytes:       uint64(max64(p.TransmitBytes, 0)),
			LastHandshake: hs,
			Address:       addr,
		})
	}
	return out, true
}

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
