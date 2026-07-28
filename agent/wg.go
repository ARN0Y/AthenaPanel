package main

import (
	"log"
	"strings"
	"time"

	"github.com/ARN0Y/AthenaPanel/agent/internal/collect"
	"github.com/ARN0Y/AthenaPanel/agent/internal/enforce"
)

// A peer counts as connected while its last handshake is this recent.
// WireGuard rekeys about every two minutes, so a shorter window would make a
// perfectly healthy peer flap in and out of the ledger between rekeys — and
// every flap would re-anchor its baseline and lose the traffic in between.
const wgOnlineWindow = 180 * time.Second

// observeWg returns this node's WireGuard peers and keeps the ledger's idea of
// who is connected in step with them.
//
// WireGuard has no connect or disconnect to hook, so "a session" has to be
// inferred: a peer becomes one at its first handshake and stops being one when
// it goes quiet. That inference is what lets a WireGuard user be metered by the
// same credit loop as an L2TP one, instead of being the one protocol that can
// run past its quota unchecked because nothing ever asks the hub about it.
func (e *creditEngine) observeWg() []collect.WgPeer {
	if e.wgIface == "" {
		return nil
	}
	peers, ok := collect.WgPeers(e.wgIface)
	if !ok {
		// The interface could not be read. Reporting no peers would look like
		// everyone disconnected at once, which is the one conclusion that is
		// never safe to draw from a failed read.
		return nil
	}

	now := time.Now()
	live := make([]collect.WgPeer, 0, len(peers))
	for _, p := range peers {
		owner := e.wgOwner(p.PublicKey)
		if owner == "" {
			// A peer this node was never told about. It is still on the
			// interface and still moving bytes, but nobody can be billed for
			// it, so it is left out of the ledger rather than charged to
			// whoever happens to be nearby.
			continue
		}
		online := p.LastHandshake > 0 &&
			now.Sub(time.Unix(p.LastHandshake, 0)) < wgOnlineWindow
		key := wgKey(p.PublicKey)

		if online {
			if e.led.HasSession(owner, key) {
				live = append(live, p)
				continue
			}
			// First handshake of a new online period. The baseline is the
			// counter as it stands now, so traffic from a previous period is
			// not charged against the grant that is about to be issued.
			e.led.AddSession(owner, key, 0, p.RxBytes, p.TxBytes)
			log.Printf("wireguard: %s is up (%s)", owner, shortKey(p.PublicKey))
			live = append(live, p)
			continue
		}
		if e.led.HasSession(owner, key) {
			// Gone quiet. Close it with the counters we have — they are the
			// last authoritative reading, and unlike a ppp hangup there is no
			// hook coming with a better number.
			e.led.CloseSession(owner, key, p.RxBytes, p.TxBytes)
			log.Printf("wireguard: %s went quiet (%s)", owner, shortKey(p.PublicKey))
		}
	}
	return live
}

// revokeWg removes the WireGuard peers named in a terminate verdict.
//
// A ppp session is ended by signalling a process; a peer has no process, so it
// is ended by taking the key off the interface. Both arrive through the same
// verdict, which is why the ledger prefixes peer entries — the prefix is the
// only thing that says which mechanism applies.
func (e *creditEngine) revokeWg(names []string) {
	if e.wgIface == "" {
		return
	}
	for _, n := range names {
		if !strings.HasPrefix(n, "wg:") {
			continue
		}
		pub := strings.TrimPrefix(n, "wg:")
		if err := enforce.RemoveWgPeer(e.wgIface, pub); err != nil {
			log.Printf("  revoke wireguard peer %s: %v", shortKey(pub), err)
			continue
		}
		log.Printf("  revoked wireguard peer %s", shortKey(pub))
	}
}

// applyWgPeers installs the peer set the hub just sent and records who owns
// each key. Returns how many peers are now configured.
func (e *creditEngine) applyWgPeers(owners map[string]string, peers []enforce.WgPeer) int {
	e.setWgOwners(owners)
	if e.wgIface == "" {
		if len(peers) > 0 {
			log.Printf("sync carried %d wireguard peer(s) but this node has no wg interface", len(peers))
		}
		return 0
	}
	n, err := enforce.SyncWgPeers(e.wgIface, peers)
	if err != nil {
		log.Printf("wireguard sync failed: %v", err)
		return 0
	}
	return n
}

// shortKey keeps a public key readable in a log line without printing the whole
// thing on every poll.
func shortKey(k string) string {
	if len(k) <= 10 {
		return k
	}
	return k[:10] + "…"
}
