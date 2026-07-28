package enforce

import (
	"encoding/base64"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"strings"

	"golang.zx2c4.com/wireguard/wgctrl"
	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

// WgPeer is one account's WireGuard peer as the hub described it.
type WgPeer struct {
	PublicKey    string
	PresharedKey string
	Address      string // a bare address or a CIDR; the /32 is implied
}

// MarkerFor is the file node-bootstrap writes beside an interface it created
// for the panel.
//
// It exists because this agent runs as root on machines that already do other
// things. A WireGuard interface on such a box is very often infrastructure — a
// backhaul, a WARP tunnel, a relay — and SyncWgPeers replaces the peer list
// wholesale. Pointing the agent at one of those by mistake, or inheriting a
// default that happens to match, would silently delete a tunnel that nothing in
// the panel knows about and nobody would connect to this process.
//
// So the rule is: the agent manages an interface it was GIVEN, never one it
// merely found. Reading counters is always allowed; writing needs the marker.
func MarkerFor(iface string) string {
	return "/etc/wireguard/" + iface + ".athena"
}

// WgManaged reports whether this interface is the panel's to rewrite.
func WgManaged(iface string) bool {
	if iface == "" {
		return false
	}
	_, err := os.Stat(MarkerFor(iface))
	return err == nil
}

// SyncWgPeers makes the node's WireGuard interface hold exactly the given set.
//
// A DIFF, deliberately, not ReplacePeers. The end state is identical either
// way, but replacing the list tears down and recreates every peer — the live
// session, its handshake and its counters all go — so a customer who was
// browsing gets a stall until their client rekeys. Since a sync is sent for ANY
// account change on the node, that would mean every WireGuard user on a node
// hiccups whenever anyone else's account is edited. Peers that have not changed
// are left strictly alone.
//
// Revocation is still exact: a key absent from the hub's list is removed, and
// removal takes effect on the next packet because WireGuard has no session to
// tear down. That is why this is both the provisioning and the enforcement path
// — and why it refuses to touch an unmarked interface at all.
func SyncWgPeers(iface string, peers []WgPeer) (applied int, err error) {
	if iface == "" {
		return 0, nil
	}
	if !WgManaged(iface) {
		return 0, fmt.Errorf(
			"refusing to rewrite %q: no %s — the panel only manages interfaces node-bootstrap created for it",
			iface, MarkerFor(iface))
	}
	c, err := wgctrl.New()
	if err != nil {
		return 0, fmt.Errorf("wgctrl: %w", err)
	}
	defer c.Close()
	dev, err := c.Device(iface)
	if err != nil {
		return 0, fmt.Errorf("no wireguard interface %q: %w", iface, err)
	}

	current := make(map[wgtypes.Key]wgtypes.Peer, len(dev.Peers))
	for _, p := range dev.Peers {
		current[p.PublicKey] = p
	}

	var cfgs []wgtypes.PeerConfig
	wanted := make(map[wgtypes.Key]bool, len(peers))
	for _, p := range peers {
		key, err := wgtypes.ParseKey(p.PublicKey)
		if err != nil {
			// One malformed key must not cost every other peer their service.
			continue
		}
		ipnet, err := allowedIP(p.Address)
		if err != nil {
			continue
		}
		wanted[key] = true
		applied++

		if have, ok := current[key]; ok && peerMatches(have, *ipnet, p.PresharedKey) {
			continue // already exactly right — do not disturb a live session
		}
		pc := wgtypes.PeerConfig{
			PublicKey:         key,
			ReplaceAllowedIPs: true,
			AllowedIPs:        []net.IPNet{*ipnet},
		}
		if p.PresharedKey != "" {
			if psk, err := wgtypes.ParseKey(p.PresharedKey); err == nil {
				pc.PresharedKey = &psk
			}
		}
		cfgs = append(cfgs, pc)
	}

	for key := range current {
		if !wanted[key] {
			cfgs = append(cfgs, wgtypes.PeerConfig{PublicKey: key, Remove: true})
		}
	}

	if len(cfgs) > 0 {
		if err := c.ConfigureDevice(iface, wgtypes.Config{Peers: cfgs}); err != nil {
			return 0, fmt.Errorf("configure %s: %w", iface, err)
		}
	}

	// wg-quick installs a route for every AllowedIP; configuring a peer over
	// netlink does NOT. Without one the kernel sends the reply to a peer's
	// address out of the DEFAULT route instead of into the tunnel, so the
	// handshake completes, `wg show` looks perfect, and not a single byte of
	// customer traffic ever arrives. Adding it here means the node is correct
	// even if its interface was addressed outside the panel's pool.
	for _, p := range peers {
		if ipnet, err := allowedIP(p.Address); err == nil {
			ensureRoute(ipnet.IP.String(), iface)
		}
	}
	return applied, nil
}

// peerMatches reports whether a peer already on the interface is exactly what
// the hub asked for, so it can be skipped rather than rewritten.
//
// The preshared key cannot be read back — the kernel never returns it — so a
// peer that should have one is compared on whether it has one at all. That
// errs toward leaving it alone, which is right: the only way a PSK changes is
// if the panel reissues the peer, and that changes the public key too.
func peerMatches(have wgtypes.Peer, want net.IPNet, psk string) bool {
	if len(have.AllowedIPs) != 1 {
		return false
	}
	got := have.AllowedIPs[0]
	if !got.IP.Equal(want.IP) {
		return false
	}
	gOnes, gBits := got.Mask.Size()
	wOnes, wBits := want.Mask.Size()
	if gOnes != wOnes || gBits != wBits {
		return false
	}
	hasPSK := have.PresharedKey != (wgtypes.Key{})
	return hasPSK == (psk != "")
}

// ensureRoute makes <addr>/32 reachable through the tunnel. Idempotent: `route
// replace` creates it or leaves it correct, so this can run on every sync.
func ensureRoute(addr, iface string) {
	cmd := exec.Command("ip", "route", "replace", addr+"/32", "dev", iface)
	if out, err := cmd.CombinedOutput(); err != nil {
		log.Printf("wireguard: could not route %s via %s: %v %s",
			addr, iface, err, strings.TrimSpace(string(out)))
	}
}

// RemoveWgPeer revokes one key. Used when a user's credit runs out, since the
// account itself may still be perfectly valid on this node.
func RemoveWgPeer(iface, publicKey string) error {
	if iface == "" || publicKey == "" {
		return nil
	}
	if !WgManaged(iface) {
		return fmt.Errorf("refusing to alter unmanaged interface %q", iface)
	}
	key, err := wgtypes.ParseKey(publicKey)
	if err != nil {
		return err
	}
	c, err := wgctrl.New()
	if err != nil {
		return err
	}
	defer c.Close()
	return c.ConfigureDevice(iface, wgtypes.Config{
		Peers: []wgtypes.PeerConfig{{PublicKey: key, Remove: true}},
	})
}

// WgPublicKey is this node's own server key, derived from the interface's
// private key. The panel needs it to build a customer config that points at
// THIS machine: handing out the master's key produces a config that looks
// correct and never completes a handshake.
//
// Reported only for a MANAGED interface. Publishing the key of some other
// tunnel would hand customers a config aimed at infrastructure, and the panel
// would rather say "this node has no WireGuard key yet" than hand out one that
// points somewhere it should not.
func WgPublicKey(iface string) (string, uint32) {
	if iface == "" || !WgManaged(iface) {
		return "", 0
	}
	c, err := wgctrl.New()
	if err != nil {
		return "", 0
	}
	defer c.Close()
	dev, err := c.Device(iface)
	if err != nil {
		return "", 0
	}
	if dev.PrivateKey == (wgtypes.Key{}) {
		return "", uint32(dev.ListenPort)
	}
	return dev.PrivateKey.PublicKey().String(), uint32(dev.ListenPort)
}

// allowedIP turns "10.66.66.4" or "10.66.66.4/32" into a single-host route.
//
// Always a /32 regardless of what was sent: the address is the peer's identity
// for accounting and for WARP routing, and a wider mask would let one customer
// source traffic as another.
func allowedIP(addr string) (*net.IPNet, error) {
	if addr == "" {
		return nil, fmt.Errorf("empty address")
	}
	if ip, _, err := net.ParseCIDR(addr); err == nil {
		return &net.IPNet{IP: ip.To4(), Mask: net.CIDRMask(32, 32)}, nil
	}
	ip := net.ParseIP(addr)
	if ip == nil || ip.To4() == nil {
		return nil, fmt.Errorf("not an IPv4 address: %q", addr)
	}
	return &net.IPNet{IP: ip.To4(), Mask: net.CIDRMask(32, 32)}, nil
}

// ValidKey reports whether a string is a usable 32-byte WireGuard key, so the
// caller can reject one before it reaches the kernel.
func ValidKey(s string) bool {
	b, err := base64.StdEncoding.DecodeString(s)
	return err == nil && len(b) == wgtypes.KeyLen
}
