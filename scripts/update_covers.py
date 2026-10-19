#!/usr/bin/env python3
"""Match catalog entries to box art and self-host it.

Older systems: lists each system's Named_Boxarts folder from the
libretro-thumbnails GitHub repos, picks the best title match (USA releases
first), downloads it, and saves a 240px JPEG in covers/.

Switch and Switch 2: libretro has no Switch art, so the script looks the game
up in blawar/titledb (the eShop catalog mirrored on GitHub) and downloads the
official eShop key art from Nintendo's CDN as a square JPEG (covers/<id>.sq.jpg).
If the CDN can't be reached, covers.js keeps the CDN address so the browser
loads it directly instead.

Writes covers.js. Rerun after editing the catalog. Games without art fall back
to a Wikipedia image at runtime, then to a placeholder.

Requires: git, Pillow (pip install pillow)
"""
import io
import os
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COVERS = ROOT / "covers"
WIDTH = 240

# Titles that differ from the libretro naming, keyed by game id.
OVERRIDES = {
    "ff-snes-final-fantasy-ii-na": "Final Fantasy II (USA)",
    "ff-snes-final-fantasy-iii-na": "Final Fantasy III (USA)",
    "ff-gb-the-final-fantasy-legend-sunsoft-na": "Final Fantasy Legend, The (USA) (Sunsoft)",
    "ff-gb-final-fantasy-legend-ii-sunsoft-na": "Final Fantasy Legend II (USA) (Sunsoft)",
    "ff-gb-final-fantasy-legend-iii-sunsoft-na": "Final Fantasy Legend III (USA) (Sunsoft)",
    "metroid-gc-metroid-prime-2-echoes-bonus-disc-na": "Metroid Prime 2 - Echoes (USA) (Bonus Disc)",
    "ff-ps1-final-fantasy-anthology-na": "Final Fantasy Anthology - Final Fantasy V (USA)",
    "ff-ps1-final-fantasy-chronicles-na": "Final Fantasy Chronicles - Final Fantasy IV (USA)",
}
# Bad automatic matches to skip (they fall back to Wikipedia art).
NOMATCH = {
    "hal-3ds-boxboxboy-na",
    "ff-ps4-final-fantasy-xv-na",
}
# Variants share art with their base release.
SHARE = {
    "metroid-3ds-metroid-samus-returns-legacy-edition-na": "Metroid - Samus Returns",
    "ff-3ds-theatrhythm-final-fantasy-curtain-call-limited-edition-na": "Theatrhythm Final Fantasy - Curtain Call",
}

