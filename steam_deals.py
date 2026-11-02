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
MAX_DEALS = 30
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


# Discord: максимум 10 embeds у повідомленні, тому до 30 ігор
# розбиваємо на 8 + 10 + 10 + 2 (перше також містить шапку та SPORE).
DISCORD_EMBEDS_LIMIT = 10
FIRST_PAGE_GAMES = 8
NEXT_PAGE_GAMES = 10
COLOR_TECH = 0x39CFC6
COLOR_PRIORITY = 0xD6AF61
STEAM_HEADER = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{}/header.jpg"


def chunk_game_ids(chosen):
    ids = [d["appid"] for d in chosen]
    groups = [ids[:FIRST_PAGE_GAMES]]
    for offset in range(FIRST_PAGE_GAMES, len(ids), NEXT_PAGE_GAMES):
        groups.append(ids[offset:offset + NEXT_PAGE_GAMES])
    return groups


def heading_embed(now, report_date, active_count, initial_count):
    desc = (
        "**Щоденний огляд знижок**\n"
        f"🇺🇦 Україна · {report_date} · UAH\n\n"
        f"🔎 **Від −{MIN_DISCOUNT}%**   ·   🎮 **{active_count}/{initial_count} активних**\n"
        "🕗 Новий звіт: **08:00**   ·   🔄 Перевірка: **щогодини о :17**\n"
        f"🟢 Останнє оновлення: **{now.strftime('%H:%M')}** (Київ)"
    )
    if not active_count:
        desc += "\n\nУсі акції з поточної добірки завершилися."
    return {
        "title": "⬡ ZHAXO  //  STEAM DEALS",
        "description": desc,
        "color": COLOR_TECH,
        "footer": {"text": "ZHAXO • Steam Market Scanner · Актуальність цін перевіряє Steam"},
    }


def spore_embed(spore, is_new):
    if spore.get("available"):
        pct = spore["discount"]
        if pct:
            desc = (
                f"🟩 **−{pct}%**   ~~{spore['old_price']}~~  →  **{spore['price']}**"
            )
            if is_new:
                desc = "🔔 **Нова або покращена знижка!**\n" + desc
        else:
            desc = f"⏳ Очікування знижки · **{spore['price']}**"
    else:
        desc = "⚠️ Ціну SPORE в гривнях зараз перевірити не вдалося."
    return {
        "title": "★ PRIORITY TARGET  /  SPORE",
        "url": "https://store.steampowered.com/app/17390/Spore/",
        "description": desc + "\n[Відкрити сторінку Steam](https://store.steampowered.com/app/17390/Spore/)",
        "color": COLOR_PRIORITY,
        "thumbnail": {"url": STEAM_HEADER.format(SPORE_APP_ID)},
    }


def game_embed(deal, position, flag=""):
    appid = deal["appid"]
    link = f"https://store.steampowered.com/app/{appid}/?cc=ua"
    name = discord_safe(deal["name"])[:180]
    text = f"🟩 **−{deal['discount']}%**   ~~{deal['old_price']}~~  →  **{deal['price']}**"
    if deal.get("ends"):
        text += "\n⏳ Акція до " + datetime.fromtimestamp(deal["ends"], KYIV).strftime("%d.%m.%Y %H:%M")
    return {
        "title": f"{position:02d} / {flag}{name}",
        "url": link,
        "description": text + "\n[Steam Store ↗](" + link + ")",
        "color": COLOR_TECH,
        "thumbnail": {"url": STEAM_HEADER.format(appid)},
    }


def make_payload_pages(groups, deals, spore, previous_state, now, flags=None,
                       spore_alert=None, report_date=None, initial_count=None):
    """Одна повідомлення на групу; індекси груп зберігаються між перевірками."""
    if not groups:
        groups = [[]]
    lookup = {d["appid"]: d for d in deals}
    report_date = report_date or now.strftime("%d.%m.%Y")
    initial_count = (sum(len(group) for group in groups)
                     if initial_count is None else initial_count)
    active_count = sum(appid in lookup for group in groups for appid in group)
    if spore_alert is None:
        spore_alert = spore_is_new_discount(spore, previous_state.get("spore") or {})
    flags = flags or {}
    pages = []
    position = 0
    for group_index, group in enumerate(groups):
        embeds = []
        if group_index == 0:
            embeds.extend([
                heading_embed(now, report_date, active_count, initial_count),
                spore_embed(spore, spore_alert),
            ])
        for appid in group:
            if appid not in lookup:
                continue
            position += 1
            embeds.append(game_embed(lookup[appid], position, flags.get(str(appid), "")))
        if group_index > 0 and not embeds:
            # Порожнє додаткове повідомлення повністю видаляється при cleanup.
            pages.append(None)
            continue
        if len(embeds) > DISCORD_EMBEDS_LIMIT:
            raise RuntimeError("Забагато Discord embeds в одному повідомленні")
        pages.append({
            "username": "ZHAXO • Steam Deals",
            "allowed_mentions": {"parse": []},
            "embeds": embeds,
        })
    return pages


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


