#!/usr/bin/env python3
"""Щоденний Discord-звіт про Steam-знижки для українського регіону.

Жодних сторонніх Python-бібліотек. Не читає Steam-логін і не здійснює покупок.
Steam Store Query — неофіційно документований магазинний API, може змінюватися.
"""

import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

COUNTRY = "UA"
MIN_DISCOUNT = 70
MAX_DEALS = 20
SPORE_APP_ID = 17390
STEAM_QUERY_URL = "https://api.steampowered.com/IStoreQueryService/Query/v1/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STATE_FILE = Path(__file__).resolve().parent / "data" / "state.json"
KYIV = ZoneInfo("Europe/Kyiv")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Steam-Discord-Deals/1.0; GitHub Actions)",
    "Accept": "application/json, text/plain, */*",
}


def get_json(url, params=None, retries=4):
    if params:
        url += "?" + urlencode(params)
    last_error = None
    for attempt in range(retries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=25) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                break
            if attempt < retries - 1:
                time.sleep(min(4 * (attempt + 1), 16))
    raise RuntimeError(f"Не вдалося отримати дані зі Steam: {type(last_error).__name__}: {last_error}")


def fmt_money(value, formatted=None):
    """Показує лише явно українську валюту, ніколи не конвертує курс сам."""
    if isinstance(formatted, str) and re.search(r"₴|грн|\bUAH\b", formatted, re.I):
        return formatted.strip()
    if isinstance(value, int) and value >= 0:
        # Магазинний Query викликаємо явно з country_code=UA.
        amount = value / 100
        return f"{amount:,.2f} грн".replace(",", " ").replace(".", ",")
    return "ціна недоступна"


def parse_game(item):
    if not isinstance(item, dict) or item.get("type") != 0 or item.get("visible") is False:
        return None
    opt = item.get("best_purchase_option") or {}
    pct = int(opt.get("discount_pct") or 0)
    if pct < MIN_DISCOUNT or pct > 100:
        return None
    appid = item.get("appid") or item.get("id")
    if not isinstance(appid, int) or appid <= 0 or not item.get("name"):
        return None
    try:
        current = int(opt.get("final_price_in_cents"))
        old = int(opt.get("original_price_in_cents"))
    except (TypeError, ValueError):
        return None
    if current < 0 or old <= 0 or current >= old:
        return None
    rev = (item.get("reviews") or {}).get("summary_filtered") or {}
    count = int(rev.get("review_count") or 0)
    positive = int(rev.get("percent_positive") or 0)
    name = re.sub(r"\s+", " ", item["name"]).strip()
    if not name:
        return None
    ends = []
    for discount in opt.get("active_discounts") or []:
        end = discount.get("discount_end_date")
        if isinstance(end, (int, float)) and end > time.time():
            ends.append(int(end))
    return {
        "appid": appid,
        "name": name,
        "discount": pct,
        "final": current,
        "original": old,
        "price": fmt_money(current, opt.get("formatted_final_price")),
        "old_price": fmt_money(old, opt.get("formatted_original_price")),
        "ends": min(ends) if ends else None,
        "reviews": count,
        "positive": positive,
    }


def query_steam_sales():
    # Сортування за назвою дає стабільну пагінацію; знижки фільтруємо локально.
    all_deals = {}
    start = 0
    count = 500
    for _page in range(40):
        query = {
            "query": {
                "start": start,
                "count": count,
                "sort": 1,
                "filters": {
                    "type_filters": {"include_games": True, "include_dlc": False},
                    "price_filters": {"min_discount_percent": MIN_DISCOUNT},
                },
            },
            "context": {"language": "english", "country_code": COUNTRY, "steam_realm": 1},
            "data_request": {
                "include_basic_info": True,
                "include_reviews": True,
                "include_all_purchase_options": True,
            },
        }
        payload = get_json(STEAM_QUERY_URL, {"input_json": json.dumps(query, separators=(",", ":"))})
        response = payload.get("response") or {}
        entries = response.get("store_items") or []
        metadata = response.get("metadata") or {}
        total = metadata.get("total_matching_records")
        if not isinstance(total, int):
            raise RuntimeError("Steam змінив структуру відповіді: немає total_matching_records")
        if not entries and (total > 0 or start == 0):
            raise RuntimeError("Steam повернув порожню сторінку: звіт не надсилатиметься")
        for item in entries:
            deal = parse_game(item)
            if deal:
                all_deals[deal["appid"]] = deal
        start += len(entries)
        if start >= total:
            break
    else:
        raise RuntimeError("Перевищено ліміт сторінок Steam; неповний список не надсилаємо")
    return list(all_deals.values())


