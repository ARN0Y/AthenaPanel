# Athena Panel — Public API v1

Everything a bot, a billing system or a script needs to run VPN accounts on
this panel, without touching the database or the panel's own UI endpoints.

- **Base URL** — `https://panelmgh.elsaw.site:8443/api/v1`
- **Auth** — an API key in a header
- **Format** — JSON in, JSON out, UTF-8, timestamps ISO-8601 UTC
- **Version** — `v1`. Fields may be *added*; existing fields will not change
  meaning or disappear inside v1.

> **Test a real path, never `/`.** The panel answers `444` on the bare root by
> design and the no-port URL hits a different service. `…:8443/api/v1/me` is a
> real request; `https://panelmgh.elsaw.site/` is not.

---

## 1. Authentication

### Getting a key

Panel → **Settings → API keys → Create**. The secret is shown **once** and is
stored only as a SHA-256 hash — it cannot be recovered, only replaced.

A key looks like:

```
ath_K7pQ2mXd91Lb_qN8vY2sR4tW6zA1cB3dE5fG7hJ9kL0mN2pQ4rS
└──────┬──────┘ └──────────────────┬───────────────────┘
   public prefix                secret
```

The prefix is what appears in logs and in the panel. Quote it when asking about
a key; never send the whole thing.

### Using a key

Either header works. Pick one and be consistent.

```http
X-API-Key: ath_K7pQ2mXd91Lb_qN8vY2sR4t...
```
```http
Authorization: Bearer ath_K7pQ2mXd91Lb_qN8vY2sR4t...
```

A panel session token also works on every `/api/v1` endpoint, which is how the
panel itself exercises this API rather than letting it rot as a second surface.

### Who a key is

**A key authenticates as the admin who created it.** This is the single most
important thing to understand about this API:

| The key belongs to | It can see and change |
| --- | --- |
| a **superadmin** | every account on the panel |
| a **reseller** | only the accounts that reseller created |

A reseller's key asking for another reseller's account gets **404, not 403** —
on purpose. A 403 would confirm the account exists, which is a customer-list
leak.

### Scopes

Scopes **narrow** a key below its owner. They never widen it: a read-only key
belonging to a superadmin still cannot write, and no scope lets a reseller's
key see somebody else's customers.

| Scope | Grants |
| --- | --- |
| `users:read` | list and read accounts, their usage and their sessions |
| `users:write` | create, edit, enable, disable, delete accounts |
| `sessions:read` | see who is connected right now |
| `sessions:write` | disconnect a live session |
| `system:read` | nodes, outbounds, panel stats |

**A key created with no scopes can do everything its owning admin can do.**
That is the right default for your own automation and the wrong one for
anything you hand to someone else.

### Rate limits

Default **120 requests per minute per key**, configurable per key. Every
response carries:

```
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 118
```

Over the limit gives `429` with `Retry-After: 60`. The window is fixed and
resets on the minute.

---

## 2. Conventions

### Errors

Every failure is a JSON body with `detail`:

```json
{ "detail": "No account named 'ali_1234'" }
```

| Code | Means | What a bot should do |
| --- | --- | --- |
| `400` | the request is malformed or asks for something impossible | fix the call; do not retry |
| `401` | missing, invalid, revoked or expired key | stop; alert the operator |
| `403` | the key lacks the scope | check `GET /me`; do not retry |
| `404` | no such account — **or it belongs to someone else** | treat as "not mine" |
| `409` | already exists | treat as success if you were retrying a create |
| `422` | a field failed validation | fix the call; the body names the field |
| `429` | rate limited | back off, honour `Retry-After` |
| `500` | the panel failed | retry once, then alert |

### Pagination

Every list answers with the same envelope:

```json
{ "items": [ … ], "total": 137, "page": 1, "page_size": 50, "pages": 3 }
```

`page` is 1-based. `page_size` is capped at 500.

### Bytes and gigabytes

Every size is given **both ways** — `limit_bytes` and `limit_gb` — so a bot can
render a number without doing byte arithmetic in three places and getting one
of them wrong. `limit_bytes: 0` means **unlimited**, and then
`remaining_bytes`, `remaining_gb` and `usage_percent` are `null`.

`used_bytes` is *effective* usage: committed bytes plus whatever the live
session has moved since. It is the same number the panel shows.

### Accounts are addressed by username

`/users/ali_1234`, never `/users/42`. Your bot stores what the customer typed;
it should not have to keep a mapping to a surrogate key it does not own.

### Renewals add, they do not set

`POST /users/{username}/extend` with `days: 30` adds thirty days to what the
account **has**. Computing an absolute expiry yourself races with the clock and
with any other bot doing the same thing. If the account already expired, the
thirty days run from *now*, not from the lapsed date — so a customer who renews
late is not silently short-changed.

---

## 3. Endpoints

### `GET /me`

Who this credential is and what it may do. **Call it first**, and call it again
whenever something returns 403. Needs no scope — a narrowly scoped key must be
able to discover why it is being refused.

