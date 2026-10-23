#!/usr/bin/env python3
"""
fetch_wikisource.py  --  STEP 1 of 2

What this does, in plain words:
  1. Asks Greek Wikisource for the list of all pages whose names start
     with "Γλώσσαι/" (one page per letter of the alphabet, possibly more).
  2. Downloads each page.
  3. Records, for every page, its REVISION NUMBER (the permanent ID of the
     exact saved version we downloaded) and the download time, in
     raw/manifest.json. The website uses these to link to the exact version
     of each page, so anyone can check our work later even if Wikisource is
     edited in the meantime.
  4. Saves two copies of each into a folder called "raw":
        - a .html file  (the page exactly as Wikisource sends it)
        - a .txt file   (the same page with the web-page markup stripped
                         out, leaving just readable text)

It does NOT split the text into dictionary entries yet. That is step 2,
and I want to look at your downloaded text first, so that the splitting
is based on how the entries are really formatted.

It only uses tools that come built into Python, so there is nothing
extra to install.

How to run it (in a terminal, inside this folder):
    python3 fetch_wikisource.py        (Mac / Linux)
    python fetch_wikisource.py         (Windows)
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

API = "https://el.wikisource.org/w/api.php"
PREFIX = "Γλώσσαι/"          # every letter page starts with this
MAIN_PAGE = "Γλώσσαι"        # the overview page
OUT_DIR = Path(__file__).parent / "raw"
MANIFEST = OUT_DIR / "manifest.json"
PAUSE_SECONDS = 10           # be polite: wait between requests

# Wikisource asks programs to identify themselves.
HEADERS = {"User-Agent": "HesychiusLookup/0.1 (personal research project; "
                         "downloads each page once)"}


def call_api(params, retries=8):
    """Send one question to the Wikisource API and return its answer.

    If Wikisource says "too many requests" (error 429), we wait a good
    while, as long as it asks us to, and then try again.
    """
    params = dict(params, format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            last_error = err
            if err.code == 429:
                # Use the wait time Wikisource suggests, if it gave one.
                suggested = err.headers.get("Retry-After", "")
                wait = int(suggested) if suggested.isdigit() else 60 * attempt
                wait = max(wait, 60)
                print(f"   Wikisource says slow down (429). "
                      f"Waiting {wait} seconds, then trying again "
                      f"(attempt {attempt} of {retries})...")
            else:
                wait = 5 * attempt
                print(f"   attempt {attempt} failed ({err}); "
                      f"retrying in {wait} seconds...")
            time.sleep(wait)
        except Exception as err:  # network hiccup, timeout, etc.
            last_error = err
            wait = 5 * attempt
            print(f"   attempt {attempt} failed ({err}); "
                  f"retrying in {wait} seconds...")
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last_error}")


def list_pages():
    """Find the names of all the pages we want."""
    titles = [MAIN_PAGE]
    params = {"action": "query", "list": "allpages", "apprefix": PREFIX,
              "apnamespace": "0", "aplimit": "500"}
    while True:
        data = call_api(params)
        titles += [p["title"] for p in data["query"]["allpages"]]
        if "continue" in data:            # more results to fetch
            params.update(data["continue"])
        else:
            break
    return titles


def get_html(title):
    """Download one page as HTML (with any embedded sub-pages filled in).
    Returns (html, revision_number, title_as_returned)."""
    data = call_api({"action": "parse", "page": title, "prop": "text|revid",
                     "redirects": "1", "disableeditsection": "1"})
    parsed = data["parse"]
    return parsed["text"], parsed.get("revid"), parsed.get("title", title)


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"source": "https://el.wikisource.org", "pages": {}}


def save_manifest(manifest):
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")


class TextExtractor(HTMLParser):
    """Turns HTML into plain text, keeping line breaks where they matter."""

    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4",
                  "h5", "h6", "dd", "dt", "blockquote", "table"}
    SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html):
    parser = TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)    # trailing spaces
    text = re.sub(r"\n{3,}", "\n\n", text)    # runs of blank lines
    return text.strip() + "\n"


def safe_filename(title):
    """'Γλώσσαι/Α' -> 'Γλώσσαι_Α' (slashes are not allowed in filenames)."""
    return title.replace("/", "_").replace(" ", "_")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("Looking up the list of pages...")
    titles = list_pages()
    print(f"Found {len(titles)} pages:")
    for t in titles:
        print("   ", t)

    manifest = load_manifest()
    for i, title in enumerate(titles, 1):
        name = safe_filename(title)
        html_path = OUT_DIR / f"{name}.html"
        txt_path = OUT_DIR / f"{name}.txt"
        if (html_path.exists() and txt_path.exists()
                and title in manifest["pages"]):
            print(f"[{i}/{len(titles)}] {title}: already downloaded, skipping")
            continue
        print(f"[{i}/{len(titles)}] Downloading {title} ...")
        html, revid, returned_title = get_html(title)
        text = html_to_text(html)
        html_path.write_text(html, encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")
        manifest["pages"][title] = {
            "revid": revid,
            "title_returned": returned_title,
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
        save_manifest(manifest)               # saved after every page
        print(f"      saved {len(text):,} characters (revision {revid})")
        time.sleep(PAUSE_SECONDS)

    print("\nDone. The 'raw' folder now holds every page, plus manifest.json,")
    print("which records the revision number of each page.")
    print("Next: run  python build_lexicon.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nStopped. Run it again and it will pick up where it left off.")
