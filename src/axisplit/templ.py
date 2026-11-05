from pathlib import Path

from fastapi.templating import Jinja2Templates

from .viewmodels import STATUS_LABELS

TEMPLAGES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLAGES_DIR))
templates.env.filters["eur"] = lambda cents: f"{cents // 100},{cents % 100:02d} €"
templates.env.filters["status_label"] = lambda status: STATUS_LABELS.get(status, status)
templates.env.filters["receipt_date"] = lambda date: date.strftime("%d %b · %H:%M")
