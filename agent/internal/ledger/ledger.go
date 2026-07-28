// Package ledger tracks, per user, how much of their current credit grant has
// been spent on this node.
//
// This is where the accuracy guarantee actually lives. The hub authorises a
// bounded number of bytes; everything here exists so the node can tell, at any
// moment and without asking anyone, whether that number has been reached.
//
// Two details make it correct rather than approximately correct:
//
//   - Consumption is measured from a BASELINE taken when the grant was applied,
//     not from zero and not from a running total. A session that was already
//     mid-flight when credit arrived contributes only what it moves afterwards.
//
//   - A session that ends does not take its bytes with it. Its final
//     contribution is folded into a closed total, because otherwise consumption
//     would appear to drop and the user would silently get their grant back
//     every time they reconnected.
package ledger

import (
	"sync"
	"time"
)

// Grant is the hub's authorisation, mirroring CreditGrant on the wire.
type Grant struct {
	ID             uint64
	GrantedBytes   uint64
	ThresholdBytes uint64
	Validity       time.Duration
	Final          bool
	Refused        bool
	RefuseReason   string
	AppliedAt      time.Time
}

// Session is one live interface belonging to a user.
type Session struct {
	Ifname string
	Pid    int32
	// Counters when the current grant was applied, or when the session started
	// if it began later. Consumption is always measured from here.
	BaseRx uint64
	BaseTx uint64
	// Newest observation.
	Rx uint64
	Tx uint64
}

func (s *Session) consumed() uint64 {
	var c uint64
	// Counters only increase; a smaller value means the interface bounced and
	// restarted from zero, in which case everything it now shows is new traffic.
	if s.Rx >= s.BaseRx {
		c += s.Rx - s.BaseRx
	} else {
		c += s.Rx
	}
	if s.Tx >= s.BaseTx {
		c += s.Tx - s.BaseTx
	} else {
		c += s.Tx
	}
	return c
}

type user struct {
	name string
	// nil until the hub answers the first request.
	grant *Grant
	// Bytes from sessions that ended while this grant was current.
	closedBytes uint64
	sessions    map[string]*Session
	// Set once a request is in flight so the loop does not ask again every tick
	// while waiting for an answer.
	awaiting bool
	// Set when the grant ran out with no answer. Kept so the decision is made
	// once and acted on, rather than re-derived every tick.
	exhausted bool
}

// Decision is what the credit loop should do about one user right now.
type Decision int

const (
	DoNothing Decision = iota
	// Ask for more credit: the threshold was crossed, or validity elapsed.
	RequestMore
	// Stop this user now: their final grant is spent, or the hub refused them.
	Terminate
)

// Ledger is safe for concurrent use: the poll loop reads counters while the
// stream handler applies grants.
type Ledger struct {
	mu    sync.Mutex
	users map[string]*user
}

func New() *Ledger {
	return &Ledger{users: map[string]*user{}}
}

func (l *Ledger) get(name string) *user {
	u, ok := l.users[name]
	if !ok {
		u = &user{name: name, sessions: map[string]*Session{}}
		l.users[name] = u
	}
	return u
}

// AddSession registers an interface for a user. Its baseline is the counter it
// has right now, so traffic that predates the registration is not charged to
// the current grant twice.
func (l *Ledger) AddSession(username, ifname string, pid int32, rx, tx uint64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	u := l.get(username)
	if s, ok := u.sessions[ifname]; ok {
		s.Pid = pid
		return
	}
	u.sessions[ifname] = &Session{Ifname: ifname, Pid: pid, BaseRx: rx, BaseTx: tx, Rx: rx, Tx: tx}
}

// RemoveSession folds a finished session's consumption into the closed total.
// Dropping it instead would hand the user their spent credit back.
func (l *Ledger) RemoveSession(username, ifname string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	u, ok := l.users[username]
	if !ok {
		return
	}
	if s, ok := u.sessions[ifname]; ok {
		u.closedBytes += s.consumed()
		delete(u.sessions, ifname)
	}
}

// Observe records the newest counters for an interface. Unknown interfaces are
// ignored: an interface with no user is not this ledger's business, and
// guessing an owner would charge the wrong account.
func (l *Ledger) Observe(counters map[string]struct{ Rx, Tx uint64 }) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for _, u := range l.users {
		for ifname, s := range u.sessions {
			if c, ok := counters[ifname]; ok {
				s.Rx, s.Tx = c.Rx, c.Tx
			}
		}
	}
}

// ApplyGrant installs a new authorisation and rebases every live session, so
// consumption starts from zero against the new grant.
func (l *Ledger) ApplyGrant(username string, g Grant) {
	l.mu.Lock()
	defer l.mu.Unlock()
	u := l.get(username)
	g.AppliedAt = time.Now()
	u.grant = &g
	u.closedBytes = 0
	u.awaiting = false
	u.exhausted = false
	for _, s := range u.sessions {
		s.BaseRx, s.BaseTx = s.Rx, s.Tx
	}
}

// Consumed is what has been spent under the current grant.
func (l *Ledger) Consumed(username string) uint64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	u, ok := l.users[username]
	if !ok {
		return 0
	}
	return u.consumed()
}

func (u *user) consumed() uint64 {
	total := u.closedBytes
	for _, s := range u.sessions {
		total += s.consumed()
	}
	return total
}

