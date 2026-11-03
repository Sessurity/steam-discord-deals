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
PREFERRED_MIN_REVIEWS = 5000  # Популярні ігри — пріоритет, решта заповнює вільні місця.
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



# Ціни RU беруться окремо з російського Steam Store, НЕ конвертуються з гривень.
# Steam може не повернути дані або повернути іншу валюту: такі ціни пропускаємо.
REGIONAL_COUNTRY = "ru"


def fmt_rub(cents, formatted=None):
    if isinstance(formatted, str) and re.search(r"₽|руб\.?|\bRUB\b", formatted, re.I):
        return discord_safe(formatted.strip()[:48])
    if not isinstance(cents, int) or cents < 0:
        return None
    whole, kopecks = divmod(cents, 100)
    if kopecks:
        return f"{whole:,}".replace(",", " ") + f",{kopecks:02d} ₽"
    return f"{whole:,}".replace(",", " ") + " ₽"


def parse_ru_price(item):
    """Приймаємо лише фактичну ціну Steam у RUB, не розраховану за FX."""
    if not isinstance(item, dict) or item.get("success") is not True:
        return None
    raw = (item.get("data") or {}).get("price_overview") or {}
    if not isinstance(raw, dict) or raw.get("currency") != "RUB":
        return None
    try:
        final = int(raw["final"])
        original = int(raw["initial"])
        discount = int(raw.get("discount_percent") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if final < 0 or original < final or not 0 <= discount <= 100:
        return None
    return {
        "final": final,
        "original": original,
        "discount": discount,
        "price": fmt_rub(final, raw.get("final_formatted")),
        "old_price": fmt_rub(original, raw.get("initial_formatted")),
    }


def fetch_ru_prices(appids):
    """Одна перевірка регіону RU на групу до 10 ігор.

    За відсутності відповіді не вигадуємо регіональну ціну і не блокуємо звіт.
    """
    ids = list(dict.fromkeys(int(a) for a in appids if isinstance(a, int) and a > 0))[:40]
    prices = {}
    for offset in range(0, len(ids), 10):
        chunk = ids[offset:offset + 10]
        try:
            payload = get_json(STEAM_DETAILS_URL, {
                "appids": ",".join(str(a) for a in chunk),
                "cc": REGIONAL_COUNTRY,
                "l": "english",
                "filters": "price_overview",
            }, retries=2)
        except RuntimeError:
            print(f"Steam RU ціни: група {offset // 10 + 1} недоступна")
            continue
        if not isinstance(payload, dict):
            continue
        for appid in chunk:
            detail = parse_ru_price(payload.get(str(appid)))
            if detail is not None:
                prices[str(appid)] = detail
    return prices


def ru_line(info):
    """Показувати окрему знижку RU, якщо відсоток відрізняється від UA."""
    if not info:
        return "🇷🇺 Ціну Steam RU не отримано"
    if info["discount"] and info["original"] > info["final"]:
        return (f"🇷🇺 **−{info['discount']}%**  "
                f"~~{info['old_price']}~~ → **{info['price']}**")
    return f"🇷🇺 Без знижки · **{info['price']}**"


def score(deal):
    return math.log1p(deal["reviews"]) * 20 + deal["positive"] * 0.2 + deal["discount"] * 0.5


def deal_sort_key(deal):
    """Найвищий відсоток першим; за рівної знижки — відгуки/популярність."""
    return (-deal["discount"], -score(deal), deal["name"].casefold(), deal["appid"])


def select_popular_deals(deals, limit=MAX_DEALS):
    """Спершу відбір відомих ігор, потім сортування готової добірки за знижкою.

    Кількість Steam-відгуків — лише індикатор популярності, а не ознака AAA.
    Якщо достатньо ігор із >=5000 відгуків, маловідомі не витіснять їх через −99%.
    Якщо таких ігор недостатньо, заповнюємо решту місць іншими пропозиціями.
    """
    if limit <= 0:
        return []
    # Команди можуть передавати generator; матеріалізуємо один раз,
    # інакше другий прохід втратить усі менш відомі ігри.
    deals = list(deals)
    popular = [d for d in deals if d.get("reviews", 0) >= PREFERRED_MIN_REVIEWS]
    less_known = [d for d in deals if d.get("reviews", 0) < PREFERRED_MIN_REVIEWS]

    def popularity_order(deal):
        return (-score(deal), -deal.get("reviews", 0),
                -deal["discount"], deal["name"].casefold(), deal["appid"])

    ranked = sorted(popular, key=popularity_order) + sorted(less_known, key=popularity_order)
    return sorted(ranked[:limit], key=deal_sort_key)


def select_report_games(deals, previous):
    """Щоденна добірка — 30 популярніших ігор; відображення за розміром знижки."""
    return select_popular_deals(deals, MAX_DEALS)


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


def report_snapshot(deals, spore, ru_prices=None):
    """Порівнюємо тільки опубліковані ігри та їхні окремі ціни RU."""
    result = {
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
    if ru_prices is not None:
        result["ru"] = {
            str(appid): [price["discount"], price["final"], price["original"]]
            for appid, price in ru_prices.items()
        }
    return result


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
        f"🟢 Перевірено: **{now.strftime('%d.%m.%Y %H:%M')}** (Київ)"
    )
    if not active_count:
        desc += "\n\nУсі акції з поточної добірки завершилися."
    return {
        "title": "⬡ ZHAXO  //  STEAM DEALS",
        "description": desc,
        "color": COLOR_TECH,
        "footer": {"text": "ZHAXO • Steam Market Scanner · Актуальність цін перевіряє Steam"},
    }


def spore_embed(spore, is_new, ru_price=None):
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
        "description": desc + "\n" + ru_line(ru_price) + "\n[Відкрити сторінку Steam](https://store.steampowered.com/app/17390/Spore/)",
        "color": COLOR_PRIORITY,
        "thumbnail": {"url": STEAM_HEADER.format(SPORE_APP_ID)},
    }


