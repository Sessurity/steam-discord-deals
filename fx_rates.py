"""Approximate UAH → RUB currency conversion for ZHAXO Steam cards.

This is NOT a Steam Russia region price. The source publishes daily quotes.
Never replace official Steam UAH prices with a fabricated RUB value.
"""
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FX_URLS = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/uah.min.json",
    "https://latest.currency-api.pages.dev/v1/currencies/uah.min.json",
)
MAX_RATE_AGE_DAYS = 3


def verified_exchange(data, today=None):
    """Validate source date and rate; reject absurd, future and stale values."""
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(data, dict):
        return None
    try:
        rate = float(data["rate"])
        source_date = date.fromisoformat(data["date"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    age = (today - source_date).days
    if not math.isfinite(rate) or not 0.01 <= rate <= 1000:
        return None
    if not 0 <= age <= MAX_RATE_AGE_DAYS:
        return None
    return {"rate": rate, "date": source_date.isoformat()}


def parse_exchange_payload(data, today=None):
    if not isinstance(data, dict):
        return None
    quotes = data.get("uah") or {}
    if not isinstance(quotes, dict):
        return None
    return verified_exchange({"rate": quotes.get("rub"), "date": data.get("date")}, today=today)


def get_uah_rub_rate():
    """Try provider and mirror, return None without failing Discord reporting."""
    for url in FX_URLS:
        try:
            request = Request(url, headers={"User-Agent": "ZHAXO-Discord/2.0",
                                           "Accept": "application/json"})
            with urlopen(request, timeout=12) as response:
                data = json.load(response)
            valid = parse_exchange_payload(data)
            if valid is not None:
                return valid
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, OSError):
            continue
    print("Курс UAH/RUB недоступний або застарілий — рублі не показуємо.")
    return None


def rub_price(uah_cents, exchange):
    """Return an approximate RUB string for exact UAH kopecks, or None."""
    if not exchange or not isinstance(uah_cents, int) or uah_cents < 0:
        return None
    try:
        rate = Decimal(str(exchange["rate"]))
        if not rate.is_finite() or rate <= 0:
            return None
        rub = ((Decimal(uah_cents) / Decimal(100)) * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    pretty = f"{rub:,.2f}".replace(",", " ").replace(".", ",")
    return f"≈ {pretty} ₽"


def rub_detail(final_cents, old_cents, exchange):
    current = rub_price(final_cents, exchange)
    if current is None:
        return ""
    old = rub_price(old_cents, exchange)
    if old is not None and old_cents != final_cents:
        return f"\n💱 **{current}** (було {old})"
    return f"\n💱 **{current}**"


def rate_caption(exchange):
    if not exchange:
        return ""
    stamp = datetime.strptime(exchange["date"], "%Y-%m-%d").strftime("%d.%m.%Y")
    return f"💱 RUB — орієнтовний перерахунок, курс на {stamp}; не ціна Steam РФ"
