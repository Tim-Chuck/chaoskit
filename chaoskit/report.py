import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(("html",)),
)


def write_report(directory, data):
    (directory / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "report.html").write_text(
        templates.get_template("report.html").render(**data), encoding="utf-8",
    )