```json
{
  "admin": "root",
  "role": "superadmin",
  "auth": "api_key",
  "key_prefix": "ath_K7pQ2mXd91Lb",
  "scopes": ["users:read", "users:write"],
  "unrestricted_scopes": false,
  "rate_limit_per_minute": 120
}
```

---

### `GET /users` — list accounts
`users:read`

| Query | Default | Notes |
| --- | --- | --- |
| `search` | — | substring of username **or** note |
| `status` | — | `active` · `disabled` · `online` · `expired` · `exceeded` |
| `node_id` | — | filter by server |
| `outbound` | — | filter by egress location name |
| `page` | `1` | |
| `page_size` | `50` | max 500 |
| `sort` | `username` | `username` · `created_at` · `used_bytes` · `expires_at` |
| `order` | `asc` | `asc` · `desc` |

```bash
curl -s -H "X-API-Key: $KEY" \
  "$BASE/users?status=exceeded&sort=used_bytes&order=desc&page_size=20"
```

---

### `GET /users/{username}` — one account
`users:read`

```json
{
  "username": "ali_1234",
  "password": "8fJ2kQx7",
  "enabled": true,
  "online": true,

  "limit_bytes": 53687091200,
  "limit_gb": 50.0,
  "used_bytes": 12884901888,
  "used_gb": 12.0,
  "remaining_bytes": 40802189312,
  "remaining_gb": 38.0,
  "usage_percent": 24.0,
  "quota_exceeded": false,

  "expires_at": "2026-09-15T10:30:00Z",
  "days_remaining": 27,
  "expired": false,
  "created_at": "2026-08-15T10:30:00Z",
  "last_seen": "2026-08-17T19:04:11Z",
  "total_sessions": 412,

  "node_id": 1,
  "node_name": "local",
  "outbound": "de-fra",
  "l2tp_mode": "ipsec",
  "rate_up_kbps": 0,
  "rate_down_kbps": 0,
  "note": "telegram:558211",
  "owner": "reseller1",

  "endpoints": {
    "l2tp": "lttp.topmeli.com",
    "l2tp_raw": "",
    "sstp": "sstp.topmeli.com",
    "wireguard": "185.235.198.73:51821"
  },
  "subscription_url": "sb.topmeli.com:2087/sub/5-nme8ii0lx_faDf8sdILB_b"
}
```

`password` is the real connection password — this is a chap-secrets stack, so
the panel holds it in clear. Treat every response containing it accordingly.

`endpoints` is already resolved **for this account's node**, so a bot can hand
the values straight to the customer without knowing anything about nodes.
An empty string means that protocol is not offered there.

---

### `POST /users` — create
`users:write` · returns `201`

```json
{
  "username": "ali_1234",
  "password": null,
  "limit_gb": 50,
  "duration_days": 30,
  "enabled": true,
  "node_id": null,
  "outbound": null,
  "l2tp_mode": "ipsec",
  "rate_up_kbps": 0,
  "rate_down_kbps": 0,
  "note": "telegram:558211"
}
```

Only `username` is required.

- `password` omitted or `null` → generated, and returned in the response.
- `duration_days` → expiry computed from now. Use `expires_at` instead for an
  absolute date; if both are given, `expires_at` wins.
- `limit_gb: 0` → unlimited.
- `node_id` omitted → the local server (node 1).

`409` if the username exists. **If you are retrying a create that timed out,
treat 409 as success** and `GET` the account.

A reseller with a `max_users` cap gets `403` once it is reached.

---

### `PATCH /users/{username}` — edit
`users:write`

Send only what changes. Every field is optional:
`password`, `limit_gb`, `expires_at`, `enabled`, `node_id`, `outbound`,
`l2tp_mode`, `rate_up_kbps`, `rate_down_kbps`, `note`.

This **sets** values. To renew, use `extend`.

An `outbound` that does not exist gives `400` rather than silently falling back
to `direct` — you asked for a location, and being told you got it when you did
not is worse than an error.

---

### `POST /users/{username}/extend` — renew
`users:write`

```json
{ "days": 30, "gb": 50, "reset_usage": false }
```

All three are optional but at least one must do something, or you get `400`.

- `days` — added to the current expiry, or to *now* if it already lapsed.
- `gb` — added to the current limit.
- `reset_usage` — zero the counter. This is a **renewal**, not a correction:
  it forgives everything used so far.

---

### `POST /users/{username}/enable` · `/disable`
`users:write` — returns the updated account.

Disabling takes effect on the customer's next connection attempt, and the
enforcer drops any live session within one cycle (30s). To cut them off now,
call `disconnect` as well.

---

### `POST /users/{username}/disconnect`
`sessions:write`

```json
{ "username": "ali_1234", "queued": false }
```

`queued: true` means the account lives on a remote node: the panel cannot
signal a node directly, so the request is delivered on that node's next report,
normally within a few seconds.

---

### `DELETE /users/{username}`
`users:write`

```json
{ "deleted": "ali_1234" }
```

Immediate and irreversible. The account's ledger history is kept.

---

