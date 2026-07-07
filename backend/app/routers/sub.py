"""Public per-user subscription / account page (signed-token, no auth).

The URL itself is the credential: /sub/<id>-<sig>. Renders a self-contained
page (no external assets — robust on filtered networks) that matches the admin
panel's zinc dark theme: flat surfaces, hairline borders, muted labels.
"""

from datetime import datetime, timedelta, timezone
from string import Template

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import livecache
from ..config import settings
from ..database import get_session
from ..models import AccountingRecord, UsageSample, User
from ..subtoken import parse_token

router = APIRouter(prefix="/sub", tags=["sub"])

BRAND = "ATHENA"

# badge palette: (text, background, border) — matches the panel's semantic colors
_GREEN = ("hsl(142 65% 56%)", "hsl(142 69% 45% / .12)", "hsl(142 40% 26%)")
_AMBER = ("hsl(38 90% 62%)", "hsl(38 92% 50% / .12)", "hsl(38 55% 30%)")
_RED = ("hsl(0 80% 68%)", "hsl(0 63% 52% / .13)", "hsl(0 50% 32%)")


def _split(n: int) -> tuple[str, str]:
    n = max(0, n or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    s = f"{f:.1f}".rstrip("0").rstrip(".")
    return s, units[i]


def _fmt(n: int) -> str:
    s, u = _split(n)
    return f"{s} {u}"


_CHART_COLOR = "hsl(142 60% 50%)"   # panel --chart-tx green
_GRID = "hsl(240 3.7% 16%)"
_AXIS = "hsl(240 5% 55%)"


def _mmdd(day: str) -> str:
    p = day.split("-")
    return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else day


def _chart_svg(series: list[tuple[str, int]], color: str = _CHART_COLOR) -> str:
    """shadcn/ui-style bar chart of daily usage (inline SVG, no deps).

    Dashed horizontal grid (CartesianGrid), top-rounded bars, muted X-axis ticks.
    series = [(YYYY-MM-DD, bytes), ...].
    """
    W, H, pt, pb, gap = 344, 112, 8, 22, 5
    plot_h = H - pt - pb
    base = pt + plot_h
    n = len(series)
    bw = (W - gap * (n - 1)) / n
    maxv = max((v for _, v in series), default=0) or 1
    parts: list[str] = []
    # CartesianGrid — faint dashed horizontals
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = pt + plot_h * (1 - f)
        parts.append(
            f'<line x1="0" y1="{gy:.1f}" x2="{W}" y2="{gy:.1f}" stroke="{_GRID}" '
            f'stroke-width="1" stroke-dasharray="3 3"/>'
        )
    # top-rounded bars
    for i, (day, v) in enumerate(series):
        h = (v / maxv) * plot_h
        if v > 0:
            h = max(h, 3)
        else:
            continue
        x = i * (bw + gap)
        y = base - h
        r = min(4.0, bw / 2, h / 2)
        parts.append(
            f'<path d="M{x:.1f},{base:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
            f'L{x + bw - r:.1f},{y:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} '
            f'L{x + bw:.1f},{base:.1f} Z" fill="{color}"><title>{day}: {_fmt(v)}</title></path>'
        )
    # X-axis ticks (a few, no axis line — shadcn style)
    for i in sorted({0, n // 3, (2 * n) // 3, n - 1}):
        cx = i * (bw + gap) + bw / 2
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        parts.append(
            f'<text x="{cx:.1f}" y="{H - 6}" fill="{_AXIS}" font-size="9.5" '
            f'text-anchor="{anchor}" font-family="ui-sans-serif,system-ui,sans-serif">{_mmdd(series[i][0])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" height="{H}" '
        f'role="img" aria-label="daily usage, last 14 days">{"".join(parts)}</svg>'
    )


_PAGE = Template(
    r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta http-equiv="refresh" content="120">
<title>$brand · $username</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:hsl(240 10% 3.9%); --card:hsl(240 8% 6.5%); --bd:hsl(240 3.7% 14%);
    --fg:hsl(0 0% 98%); --mut:hsl(240 5% 64.9%); --mut2:hsl(240 4% 46%);
    --track:hsl(240 3.7% 13%); --ac:$accent;
  }
  html,body{height:100%}
  body{background:var(--bg);color:var(--fg);font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,system-ui,sans-serif;
    display:flex;align-items:center;justify-content:center;min-height:100%;padding:20px;-webkit-font-smoothing:antialiased}
  .card{width:100%;max-width:380px;background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden}
  .hd{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--bd)}
  .brand{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:500;letter-spacing:.16em;color:var(--fg)}
  .brand svg{width:15px;height:15px;color:var(--ac)}
  .badge{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:500;padding:4px 10px;border-radius:8px;border:1px solid $badgebd;color:$badgefg;background:$badgebg}
  .badge .dot{width:6px;height:6px;border-radius:50%;background:$badgefg}
  .body{padding:18px}
  .uname{font-size:18px;font-weight:600;letter-spacing:-.01em;line-height:1.1}
  .sub{font-size:12px;color:var(--mut);margin-top:3px}
  .big{display:flex;align-items:baseline;gap:8px;margin:20px 0 12px}
  .big b{font-size:34px;font-weight:600;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}
  .big span{font-size:13px;color:var(--mut)}
  .track{height:7px;border-radius:6px;background:var(--track);overflow:hidden}
  .fill{height:100%;border-radius:6px;background:var(--ac);width:$pct%;min-width:$minw}
  .pctrow{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);margin-top:8px}
  .rows{margin-top:18px;border-top:1px solid var(--bd)}
  .row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid var(--bd);font-size:13px}
  .row:last-child{border-bottom:0}
  .row .k{color:var(--mut)}
  .row .v{font-weight:500;font-variant-numeric:tabular-nums}
  .chart{margin-top:18px;padding-top:16px;border-top:1px solid var(--bd)}
  .crow{display:flex;justify-content:space-between;align-items:baseline;font-size:11px;color:var(--mut);margin-bottom:10px}
  .crow b{color:var(--fg);font-weight:500;font-variant-numeric:tabular-nums}
  .chart svg{display:block;border-radius:3px}
  .cempty{font-size:12px;color:var(--mut2);text-align:center;padding:14px 0}
  .ft{padding:11px 18px;border-top:1px solid var(--bd);font-size:11px;color:var(--mut2);text-align:center;letter-spacing:.04em}
