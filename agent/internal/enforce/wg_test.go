package enforce

import (
	"net"
	"os"
	"path/filepath"
	"testing"

	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

func TestWgWritesRefuseAnUnmarkedInterface(t *testing.T) {
	// The interface a node already has is very often infrastructure — the UAE
	// node's wg0 was a backhaul carrying 1.29 GiB when this was written. A
	// ReplacePeers against it would have deleted that tunnel silently.
	if WgManaged("definitely-not-a-real-iface") {
		t.Fatal("an interface with no marker must never be considered managed")
	}
	if _, err := SyncWgPeers("definitely-not-a-real-iface", []WgPeer{{PublicKey: "x"}}); err == nil {
		t.Fatal("SyncWgPeers must refuse an unmarked interface")
	}
	if err := RemoveWgPeer("definitely-not-a-real-iface", "x"); err == nil {
		t.Fatal("RemoveWgPeer must refuse an unmarked interface")
	}
	if k, _ := WgPublicKey("definitely-not-a-real-iface"); k != "" {
		t.Fatalf("an unmanaged interface must not publish a server key, got %q", k)
	}
}

func TestEmptyInterfaceIsANoOpNotAnError(t *testing.T) {
	// A node bootstrapped with --no-wg has no interface at all; that is a
	// normal configuration, not a fault to log every sync.
	if n, err := SyncWgPeers("", []WgPeer{{PublicKey: "x"}}); err != nil || n != 0 {
		t.Fatalf("no interface should be a silent no-op, got n=%d err=%v", n, err)
	}
	if err := RemoveWgPeer("", "x"); err != nil {
		t.Fatalf("no interface should be a silent no-op, got %v", err)
	}
}

func TestMarkerPathIsBesideTheInterfaceConfig(t *testing.T) {
	if got := MarkerFor("wg-panel"); got != filepath.ToSlash("/etc/wireguard/wg-panel.athena") {
		t.Fatalf("marker path: %s", got)
	}
	_ = os.Stat
}

func TestAllowedIPIsAlwaysASingleHost(t *testing.T) {
	// A wider mask would let one customer source traffic as another, and the
	// address is the identity everything downstream bills and routes on.
	for _, in := range []string{"10.66.66.4", "10.66.66.4/32", "10.66.66.4/24"} {
		n, err := allowedIP(in)
		if err != nil {
			t.Fatalf("%s: %v", in, err)
		}
		if ones, _ := n.Mask.Size(); ones != 32 {
			t.Fatalf("%s produced a /%d, must always be /32", in, ones)
		}
	}
	if _, err := allowedIP(""); err == nil {
		t.Fatal("an empty address must be rejected")
	}
	if _, err := allowedIP("not-an-ip"); err == nil {
		t.Fatal("a non-address must be rejected")
	}
}

func TestPeerMatchesLeavesAnUnchangedPeerAlone(t *testing.T) {
	// The whole point of the diff: a live session must not be torn down because
	// somebody else's account was edited. wg-panel's counters resetting from
	// 6.25 MiB to 844 KiB on an agent restart is what this prevents.
	want := net.IPNet{IP: net.ParseIP("10.10.0.17").To4(), Mask: net.CIDRMask(32, 32)}
	psk := wgtypes.Key{1, 2, 3}

	have := wgtypes.Peer{AllowedIPs: []net.IPNet{want}, PresharedKey: psk}
	if !peerMatches(have, want, "somekey") {
		t.Fatal("an identical peer must be recognised and skipped")
	}

	// A different address is a real change and must be applied.
	other := net.IPNet{IP: net.ParseIP("10.10.0.18").To4(), Mask: net.CIDRMask(32, 32)}
	if peerMatches(have, other, "somekey") {
		t.Fatal("a moved address must not be treated as unchanged")
	}

	// Gaining or losing a preshared key is a change.
	if peerMatches(wgtypes.Peer{AllowedIPs: []net.IPNet{want}}, want, "somekey") {
		t.Fatal("a peer with no PSK must not match one that should have a PSK")
	}
	if peerMatches(have, want, "") {
		t.Fatal("a peer with a PSK must not match one that should have none")
	}

	// A peer carrying extra AllowedIPs is not ours to keep.
	wide := wgtypes.Peer{AllowedIPs: []net.IPNet{want, other}, PresharedKey: psk}
	if peerMatches(wide, want, "somekey") {
		t.Fatal("a peer allowed more than its own /32 must be rewritten")
	}
}