# eShop names (from titledb) for Switch and Switch 2 games, keyed by game id.
# Names are compared with trademark symbols and punctuation stripped.
ESHOP = {
    "metroid-switch-metroid-dread-na": "Metroid Dread",
    "metroid-switch-metroid-dread-special-edition-na": "Metroid Dread",
    "metroid-switch-metroid-prime-remastered-na": "Metroid Prime Remastered",
    "metroid-switch-metroid-prime-4-beyond-na": "Metroid Prime 4: Beyond",
    "metroid-switch2-metroid-prime-4-beyond-nintendo-switch-2-edition-na":
        "Metroid Prime 4: Beyond – Nintendo Switch 2 Edition",
    "hal-switch-kirby-star-allies-na": "Kirby Star Allies",
    "hal-switch-boxboy-boxgirl-na": "BOXBOY! + BOXGIRL!",
    "hal-switch-kirby-fighters-2-na": "Kirby Fighters 2",
    "hal-switch-kirby-and-the-forgotten-land-na": "Kirby and the Forgotten Land",
    "hal-switch-kirby-s-dream-buffet-na": "Kirby’s Dream Buffet",
    "hal-switch-kirby-s-return-to-dream-land-deluxe-na": "Kirby’s Return to Dream Land Deluxe",
    "hal-switch2-kirby-and-the-forgotten-land-nintendo-switch-2-edition-star-crossed-world-na":
        "Kirby and the Forgotten Land – Nintendo Switch 2 Edition + Star-Crossed World",
    "hal-switch2-kirby-air-riders-na": "Kirby Air Riders",
    "ff-switch-world-of-final-fantasy-maxima-na": "WORLD OF FINAL FANTASY MAXIMA",
    "ff-switch-final-fantasy-x-x-2-hd-remaster-na": "FINAL FANTASY X/X-2 HD Remaster",
    "ff-switch-final-fantasy-xii-the-zodiac-age-na": "FINAL FANTASY XII THE ZODIAC AGE",
    "ff-switch-chocobo-s-mystery-dungeon-every-buddy-na": "Chocobo's Mystery Dungeon EVERY BUDDY!",
    "ff-switch-collection-of-mana-na": "Collection of Mana",
    "ff-switch-final-fantasy-crystal-chronicles-remastered-edition-jp":
        "FINAL FANTASY CRYSTAL CHRONICLES Remastered Edition",
    "ff-switch-final-fantasy-ix-as": "FINAL FANTASY IX",
    "ff-switch-final-fantasy-ix-na": "FINAL FANTASY IX",
    "ff-switch-chocobo-gp-na": "Chocobo GP",
    "ff-switch-crisis-core-final-fantasy-vii-reunion-na": "CRISIS CORE –FINAL FANTASY VII– REUNION",
    "ff-switch-theatrhythm-final-bar-line-na": "THEATRHYTHM FINAL BAR LINE",
    "ff-switch-final-fantasy-tactics-the-ivalice-chronicles-na": "FINAL FANTASY TACTICS - The Ivalice Chronicles",
    "ff-switch-final-fantasy-resonance-na": "FINAL FANTASY RESONANCE",
    "ff-switch2-final-fantasy-resonance-na": "FINAL FANTASY RESONANCE",
    "ff-switch2-final-fantasy-vii-remake-intergrade-na": "FINAL FANTASY VII REMAKE INTERGRADE",
    "ff-switch2-final-fantasy-vii-rebirth-na": "FINAL FANTASY VII REBIRTH",
    "ff-switch2-final-fantasy-xiv-online-na": "FINAL FANTASY XIV Online",
    "ff-switch2-final-fantasy-vii-revelation-na": "FINAL FANTASY VII REVELATION",
    "metroid-switch2-metroid-ravenous-na": "Metroid Ravenous",
}
TITLEDB = ["US.en.json", "GB.en.json", "JP.ja.json"]

# Wikipedia article names for the last-resort lookup; None skips it.
WIKI = {
    "ff-switch2-final-fantasy-vii-remake-intergrade-na": "Final Fantasy VII Remake",
    "ff-switch2-final-fantasy-xiv-online-na": "Final Fantasy XIV",
    "ff-ps3-final-fantasy-xiv-online-a-realm-reborn-na": "Final Fantasy XIV",
    "ff-switch-final-fantasy-i-vi-pixel-remaster-collection-na": "Final Fantasy Pixel Remaster",
    "ff-switch-final-fantasy-vii-and-final-fantasy-viii-remastered-twin-pack-na": None,
    "ff-switch-final-fantasy-vii-and-final-fantasy-viii-remastered-twin-pack-as": None,
    "hal-3ds-boxboxboy-na": None,
    "hal-3ds-bye-bye-boxboy-na": None,
}


def wiki_title(g):
    if g["id"] in WIKI:
        return WIKI[g["id"]]
    t = re.sub(r" \(Sunsoft\)$", "", g["title"])
    t = re.sub(r" Nintendo Switch 2 Edition.*$", "", t)
    return re.sub(r":? (Special|Legacy|Limited) Edition$", "", t)


def wiki_image(g):
    """Lead image of the game's Wikipedia article, including non-free cover art."""
    title = wiki_title(g)
    if not title:
        return None
    q = urllib.parse.urlencode({"action": "query", "format": "json", "redirects": 1, "prop": "pageimages",
                                "pilicense": "any", "pithumbsize": 480, "titles": title})
    j = json.loads(fetch("https://en.wikipedia.org/w/api.php?" + q))
    for page in j.get("query", {}).get("pages", {}).values():
        if page.get("thumbnail"):
            return page["thumbnail"]["source"]
    return None


