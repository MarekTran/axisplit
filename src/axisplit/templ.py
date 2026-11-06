from pathlib import Path

from fastapi.templating import Jinja2Templates

from .viewmodels import CURRENCIES, STATUS_LABELS, currency_sign, money, money_by_currency

TEMPLAGES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLAGES_DIR))
templates.env.filters["money"] = money  # cents | money(receipt.currency)
templates.env.filters["money_by_currency"] = money_by_currency
templates.env.filters["currency_sign"] = currency_sign
templates.env.globals["currencies"] = list(CURRENCIES)
templates.env.filters["status_label"] = lambda status: STATUS_LABELS.get(status, status)
templates.env.filters["receipt_date"] = lambda date: date.strftime("%d %b · %H:%M")
