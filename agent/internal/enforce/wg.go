package enforce

import (
	"encoding/base64"
	"fmt"
	"net"

	"golang.zx2c4.com/wireguard/wgctrl"
	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

// WgPeer is one account's WireGuard peer as the hub described it.
type WgPeer struct {
	PublicKey    string
	PresharedKey string
	Address      string // a bare address or a CIDR; the /32 is implied
}

// SyncWgPeers makes the node's WireGuard interface hold exactly the given set.
//
// ReplacePeers, not add-then-remove: the hub always sends the complete list, so
// replacing it outright is the only way a peer that was deleted while this node
// was offline actually goes away. Anything else leaves a revoked key working
// until someone notices.
//
// Removing a peer takes effect immediately — WireGuard has no session to tear
// down, so the next packet from that key is simply not decrypted. That is why
// this is both the provisioning path and the enforcement path.
func SyncWgPeers(iface string, peers []WgPeer) (applied int, err error) {
	if iface == "" {
		return 0, nil
	}
	c, err := wgctrl.New()
	if err != nil {
		return 0, fmt.Errorf("wgctrl: %w", err)
	}
	defer c.Close()
	if _, err := c.Device(iface); err != nil {
		return 0, fmt.Errorf("no wireguard interface %q: %w", iface, err)
	}

	cfgs := make([]wgtypes.PeerConfig, 0, len(peers))
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
		applied++
	}

	if err := c.ConfigureDevice(iface, wgtypes.Config{
		ReplacePeers: true,
		Peers:        cfgs,
	}); err != nil {
		return 0, fmt.Errorf("configure %s: %w", iface, err)
	}
	return applied, nil
}

// RemoveWgPeer revokes one key. Used when a user's credit runs out, since the
// account itself may still be perfectly valid on this node.
func RemoveWgPeer(iface, publicKey string) error {
	if iface == "" || publicKey == "" {
		return nil
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
func WgPublicKey(iface string) (string, uint32) {
	if iface == "" {
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
