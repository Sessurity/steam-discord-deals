#!/usr/bin/env python3
"""Read chat commands from a Discord channel using scheduled GitHub Actions."""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from steam_deals import KYIV, STEAM_HEADER, deal_sort_key, select_popular_deals, discord_safe, get_spore_price, query_steam_sales

API = "https://discord.com/api/v10"
STATE = Path(__file__).resolve().parent / "data" / "command_state.json"
MAX_COMMANDS = 5
MAX_DEALS_REPLY = 30
DEALS_PER_MESSAGE = 10

def parse_command(content):
    parts = (content or "").strip().split(maxsplit=1)
    if not parts:
        return None
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("!help", "!допомога"):
        return ("help", None)
    if cmd == "!spore":
        return ("spore", None)
    if cmd in ("!deals", "!steam"):
        if not arg:
            return ("deals", 70)
        if not arg.isascii() or not arg.isdecimal() or not 70 <= int(arg) <= 100:
            return ("error", "Введи відсоток від 70 до 100, наприклад: !deals 90")
        return ("deals", int(arg))
    if cmd in ("!find", "!game"):
        if not 2 <= len(arg) <= 80:
            return ("error", "Напиши назву гри, наприклад: !find Dying Light")
        return ("find", arg)
    return None

def game_line(game):
    link = f"https://store.steampowered.com/app/{game['appid']}/?cc=ua"
    name = discord_safe(game["name"])[:80]
    return f"**[{name}]({link})** · **−{game['discount']}%**\n{game['price']} · було {game['old_price']}"

def embed(title, description, color=0x39CFC6):
    return {"title": title, "description": description[:3800], "color": color}

