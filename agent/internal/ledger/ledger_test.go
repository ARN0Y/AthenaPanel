package ledger

import (
	"testing"
	"time"
)

const MB = 1024 * 1024

func obs(m map[string]struct{ Rx, Tx uint64 }) map[string]struct{ Rx, Tx uint64 } { return m }

func TestConsumptionIsMeasuredFromTheGrantBaseline(t *testing.T) {
	l := New()
	// The session has already moved 500 MB before any credit arrives.
	l.AddSession("bob", "ppp0", 100, 300*MB, 200*MB)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})

	if got := l.Consumed("bob"); got != 0 {
		t.Fatalf("a fresh grant must start at zero, got %d", got)
	}
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 310 * MB, Tx: 200 * MB}}))
	if got := l.Consumed("bob"); got != 10*MB {
		t.Fatalf("only traffic after the grant counts: want 10MB, got %d", got/MB)
	}
}

func TestEndedSessionKeepsItsConsumption(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 30 * MB, Tx: 0}}))
	l.RemoveSession("bob", "ppp0")

	if got := l.Consumed("bob"); got != 30*MB {
		t.Fatalf("a finished session must not return its credit: want 30MB, got %d", got/MB)
	}
	// Reconnecting must not reset anything either.
	l.AddSession("bob", "ppp1", 101, 0, 0)
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp1": {Rx: 5 * MB, Tx: 0}}))
	if got := l.Consumed("bob"); got != 35*MB {
		t.Fatalf("reconnect must add, not reset: want 35MB, got %d", got/MB)
	}
}

func TestCounterResetIsNotNegative(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 900*MB, 900*MB)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB})
	// Interface bounced: counters restart from a small number.
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 2 * MB, Tx: 1 * MB}}))
	if got := l.Consumed("bob"); got != 3*MB {
		t.Fatalf("a reset counter is new traffic, not a negative: want 3MB, got %d", got/MB)
	}
}

func TestRefillsEarlyAtTheThreshold(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 7, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})

	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 50 * MB}}))
	if v := l.Evaluate(true); len(v) != 0 {
		t.Fatalf("must not ask at half a grant, got %+v", v)
	}
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 81 * MB}}))
	v := l.Evaluate(true)
	if len(v) != 1 || v[0].Decision != RequestMore || v[0].Reason != "THRESHOLD" {
		t.Fatalf("must ask once past the threshold, got %+v", v)
	}
	if v[0].GrantID != 7 {
		t.Fatalf("the request must quote the grant it spent: got %d", v[0].GrantID)
	}
	// It must not ask again while that request is in flight.
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 85 * MB}}))
	if v := l.Evaluate(true); len(v) != 0 {
		t.Fatalf("must not re-ask while awaiting, got %+v", v)
	}
}

func TestFinalGrantTerminatesAtExhaustion(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 9, GrantedBytes: 30 * MB, Final: true})

	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 29 * MB}}))
	if v := l.Evaluate(true); len(v) != 0 {
		t.Fatalf("still inside the final grant, got %+v", v)
	}
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 30 * MB}}))
	v := l.Evaluate(true)
	if len(v) != 1 || v[0].Decision != Terminate {
		t.Fatalf("a spent final grant must terminate, got %+v", v)
	}
	if len(v[0].Pids) != 1 || v[0].Pids[0] != 100 {
		t.Fatalf("the verdict must carry what to kill, got %+v", v[0])
	}
}

func TestHubDownSpendsTheGrantThenStops(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 3, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})

	// Past the threshold, but there is nobody to ask.
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 90 * MB}}))
	if v := l.Evaluate(false); len(v) != 0 {
		t.Fatalf("with the hub down it should keep serving the held credit, got %+v", v)
	}
	// Grant fully spent: now it must stop, because the grant WAS the budget.
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 100 * MB}}))
	v := l.Evaluate(false)
	if len(v) != 1 || v[0].Decision != Terminate {
		t.Fatalf("a spent grant with no hub must terminate, got %+v", v)
	}
}

func TestRefusalTerminatesImmediately(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 4, Refused: true, RefuseReason: "quota exhausted"})
	v := l.Evaluate(true)
	if len(v) != 1 || v[0].Decision != Terminate || v[0].Reason != "quota exhausted" {
		t.Fatalf("a refusal must terminate with its reason, got %+v", v)
	}
}

func TestValidityForcesAReportEvenWhenIdle(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 5, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB,
		Validity: 10 * time.Millisecond})
	if v := l.Evaluate(true); len(v) != 0 {
		t.Fatalf("nothing to do yet, got %+v", v)
	}
	time.Sleep(15 * time.Millisecond)
	v := l.Evaluate(true)
	if len(v) != 1 || v[0].Reason != "VALIDITY" {
		t.Fatalf("validity must force a reconcile, got %+v", v)
	}
}

