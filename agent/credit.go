package main

import (
	"log"
	"sync"
	"time"

	"github.com/ARN0Y/AthenaPanel/agent/internal/collect"
	"github.com/ARN0Y/AthenaPanel/agent/internal/enforce"
	"github.com/ARN0Y/AthenaPanel/agent/internal/hooks"
	"github.com/ARN0Y/AthenaPanel/agent/internal/ledger"
	pb "github.com/ARN0Y/AthenaPanel/agent/pb"
)

// creditEngine owns everything that has to keep working whether or not the hub
// is reachable: the session registry, the credit ledger, and the loop that
// enforces quota locally.
//
// It deliberately outlives any single stream. A reconnect swaps the send
// channel underneath it; the ledger, and therefore every user's spent credit,
// is untouched. Losing that on a reconnect would hand everyone their grant back
// and turn a network blip into free traffic.
type creditEngine struct {
	led      *ledger.Ledger
	chapPath string
	chapSrv  string
	wgIface  string

	// Closed-over signal asking the report loop to send NOW rather than at the
	// next tick. Buffered depth 1: a hundred sessions coming up at once should
	// produce one extra report, not a hundred.
	kick chan struct{}

	mu     sync.Mutex
	send   func(*pb.AgentMessage) error // nil while disconnected
	pollMs int
	// public key -> account, learned from the last sync. WireGuard reports a
	// key, never a name, so without this a peer's traffic is unattributable and
	// therefore unbillable.
	wgOwners map[string]string
}

func newCreditEngine(chapPath, chapServerField, wgIface string) *creditEngine {
	return &creditEngine{
		led:      ledger.New(),
		chapPath: chapPath,
		chapSrv:  chapServerField,
		wgIface:  wgIface,
		pollMs:   1000,
		wgOwners: map[string]string{},
		kick:     make(chan struct{}, 1),
	}
}

// reportNow asks for an out-of-band report.
//
// The periodic report is telemetry and 15s is the right cadence for it: node
// load, throughput and host stats do not change meaningfully faster, and a
// node with 130 sessions sends ~13 KB each time. But a session STARTING is not
// telemetry, it is an event, and the node knows the exact instant it happened
// because the ip-up hook just told it. Waiting up to a full interval to
// mention it would make the panel look slow for no reason other than the
// schedule. Pushing on the event costs nothing when nothing is happening,
// which is why this is better than simply reporting more often.
func (e *creditEngine) reportNow() {
	select {
	case e.kick <- struct{}{}:
	default: // one already pending; that report will carry this change too
	}
}

// wgKey is how a WireGuard peer is named inside the ledger. Prefixed so it can
// never collide with an interface name, and so termination can tell the two
// apart: a ppp session is a process to signal, a peer is a key to revoke.
func wgKey(publicKey string) string { return "wg:" + publicKey }

func (e *creditEngine) wgOwner(publicKey string) string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.wgOwners[publicKey]
}

func (e *creditEngine) setWgOwners(m map[string]string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.wgOwners = m
}

func (e *creditEngine) attach(send func(*pb.AgentMessage) error, pollMs int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.send = send
	if pollMs > 0 {
		e.pollMs = pollMs
	}
}

func (e *creditEngine) detach() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.send = nil
	// Every in-flight request died with the stream. Clearing the markers is what
	// lets the next connection re-ask instead of waiting forever for answers
	// that can never arrive.
	for _, u := range e.led.Users() {
		e.led.ClearAwaiting(u)
	}
}

func (e *creditEngine) sender() (func(*pb.AgentMessage) error, int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.send, e.pollMs
}

// ---------------------------------------------------------------- ppp hooks

func (e *creditEngine) OnUp(ev hooks.Event) (bool, string) {
	rx, tx := collect.IfaceBytes(ev.Ifname)
	e.led.AddSession(ev.Username, ev.Ifname, ev.Pid, rx, tx)
	log.Printf("session up: %s on %s (pid %d)", ev.Username, ev.Ifname, ev.Pid)
	// The panel should show this customer now, not at the next tick.
	e.reportNow()

	// Fail OPEN. The alternative is refusing a paying customer because a
	// control-plane message was slow, which is a worse failure than serving one
	// session's worth of traffic before the first grant arrives — and that
	// traffic is still counted, because the ledger started at this moment.
	return true, ""
}