def save_cover(data, out, square):
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    size = (WIDTH, WIDTH) if square else (WIDTH, round(img.height * WIDTH / img.width))
    img.resize(size, Image.LANCZOS).save(out, "JPEG", quality=84, optimize=True)


def try_wiki(g):
    """Download a Wikipedia cover for g into covers/; return its path or None."""
    out = COVERS / f"{g['id']}.jpg"
    if out.exists():
        return f"covers/{out.name}"
    try:
        src = wiki_image(g)
        if not src:
            return None
        save_cover(fetch(src), out, square=False)
        return f"covers/{out.name}"
    except Exception as e:  # noqa: BLE001
        print(f"  {g['title']}: Wikipedia lookup failed ({e})", file=sys.stderr)
        return None


def eshop_norm(s):
    s = re.sub(r"[™®©]", "", s or "")
    s = unicodedata.normalize("NFKD", s.lower()).replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_titledb(work):
    """Map normalized eShop name -> icon URL, US first, then UK and Japan."""
    index = {}
    local = os.environ.get("TITLEDB_DIR")
    for name in TITLEDB:
        path = Path(local) / name if local else Path(work) / name
        if not path.exists():
            url = f"https://raw.githubusercontent.com/blawar/titledb/master/{name}"
            print(f"downloading titledb {name}", file=sys.stderr)
            try:
                urllib.request.urlretrieve(url, path)
            except Exception as e:  # noqa: BLE001
                print(f"  titledb {name} failed: {e}", file=sys.stderr)
                continue
        for v in json.loads(path.read_text(encoding="utf-8")).values():
            if v.get("name") and v.get("iconUrl"):
                index.setdefault(eshop_norm(v["name"]), v["iconUrl"])
    return index


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 nintendo-collection-covers"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def slug(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_catalog():
    src = (ROOT / "games.js").read_text(encoding="utf-8")
    systems_js = re.search(r"window\.SYSTEMS = (\{.*?\n\});", src, re.S).group(1)
    systems = {}
    for m in re.finditer(r'"?([a-z0-9]+)"?: \{ name: "[^"]+", order: \d+, libretro: (null|"[^"]+")', systems_js):
        systems[m.group(1)] = None if m.group(2) == "null" else m.group(2).strip('"')
    games = []
    for m in re.finditer(r'\["(\w+)", "(\w+)", "((?:[^"\\]|\\.)*)", (\d+), "(\w+)", "((?:[^"\\]|\\.)*)"\]', src):
        st, sy, title, year, region, note = m.groups()
        gid = "-".join([st, sy, slug(title), region.lower()])
        games.append(dict(id=gid, set=st, sys=sy, title=title, region=region, note=note))
    return systems, games


