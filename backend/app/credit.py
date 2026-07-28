"""Quota credit: how much traffic a node may serve a user before asking again.

The problem this solves is not "how do we count bytes" — the counters already
do that. It is that the authority (this panel) and the enforcement point (a
node, possibly a continent away on a flaky link) are different machines, and
the customer's traffic does not pause while they talk.

Polling from the panel cannot work at the accuracy asked for. At a 30s poll a
user on a 100 Mbps line is 375 MB past their limit before the panel even looks.
The answer, which telecoms settled on decades ago in RFC 4006, is to invert it:
the authority GRANTS a bounded amount of traffic, the enforcement point spends
it locally at whatever resolution it likes, and asks again before it runs out.

Two properties follow, and both are the point:

  * Accuracy is set by the NODE's poll interval, not the network round trip.
    Overshoot = poll_interval x line_rate. At 1s and a rate-limited user that
    is single-digit megabytes.

  * The grant is simultaneously the failure budget. If this panel disappears, a
    node can serve at most the credit it is holding. There is no agonising
    trade-off between cutting customers off during a hiccup and losing unbounded
    traffic: the grant size IS the exposure, and it is chosen here, on purpose.

Sizing is where the judgement lives. Too small and a busy user generates a
request every few seconds; too large and an unreachable panel costs real money.
So the grant is proportional to what the user is actually moving, clamped at
both ends, and the LAST grant before their quota runs out is exact and flagged
final — which is why the number a customer can exceed their package by is one
poll interval, never one grant.
"""

from __future__ import annotations

import itertools
import time as _time
from dataclasses import dataclass

# Smallest grant. Below this a busy session would spend more time asking for
# credit than using it, and every request costs a round trip on a link that may
# be crossing a filtered border.
MIN_GRANT_BYTES = 64 * 1024 * 1024          # 64 MB

# Largest grant, and therefore the most traffic a single user can consume if the
# panel becomes unreachable the instant after granting it. Deliberately modest:
# with a hundred users that is a bounded, affordable worst case, and a healthy
# panel simply refills more often.
MAX_GRANT_BYTES = 2 * 1024 * 1024 * 1024    # 2 GB

# A grant should last roughly this long at the user's current rate. This is what
# turns "how fast is this user going" into "how much should we hand them".
TARGET_GRANT_SECONDS = 300

# Ask again once this fraction of the grant is left. It must cover a full round
# trip plus the hub's think time, or a customer briefly stalls at the boundary.
THRESHOLD_FRACTION = 0.2

# Re-ask on a timer as well as on traffic, so an idle session still reconciles
# and a silently dead link is noticed rather than assumed healthy.
VALIDITY_SECONDS = 300

# How often the node samples its own counters. THIS is the accuracy knob:
# overshoot is bounded by this times the user's line rate. 1s costs a handful of
# sysfs reads per session per second, which is nothing next to moving the
# packets themselves.
CREDIT_POLL_MS = 1000

# Grant ids must increase across hub RESTARTS, not merely within a process.
#
# The hub compares an incoming request's grant id against a watermark it has
# persisted for that user. Restarting the counter at 1 would make every freshly
# issued grant compare as OLDER than the watermark left by the previous process,
# so the user's consumption would be judged stale and dropped — they would run
# unbilled until the counter climbed past a number it could take days to reach.
#
# Seeding from the wall clock in milliseconds is enough: it is monotonic across
# restarts, needs no coordination, and cannot collide with the previous run's
# ids unless the clock steps backwards further than that run issued grants.
_grant_ids = itertools.count(int(_time.time() * 1000))


@dataclass(frozen=True)
class Grant:
    """What the hub authorises. Mirrors CreditGrant on the wire."""

    grant_id: int
    granted_bytes: int
    threshold_bytes: int
    validity_seconds: int
    final: bool
    refused: bool = False
    refuse_reason: str = ""

    @property
    def is_service(self) -> bool:
        return not self.refused and self.granted_bytes > 0


def refuse(reason: str) -> Grant:
    """No service for this user. The node drops them immediately."""
    return Grant(
        grant_id=next(_grant_ids),
        granted_bytes=0,
        threshold_bytes=0,
        validity_seconds=0,
        final=True,
        refused=True,
        refuse_reason=reason,
    )


def size_grant(remaining_bytes: int | None, rate_bps: int) -> int:
    """How many bytes to hand out next.

    `remaining_bytes` is None for an unlimited account. `rate_bps` is what the
    user is actually moving, or their configured limit if that is all we know —
    either way it answers "how long will this last", which is the only question
    that matters for sizing.
    """
    if rate_bps <= 0:
        want = MIN_GRANT_BYTES
    else:
        want = int(rate_bps / 8 * TARGET_GRANT_SECONDS)

    want = max(MIN_GRANT_BYTES, min(MAX_GRANT_BYTES, want))
    if remaining_bytes is not None:
        want = min(want, max(0, remaining_bytes))
    return want


def allocate(
    *,
    remaining_bytes: int | None,
    rate_bps: int,
    enabled: bool,
    refuse_reason: str = "",
) -> Grant:
    """Decide the next grant for one user.

    `remaining_bytes` None = unlimited quota. Zero or less means the package is
    spent, which is a refusal rather than an empty grant so the node has an
    unambiguous instruction instead of a number to interpret.
    """
    if not enabled:
        return refuse(refuse_reason or "account not permitted")
    if remaining_bytes is not None and remaining_bytes <= 0:
        return refuse("quota exhausted")

    granted = size_grant(remaining_bytes, rate_bps)

    # `final` is what makes the customer-visible overshoot small. It is set only
    # when this grant covers everything the user has left, so the node knows to
    # stop at exhaustion instead of asking for more. Everywhere else the node
    # refills early and the customer never notices a boundary.
    final = remaining_bytes is not None and granted >= remaining_bytes

    # No point warning about a threshold on the last grant: there is nothing
    # left to ask for, and an early request would only be refused.
    threshold = 0 if final else int(granted * THRESHOLD_FRACTION)

    return Grant(
        grant_id=next(_grant_ids),
        granted_bytes=granted,
        threshold_bytes=threshold,
        validity_seconds=VALIDITY_SECONDS,
        final=final,
    )


def overshoot_bound_bytes(rate_bps: int, poll_ms: int = CREDIT_POLL_MS) -> int:
    """The most a user can exceed their quota by, in bytes.

    Exists so the guarantee is testable rather than asserted in a comment: the
    node cuts at the first poll after exhaustion, so the excess is whatever the
    line carried during one interval.
    """
    return int(rate_bps / 8 * (poll_ms / 1000.0))