def get_spore_price():
    """Окремий запит, тому SPORE видно навіть без знижки."""
    payload = get_json(STEAM_DETAILS_URL, {
        "appids": SPORE_APP_ID,
        "cc": COUNTRY.lower(),
        "l": "ukrainian",
        "filters": "price_overview",
    })
    item = payload.get(str(SPORE_APP_ID)) or {}
    overview = (item.get("data") or {}).get("price_overview") or {}
    if not item.get("success") or overview.get("currency") != "UAH":
        return {"available": False, "discount": None, "final": None}
    return {
        "available": True,
        "discount": int(overview.get("discount_percent") or 0),
        "final": int(overview.get("final")),
        "price": fmt_money(overview.get("final"), overview.get("final_formatted")),
        "old_price": fmt_money(overview.get("initial"), overview.get("initial_formatted")),
    }


def score(deal):
    return math.log1p(deal["reviews"]) * 20 + deal["positive"] * 0.2 + deal["discount"] * 0.5


def select_report_games(deals, previous):
    """Вибираємо добірку один раз на день, без автоматичної заміни видалених."""
    ranked = sorted(deals, key=score, reverse=True)
    new = [d for d in ranked if str(d["appid"]) not in previous] if previous else []
    return (new[:5] + [d for d in ranked if d not in new[:5]])[:MAX_DEALS]


def deal_flag(deal, previous):
    old = previous.get(str(deal["appid"])) if isinstance(previous, dict) else None
    if old is None and previous:
        return "🆕 "
    if isinstance(old, dict) and old.get("discount", 0) < deal["discount"]:
        return "🔥 "
    return ""


def spore_is_new_discount(spore, old_spore):
    return bool(
        spore.get("available") and spore.get("discount") and
        (not old_spore.get("available") or
         spore["discount"] > (old_spore.get("discount") or 0))
    )


def report_snapshot(deals, spore):
    """Порівняння лише опублікованих ігор, не тисяч записів Steam."""
    return {
        "games": {
            str(d["appid"]): [d["discount"], d["final"], d["original"], d.get("ends")]
            for d in deals
        },
        "spore": {
            "available": spore.get("available", False),
            "discount": spore.get("discount"),
            "final": spore.get("final"),
        },
    }