def build_reply(command, discounted=None):
    kind, arg = command
    if kind == "help":
        return embed("⬡ ZHAXO · Steam Commands",
                     "**!deals** — до 30 знижок від 70%\n"
                     "**!deals 90** — до 30 знижок від 90%\n"
                     "**!spore** — актуальна ціна SPORE\n"
                     "**!find Dying Light** — пошук гри серед знижок від 70%\n"
                     "Перевірка приблизно кожні 5 хвилин; GitHub іноді затримує запуск.")
    if kind == "error":
        return embed("⚠️ Формат команди", arg)
    if kind == "spore":
        s = get_spore_price()
        if not s.get("available"):
            text = "Ціну SPORE в гривнях зараз отримати не вдалося."
        elif s.get("discount"):
            text = f"−{s['discount']}% · **{s['price']}** (було {s['old_price']})"
        else:
            text = f"Без знижки · **{s['price']}**"
        return {**embed("★ SPORE · пріоритет",
                        text + "\n[Відкрити Steam](https://store.steampowered.com/app/17390/Spore/)",
                        0xD6AF61),
                "thumbnail": {"url": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/17390/header.jpg"}}
    sales = discounted if discounted is not None else query_steam_sales()
    if kind == "deals":
        items = select_popular_deals((g for g in sales if g["discount"] >= arg), DEALS_PER_MESSAGE)
        desc = (f"🇺🇦 Україна · знижки від −{arg}%\n\n" +
                "\n\n".join(map(game_line, items))) if items else "Зараз таких акцій не знайдено."
        return embed("⬡ ZHAXO · Steam Deals", desc)
    if kind == "find":
        items = sorted((g for g in sales if arg.casefold() in g["name"].casefold()), key=deal_sort_key)[:5]
        desc = ("\n\n".join(map(game_line, items)) if items else
                "Не знайдено серед актуальних знижок від 70%. Це не означає, що гри немає у Steam.")
        return embed("🔎 Пошук знижок", desc)
    raise ValueError("Unknown command")

def game_card(game, number):
    """Варіант A: окрема Discord-картка зі збільшеною обкладинкою Steam."""
    appid = game["appid"]
    url = f"https://store.steampowered.com/app/{appid}/?cc=ua"
    name = discord_safe(game["name"])[:130]
    description = (
        f"**−{game['discount']}%**  ·  ~~{game['old_price']}~~  →  **{game['price']}**"
    )
    if game.get("ends"):
        description += "\n⏳ До " + datetime.fromtimestamp(game["ends"], KYIV).strftime("%d.%m.%Y %H:%M")
    description += f"\n[Відкрити у Steam]({url})"
    return {
        "title": f"{number:02d} / {name}",
        "url": url,
        "description": description,
        "color": 0x39CFC6,
        "image": {"url": STEAM_HEADER.format(appid)},
    }


def build_replies(command, discounted=None):
    """Повертає Discord payloads: максимум 10 великих карток у повідомленні."""
    kind, arg = command
    if kind not in ("deals", "find"):
        return [{"embeds": [build_reply(command, discounted)]}]
    sales = discounted if discounted is not None else query_steam_sales()
    if kind == "deals":
        matches = select_popular_deals(
            (game for game in sales if game["discount"] >= arg),
            MAX_DEALS_REPLY
        )
        headline = f"🇺🇦 **ZHAXO // Steam Deals** · знижки від **−{arg}%**"
    else:
        matches = sorted(
            (game for game in sales if arg.casefold() in game["name"].casefold()),
            key=deal_sort_key
        )[:5]
        headline = f"🔎 **ZHAXO // Пошук** · {discord_safe(arg)[:80]}"
    if not matches:
        message = ("Зараз таких знижок не знайдено." if kind == "deals"
                   else "Не знайдено серед активних знижок від 70%.")
        return [{"embeds": [embed("⬡ ZHAXO · Steam", headline + "\n" + message)]}]
    pages = []
    total = len(matches)
    for offset in range(0, total, DEALS_PER_MESSAGE):
        part = matches[offset:offset + DEALS_PER_MESSAGE]
        cards = [game_card(game, offset + i + 1) for i, game in enumerate(part)]
        if len(cards) > 10 or sum(len(c["title"]) + len(c["description"]) for c in cards) > 6000:
            raise RuntimeError("Перевищено ліміт Discord для карток")
        summary = f"{headline} · **{offset + 1}–{offset + len(part)} із {total}**\n🔽 Від найбільшої знижки до найменшої"
        pages.append({"content": summary, "embeds": cards})
    return pages


def discord_api(token, method, path, params=None, body=None):
    url = API + path + ("?" + urlencode(params) if params else "")
    headers = {"Authorization": "Bot " + token, "Accept": "application/json",
               "User-Agent": "ZHAXO-Steam-Commands/1.0"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(Request(url, headers=headers, data=data, method=method), timeout=25) as r:
            return json.load(r) if r.status != 204 else None
    except HTTPError as e:
        raise RuntimeError(f"Discord API HTTP {e.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Discord API connection failed") from None

def get_messages(token, channel, cursor):
    args = {"limit": 100 if cursor else 50}
    if cursor:
        args["after"] = str(cursor)
    items = discord_api(token, "GET", f"/channels/{channel}/messages", params=args)
    if not isinstance(items, list):
        raise RuntimeError("Discord returned invalid messages")
    return sorted(items, key=lambda item: int(item["id"]))

def authorized(message, allowed):
    author = message.get("author") or {}
    return not author.get("bot") and not message.get("webhook_id") and (
        not allowed or str(author.get("id")) in allowed)

def fresh_on_first_poll(message, now):
    try:
        stamp = datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
        return now - timedelta(minutes=10) <= stamp <= now + timedelta(minutes=1)
    except (KeyError, TypeError, ValueError):
        return False

def process_messages(messages, cursor, token, channel, allowed=None, now=None, send=None):
    allowed = allowed or set()
    now = now or datetime.now(timezone.utc)
    send = send or (lambda msg_id, answer: discord_api(
        token, "POST", f"/channels/{channel}/messages", body={
            **answer,
            "message_reference": {"message_id": msg_id, "channel_id": channel, "fail_if_not_exists": False},
            "allowed_mentions": {"parse": []},
        }))
    seen = str(cursor) if cursor else None
    replies = 0
    cache = None
    for message in messages:
        msg_id = str(message["id"])
        if seen is not None and int(msg_id) <= int(seen):
            continue
        if (cursor or fresh_on_first_poll(message, now)) and authorized(message, allowed):
            command = parse_command(message.get("content"))
            if command is not None:
                try:
                    if command[0] in ("deals", "find") and cache is None:
                        cache = query_steam_sales()
                    answers = build_replies(command, discounted=cache)
                except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                    print(f"Command error: {type(exc).__name__}")
                    answers = [embed("⚠️ Steam тимчасово недоступний", "Спробуй пізніше.")]
                for answer in answers:
                    send(msg_id, answer)
                replies += 1
        seen = msg_id
        if replies >= MAX_COMMANDS:
            break
    return seen, replies

def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel:
        print("Discord commands not configured: need DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID")
        return
    if not channel.isdecimal():
        raise RuntimeError("DISCORD_CHANNEL_ID must be numeric")
    allowed = {x.strip() for x in os.environ.get("DISCORD_ALLOWED_USER_IDS", "").split(",") if x.strip()}
    previous = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    old_cursor = previous.get("last_message_id")
    messages = get_messages(token, channel, old_cursor)
    new_cursor, count = process_messages(messages, old_cursor, token, channel, allowed)
    if new_cursor and new_cursor != old_cursor:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"last_message_id": new_cursor}, indent=2) + "\n", encoding="utf-8")
    print(f"Checked {len(messages)} messages; responded to {count} commands")

if __name__ == "__main__":
    main()