def norm(t):
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = t.replace("_", " ").replace(" - ", " ").replace(":", " ")
    t = re.sub(r"^the |, the\b", " ", t.lower())
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def list_repo(repo, workdir):
    dest = Path(workdir) / repo
    if not dest.exists():
        subprocess.run(
            ["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth", "1",
             f"https://github.com/libretro-thumbnails/{repo}.git", str(dest)],
            check=True, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"})
    out = subprocess.run(["git", "-C", str(dest), "ls-tree", "HEAD", "Named_Boxarts/"],
                         capture_output=True, text=True, check=True).stdout
    entries = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        mode, _, sha = meta.split()
        name = path.split("/", 1)[1]
        if not name.lower().endswith(".png"):
            continue
        if mode == "120000":  # symlink: resolve to the real file name
            target = subprocess.run(["git", "-C", str(dest), "cat-file", "-p", sha],
                                    capture_output=True, text=True).stdout.strip()
            name_real = target.split("/")[-1]
        else:
            name_real = name
        entries[name[:-4]] = name_real
    return entries


def best_match(title, names, override=None):
    if override and override in names:
        return override
    want = norm(title)
    best, best_score = None, 0.0
    for n in names:
        score = SequenceMatcher(None, want, norm(n)).ratio()
        if norm(n) == want:
            score += 0.5
        if "(USA" in n:
            score += 0.08
        elif "(World" in n:
            score += 0.05
        if "(Japan)" in n:
            score -= 0.05
        if re.search(r"\((Beta|Proto|Demo|Sample|Kiosk)", n):
            score -= 0.3
        if "Bonus Disc" in n:
            score -= 0.3
        if re.search(r"Virtual Console|Switch Online|Classic Mini|Wii U|Retro-Bit|Collection", n):
            score -= 0.04
        if score > best_score:
            best, best_score = n, score
    return best if best_score >= 0.86 else None


def download(repo, real_name):
    url = (f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/"
           + urllib.parse.quote(real_name))
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def main():
    from PIL import Image

    systems, games = load_catalog()
    COVERS.mkdir(exist_ok=True)
    mapping, misses = {}, []
    with tempfile.TemporaryDirectory() as work:
        cache = {}
        for g in games:
            repo = systems.get(g["sys"])
            if g["id"] in ESHOP or not repo or g["id"] in NOMATCH:
                misses.append(g)
                continue
            if repo not in cache:
                print(f"listing {repo}", file=sys.stderr)
                try:
                    cache[repo] = list_repo(repo, work)
                except subprocess.CalledProcessError:
                    print(f"  no repo for {repo}", file=sys.stderr)
                    cache[repo] = {}
            names = cache[repo]
            title = SHARE.get(g["id"], g["title"])
            hit = best_match(title, names, OVERRIDES.get(g["id"]))
            if not hit:
                misses.append(g)
                continue
            out = COVERS / f"{g['id']}.jpg"
            if not out.exists():
                try:
                    img = Image.open(io.BytesIO(download(repo, names[hit]))).convert("RGB")
                    h = round(img.height * WIDTH / img.width)
                    img.resize((WIDTH, h), Image.LANCZOS).save(out, "JPEG", quality=82, optimize=True)
                except Exception as e:  # noqa: BLE001
                    print(f"  failed {g['title']}: {e}", file=sys.stderr)
                    misses.append(g)
                    continue
            mapping[g["id"]] = f"covers/{g['id']}.jpg"
            print(f"  {g['title']}  <-  {hit}", file=sys.stderr)

        # Switch and Switch 2: official eShop key art.
        eshop_games = [g for g in misses if g["id"] in ESHOP]
        if eshop_games:
            index = load_titledb(work)
            for g in eshop_games:
                icon = index.get(eshop_norm(ESHOP[g["id"]]))
                if not icon:
                    continue
                out = COVERS / f"{g['id']}.sq.jpg"
                if not out.exists():
                    try:
                        save_cover(fetch(icon), out, square=True)
                    except Exception as e:  # noqa: BLE001
                        print(f"  {g['title']}: eShop download failed ({e})", file=sys.stderr)
                        mapping[g["id"]] = try_wiki(g) or icon
                        misses.remove(g)
                        continue
                mapping[g["id"]] = f"covers/{g['id']}.sq.jpg"
                misses.remove(g)
                print(f"  {g['title']}  <-  eShop: {ESHOP[g['id']]}", file=sys.stderr)

        # Last resort for anything still missing: Wikipedia's cover art.
        for g in list(misses):
            path = try_wiki(g)
            if path:
                mapping[g["id"]] = path
                misses.remove(g)
                print(f"  {g['title']}  <-  Wikipedia: {wiki_title(g)}", file=sys.stderr)

    (ROOT / "covers.js").write_text(
        "// Generated by scripts/update_covers.py\nwindow.COVERS = "
        + json.dumps(mapping, indent=1, sort_keys=True) + ";\n", encoding="utf-8")
    remote = sum(1 for v in mapping.values() if v.startswith("http"))
    print(f"\n{len(mapping)} matched ({remote} loading from Nintendo's CDN), "
          f"{len(misses)} without art:", file=sys.stderr)
    for g in misses:
        print(f"  [{g['sys']}] {g['title']}", file=sys.stderr)


if __name__ == "__main__":
    main()
