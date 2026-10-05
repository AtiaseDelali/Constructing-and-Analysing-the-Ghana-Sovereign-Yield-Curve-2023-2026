"""
Download every GFIM (Ghana Fixed Income Market) daily trading report
for one or more years.

    python gfim_download.py                  # asks which years you want
    python gfim_download.py 2023-2026        # or pass them in, no prompt
    python gfim_download.py 2023 2025 2026
    python gfim_download.py 2026 --from 2026-01-01 --to 2026-06-30

In Colab or Jupyter just run the cell: it will ask which years you want.

How it works
------------
Three discovery routes are tried in order, and whatever they find is merged:

  1. WordPress media API  - gfim.com.gh runs WordPress, so /wp-json/wp/v2/media
                            usually lists every uploaded file with its real URL.
                            This is the most complete route when it is enabled.
  2. Page scrape          - any report links present in the Daily Trading
                            Reports page HTML.
  3. Pattern guessing     - for each business day still missing, try the known
                            file-name patterns against the likely upload
                            folders. This is slow and usually optional: you are
                            asked before it runs, and --no-guess turns it off.
                            Routes 1 and 2 already find most of the archive.

Everything is cached, so re-running only fetches what you do not already have.

Output - one parent folder, one sub-folder per year
---------------------------------------------------
    GFIM_Reports/
      2023/  TRADING-REPORT-FOR-GFIM-03012023.xlsx ...
      2024/  ...
      2025/  ...
      2026/  ...
      _manifest.csv     date, url, file, size, status
      _missing.csv      business days nothing was found for

Install:  pip install requests beautifulsoup4
Be polite: the default settings stay well under one request per second.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
import time
from urllib.parse import urljoin, unquote, urlparse

import requests
from bs4 import BeautifulSoup

SITE = "https://gfim.com.gh"
PAGE = f"{SITE}/daily-trading-reports/"
MEDIA_API = f"{SITE}/wp-json/wp/v2/media"
BASE = "GFIM_Reports"                    # one parent folder for everything
MANIFEST = os.path.join(BASE, "_manifest.csv")
MISSING = os.path.join(BASE, "_missing.csv")

HEADERS = {"User-Agent": "Mozilla/5.0 (research; Ghana yield curve project)"}
PAUSE = 0.8          # seconds between network calls
TIMEOUT = 120
MIN_BYTES = 1000     # anything smaller is an error page, not a report

# File-name patterns seen on the site. {d} is the date; placeholders are filled
# with several date spellings, so each pattern is tried a few ways.
# Only the name patterns actually seen on the site. Each extra pattern,
# extension or folder multiplies the work done in step 3, so keep this short.
NAME_PATTERNS = [
    "TRADING-REPORT-FOR-GFIM-{ddmmyyyy}",
    "TRADING-REPORT-FOR-GFIM-{dd-mm-yyyy}",
]
EXTENSIONS = [".xlsx"]                 # add ".xls", ".pdf" only if you hit them

# extensions accepted when reading links found by the API or the page scrape
LINK_EXTENSIONS = [".xlsx", ".xls", ".pdf"]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def date_spellings(d: dt.date) -> dict:
    return {
        "ddmmyyyy": d.strftime("%d%m%Y"),
        "dd-mm-yyyy": d.strftime("%d-%m-%Y"),
        "ddmmyy": d.strftime("%d%m%y"),
    }


def business_days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def date_from_name(name: str):
    """Pull the report date out of a file name, if it is in there."""
    stem = unquote(name).upper()
    for pat, fmt in (
        (r"(\d{2})[-_ ]?(\d{2})[-_ ]?(\d{4})", "%d%m%Y"),
        (r"(\d{4})[-_ ]?(\d{2})[-_ ]?(\d{2})", "%Y%m%d"),
    ):
        for m in re.finditer(pat, stem):
            try:
                return dt.datetime.strptime("".join(m.groups()), fmt).date()
            except ValueError:
                continue
    return None


def looks_like_report(url: str) -> bool:
    u = unquote(url).lower()
    if not u.endswith(tuple(LINK_EXTENSIONS)):
        return False
    if "status-report" in u or "monthly" in u:   # those are the monthly files
        return False
    return "trading" in u and "gfim" in u or "trading-report" in u


def local_name(url: str) -> str:
    return unquote(os.path.basename(urlparse(url).path))


def year_dir(year: int) -> str:
    """GFIM_Reports/<year>/ , created on demand."""
    path = os.path.join(BASE, str(year))
    os.makedirs(path, exist_ok=True)
    return path


def target_path(d: dt.date, url: str) -> str:
    """Where the report for date d is saved."""
    return os.path.join(BASE, str(d.year), local_name(url))


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def from_media_api(s, years) -> dict:
    """Ask the WordPress media library for every trading-report file."""
    found = {}
    log("1. WordPress media API ...")
    for page in range(1, 51):                      # 50 pages x 100 = 5000 files
        params = {"search": "trading report", "per_page": 100, "page": page}
        try:
            r = s.get(MEDIA_API, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            log(f"   media API unreachable ({e}); skipping this route")
            return found
        if r.status_code == 400:                   # past the last page
            break
        if r.status_code != 200:
            log(f"   media API returned {r.status_code}; skipping this route")
            return found
        try:
            items = r.json()
        except ValueError:
            log("   media API did not return JSON; skipping this route")
            return found
        if not items:
            break
        for item in items:
            url = item.get("source_url", "")
            if not looks_like_report(url):
                continue
            d = date_from_name(url)
            if d and d.year in years:
                found.setdefault(d, url)
        time.sleep(PAUSE)
    log(f"   found {len(found)} reports")
    return found


def from_page(s, years) -> dict:
    """Scrape whatever links the Daily Trading Reports page exposes."""
    found = {}
    log("2. Page scrape ...")
    try:
        r = s.get(PAGE, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        log(f"   could not load the page ({e})")
        return found

    soup = BeautifulSoup(r.text, "html.parser")
    urls = {urljoin(PAGE, a["href"]) for a in soup.find_all("a", href=True)}
    # the tabs sometimes hold their links in data attributes or inline JSON
    urls |= {urljoin(PAGE, m) for m in re.findall(r'https?://[^\s"\'<>]+?\.(?:xlsx|xls|pdf)', r.text)}

    for url in urls:
        if not looks_like_report(url):
            continue
        d = date_from_name(url)
        if d and d.year in years:
            found.setdefault(d, url)
    log(f"   found {len(found)} reports")
    return found


def guess_url(s, d: dt.date):
    """Try the known name patterns for one date. Returns a URL or None."""
    spell = date_spellings(d)
    # upload folder is usually the report's month, occasionally a neighbour
    folders = {d.strftime("%Y/%m"),
               (d + dt.timedelta(days=20)).strftime("%Y/%m")}
    for folder in sorted(folders):
        for pattern in NAME_PATTERNS:
            stem = pattern.format(**spell)
            for ext in EXTENSIONS:
                url = f"{SITE}/wp-content/uploads/{folder}/{stem}{ext}"
                try:
                    h = s.head(url, timeout=30, allow_redirects=True)
                except requests.RequestException:
                    continue
                finally:
                    time.sleep(PAUSE / 4)
                if h.status_code == 200:
                    return url
    return None


def tries_per_date() -> int:
    """How many HEAD requests one date costs in step 3."""
    return 2 * len(NAME_PATTERNS) * len(EXTENSIONS)   # 2 candidate folders


def fill_gaps(s, wanted, found):
    """Pattern-guess every business day not already found."""
    gaps = [d for d in wanted if d not in found]
    secs = len(gaps) * tries_per_date() * (PAUSE / 4)
    log(f"3. Pattern guessing for {len(gaps)} remaining dates "
        f"(up to {tries_per_date()} checks each, roughly "
        f"{secs/60:.0f} min) ...")
    log("   Most gaps are holidays or days with no publication.")
    for i, d in enumerate(gaps, 1):
        url = guess_url(s, d)
        if url:
            found[d] = url
            log(f"   [{i}/{len(gaps)}] {d:%Y-%m-%d}  found")
        elif i % 25 == 0:
            log(f"   [{i}/{len(gaps)}] ...")
    return found


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def download(s, found: dict, wanted) -> None:
    os.makedirs(BASE, exist_ok=True)
    rows, saved, skipped, failed = [], 0, 0, 0
    per_year = {}

    for d in sorted(found):
        url = found[d]
        name = local_name(url)
        year_dir(d.year)                      # make sure the folder exists
        path = target_path(d, url)

        if os.path.exists(path):
            rows.append([d.isoformat(), url, name, os.path.getsize(path), "cached"])
            per_year[d.year] = per_year.get(d.year, 0) + 1
            skipped += 1
            continue
        try:
            r = s.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            log(f"   error  {d:%Y-%m-%d}  {e}")
            rows.append([d.isoformat(), url, "", 0, "error"])
            failed += 1
            continue
        if r.status_code == 200 and len(r.content) > MIN_BYTES:
            with open(path, "wb") as f:
                f.write(r.content)
            rows.append([d.isoformat(), url, name, len(r.content), "downloaded"])
            log(f"   saved  {d:%Y-%m-%d}  {d.year}/{name}  ({len(r.content)//1024} KB)")
            per_year[d.year] = per_year.get(d.year, 0) + 1
            saved += 1
        else:
            rows.append([d.isoformat(), url, "", 0, f"http {r.status_code}"])
            failed += 1
        time.sleep(PAUSE)

    with open(MANIFEST, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "url", "file", "bytes", "status"])
        w.writerows(rows)

    gaps = [d for d in wanted if d not in found]
    with open(MISSING, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"])
        w.writerows([[d.isoformat()] for d in gaps])

    log("")
    log(f"Downloaded {saved}, already had {skipped}, failed {failed}.")
    for y in sorted(per_year):
        log(f"   {BASE}/{y}/  ->  {per_year[y]} reports")
    log(f"No file found for {len(gaps)} business days "
        f"(holidays, or names outside the known patterns).")
    log(f"Manifest: {MANIFEST}")
    log(f"Missing dates: {MISSING}")
    if gaps:
        log("Check a few of those dates on the website; if the names follow a "
            "new pattern, add it to NAME_PATTERNS and re-run.")


# --------------------------------------------------------------------------

def parse_years(tokens):
    """Accept 2026, 2023-2026, '2023 2025 2026', '2023,2026' - any mix."""
    years = set()
    for raw in tokens:
        for tok in re.split(r"[,\s]+", str(raw).strip()):
            if not tok:
                continue
            m = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", tok)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                years.update(range(min(a, b), max(a, b) + 1))
            elif re.fullmatch(r"\d{4}", tok):
                years.add(int(tok))
            else:
                raise ValueError(f"could not read '{tok}' as a year")
    return years


def validate_years(years):
    this_year = dt.date.today().year
    good = {y for y in years if 2015 <= y <= this_year}
    for y in sorted(years - good):
        log(f"   ignoring {y} (outside 2015-{this_year})")
    return good


def ask_years():
    """Ask which years to download; keeps asking until the answer is usable."""
    this_year = dt.date.today().year
    log("GFIM daily trading report downloader")
    log("-" * 38)
    log(f"Available years: 2015-{this_year}")
    log("You can pick several:  2026    2023-2026    2023 2025 2026")
    while True:
        try:
            raw = input("\nWhich years do you want? > ").strip()
        except EOFError:
            log("No input available here - pass the years in instead, "
                "e.g. main(['2023-2026'])")
            sys.exit(1)
        if not raw:
            log("   type at least one year, or press Ctrl+C to quit")
            continue
        try:
            years = validate_years(parse_years([raw]))
        except ValueError as e:
            log(f"   {e}; try again")
            continue
        if years:
            log(f"   selected: {', '.join(str(y) for y in sorted(years))}")
            return years
        log("   nothing usable in that; try again")


def main(args=None):
    global PAUSE
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("years", nargs="*",
                   help="e.g. 2023 2024 2025 2026  or  2023-2026 "
                        "(leave it out and you will be asked)")
    p.add_argument("--from", dest="start", help="YYYY-MM-DD, narrows the range")
    p.add_argument("--to", dest="end", help="YYYY-MM-DD, narrows the range")
    p.add_argument("--no-guess", action="store_true",
                   help="skip pattern guessing (much faster, less complete)")
    p.add_argument("--pause", type=float, default=PAUSE,
                   help="seconds between requests (default 0.8)")

    if args is None:
        # Inside a notebook/Colab kernel sys.argv holds kernel arguments,
        # so ignore it and let the prompt below ask which years you want.
        if any("ipykernel" in arg or "jupyter" in arg or arg.endswith(".json")
               for arg in sys.argv):
            args = []
        else:
            args = sys.argv[1:]

    a = p.parse_args(args)
    PAUSE = a.pause

    if a.years:                       # years given on the command line
        years = validate_years(parse_years(a.years))
        if not years:
            log("No usable years given.")
            return
    else:                             # nothing given, so ask
        years = ask_years()
        if not a.no_guess:
            try:
                reply = input(
                    "\nAlso try pattern guessing for dates the site does not "
                    "list?\nSlow, and most gaps are just holidays. [y/N] "
                ).strip().lower()
            except EOFError:
                reply = ""
            a.no_guess = not reply.startswith("y")
            log("   guessing: " + ("on" if not a.no_guess else "off"))

    start = dt.date.fromisoformat(a.start) if a.start else dt.date(min(years), 1, 1)
    end = dt.date.fromisoformat(a.end) if a.end else dt.date(max(years), 12, 31)
    end = min(end, dt.date.today())

    wanted = [d for d in business_days(start, end) if d.year in years]
    log(f"\nTarget: {len(wanted)} business days from {start} to {end}.")
    log(f"Saving into: {os.path.abspath(BASE)}{os.sep}<year>{os.sep}\n")

    s = session()
    found = {}
    found.update(from_media_api(s, years))
    for d, url in from_page(s, years).items():
        found.setdefault(d, url)
    if not a.no_guess:
        fill_gaps(s, wanted, found)

    log(f"\nHave URLs for {len(found)} of {len(wanted)} dates. Downloading ...")
    download(s, found, wanted)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nStopped. Re-run the same command to resume; finished files are kept.")
        sys.exit(1)
