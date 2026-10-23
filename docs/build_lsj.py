#!/usr/bin/env python3
"""
build_lsj.py  --  OPTIONAL: quick LSJ definitions beside Hesychius entries

What this does, in plain words:
  1. Downloads the LSJ (Liddell-Scott-Jones Greek-English Lexicon) as digitized by
     the Perseus Digital Library: 27 XML files, about 270 MB, from GitHub. They are
     saved in a folder called "raw_lsj" and are not downloaded again if already there.
  2. Reads the headwords in lexicon.js (made by build_lexicon.py).
  3. Finds LSJ entries with the same headword, ignoring accents, breathings and capitals.
  4. For each, keeps a SHORT definition and nothing else:
        - ordinary entries: the first few English translations the LSJ gives
        - "gloss" entries (the LSJ's own record of glosses from Hesychius and similar
          sources): the Greek gloss, plus the source abbreviations (Hsch., Cyr. ...)
  5. Writes lsj.js, which index.html loads. If lsj.js is missing, the site works
     exactly as before, just without the "Show LSJ entry" button.

Only the entries that match are kept, so lsj.js is a few megabytes, not 270.

LSJ data: Perseus Digital Library, https://github.com/PerseusDL/lexica,
licensed CC BY-SA 4.0. The exact version used is recorded in raw_lsj/manifest.json
and in lsj.js.

Run it (in a terminal, inside this folder), after build_lexicon.py:
    python build_lsj.py
"""

import glob
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from build_lexicon import beta_to_greek      # the Beta Code converter we already use

HERE = Path(__file__).parent
RAW = HERE / "raw_lsj"
LEXICON_JS = HERE / "lexicon.js"
OUT_JS = HERE / "lsj.js"

REPO = "PerseusDL/lexica"
PATH = "CTS_XML_TEI/perseus/pdllex/grc/lsj/grc.lsj.perseus-eng{n}.xml"
N_FILES = 27
HEADERS = {"User-Agent": "HesychiusLookup/0.1 (personal research project)"}

MAX_TRANSLATIONS = 4        # how many English translations to keep per entry
MAX_DEF_CHARS = 120         # stop adding translations once the definition is this long
MAX_PER_HEADWORD = 3        # homographs: keep at most this many LSJ entries per word


# ------------------------------------------------------------------ downloading

def pinned_ref():
    """Ask GitHub for the current commit, so the exact version can be recorded."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/master", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["sha"]
    except Exception as err:
        print(f"   (could not pin the exact version: {err}; using 'master')")
        return "master"


def fetch(url, dest, retries=4):
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return len(data)
        except Exception as err:
            last = err
            wait = 5 * attempt
            print(f"   attempt {attempt} failed ({err}); retrying in {wait} seconds...")
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last}")


def download_lsj():
    RAW.mkdir(exist_ok=True)
    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if manifest and all((RAW / f"grc.lsj.perseus-eng{n}.xml").exists() for n in range(1, N_FILES + 1)):
        print(f"LSJ already downloaded (version {manifest.get('ref', '?')[:10]}), skipping.")
        return manifest
    print("Downloading the LSJ from GitHub (about 270 MB; this can take a few minutes)...")
    ref = pinned_ref()
    files = {}
    for n in range(1, N_FILES + 1):
        name = f"grc.lsj.perseus-eng{n}.xml"
        dest = RAW / name
        if not dest.exists():
            url = f"https://raw.githubusercontent.com/{REPO}/{ref}/" + PATH.format(n=n)
            size = fetch(url, dest)
            print(f"   [{n}/{N_FILES}] {name}: {size / 1048576:.1f} MB")
            time.sleep(0.5)
        files[name] = dest.stat().st_size
    manifest = {"repo": f"https://github.com/{REPO}", "ref": ref,
                "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "files": files}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ------------------------------------------------------------------ matching

def norm(s):
    """Same folding the website uses when you search: no accents, lower case, ς = σ."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[<>\[\]\^_]", "", s.lower().replace("ς", "σ")).strip()


def beta_word(token):
    """Convert one Beta Code word (LSJ stores Greek that way) to Greek."""
    token = re.sub(r"[\^_]", "", token)
    return beta_to_greek(token.upper())


def beta_phrase(text):
    out = []
    for tok in text.split():
        trail = ""
        while tok and tok[-1] in ",.;:":
            trail = tok[-1] + trail
            tok = tok[:-1]
        out.append((beta_word(tok) if tok else "") + trail)
    return " ".join(out).strip(" ,;:")


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def gloss_parts(body):
    """(Greek gloss, [source abbreviations]) for a glossary-style entry."""
    foreign = re.search(r"<foreign\b[^>]*>(.*?)</foreign>", body, re.S)
    authors = []
    for a in re.findall(r"<author\b[^>]*>(.*?)</author>", body, re.S):
        a = strip_tags(a)
        if a and a not in authors:
            authors.append(a)
    greek = beta_phrase(strip_tags(foreign.group(1)).lstrip(": ")) if foreign else ""
    return greek, authors


def greek_gloss(body):
    """The Greek gloss and its source abbreviations, e.g. 'σύστημα ὕδατος (Hsch., Phot.)'."""
    greek, authors = gloss_parts(body)
    if not greek:
        return ""
    return (greek + (" (" + ", ".join(authors[:3]) + ")" if authors else "")).strip()


def translation_text(body):
    """The first few translations an entry gives (used for glossary entries whose gloss is Latin)."""
    picked, length = [], 0
    for t in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, re.S):
        t = strip_tags(t).strip(" ,;:.")
        if not t or t in picked:
            continue
        if picked and length + len(t) > MAX_DEF_CHARS:
            break
        picked.append(t)
        length += len(t) + 2
        if len(picked) >= MAX_TRANSLATIONS:
            break
    return "; ".join(picked)