def delete_discord(webhook, message_id, ignore_missing=False):
    validate_webhook(webhook)
    try:
        webhook_request(webhook_message_url(webhook, message_id), method="DELETE")
    except RuntimeError as exc:
        if ignore_missing and str(exc) == "Discord повернув HTTP 404":
            return
        raise


def report_message_ids(report):
    """Міграція попередньої версії з одним Discord-повідомленням."""
    ids = report.get("message_ids")
    if isinstance(ids, list):
        return [str(value) for value in ids if str(value).isdigit()]
    old = report.get("message_id")
    return [str(old)] if old and str(old).isdigit() else []


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("Додай репозиторний секрет DISCORD_WEBHOOK_URL у GitHub Actions")
    now = datetime.now(KYIV)
    state = load_state()
    mode = os.environ.get("RUN_MODE", "daily").strip()

    if mode == "cleanup":
        report = state.get("report") or {}
        ids = report_message_ids(report)
        groups = report.get("groups") or []
        if not ids or not groups or len(ids) != len(groups):
            print("Немає нового багатокарткового звіту для редагування. Наступний денний запуск його створить.")
            return

        tracked = [appid for group in groups for appid in group]
        live = query_steam_sales() if tracked else []
        lookup = {d["appid"]: d for d in live}
        active_groups = [[appid for appid in group if appid in lookup] for group in groups]
        active = [lookup[appid] for group in active_groups for appid in group]
        spore = checked_spore()
        snapshot = report_snapshot(active, spore)
        if snapshot == report.get("snapshot"):
            print("Жодних змін у знижках; повідомлення не оновлюються")
            return

        pages = make_payload_pages(
            active_groups, active, spore, state, now,
            flags=report.get("flags") or {},
            spore_alert=report.get("spore_alert", False),
            report_date=report.get("date"),
            initial_count=report.get("initial_count", len(tracked)),
        )
        # Спочатку редагуємо картки, потім видаляємо порожні повідомлення.
        for message_id, page in zip(ids, pages):
            if page is not None:
                edit_discord(webhook, message_id, page)
        for message_id, page in zip(ids, pages):
            if page is None:
                delete_discord(webhook, message_id, ignore_missing=True)

        report["groups"] = [group for page, group in zip(pages, active_groups) if page is not None]
        report["message_ids"] = [message_id for message_id, page in zip(ids, pages) if page is not None]
        report["snapshot"] = snapshot
        report.pop("message_id", None)
        state["report"] = report
        save_state(state)
        print(f"Оновлено Discord: залишилось {len(active)} активних карток")
        return

    if mode != "daily":
        raise RuntimeError("RUN_MODE має бути daily або cleanup")
    if state.get("last_post_date") == now.strftime("%Y-%m-%d") and os.environ.get("FORCE_SEND") != "1":
        print("Сьогодні вже відправлено; повтор не потрібний")
        return

    print("Отримую знижки Steam для України...")
    deals = query_steam_sales()
    spore = checked_spore()
    prev = state.get("deals") or {}
    chosen = select_report_games(deals, prev)
    groups = chunk_game_ids(chosen)
    flags = {str(d["appid"]): deal_flag(d, prev) for d in chosen}
    spore_alert = spore_is_new_discount(spore, state.get("spore") or {})
    pages = make_payload_pages(
        groups, chosen, spore, state, now,
        flags=flags, spore_alert=spore_alert, initial_count=len(chosen),
    )

    new_ids = []
    try:
        for page in pages:
            if page is not None:
                new_ids.append(send_discord(webhook, page))
    except Exception:
        # Якщо друга або третя картка не надіслалась, прибираємо вже створені.
        for message_id in new_ids:
            try:
                delete_discord(webhook, message_id, ignore_missing=True)
            except RuntimeError:
                pass
        raise

    new_state = {
        "last_post_date": now.strftime("%Y-%m-%d"),
        "spore": spore,
        "deals": {str(d["appid"]): {"discount": d["discount"], "final": d["final"]} for d in deals},
        "report": {
            "message_ids": new_ids,
            "groups": groups,
            "date": now.strftime("%d.%m.%Y"),
            "initial_count": len(chosen),
            "flags": flags,
            "spore_alert": spore_alert,
            "snapshot": report_snapshot(chosen, spore),
        },
    }
    save_state(new_state)

    # Після збереження нових IDs можна безпечно прибрати старі повідомлення.
    old_ids = report_message_ids(state.get("report") or {})
    for message_id in old_ids:
        if message_id not in new_ids:
            try:
                delete_discord(webhook, message_id, ignore_missing=True)
            except RuntimeError:
                print("Попередження: не вдалося видалити попередню Discord-картку")
    print(f"Надіслано до Discord: {len(chosen)} ігор, {len(new_ids)} повідомлень + SPORE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        sys.exit(1)
