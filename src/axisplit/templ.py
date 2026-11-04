from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLAGES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLAGES_DIR))
templates.env.filters["eur"] = lambda cents: f"{cents // 100},{cents % 100:02d} €"
