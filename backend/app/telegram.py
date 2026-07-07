"""Telegram bot helpers — send DB backups + notifications.

Uses `curl` (already on the host) so there is no extra Python HTTP dependency,
consistent with how the rest of the app shells out. Bot API document limit is
50 MB; larger files are rejected by Telegram (the caller guards on size).
"""

import asyncio
import json

_API = "https://api.telegram.org/bot{token}/{method}"
TG_DOC_LIMIT = 49 * 1024 * 1024  # stay just under Telegram's 50 MB sendDocument cap


async def _curl(*args: str, timeout: int = 180) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "curl", "-sS", "--max-time", str(timeout), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, (out.decode("utf-8", "replace") or err.decode("utf-8", "replace"))


def _ok(raw: str) -> bool:
    try:
        return bool(json.loads(raw).get("ok"))
    except Exception:  # noqa: BLE001
        return False


async def send_message(token: str, chat_id: str, text: str) -> bool:
    rc, out = await _curl(
        _API.format(token=token, method="sendMessage"),
        "-d", f"chat_id={chat_id}", "--data-urlencode", f"text={text}",
        timeout=20,
    )
    return rc == 0 and _ok(out)


async def send_document(token: str, chat_id: str, path: str, caption: str = "") -> tuple[bool, str]:
    rc, out = await _curl(
        _API.format(token=token, method="sendDocument"),
        "-F", f"chat_id={chat_id}",
        "-F", f"document=@{path}",
        "-F", f"caption={caption}",
    )
    return (rc == 0 and _ok(out)), out


async def resolve_chat_id(token: str) -> str | None:
    """Most recent chat that messaged the bot (so the operator just /start's the
    bot and the panel auto-discovers where to send)."""
    rc, out = await _curl(_API.format(token=token, method="getUpdates"), timeout=20)
    if rc != 0:
        return None
    try:
        updates = json.loads(out).get("result", [])
    except Exception:  # noqa: BLE001
        return None
    for u in reversed(updates):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        cid = (msg.get("chat") or {}).get("id")
        if cid:
            return str(cid)
    return None
