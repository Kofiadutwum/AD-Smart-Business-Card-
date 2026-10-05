"""Display currencies.

Customers are always charged in Ghana cedis. Every other currency is an
approximation from the newest stored exchange rate, shown large with the cedi
amount beneath (CUR-01..05). Dollars are the default; a visitor can pick another
currency and the choice is remembered in a cookie and, when signed in, on the
account.
"""

from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .utils import rate_value, to_usd

COOKIE_NAME = "adsmart_currency"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
DEFAULT = "USD"

# symbol: what goes in front of the number; decimals: places shown when the amount is not whole.
CURRENCIES = {
    "USD": {"symbol": "$", "decimals": 2, "name": "US dollar"},
    "GBP": {"symbol": "£", "decimals": 2, "name": "British pound"},
    "EUR": {"symbol": "€", "decimals": 2, "name": "Euro"},
    "CNY": {"symbol": "CN¥", "decimals": 2, "name": "Chinese yuan"},
    "NGN": {"symbol": "₦", "decimals": 0, "name": "Nigerian naira"},
    "XOF": {"symbol": "CFA ", "decimals": 0, "name": "West African CFA franc"},
    "GHS": {"symbol": "GHS ", "decimals": 2, "name": "Ghana cedi"},
}
# Currencies worked out from the dollar rate and the provider's other rates.
CONVERTED = ("GBP", "EUR", "CNY", "NGN", "XOF")

_chosen = ContextVar("adsmart_currency", default=DEFAULT)


def valid(code):
    code = (code or "").upper()
    return code if code in CURRENCIES else None


def activate(code):
    """Remember this request's currency (set by the middleware)."""
    _chosen.set(valid(code) or DEFAULT)


def current():
    return _chosen.get()


def choice_for(request):
    """The currency this visitor picked: staff pages are always dollars; else cookie, account, dollars."""
    if request.path.startswith("/staff"):
        return DEFAULT
    code = valid(request.COOKIES.get(COOKIE_NAME))
    if code is None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            code = valid(getattr(user, "currency", ""))
    return code or DEFAULT


def units_per_usd(today, code):
    """How many units of ``code`` one US dollar buys at ``today``'s rates; None when unknown."""
    if code == "USD":
        return Decimal(1)
    if code == "GHS":
        return rate_value(today)
    value = (getattr(today, "other_rates", None) or {}).get(code)
    try:
        number = Decimal(str(value)) if value else None
    except InvalidOperation:
        return None
    return number if number and number > 0 else None


def effective(code, today):
    """The chosen currency if its rate is known; dollars until it is."""
    code = valid(code) or DEFAULT
    if code in ("USD", "GHS") or units_per_usd(today, code):
        return code
    return DEFAULT


def unit_rate(code, today):
    """Ghana cedis for one unit of ``code`` (what the price calculators divide by); None for cedis."""
    ghs_per_usd = rate_value(today)
    if code == "GHS" or not ghs_per_usd:
        return None
    per_usd = units_per_usd(today, code)
    return ghs_per_usd / per_usd if per_usd else None


def format_amount(value, code):
    """'£8.12', '$9', '₦13,500': whole amounts without decimals, otherwise the currency's own places."""
    info = CURRENCIES[code]
    places = info["decimals"]
    value = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole = places == 0 or value == value.to_integral()
    text = f"{value:,.0f}" if whole else f"{value:,.2f}"
    return f"{info['symbol']}{text}"


def local_text(minor, rate, today, code=None):
    """A cedi amount (pesewas) in the chosen currency, or '' when it cannot be worked out.

    ``rate`` is the dollar rate for this amount (today's, or a payment's own, so old
    payments keep their dollar value); ``today`` supplies the other currencies' rates.
    Cedis are never converted: '' tells the caller to show the cedis alone.
    """
    code = effective(code or current(), today)
    if code == "GHS":
        return ""
    usd = to_usd(minor, rate)
    if usd is None:
        return ""
    per_usd = units_per_usd(today, code)
    if per_usd is None:
        return ""
    return format_amount(usd * per_usd, code)


def config_for(code, today):
    """What the price calculators in the browser need."""
    rate = unit_rate(code, today)
    info = CURRENCIES[code]
    return {
        "code": code, "symbol": info["symbol"], "decimals": info["decimals"],
        "rate": str(rate.quantize(Decimal("0.000001"))) if rate else None,
    }


def format_money_local(minor, today):
    """'£8.12 (GHS 150.00)' for button labels and form choices, in the visitor's currency;
    the cedi amount alone when there is no rate or the visitor chose cedis."""
    from .utils import format_ghs

    text = local_text(minor, today, today)
    ghs = format_ghs(minor)
    return f"{text} ({ghs})" if text else ghs
