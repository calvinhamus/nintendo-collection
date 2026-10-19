#!/usr/bin/env python3
"""Pull average sale prices from the PriceCharting API into prices.json.

Usage:  PRICECHARTING_TOKEN=your_token python3 scripts/update_prices.py

Needs a PriceCharting API token. Games that match the wrong product can be
fixed with a search string in price-overrides.json, keyed by game id.
Digital-only and upcoming games are skipped.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://www.pricecharting.com/api/product"

CONSOLE = {
    "nes": "NES", "gb": "GameBoy", "snes": "Super Nintendo", "n64": "Nintendo 64",
    "gbc": "GameBoy Color", "gba": "GameBoy Advance", "gc": "Gamecube", "ds": "Nintendo DS",
    "wii": "Wii", "3ds": "Nintendo 3DS", "wiiu": "Wii U", "switch": "Nintendo Switch",
    "switch2": "Nintendo Switch 2", "ps1": "Playstation", "ps2": "Playstation 2",
    "ps3": "Playstation 3", "ps4": "Playstation 4", "ps5": "Playstation 5",
}
REGION_PREFIX = {"NA": "", "JP": "JP ", "AS": "Asian English "}


def slug(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def catalog():
    src = (ROOT / "games.js").read_text(encoding="utf-8")
    for m in re.finditer(r'\["(\w+)", "(\w+)", "((?:[^"\\]|\\.)*)", (\d+), "(\w+)", "((?:[^"\\]|\\.)*)"\]', src):
        st, sy, title, year, region, note = m.groups()
        yield dict(id="-".join([st, sy, slug(title), region.lower()]), sys=sy, title=title,
                   region=region, note=note)


def main():
    token = os.environ.get("PRICECHARTING_TOKEN")
    if not token:
        sys.exit("Set PRICECHARTING_TOKEN first.")
    overrides_path = ROOT / "price-overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    prices = {}
    for g in catalog():
        if re.match(r"(Digital only|Upcoming)", g["note"], re.I):
            continue
        q = overrides.get(g["id"]) or (
            REGION_PREFIX[g["region"]] + CONSOLE[g["sys"]] + " " + re.sub(r"\s*\(Sunsoft\)", "", g["title"]))
        url = API + "?" + urllib.parse.urlencode({"t": token, "q": q})
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                j = json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"  error  {g['title']}: {e}", file=sys.stderr)
            continue
        if j.get("status") != "success":
            print(f"  miss   {g['title']}  ({q})", file=sys.stderr)
            continue
        entry = {}
        for key, field in (("loose", "loose-price"), ("cib", "cib-price"), ("new", "new-price")):
            if j.get(field):
                entry[key] = round(j[field] / 100, 2)
        if entry:
            prices[g["id"]] = entry
            print(f"  ok     {g['title']}  ->  {j.get('product-name')} [{j.get('console-name')}]", file=sys.stderr)
        time.sleep(1.1)
    out = {"updated": time.strftime("%Y-%m-%d"), "prices": prices}
    (ROOT / "prices.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"\nWrote {len(prices)} prices.", file=sys.stderr)


if __name__ == "__main__":
    main()