func TestOvershootIsOneTickOfTraffic(t *testing.T) {
	// The guarantee, measured: a user on a final grant is cut at the first tick
	// after exhaustion, so the excess is whatever one tick carried.
	const rateBps = 100_000_000
	const tickMs = 1000
	perTick := uint64(rateBps / 8 * tickMs / 1000)

	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 500 * MB, Final: true})

	var moved uint64
	for i := 0; i < 10000; i++ {
		moved += perTick
		l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: moved}}))
		v := l.Evaluate(true)
		if len(v) == 1 && v[0].Decision == Terminate {
			over := moved - 500*MB
			if over >= perTick {
				t.Fatalf("overshoot %d must be under one tick %d", over, perTick)
			}
			t.Logf("terminated %d bytes (%.2f MB) past the grant; one tick is %.2f MB",
				over, float64(over)/MB, float64(perTick)/MB)
			return
		}
	}
	t.Fatal("never terminated")
}

func TestPidsDoesNotDisturbCreditState(t *testing.T) {
	// Reading pids used to go through Evaluate, which is a decision function
	// with side effects: it marks users as awaiting an answer. Enumerating one
	// user's processes must not change what the loop decides about another.
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.AddSession("carol", "ppp1", 200, 0, 0)
	l.ApplyGrant("carol", Grant{ID: 1, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp1": {Rx: 90 * MB}}))

	if pids := l.Pids("bob"); len(pids) != 1 || pids[0] != 100 {
		t.Fatalf("bob's pids: %v", pids)
	}
	if n := l.SessionCount("carol"); n != 1 {
		t.Fatalf("carol should hold one session, got %d", n)
	}
	// carol is over her threshold, so the FIRST Evaluate must still ask for
	// more. (bob appears too, with INITIAL — he has no grant yet.)
	verdicts := l.Evaluate(true)
	found := false
	for _, v := range verdicts {
		if v.Username != "carol" {
			continue
		}
		found = true
		if v.Decision != RequestMore || v.Reason != "THRESHOLD" {
			t.Fatalf("carol's verdict was disturbed: %+v", v)
		}
	}
	if !found {
		t.Fatalf("reading pids consumed carol's pending request: %+v", verdicts)
	}
}

func TestSessionCountSeesPidlessSessions(t *testing.T) {
	// accel-ppp owns its SSTP sessions and reports no pid. Such a session is
	// still a session; if it did not count, a user whose account was deleted
	// would never be evicted on sync.
	l := New()
	l.AddSession("dave", "ppp0", 0, 0, 0)
	if len(l.Pids("dave")) != 0 {
		t.Fatal("a pid of 0 must not be offered as a kill target")
	}
	if n := l.SessionCount("dave"); n != 1 {
		t.Fatalf("session count must still be 1, got %d", n)
	}
}

func TestPidsOfUnknownUserIsEmpty(t *testing.T) {
	l := New()
	if pids := l.Pids("nobody"); len(pids) != 0 {
		t.Fatalf("unknown user must yield no pids, got %v", pids)
	}
	if n := l.SessionCount("nobody"); n != 0 {
		t.Fatalf("unknown user must hold no sessions, got %d", n)
	}
}

func TestCloseSessionBillsTheBytesTheLastPollMissed(t *testing.T) {
	// A link drops between polls. The interface is gone before ip-down runs, so
	// the only record of those last bytes is what pppd reports; without taking
	// it, every session leaks up to one poll interval of the customer's rate.
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB, ThresholdBytes: 20 * MB})
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 4 * MB, Tx: 6 * MB}}))
	if got := l.Consumed("bob"); got != 10*MB {
		t.Fatalf("observed consumption: %d", got)
	}
	// pppd saw 3 MB more than the last poll did.
	l.CloseSession("bob", "ppp0", 5*MB, 8*MB)
	if got := l.Consumed("bob"); got != 13*MB {
		t.Fatalf("final consumption must include what the poll missed, got %d MB", got/MB)
	}
}

func TestCloseSessionNeverRefundsMeasuredTraffic(t *testing.T) {
	// A hook that under-reports must not be able to hand credit back.
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB})
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 9 * MB, Tx: 9 * MB}}))
	l.CloseSession("bob", "ppp0", 1, 1)
	if got := l.Consumed("bob"); got != 18*MB {
		t.Fatalf("measured traffic must stand, got %d MB", got/MB)
	}
}

func TestRemoveSessionStillWorks(t *testing.T) {
	l := New()
	l.AddSession("bob", "ppp0", 100, 0, 0)
	l.ApplyGrant("bob", Grant{ID: 1, GrantedBytes: 100 * MB})
	l.Observe(obs(map[string]struct{ Rx, Tx uint64 }{"ppp0": {Rx: 7 * MB}}))
	l.RemoveSession("bob", "ppp0")
	if got := l.Consumed("bob"); got != 7*MB {
		t.Fatalf("an ended session keeps its consumption, got %d MB", got/MB)
	}
	if n := l.SessionCount("bob"); n != 0 {
		t.Fatalf("session should be gone, got %d", n)
	}
}