def game_embed(deal, position, flag="", ru_price=None):
    appid = deal["appid"]
    link = f"https://store.steampowered.com/app/{appid}/?cc=ua"
    name = discord_safe(deal["name"])[:180]
    text = f"🟩 **−{deal['discount']}%**   ~~{deal['old_price']}~~  →  **{deal['price']}**"
    if deal.get("ends"):
        text += "\n⏳ Акція до " + datetime.fromtimestamp(deal["ends"], KYIV).strftime("%d.%m.%Y %H:%M")
    return {
        "title": f"{position:02d} / {flag}{name}",
        "url": link,
        "description": text + "\n" + ru_line(ru_price) + "\n[Steam Store ↗](" + link + ")",
        "color": COLOR_TECH,
        "thumbnail": {"url": STEAM_HEADER.format(appid)},
    }


def make_payload_pages(groups, deals, spore, previous_state, now, flags=None,
                       spore_alert=None, report_date=None, initial_count=None,
                       ru_prices=None):
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
    ru_prices = ru_prices or {}
    pages = []
    position = 0
    for group_index, group in enumerate(groups):
        embeds = []
        if group_index == 0:
            embeds.extend([
                heading_embed(now, report_date, active_count, initial_count),
                spore_embed(spore, spore_alert, ru_prices.get(str(SPORE_APP_ID))),
            ])
        for appid in group:
            if appid not in lookup:
                continue
            position += 1
            embeds.append(game_embed(lookup[appid], position, flags.get(str(appid), ""),
                                     ru_prices.get(str(appid))))
        if group_index > 0 and not embeds:
            # Порожнє додаткове повідомлення повністю видаляється при cleanup.
            pages.append(None)
            continue
        if len(embeds) > DISCORD_EMBEDS_LIMIT:
            raise RuntimeError("Забагато Discord embeds в одному повідомленні")
        pages.append({
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


# ZHAXO — єдиний автор нових звітів і відповідей на команди.
# Старий Webhook потрібен виключно для очищення історичних повідомлень.
DISCORD_BOT_API = "https://discord.com/api/v10"


def bot_request(token, channel, method, payload=None, message_id=None):
    if not token or not str(channel).isdigit():
        raise RuntimeError("Налаштуй DISCORD_BOT_TOKEN та DISCORD_CHANNEL_ID")
    url = f"{DISCORD_BOT_API}/channels/{channel}/messages"
    if message_id is not None:
        if not str(message_id).isdigit():
            raise RuntimeError("Некоректний ID повідомлення Discord")
        url += "/" + str(message_id)
    headers = {"Authorization": "Bot " + token, "User-Agent": "ZHAXO-Steam/2.0",
               "Accept": "application/json"}
    data = None
    if payload is not None:
        clean = {k: v for k, v in payload.items() if k not in ("username", "avatar_url")}
        clean["allowed_mentions"] = {"parse": []}
        data = json.dumps(clean, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=25) as res:
            if res.status == 204:
                return None
            if res.status not in (200, 201):
                raise RuntimeError(f"Discord Bot API HTTP {res.status}")
            return json.load(res)
    except HTTPError as exc:
        raise RuntimeError(f"Discord Bot API HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Не вдалося підключитися до Discord Bot API") from None


def bot_send(token, channel, payload):
    result = bot_request(token, channel, "POST", payload)
    message_id = (result or {}).get("id")
    if not message_id or not str(message_id).isdigit():
        raise RuntimeError("ZHAXO не повернув ID повідомлення")
    return str(message_id)


def bot_edit(token, channel, message_id, payload):
    bot_request(token, channel, "PATCH", payload, message_id)


def bot_delete(token, channel, message_id, ignore_missing=False):
    try:
        bot_request(token, channel, "DELETE", message_id=message_id)
    except RuntimeError as exc:
        if ignore_missing and str(exc) == "Discord Bot API HTTP 404":
            return
        raise


def report_transport(report):
    # Старі звіти не містять transport, отже належать Webhook.
    return "bot" if report.get("transport") == "bot" else "webhook"


def edit_report(report, message_id, payload, token, channel, webhook):
    if report_transport(report) == "bot":
        bot_edit(token, channel, message_id, payload)
    elif webhook:
        edit_discord(webhook, message_id, payload)
    else:
        raise RuntimeError("Старий звіт Webhook потребує DISCORD_WEBHOOK_URL")


def delete_report(transport, message_id, token, channel, webhook):
    if transport == "bot":
        bot_delete(token, channel, message_id, ignore_missing=True)
    elif webhook:
        delete_discord(webhook, message_id, ignore_missing=True)
    else:
        raise RuntimeError("Старі Webhook-картки неможливо видалити без Webhook URL")


def clear_pending(state, token, channel, webhook):
    """Якщо старі повідомлення не вдалося прибрати — повторимо пізніше."""
    pending = state.get("pending_message_batches") or []
    if not pending:
        return False
    keep = []
    for batch in pending:
        remaining = []
        for mid in batch.get("ids") or []:
            try:
                delete_report(batch.get("transport", "webhook"), mid, token, channel, webhook)
            except RuntimeError:
                remaining.append(str(mid))
                print(f"Попередження: старе повідомлення {mid} не вдалося видалити")
        if remaining:
            keep.append({"transport": batch.get("transport", "webhook"), "ids": remaining})
    if keep:
        state["pending_message_batches"] = keep
    else:
        state.pop("pending_message_batches", None)
    return keep != pending


def report_message_ids(report):
    """Міграція попередньої версії з одним Discord-повідомленням."""
    ids = report.get("message_ids")
    if isinstance(ids, list):
        return [str(value) for value in ids if str(value).isdigit()]
    old = report.get("message_id")
    return [str(old)] if old and str(old).isdigit() else []


def resolve_run_mode(requested_mode, state, now, scheduled=False):
    """Відновити пропущений денний звіт під час будь-якого наступного schedule."""
    if requested_mode == "cleanup" and scheduled and now.hour >= 8:
        old = state.get("report") or {}
        if state.get("last_post_date") != now.strftime("%Y-%m-%d"):
            return "daily"
        if report_message_ids(old) and report_transport(old) == "webhook":
            return "daily"
    return requested_mode


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not token or not channel.isdigit():
        raise RuntimeError("Потрібні DISCORD_BOT_TOKEN та DISCORD_CHANNEL_ID у GitHub Secrets")
    now = datetime.now(KYIV)
    state = load_state()
    if clear_pending(state, token, channel, webhook):
        save_state(state)
    requested_mode = os.environ.get("RUN_MODE", "daily").strip()
    mode = resolve_run_mode(
        requested_mode, state, now,
        scheduled=os.environ.get("SCHEDULED_RUN") == "1",
    )
    if requested_mode == "cleanup" and mode == "daily":
        print("Потрібен новий звіт або перехід із Webhook — публікує ZHAXO.")

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
        ru_prices = fetch_ru_prices([SPORE_APP_ID] + tracked)
        snapshot = report_snapshot(active, spore, ru_prices)
        unchanged = snapshot == report.get("snapshot")

        pages = make_payload_pages(
            active_groups, active, spore, state, now,
            flags=report.get("flags") or {},
            spore_alert=report.get("spore_alert", False),
            report_date=report.get("date"),
            initial_count=report.get("initial_count", len(tracked)),
            ru_prices=ru_prices,
        )
        if unchanged:
            # Steam перевірено — оновлюємо лише шапку, не чіпаючи ігрові картки.
            edit_report(report, ids[0], pages[0], token, channel, webhook)
            report["last_checked_at"] = now.isoformat()
            state["report"] = report
            save_state(state)
            print(f"Steam перевірено о {now.strftime('%H:%M')} (Київ): змін немає")
            return

        # Спочатку редагуємо картки, потім видаляємо порожні повідомлення.
        for message_id, page in zip(ids, pages):
            if page is not None:
                edit_report(report, message_id, page, token, channel, webhook)
        for message_id, page in zip(ids, pages):
            if page is None:
                delete_report(report_transport(report), message_id, token, channel, webhook)

        report["groups"] = [group for page, group in zip(pages, active_groups) if page is not None]
        report["message_ids"] = [message_id for message_id, page in zip(ids, pages) if page is not None]
        report["snapshot"] = snapshot
        report["last_checked_at"] = now.isoformat()
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
    ru_prices = fetch_ru_prices([SPORE_APP_ID] + [d["appid"] for d in chosen])
    groups = chunk_game_ids(chosen)
    flags = {str(d["appid"]): deal_flag(d, prev) for d in chosen}
    spore_alert = spore_is_new_discount(spore, state.get("spore") or {})
    pages = make_payload_pages(
        groups, chosen, spore, state, now,
        flags=flags, spore_alert=spore_alert, initial_count=len(chosen),
        ru_prices=ru_prices,
    )

    new_ids = []
    try:
        for page in pages:
            if page is not None:
                new_ids.append(bot_send(token, channel, page))
    except Exception:
        # Невдалий випуск: прибираємо частково надіслані власні повідомлення.
        for message_id in new_ids:
            try:
                bot_delete(token, channel, message_id, ignore_missing=True)
            except RuntimeError:
                pass
        raise

    old_report = state.get("report") or {}
    previous_ids = report_message_ids(old_report)
    pending = list(state.get("pending_message_batches") or [])
    if previous_ids:
        pending.append({"transport": report_transport(old_report), "ids": previous_ids})

    new_state = {
        "last_post_date": now.strftime("%Y-%m-%d"),
        "spore": spore,
        "deals": {str(d["appid"]): {"discount": d["discount"], "final": d["final"]} for d in deals},
        "report": {
            "transport": "bot",
            "message_ids": new_ids,
            "groups": groups,
            "date": now.strftime("%d.%m.%Y"),
            "last_checked_at": now.isoformat(),
            "initial_count": len(chosen),
            "flags": flags,
            "spore_alert": spore_alert,
            "snapshot": report_snapshot(chosen, spore, ru_prices),
        },
    }
    if pending:
        new_state["pending_message_batches"] = pending
    save_state(new_state)

    # Видаляємо старі картки вже після збереження нових ID.
    if clear_pending(new_state, token, channel, webhook):
        save_state(new_state)
    print(f"ZHAXO: {len(chosen)} ігор, {len(new_ids)} повідомлень + SPORE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        sys.exit(1)