func (e *creditEngine) OnDown(ev hooks.Event) {
	// Close with pppd's own totals, not just the last poll: the interface is
	// already gone, so anything moved after the final tick can never be read
	// back and would go unbilled on every single session.
	e.led.CloseSession(ev.Username, ev.Ifname, ev.InOctets, ev.OutOctets)
	log.Printf("session down: %s on %s (%d/%d bytes reported)",
		ev.Username, ev.Ifname, ev.InOctets, ev.OutOctets)
	// Same on the way out: a session that ended should stop being displayed as
	// live immediately, not linger for an interval.
	e.reportNow()

	// Report immediately rather than at the next tick: this is the last chance
	// to bill the session, and the hub needs it before it decides anything else
	// about this user.
	send, _ := e.sender()
	if send == nil {
		return
	}
	consumed := e.led.Consumed(ev.Username)
	_ = send(&pb.AgentMessage{Payload: &pb.AgentMessage_CreditRequest{
		CreditRequest: &pb.CreditRequest{
			Username:       ev.Username,
			Reason:         pb.CreditRequest_SESSION_ENDED,
			ConsumedBytes:  consumed,
			SessionRxBytes: ev.InOctets,
			SessionTxBytes: ev.OutOctets,
			Ifname:         ev.Ifname,
		},
	}})
}

// ---------------------------------------------------------------- grants

func (e *creditEngine) applyGrant(g *pb.CreditGrant) {
	e.led.ApplyGrant(g.Username, ledger.Grant{
		ID:             g.GrantId,
		GrantedBytes:   g.GrantedBytes,
		ThresholdBytes: g.ThresholdBytes,
		Validity:       time.Duration(g.ValiditySeconds) * time.Second,
		Final:          g.Final,
		Refused:        g.Refused,
		RefuseReason:   g.RefuseReason,
	})
	if g.Refused {
		log.Printf("credit refused for %s: %s", g.Username, g.RefuseReason)
	}
}

func (e *creditEngine) applySync(s *pb.UserSync) (int, error) {
	users := make([]enforce.User, 0, len(s.Users))
	wgPeers := make([]enforce.WgPeer, 0)
	wgOwners := make(map[string]string)
	for _, u := range s.Users {
		users = append(users, enforce.User{
			Username: u.Username, Password: u.Password, Enabled: u.Enabled,
			RateDownKbps: u.RateDownKbps, RateUpKbps: u.RateUpKbps,
			L2tpMode: u.L2TpMode, Outbound: u.Outbound,
		})
		// A disabled account's peers are simply left out of the set, which is
		// the same rule chap-secrets follows — and for WireGuard it is also the
		// enforcement, since a key that is not on the interface cannot connect.
		if !u.Enabled {
			continue
		}
		for _, p := range u.WgPeers {
			wgPeers = append(wgPeers, enforce.WgPeer{
				PublicKey:    p.PublicKey,
				PresharedKey: p.PresharedKey,
				Address:      p.Address,
			})
			wgOwners[p.PublicKey] = u.Username
		}
	}
	n, err := enforce.WriteChapSecrets(e.chapPath, e.chapSrv, users)
	if err != nil {
		return 0, err
	}
	enforce.ReloadAccel()
	wgN := e.applyWgPeers(wgOwners, wgPeers)
	log.Printf("applied sync %d: %d of %d accounts enabled, %d wireguard peer(s)",
		s.SyncId, n, len(s.Users), wgN)

	// chap-secrets only decides who may AUTHENTICATE. A user who was deleted,
	// disabled, or moved to another node is already connected, and rewriting the
	// file does nothing to them — they stay online until their credit happens to
	// run out, which for a deleted account is never asked about again. The sync
	// is the full list by contract, so anyone holding a session who is not in it
	// (or is in it as disabled) must go now.
	//
	// Only sessions this agent knows about are touched: a name that is not in
	// the ledger has nothing to drop, so an empty list is not an instruction to
	// disconnect the whole node.
	if s.Full && len(s.Users) == 0 && e.anyLiveSession() {
		// An empty list means "this node serves nobody now", which is a real
		// thing an operator can do. It is also what a bug on the other end
		// would look like, and here the two are indistinguishable — so the one
		// case that would disconnect EVERY customer at once is the one case
		// this does not act on. They are still cut individually as their credit
		// is refused, which is slower and certain rather than fast and blind.
		log.Printf("sync %d is empty while sessions are live; not mass-disconnecting", s.SyncId)
	} else if s.Full {
		allowed := make(map[string]bool, len(s.Users))
		for _, u := range s.Users {
			allowed[u.Username] = u.Enabled
		}
		for _, name := range e.led.Users() {
			if enabled, listed := allowed[name]; listed && enabled {
				continue
			}
			if e.led.SessionCount(name) == 0 {
				continue
			}
			log.Printf("sync %d: %s is no longer served here, disconnecting", s.SyncId, name)
			// Not Forget()ten: the ledger entry is what keeps billing this
			// session if the kill does not take. It is cleaned up by the ip-down
			// hook when the link actually drops, which is the only evidence that
			// it did.
			e.disconnectUser(name)
		}
	}
	return n, nil
}

