#!/usr/bin/env python3
"""Remove only obsolete PUBLIC ZHAXO slash-command answers from #deals.

The daily Steam report is NEVER a cleanup target. Cloudflare now responds
ephemerally, so these old public responses should be removed once.
"""
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://discord.com/api/v10"
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "state.json"


def api_call(method, url, token):
    req = Request(url, headers={
        "Authorization": "Bot " + token,
        "Accept": "application/json",
        "User-Agent": "ZHAXO-Legacy-Command-Cleanup/1.0",
    }, method=method)
    for attempt in range(4):
        try:
            with urlopen(req, timeout=20) as response:
                return None if response.status == 204 else json.load(response)
        except HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                try:
                    info = json.loads(exc.read().decode("utf-8"))
                    delay = float(info.get("retry_after", 1.5))
                except (ValueError, TypeError):
                    delay = 1.5
                time.sleep(min(10, max(0.2, delay)))
                continue
            raise RuntimeError(f"Discord API HTTP {exc.code}") from None
        except URLError:
            raise RuntimeError("Discord API connection unavailable") from None
    raise RuntimeError("Discord API request retry limit")


def protected_report_ids(path=STATE_FILE):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return {str(v) for v in (state.get("report") or {}).get("message_ids", [])}
    except (FileNotFoundError, ValueError, OSError):
        # Fail closed: if we cannot read current report IDs, don't delete anything.
        raise RuntimeError("Cannot load daily report state; cleanup cancelled")


def legacy_slash_reply(message, bot_id, protected):
    """Strictly match own old slash replies; exclude all other bot messages."""
    if str(message.get("id")) in protected:
        return False
    if str((message.get("author") or {}).get("id")) != str(bot_id):
        return False
    metadata = message.get("interaction_metadata") or message.get("interaction") or {}
    if not metadata:
        return False
    content = str(message.get("content") or "")
    embeds = message.get("embeds") or []
    titles = [str(x.get("title") or "") for x in embeds if isinstance(x, dict)]
    # ZHAXO slash messages produced by the previous Cloudflare implementation.
    if content.startswith("⬡ **ZHAXO / ЗНИЖКИ ВІД −"):
        return True
    if content.startswith("⬡ **ZHAXO / ПОШУК"):
        return True
    if "★ SPORE · головний пріоритет" in titles:
        return True
    if "⬡ ZHAXO · Команди" in titles:
        return True
    return False


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel.isdecimal():
        raise RuntimeError("DISCORD_BOT_TOKEN and numeric DISCORD_CHANNEL_ID are required")
    protected = protected_report_ids()
    dry_run = os.environ.get("DRY_RUN", "1") != "0"
    bot = api_call("GET", API + "/users/@me", token)
    bot_id = str(bot["id"])
    scanned = removed = matched = 0
    before = None
    for _ in range(5):
        params = {"limit": 100}
        if before:
            params["before"] = before
        url = API + "/channels/" + channel + "/messages?" + urlencode(params)
        messages = api_call("GET", url, token)
        if not isinstance(messages, list):
            raise RuntimeError("Discord returned invalid channel messages")
        if not messages:
            break
        for message in messages:
            scanned += 1
            if not legacy_slash_reply(message, bot_id, protected):
                continue
            matched += 1
            if matched > 100:
                raise RuntimeError("More than 100 matched messages; refusing mass deletion")
            if dry_run:
                print(f"DRY RUN: old ZHAXO slash response {message['id']} would be removed")
                continue
            api_call("DELETE", API + "/channels/" + channel + "/messages/" + str(message["id"]), token)
            removed += 1
        before = messages[-1]["id"]
        if len(messages) < 100:
            break
    print(f"Checked {scanned} messages; matched {matched} old command replies; deleted {removed}; "
          f"dry_run={dry_run}; protected_daily_messages={len(protected)}")


if __name__ == "__main__":
    main()