### `GET /users/{username}/sessions` · `GET /sessions`
`sessions:read`

```json
[
  {
    "username": "ali_1234",
    "node_id": 1,
    "node_name": "local",
    "ifname": "ppp42",
    "protocol": "L2TP",
    "peer_ip": "192.168.44.19",
    "started_at": "2026-08-17T18:55:00Z",
    "duration_seconds": 900,
    "bytes_in": 10485760,
    "bytes_out": 83886080,
    "bytes_total": 94371840
  }
]
```

`bytes_in` is from the customer (their upload), `bytes_out` is to them.

---

### `GET /nodes`
`system:read` — where accounts can be placed.

A superadmin gets health and throughput; a reseller gets `id` and `name` only,
because the address and state of the operator's servers are not theirs.

### `GET /outbounds`
`system:read` — egress locations an account can be assigned to.

```json
[
  { "name": "direct",  "label": "Direct",  "country": "",   "status": "up", "users": 104 },
  { "name": "de-fra",  "label": "de-fra",  "country": "de", "status": "up", "users": 1 }
]
```

Use `name` as the value for a user's `outbound`.

### `GET /stats`
`system:read` — totals for whatever the caller can see. A reseller gets their
own numbers, not the platform's.

```json
{
  "users_total": 137, "users_enabled": 130, "users_online": 42,
  "users_expired": 4, "users_quota_exceeded": 3,
  "used_bytes_total": 4123456789012, "used_gb_total": 3839.7
}
```

---

## 4. Managing keys

`/api/api-keys` — **session token only, never an API key.** A key that can mint
keys can escalate past its own scopes and outlive its own revocation.

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/api-keys/scopes` | the scope vocabulary, served from the code |
| `GET` | `/api/api-keys` | your keys (a superadmin sees everyone's) |
| `POST` | `/api/api-keys` | mint — **the only time the secret is returned** |
| `PATCH` | `/api/api-keys/{id}` | rename, re-scope, change the limit |
| `POST` | `/api/api-keys/{id}/revoke` | switch off, keep the history |
| `DELETE` | `/api/api-keys/{id}` | remove entirely |

Prefer **revoke** to delete: the counters and the audit trail survive the
incident that made you revoke it.

---

## 5. Worked example — a renewal bot

```python
import os, httpx

BASE = "https://panelmgh.elsaw.site:8443/api/v1"
KEY  = os.environ["ATHENA_API_KEY"]
api  = httpx.Client(base_url=BASE, headers={"X-API-Key": KEY}, timeout=20)


def sell(telegram_id: int, plan_gb: int, plan_days: int) -> dict:
    """Create an account, or renew it if this customer already has one."""
    username = f"tg{telegram_id}"

    r = api.get(f"/users/{username}")
    if r.status_code == 200:
        return api.post(f"/users/{username}/extend",
                        json={"days": plan_days, "gb": plan_gb,
                              "reset_usage": True}).raise_for_status().json()

    r = api.post("/users", json={"username": username,
                                 "limit_gb": plan_gb,
                                 "duration_days": plan_days,
                                 "note": f"telegram:{telegram_id}"})
    if r.status_code == 409:
        # Someone (or a retry of ours) created it in between. Not an error.
        return api.get(f"/users/{username}").raise_for_status().json()
    return r.raise_for_status().json()


def status_message(username: str) -> str:
    u = api.get(f"/users/{username}").raise_for_status().json()
    if u["limit_gb"]:
        data = f"{u['used_gb']:.1f} / {u['limit_gb']:.0f} GB ({u['usage_percent']:.0f}%)"
    else:
        data = f"{u['used_gb']:.1f} GB used, unlimited"
    when = f"{u['days_remaining']} days left" if u["days_remaining"] is not None else "no expiry"
    dot = "🟢" if u["online"] else "⚪"
    return (f"{dot} {u['username']}\n{data}\n{when}\n\n"
            f"L2TP: {u['endpoints']['l2tp']}\n"
            f"Subscription: {u['subscription_url']}")
```

### Things that will bite you if you skip them

- **Retry `POST /users` into a 409, not into a duplicate.** A timeout does not
  mean the account was not created.
- **Do not poll `/users` in a loop to find one account.** `GET /users/{name}`
  is one query; listing 137 accounts to find one is 137.
- **Cache `/outbounds` and `/nodes`.** They change when the operator changes
  them, which is rarely.
- **A 404 may mean "not yours".** If your bot serves several resellers, do not
  report "no such account" as if it were authoritative.
- **`used_bytes` moves while a session is live.** Two reads a second apart
  legitimately differ; that is not a bug to work around.

---

## 6. Compatibility

Inside v1 the panel may **add** response fields and **add** optional request
fields. It will not remove a field, rename one, change its type, or change what
it means. Parse defensively — ignore fields you do not know — and a panel
upgrade will not break your bot.

Anything under `/api/` that is **not** `/api/v1/` is the panel's own UI surface.
It ships with the frontend, changes without notice, and is not covered by this
document. Do not build on it.