// Totals returns the absolute counters across a user's live sessions, for the
// cross-check the hub does between the credit ledger and the traffic ledger.
func (l *Ledger) Totals(username string) (rx, tx uint64, ifname string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	u, ok := l.users[username]
	if !ok {
		return 0, 0, ""
	}
	for _, s := range u.sessions {
		rx += s.Rx
		tx += s.Tx
		if ifname == "" {
			ifname = s.Ifname
		}
	}
	return rx, tx, ifname
}

// Verdict is one user's state after a poll.
type Verdict struct {
	Username string
	Decision Decision
	Consumed uint64
	GrantID  uint64
	Reason   string
	Pids     []int32
	Ifnames  []string
}

// Evaluate walks every user and decides what to do. Called once per poll tick,
// which is what sets the accuracy: a user is cut at the first tick after their
// final grant is spent, so the overshoot is one tick of their line rate.
//
// hubUp says whether the control stream is currently usable. When it is not, a
// user whose grant is spent is terminated rather than left running, because the
// grant was the failure budget and it has now been used.
func (l *Ledger) Evaluate(hubUp bool) []Verdict {
	l.mu.Lock()
	defer l.mu.Unlock()

	out := make([]Verdict, 0, 4)
	now := time.Now()
	for name, u := range l.users {
		if len(u.sessions) == 0 && u.grant == nil {
			continue
		}
		v := Verdict{Username: name, Consumed: u.consumed()}
		for _, s := range u.sessions {
			if s.Pid > 0 {
				v.Pids = append(v.Pids, s.Pid)
			}
			v.Ifnames = append(v.Ifnames, s.Ifname)
		}

		// No credit yet: ask, unless a request is already in flight.
		if u.grant == nil {
			if !u.awaiting && hubUp {
				u.awaiting = true
				v.Decision, v.Reason = RequestMore, "INITIAL"
				out = append(out, v)
			}
			continue
		}
		v.GrantID = u.grant.ID

		if u.grant.Refused {
			v.Decision, v.Reason = Terminate, u.grant.RefuseReason
			out = append(out, v)
			continue
		}

		spent := v.Consumed >= u.grant.GrantedBytes
		if spent {
			// A final grant is the user's last bytes: stop, do not ask again.
			// Without a hub there is nothing to ask, and the grant we were
			// holding is exactly the loss we agreed to accept.
			if u.grant.Final || !hubUp {
				v.Decision = Terminate
				if u.grant.Final {
					v.Reason = "quota exhausted"
				} else {
					v.Reason = "credit spent, hub unreachable"
				}
				out = append(out, v)
				continue
			}
			if !u.awaiting {
				u.awaiting = true
				u.exhausted = true
				v.Decision, v.Reason = RequestMore, "EXHAUSTED"
				out = append(out, v)
			}
			continue
		}

		if u.awaiting || !hubUp {
			continue
		}

		// Refill early. Asking only at exhaustion would stall a paying customer
		// for a full round trip every time.
		if !u.grant.Final && u.grant.ThresholdBytes > 0 &&
			v.Consumed >= u.grant.GrantedBytes-u.grant.ThresholdBytes {
			u.awaiting = true
			v.Decision, v.Reason = RequestMore, "THRESHOLD"
			out = append(out, v)
			continue
		}

		// Reconcile on a timer too, so an idle session still reports and a link
		// that has quietly died is noticed.
		if u.grant.Validity > 0 && now.Sub(u.grant.AppliedAt) >= u.grant.Validity {
			u.awaiting = true
			v.Decision, v.Reason = RequestMore, "VALIDITY"
			out = append(out, v)
		}
	}
	return out
}

// ClearAwaiting releases the in-flight marker, so a request that failed to send
// or was never answered is retried on a later tick instead of wedging the user.
func (l *Ledger) ClearAwaiting(username string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if u, ok := l.users[username]; ok {
		u.awaiting = false
	}
}

// Forget drops a user entirely, used when their sessions are gone and they have
// been terminated.
func (l *Ledger) Forget(username string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.users, username)
}

// Pids lists the live pppd processes belonging to a user.
//
// Exists so that terminating someone does not have to go through Evaluate,
// which is a decision function with side effects: it sets the in-flight and
// exhausted markers as it goes. Calling it merely to read pids would silently
// change what the next credit tick decides about every OTHER user in the
// ledger, which is a very expensive way to ask a simple question.
func (l *Ledger) Pids(username string) []int32 {
	l.mu.Lock()
	defer l.mu.Unlock()
	u, ok := l.users[username]
	if !ok {
		return nil
	}
	out := make([]int32, 0, len(u.sessions))
	for _, s := range u.sessions {
		if s.Pid > 0 {
			out = append(out, s.Pid)
		}
	}
	return out
}

// SessionCount is how many live interfaces a user holds. Zero for a name that
// is tracked but not currently connected — and a session with no usable pid
// (accel-ppp owns its own) still counts, because it is still a live session.
func (l *Ledger) SessionCount(username string) int {
	l.mu.Lock()
	defer l.mu.Unlock()
	if u, ok := l.users[username]; ok {
		return len(u.sessions)
	}
	return 0
}

// Users lists everyone currently tracked.
func (l *Ledger) Users() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]string, 0, len(l.users))
	for n := range l.users {
		out = append(out, n)
	}
	return out
}