def save_state(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def checked_spore():
    try:
        return get_spore_price()
    except (RuntimeError, TypeError, ValueError) as exc:
        print(f"Попередження: SPORE зараз недоступний ({type(exc).__name__})")
        return {"available": False, "discount": None, "final": None}


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def discord_safe(text):
    # Контент Steam може містити Markdown або @-згадки.
    return re.sub(r"([\\`*_[\]~|])", r"\\\1", str(text)).replace("@", "＠")


def deal_line(deal, previous, forced_flag=None):
    flag = deal_flag(deal, previous) if forced_flag is None else forced_flag
    name = discord_safe(deal["name"])[:70]
    url = f"https://store.steampowered.com/app/{deal['appid']}/?cc=ua"
    end = ""
    if deal.get("ends"):
        day = datetime.fromtimestamp(deal["ends"], KYIV).strftime("%d.%m")
        end = f" · до {day}"
    return (
        f"{flag}**[{name}]({url})** — **−{deal['discount']}%** · "
        f"{deal['price']} (було {deal['old_price']}){end}"
    )


def make_payload(deals, spore, previous_state, now, selected_ids=None, flags=None,
                 spore_alert=None, report_date=None, total_deals=None):
    prev = previous_state.get("deals") or {}
    if selected_ids is None:
        picked = select_report_games(deals, prev)
    else:
        lookup = {d["appid"]: d for d in deals}
        picked = [lookup[appid] for appid in selected_ids if appid in lookup]

    if spore.get("available"):
        discount = spore["discount"]
        message = (f"**−{discount}%** · {spore['price']} (було {spore['old_price']})"
                   if discount else f"Зараз **без знижки** · {spore['price']}")
    else:
        message = "Не вдалося перевірити ціну в гривнях. Не припускаємо, що знижки немає."

    if spore_alert is None:
        spore_alert = spore_is_new_discount(spore, previous_state.get("spore") or {})
    if spore.get("available") and spore.get("discount") and spore_alert:
        message = "🔔 **Нова/краща знижка!**\n" + message

    embeds = [{
        "title": "⭐ SPORE — головний пріоритет",
        "url": "https://store.steampowered.com/app/17390/Spore/",
        "description": message,
        "color": 0x34B3A0,
        "thumbnail": {"url": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/17390/header.jpg"},
    }]

    date = report_date or now.strftime("%d.%m.%Y")
    available_count = len(deals) if total_deals is None else total_deals
    heading = f"🇺🇦 Український регіон · {date}\nІгор від −{MIN_DISCOUNT}%: **{available_count}**"
    if selected_ids is not None:
        heading += f"\n🕒 Оновлено: {now.strftime('%d.%m о %H:%M')} за Києвом"
    elif not prev:
        heading += "\nПерший запуск: позначки «нове» ще не визначаємо."
    if not picked:
        heading += "\nУсі пропозиції з цього списку завершилися. Нова добірка — о 08:00."

    current = []
    for game in picked:
        forced = (flags or {}).get(str(game["appid"]), "") if flags is not None else None
        line = deal_line(game, prev, forced_flag=forced)
        if len("\n".join(current + [line])) > 3500:
            embeds.append({
                "title": "🎮 Знижки Steam від 70%",
                "description": heading + "\n\n" + "\n".join(current),
                "color": 0x2563EB,
            })
            current, heading = [], "Продовження списку"
        current.append(line)
    embeds.append({
        "title": "🎮 Знижки Steam від 70%",
        "description": heading + ("\n\n" + "\n".join(current) if current else ""),
        "color": 0x2563EB,
        "footer": {"text": "Джерело: Steam Store · Ціни можуть змінюватися · Час: Київ"},
    })
    return {"username": "Steam Deals", "allowed_mentions": {"parse": []}, "embeds": embeds}


def validate_webhook(webhook):
    if not (webhook.startswith("https://discord.com/api/webhooks/") or
            webhook.startswith("https://discordapp.com/api/webhooks/")):
        raise RuntimeError("DISCORD_WEBHOOK_URL не схожий на Discord Webhook")


def webhook_message_url(webhook, message_id):
    if not str(message_id).isdigit():
        raise RuntimeError("Не вдалося визначити ID повідомлення Discord")
    return webhook.rstrip("/") + "/messages/" + str(message_id)


def webhook_request(url, payload=None, method="POST"):
    headers = {"Content-Type": "application/json", "User-Agent": HEADERS["User-Agent"]}
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=25) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord відповів HTTP {response.status}")
            return json.load(response) if response.status == 200 else None
    except HTTPError as exc:
        # Не друкуємо повний URL: він містить секретний токен Webhook.
        raise RuntimeError(f"Discord повернув HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Не вдалося підключитися до Discord") from None


def send_discord(webhook, payload):
    validate_webhook(webhook)
    result = webhook_request(webhook.rstrip("/") + "?wait=true", payload, "POST")
    message_id = (result or {}).get("id")
    if not message_id or not str(message_id).isdigit():
        raise RuntimeError("Discord не повернув ID повідомлення; не можна буде видаляти застарілі знижки")
    return str(message_id)


def edit_discord(webhook, message_id, payload):
    validate_webhook(webhook)
    webhook_request(webhook_message_url(webhook, message_id), payload, "PATCH")


def delete_discord(webhook, message_id):
    validate_webhook(webhook)
    webhook_request(webhook_message_url(webhook, message_id), method="DELETE")


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("Додай репозиторний секрет DISCORD_WEBHOOK_URL у GitHub Actions")
    now = datetime.now(KYIV)
    state = load_state()
    mode = os.environ.get("RUN_MODE", "daily").strip()

    if mode == "cleanup":
        report = state.get("report") or {}
        message_id = report.get("message_id")
        if not message_id:
            print("Ще немає повідомлення з ID для редагування. Запусти денний звіт.")
            return
        original_ids = report.get("active_appids", [])
        # Беремо тільки ігри, вже показані в цьому повідомленні: не замінюємо їх новими.
        live_deals = query_steam_sales() if original_ids else []
        lookup = {d["appid"]: d for d in live_deals}
        active_ids = [appid for appid in original_ids if appid in lookup]
        active_games = [lookup[appid] for appid in active_ids]
        spore = checked_spore()
        snapshot = report_snapshot(active_games, spore)

        if snapshot == report.get("snapshot"):
            print("У списку немає змін; Discord-повідомлення не редагуємо")
            return

        payload = make_payload(
            active_games, spore, state, now,
            selected_ids=active_ids,
            flags=report.get("flags") or {},
            spore_alert=report.get("spore_alert", False),
            report_date=report.get("date"),
            total_deals=len(live_deals),
        )
        edit_discord(webhook, message_id, payload)
        report["active_appids"] = active_ids
        report["snapshot"] = snapshot
        state["report"] = report
        save_state(state)
        print(f"Оновлено Discord-повідомлення: залишилось {len(active_ids)} активних ігор")
        return

    if mode != "daily":
        raise RuntimeError("RUN_MODE має бути daily або cleanup")
    if state.get("last_post_date") == now.strftime("%Y-%m-%d") and os.environ.get("FORCE_SEND") != "1":
        print("Сьогодні вже відправлено; повтор не потрібний")
        return

    print("Отримую поточні знижки Steam для країни UA...")
    deals = query_steam_sales()
    spore = checked_spore()
    prev = state.get("deals") or {}
    chosen = select_report_games(deals, prev)
    chosen_ids = [d["appid"] for d in chosen]
    flags = {str(d["appid"]): deal_flag(d, prev) for d in chosen}
    spore_alert = spore_is_new_discount(spore, state.get("spore") or {})
    payload = make_payload(deals, spore, state, now)
    message_id = send_discord(webhook, payload)

    # Після успішної публікації прибираємо вчорашній звіт,
    # щоб старі пропозиції не висіли в історії каналу.
    old_message_id = (state.get("report") or {}).get("message_id")
    if old_message_id and old_message_id != message_id:
        try:
            delete_discord(webhook, old_message_id)
        except RuntimeError:
            print("Попередження: не вдалося видалити попередній звіт Discord")

    new_state = {
        "last_post_date": now.strftime("%Y-%m-%d"),
        "spore": spore,
        "deals": {str(d["appid"]): {"discount": d["discount"], "final": d["final"]} for d in deals},
        "report": {
            "message_id": message_id,
            "date": now.strftime("%d.%m.%Y"),
            "active_appids": chosen_ids,
            "flags": flags,
            "spore_alert": spore_alert,
            "snapshot": report_snapshot(chosen, spore),
        },
    }
    save_state(new_state)
    print(f"Надіслано до Discord: {len(deals)} акцій, у звіті {len(chosen)} ігор + SPORE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        sys.exit(1)