// ---------------------------------------------------------------- the loop

// runCredit is the accuracy guarantee in code. It samples this node's own
// counters every pollMs and acts immediately, so a user is cut at the first
// tick after their final grant is spent — the overshoot is one tick of their
// line rate and nothing else.
func (e *creditEngine) runCredit(stop <-chan struct{}) {
	ticker := time.NewTicker(time.Duration(e.poll()) * time.Millisecond)
	defer ticker.Stop()
	lastPoll := e.poll()

	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
		}

		if p := e.poll(); p != lastPoll {
			ticker.Reset(time.Duration(p) * time.Millisecond)
			lastPoll = p
		}

		ifaces, ok := collect.PppInterfaces()
		if !ok {
			// The scan failed. Acting on it would look like everybody vanished.
			continue
		}
		counters := make(map[string]struct{ Rx, Tx uint64 }, len(ifaces))
		for _, i := range ifaces {
			counters[i.Ifname] = struct{ Rx, Tx uint64 }{i.RxBytes, i.TxBytes}
		}
		// WireGuard peers are billed through the SAME ledger as ppp sessions.
		// They have no connect or disconnect, so a peer counts as live from its
		// first handshake and stays live until it goes quiet — anything else
		// would mean a WireGuard user could exceed their quota indefinitely,
		// because nothing would ever ask the hub about them.
		for _, p := range e.observeWg() {
			counters[wgKey(p.PublicKey)] = struct{ Rx, Tx uint64 }{p.RxBytes, p.TxBytes}
		}
		e.led.Observe(counters)

		send, _ := e.sender()
		for _, v := range e.led.Evaluate(send != nil) {
			switch v.Decision {
			case ledger.Terminate:
				log.Printf("terminating %s: %s (%.1f MB spent)",
					v.Username, v.Reason, float64(v.Consumed)/(1024*1024))
				if n, err := enforce.Disconnect(v.Pids); err != nil {
					log.Printf("  disconnect %s: %v (%d killed)", v.Username, err, n)
				}
				_ = enforce.DisconnectAccel(v.Username)
				e.revokeWg(v.Ifnames)
				// Forget them, or every tick would re-decide the same thing and
				// re-kill a process that is already gone — a second of log spam
				// per second, and real work for nothing. If they reconnect the
				// ip-up hook registers them again from scratch, which is also
				// what makes the next grant start from a clean baseline.
				e.led.Forget(v.Username)

			case ledger.RequestMore:
				if send == nil {
					continue
				}
				rx, tx, ifname := e.led.Totals(v.Username)
				if err := send(&pb.AgentMessage{Payload: &pb.AgentMessage_CreditRequest{
					CreditRequest: &pb.CreditRequest{
						Username:       v.Username,
						Reason:         reasonOf(v.Reason),
						ConsumedBytes:  v.Consumed,
						GrantId:        v.GrantID,
						SessionRxBytes: rx,
						SessionTxBytes: tx,
						Ifname:         ifname,
					},
				}}); err != nil {
					// The send failed, so no answer is coming. Release the
					// marker now or this user would never ask again.
					e.led.ClearAwaiting(v.Username)
				}
			}
		}
	}
}

func (e *creditEngine) poll() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.pollMs
}

func reasonOf(s string) pb.CreditRequest_Reason {
	switch s {
	case "INITIAL":
		return pb.CreditRequest_INITIAL
	case "THRESHOLD":
		return pb.CreditRequest_THRESHOLD
	case "VALIDITY":
		return pb.CreditRequest_VALIDITY
	case "EXHAUSTED":
		return pb.CreditRequest_EXHAUSTED
	default:
		return pb.CreditRequest_INITIAL
	}
}

// anyLiveSession reports whether this node is currently serving anyone.
func (e *creditEngine) anyLiveSession() bool {
	for _, name := range e.led.Users() {
		if e.led.SessionCount(name) > 0 {
			return true
		}
	}
	return false
}

// disconnectUser drops every session a user has on this node, on the hub's
// instruction rather than because credit ran out.
func (e *creditEngine) disconnectUser(username string) {
	if pids := e.led.Pids(username); len(pids) > 0 {
		if _, err := enforce.Disconnect(pids); err != nil {
			log.Printf("disconnect %s: %v", username, err)
		}
	}
	// accel-ppp owns its SSTP sessions itself and exposes no pid, so it is
	// always asked separately — including when the ledger knows of no session,
	// because an SSTP session that predates this agent has none.
	_ = enforce.DisconnectAccel(username)
}