def short_definition(body, kind):
    """Pull a short definition out of one LSJ entry. Returns (text, type_code).
    type_code: "m" = an English definition, "g" = a Greek gloss (Hesychius and similar)."""
    if kind == "gloss":
        text = greek_gloss(body)
        if text:
            return text, "g"
        # Some glossary entries (the bilingual Greek-Latin glossaries) give a Latin translation, not Greek.
        greek, authors = gloss_parts(body)
        tr = translation_text(body)
        if tr:
            return tr + (" (" + ", ".join(authors[:3]) + ")" if authors else ""), "g"
        return "", "g"

    # 1. the first few English translations the entry gives
    picked, length = [], 0
    for t in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, re.S):
        t = strip_tags(t).strip(" ,;:.")
        if not t or t in picked:
            continue
        if picked and length + len(t) > MAX_DEF_CHARS:
            break
        picked.append(t)
        length += len(t) + 2
        if len(picked) >= MAX_TRANSLATIONS:
            break
    if picked:
        return "; ".join(picked), "m"

    # 2. ordinary entries that are really glossary items (a Greek gloss and a source)
    gloss = greek_gloss(body)
    if gloss:
        return gloss, "g"

    # 3. cross-references: "see X"
    refs = [beta_word(strip_tags(r).strip(" .,;:")) for r in re.findall(r"<ref\b[^>]*>(.*?)</ref>", body, re.S)]
    refs = [r for r in refs if r][:2]
    if refs:
        return "see " + ", ".join(refs), "m"

    # 4. last resort: the plain English text after the headword, without citations
    plain = re.sub(r"<orth\b[^>]*>.*?</orth>|<bibl\b[^>]*>.*?</bibl>", "", body, flags=re.S)
    plain = strip_tags(plain).strip(" ,;:.")
    if len(plain) > MAX_DEF_CHARS:
        plain = plain[:MAX_DEF_CHARS].rsplit(" ", 1)[0] + "..."
    return plain, "m"


def read_hesychius_headwords():
    text = LEXICON_JS.read_text(encoding="utf-8")
    start = text.index("const LEXICON = ") + len("const LEXICON = ")
    entries = json.loads(text[start:text.rindex("];") + 1])
    exact = {unicodedata.normalize("NFC", e["h"]) for e in entries}
    return {norm(h) for h in exact}, exact, len(entries)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not LEXICON_JS.exists():
        sys.exit("lexicon.js not found. Run build_lexicon.py first.")

    manifest = download_lsj()
    targets, exact_forms, n_entries = read_hesychius_headwords()
    print(f"\nHesychius: {n_entries:,} entries, {len(targets):,} distinct headwords to look for.")

    found = {}
    scanned = 0
    files = sorted(glob.glob(str(RAW / "grc.lsj.perseus-eng*.xml")),
                   key=lambda f: int(re.search(r"eng(\d+)", f).group(1)))
    for f in files:
        text = Path(f).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"<entryFree\b([^>]*)>(.*?)</entryFree>", text, re.S):
            attrs = m.group(1)
            k = re.search(r'\bkey="([^"]*)"', attrs)
            if not k:
                continue
            scanned += 1
            greek = beta_word(re.sub(r"\d+$", "", k.group(1)))
            key = norm(greek)
            if key not in targets:
                continue
            ty = re.search(r'\btype="([^"]*)"', attrs)
            definition, code = short_definition(m.group(2), ty.group(1) if ty else "main")
            if definition:
                found.setdefault(key, []).append([unicodedata.normalize("NFC", greek), definition, code])
    # ordinary entries first, then glosses; keep a few per word
    for key, lst in found.items():
        lst.sort(key=lambda x: x[2] != "m")
        found[key] = lst[:MAX_PER_HEADWORD]

    n_main = sum(1 for v in found.values() if any(x[2] == "m" for x in v))
    n_gloss = sum(1 for v in found.values() if all(x[2] == "g" for x in v))
    info = {"source": "Perseus Digital Library, LSJ (CC BY-SA 4.0)", "repo": manifest.get("repo"),
            "ref": manifest.get("ref"), "fetched": manifest.get("fetched"),
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "matched": len(found)}
    with OUT_JS.open("w", encoding="utf-8") as fh:
        fh.write("// Built by build_lsj.py from the Perseus LSJ (CC BY-SA 4.0). Do not edit by hand.\n")
        fh.write("const LSJ_INFO = " + json.dumps(info, ensure_ascii=False) + ";\n")
        fh.write("const LSJ = " + json.dumps(found, ensure_ascii=False, separators=(",", ":")) + ";\n")

    size = OUT_JS.stat().st_size
    pct = 100 * len(found) / max(1, len(targets))
    print(f"Scanned {scanned:,} LSJ entries.")
    print(f"{len(found):,} of {len(targets):,} Hesychius headwords ({pct:.0f}%) have an LSJ entry:")
    print(f"   {n_main:,} with an ordinary entry, {n_gloss:,} only as an LSJ gloss entry")
    n_exact = sum(1 for v in found.values() if any(x[0] in exact_forms for x in v))
    print(f"   {n_exact:,} match with the accents and breathings exactly as Hesychius has them; "
          f"the rest differ in accent or breathing (the page labels those as near matches)")
    print(f"Wrote {OUT_JS.name} ({size / 1048576:.1f} MB). Now reload index.html.")


if __name__ == "__main__":
    main()