</style>
</head>
<body>
  <div class="card">
    <div class="hd">
      <div class="brand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2 4 6v6c0 5 3.4 7.8 8 10 4.6-2.2 8-5 8-10V6z"/></svg>$brand</div>
      <div class="badge">$dot$status</div>
    </div>
    <div class="body">
      <div class="uname">$username</div>
      <div class="sub">subscription</div>
      <div class="big"><b>$big_num</b><span>$big_unit&nbsp;$big_rest</span></div>
      <div class="track"><div class="fill"></div></div>
      <div class="pctrow"><span>$pct% used</span><span>$total total</span></div>
      <div class="rows">
        <div class="row"><span class="k">Used</span><span class="v">$used</span></div>
        <div class="row"><span class="k">Data limit</span><span class="v">$total</span></div>
        <div class="row"><span class="k">Expires</span><span class="v">$expiry</span></div>
        <div class="row"><span class="k">Days left</span><span class="v">$days</span></div>
      </div>
      $chart_block
    </div>
    <div class="ft">$brand</div>
  </div>
</body>
</html>"""
)


def _render(user: User, used: int, quota: int, online: bool, series: list[tuple[str, int]]) -> str:
    now = datetime.now(timezone.utc)
    unlimited = quota <= 0
    expired = bool(user.is_expired)
    exceeded = not unlimited and used >= quota
    pct = 0.0 if unlimited else (min(100.0, (used / quota) * 100) if quota else 0.0)

    if unlimited:
        bnum, bunit = _split(used)
        brest = "used"
    else:
        bnum, bunit = _split(max(0, quota - used))
        brest = "remaining"

    if unlimited or pct < 70:
        accent = "hsl(142 69% 45%)"
    elif pct < 90:
        accent = "hsl(38 92% 50%)"
    else:
        accent = "hsl(0 63% 52%)"

    if not user.is_active:
        status, badge, dot = "Disabled", _RED, ""
    elif expired:
        status, badge, dot = "Expired", _RED, ""
    elif exceeded:
        status, badge, dot = "Limit reached", _AMBER, ""
    elif online:
        status, badge, dot = "Online", _GREEN, '<span class="dot"></span>'
    else:
        status, badge, dot = "Active", _GREEN, ""

    exp = user.expires_at
    if exp is None:
        expiry, days = "Never", "∞"
    else:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        expiry = exp.strftime("%Y-%m-%d")
        days = str(max(0, (exp - now).days))

    chart_total = sum(v for _, v in series)
    if chart_total > 0:
        chart_block = (
            '<div class="chart"><div class="crow"><span>Last 14 days</span>'
            f'<b>{_fmt(chart_total)}</b></div>{_chart_svg(series)}</div>'
        )
    else:
        chart_block = (
            '<div class="chart"><div class="crow"><span>Last 14 days</span><span>—</span></div>'
            '<div class="cempty">No usage recorded yet</div></div>'
        )

    return _PAGE.safe_substitute(
        brand=BRAND, username=user.username,
        status=status, dot=dot,
        badgefg=badge[0], badgebg=badge[1], badgebd=badge[2],
        accent=accent, pct=round(pct, 1), minw=("0" if pct <= 0 else "10px"),
        big_num=bnum, big_unit=bunit, big_rest=brest,
        used=_fmt(used), total=("Unlimited" if unlimited else _fmt(quota)),
        expiry=expiry, days=days, chart_block=chart_block,
    )


@router.get("/{token}", response_class=HTMLResponse)
async def sub_page(token: str, db: AsyncSession = Depends(get_session)):
    uid = parse_token(token)
    if uid is None:
        raise HTTPException(status_code=404, detail="Not found")
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    snap = livecache.snapshot()
    live_bytes = snap["live_by_user"].get(user.username, 0)
    online = user.username in snap["online"]
    used = (user.used_bytes or 0) + max(0, live_bytes)

    # daily usage, last 14 days, from the closed-session accounting ledger
    now = datetime.now(timezone.utc)
    today = now.date()
    day_col = func.date(AccountingRecord.stopped_at)
    rows = (await db.execute(
        select(day_col, func.sum(AccountingRecord.bytes_in + AccountingRecord.bytes_out))
        .where(
            AccountingRecord.username == user.username,
            AccountingRecord.stopped_at >= now - timedelta(days=14),
        )
        .group_by(day_col)
    )).all()
    by_day = {str(d): int(b or 0) for d, b in rows}

    # WireGuard has no closed-session ledger rows (peers are perpetual), so derive
    # its per-day bytes from the usage_samples counter series: (max - min) of the
    # cumulative counter within each day, per wg iface, summed. usage_samples
    # stores RAW counters, so apply the accounting multiplier to match the ledger
    # (whose bytes are already scaled at finalize).
    wg_inner = (
        select(
            func.date(UsageSample.ts).label("d"),
            (func.max(UsageSample.rx_bytes + UsageSample.tx_bytes)
             - func.min(UsageSample.rx_bytes + UsageSample.tx_bytes)).label("delta"),
        )
        .where(
            UsageSample.username == user.username,
            UsageSample.proto == "wg",
            UsageSample.ts >= now - timedelta(days=14),
        )
        .group_by(func.date(UsageSample.ts), UsageSample.ifname)
        .subquery()
    )
    wg_rows = (await db.execute(
        select(wg_inner.c.d, func.sum(wg_inner.c.delta)).group_by(wg_inner.c.d)
    )).all()
    mult = settings.usage_multiplier
    for d, delta in wg_rows:
        raw = int(delta or 0)  # func.sum -> Decimal on PG; may be <0 on a mid-day counter reset
        if raw > 0:
            key = str(d)
            by_day[key] = by_day.get(key, 0) + int(raw * mult)

    series = [
        (str(today - timedelta(days=i)), by_day.get(str(today - timedelta(days=i)), 0))
        for i in range(13, -1, -1)
    ]
    if series and live_bytes > 0:  # fold today's in-progress session into the last bar
        series[-1] = (series[-1][0], series[-1][1] + max(0, live_bytes))

    return HTMLResponse(_render(user, used, user.quota_bytes or 0, online, series))
