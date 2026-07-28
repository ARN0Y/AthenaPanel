package enforce

import (
	"os"
	"path/filepath"
	"testing"
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
