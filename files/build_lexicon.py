#!/usr/bin/env python3
"""
build_lexicon.py  --  STEP 2 of 2

What this does, in plain words:
  1. Reads every Γλώσσαι_*.txt file in the "raw" folder (made by step 1).
  2. Splits the text into dictionary entries. A clean entry looks like:

         *<ἀμίς>            <- a line holding only the headword in <angle
                               brackets>, sometimes with marks (* † [ )
                            <- blank line
         σταμνίον           <- the definition

  3. Wikisource's transcription is sometimes irregular (stray or missing
     brackets, a headword and definition run together, etc.). Where the
     pattern is obvious, this script repairs it. Wherever it had to repair
     or guess, it FLAGS the entry, and the website shows a "check this
     against Wikisource" note on it.
  4. Writes "lexicon.js" (what the website reads) and "build_report.txt"
     (a log of what was repaired, glued or flagged).

Provenance: every entry that was changed or interpreted in ANY way carries
a record of what was done, plus the original Wikisource lines, so the
website can show them. Fields written into the data:

    f   possible-error flags:
          r  reconstructed from irregular formatting
          g  extra lines were attached and may belong elsewhere
          e  no definition was found after this headword
          b  typed in Beta Code (Greek in Latin letters) and converted
    k   for flag r: which kind of repair (a short English label)
    s   routine edits (not errors, but still disclosed):
          h  line-break hyphens were joined (details in field j)
          c  a <bracketed> line after the headword was read as its definition
          u  the headword had no angle brackets and was read from a short line
    j   the exact hyphen joins made, e.g. "κεκομ- μένων → κεκομμένων"
    o   the original Wikisource lines for this entry (present whenever
        f or s is present)

Run it (in a terminal, inside this folder):
    python build_lexicon.py
"""

import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

# ----------------------------- settings --------------------------------
JOIN_HYPHENATED_WORDS = True   # "κεκομ- μένων" becomes "κεκομμένων"
MAX_STRAY_LEMMA_LENGTH = 70    # longest line we will treat as a lone lemma
MAX_LEMMA_LENGTH = 45          # longest cleaned headword we will accept
# ------------------------------------------------------------------------

HERE = Path(__file__).parent
RAW_DIR = HERE / "raw"
OUT_JS = HERE / "lexicon.js"
OUT_REPORT = HERE / "build_report.txt"

LETTERS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"

# Marks that can sit in front of a headword: * † [ ] ( ) and labels "a)".
MARK_TOKEN = r"(?:[\*†\[\]()\s]|[a-z]\))"
LEADING_MARKS = re.compile(rf"^{MARK_TOKEN}*")
MARKS_THEN_BRACKETS = re.compile(rf"^(?P<marks>{MARK_TOKEN}*)(?P<rest><.*>)\s*$")
# <<lemma>·> gloss   (headword and definition run together on one line)
LEMMA_AND_GLOSS = re.compile(
    rf"^(?P<marks>{MARK_TOKEN}*)<<(?P<head>[^<>]+?)>\s*·?\s*>\s*(?P<rest>.+)$")

GREEK = "\u0370-\u03ff\u1f00-\u1fff"
HYPHEN_BREAK = re.compile(f"(?<=[{GREEK}])- (?=[{GREEK}])")
CONNECTORS = re.compile(r"\b(καὶ|ἢ|ἤγουν|ἡ|ὁ|τό)\b")


# ------------------------------ helpers ---------------------------------

def plain(s):
    """Lower-case, accent-free version of a string, for loose comparisons."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("ς", "σ").strip()


def page_title_from_filename(path):
    """'Γλώσσαι_Α.txt' -> 'Γλώσσαι/Α' (the Wikisource page name)."""
    return path.stem.replace("_", "/", 1)


def sort_key(path):
    suffix = path.stem.split("_", 1)[1] if "_" in path.stem else ""
    pos = LETTERS.find(suffix[:1]) if suffix else -1
    return (pos if pos >= 0 else 99, suffix)


def prepare(text):
    """Unicode-normalise and collapse runs of spaces (applied to all text)."""
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


WORD = f"[{GREEK}\u0300-\u036f]+"
HYPHEN_PAIR = re.compile(f"({WORD})- ({WORD})")


def find_hyphen_joins(prepared_text):
    """List the hyphen joins tidy() will make, for disclosure."""
    if not JOIN_HYPHENATED_WORDS:
        return []
    return [f"{m.group(1)}- {m.group(2)} → {m.group(1)}{m.group(2)}"
            for m in HYPHEN_PAIR.finditer(prepared_text)]


def tidy(text):
    text = prepare(text)
    if JOIN_HYPHENATED_WORDS:
        text = HYPHEN_BREAK.sub("", text)
    return text


def is_navigation(line):
    """Short lines like 'Β→' or '←Α' that are page navigation, not text."""
    return len(line) <= 12 and ("←" in line or "→" in line)


def angle_depths(s):
    """(final depth, lowest depth) of < > nesting. (0, 0) means balanced."""
    depth = low = 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            low = min(low, depth)
    return depth, low


def split_marks(line):
    m = LEADING_MARKS.match(line)
    return line[:m.end()], line[m.end():]


def clean_lemma(text):
    """Remove stray brackets and trailing punctuation from a lemma."""
    text = re.sub(r"[<>\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ·.,;")
    # drop an unmatched parenthesis, but keep real ones like Περκώς(ι)ος
    if text.count(")") > text.count("("):
        text = text.replace(")", "")
    if text.count("(") > text.count(")"):
        text = text.replace("(", "")
    return text.strip()


def looks_like_definition(head):
    """True if a candidate lemma carries the apparatus of a definition:
    a parenthesised citation, a mid-line asterisk, a sentence break, or
    simply too much text to be a headword."""
    return bool(
        len(head) > MAX_LEMMA_LENGTH
        or re.search(r"\([^)]*\d[^)]*\)", head)      # (Κ 264)
        or re.search(r"\s\*", head)                    # " *ζηλοτυπία"
        or re.search(r"[.;]\s+\S", head)               # sentence break
    )


def parse_headword(line):
    """If the line is a clean headword line, return (marks, headword)."""
    m = MARKS_THEN_BRACKETS.match(line)
    if not m:
        return None
    rest = m.group("rest")
    depth = 0
    for idx, ch in enumerate(rest):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth < 0 or (depth == 0 and idx != len(rest) - 1):
                return None
    if depth != 0:
        return None
    head = rest[1:-1].strip()
    return (m.group("marks"), head) if head else None


def only_bracketed_lemmas(line):
    """True for lines like  *<ἄβακτον> καὶ <ἄβυκτον>  -- nothing but
    bracketed words and little connecting words."""
    marks, rest = split_marks(line)
    depth, low = angle_depths(rest)
    if depth != 0 or low != 0 or "<" not in rest:
        return False
    # remove every balanced <...> group (innermost first)
    prev = None
    while prev != rest:
        prev = rest
        rest = re.sub(r"<[^<>]*>", "", rest)
    rest = CONNECTORS.sub("", rest)
    rest = re.sub(r"[\s\[\]·.,;*†()]", "", rest)
    return rest == ""


# --------------------------- Beta Code ---------------------------------
# Some Wikisource entries have Greek typed in "Beta Code": Greek written in
# capital Latin letters, with symbols for breathings and accents.
#   letters: A α  B β  G γ  D δ  E ε  Z ζ  H η  Q θ  I ι  K κ  L λ  M μ
#            N ν  C ξ  O ο  P π  R ρ  S σ/ς  T τ  U υ  F φ  X χ  Y ψ  W ω
#   symbols after a letter:  )  smooth breathing    (  rough breathing
#            /  acute   \  grave   =  circumflex   |  iota subscript
#   * in front of a letter makes it a capital (*Q = Θ); for capitals the
#   symbols come between the * and the letter (*)/A = Ἄ).
# On this site's source, accents are sometimes already real accented Latin
# capitals (Á = acute, Û = circumflex, ...). Both styles are handled.

BETA_LETTERS = {
    "A": "α", "B": "β", "G": "γ", "D": "δ", "E": "ε", "Z": "ζ", "H": "η",
    "Q": "θ", "I": "ι", "K": "κ", "L": "λ", "M": "μ", "N": "ν", "C": "ξ",
    "O": "ο", "P": "π", "R": "ρ", "S": "σ", "T": "τ", "U": "υ", "F": "φ",
    "X": "χ", "Y": "ψ", "W": "ω", "V": "ϝ"}
BETA_SYMBOLS = {")": "\u0313", "(": "\u0314", "/": "\u0301", "\\": "\u0300",
                "=": "\u0342", "|": "\u0345", "+": "\u0308"}
LATIN_ACCENT_TO_GREEK = {"\u0301": "\u0301", "\u0300": "\u0300",
                         "\u0302": "\u0342", "\u0308": "\u0308"}
ACCENTED_CAPITALS = "ÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÏÜ"
BETA_CORE = re.compile(rf"^[*)(/\\=|+A-Z{ACCENTED_CAPITALS}]+$")
BETA_SIGLA = re.compile(r"^[ASP]+$")          # manuscript sigla, not Greek
POSSIBLE_BETA = re.compile(
    rf"[{ACCENTED_CAPITALS}]|\*[)(/\\=]*[A-Z]|[A-Z][)(/\\=]+[A-Z]")
# <LEMMA>: gloss   -- the shape of the Beta Code entries
BETA_ENTRY = re.compile(
    rf"^(?P<marks>{MARK_TOKEN}*)<(?P<lemma>[^<>]*)>\s*:\s*(?P<gloss>.+)$")


def beta_to_greek(token):
    out, i, n = [], 0, len(token)
    while i < n:
        ch = token[i]
        capital = False
        marks = []
        if ch == "*":
            capital = True
            i += 1
            while i < n and token[i] in BETA_SYMBOLS:
                marks.append(BETA_SYMBOLS[token[i]])
                i += 1
            if i >= n:
                out.append("*")
                break
            ch = token[i]
        decomposed = unicodedata.normalize("NFD", ch)
        base, extra = decomposed[0], decomposed[1:]
        if base not in BETA_LETTERS:
            out.append(ch)
            i += 1
            continue
        for c in extra:
            if c in LATIN_ACCENT_TO_GREEK:
                marks.append(LATIN_ACCENT_TO_GREEK[c])
        i += 1
        while i < n and token[i] in BETA_SYMBOLS:
            marks.append(BETA_SYMBOLS[token[i]])
            i += 1
        greek = BETA_LETTERS[base]
        if base == "S" and not capital:
            nxt = token[i] if i < n else ""
            nxt_base = unicodedata.normalize("NFD", nxt)[:1] if nxt else ""
            if nxt_base not in BETA_LETTERS:
                greek = "ς"                       # final sigma
        if capital:
            greek = greek.upper()
        out.append(greek + "".join(marks))
    return unicodedata.normalize("NFC", "".join(out))


def beta_text_to_greek(text):
    """Convert the Beta Code words in a line; leave Latin text
    (citations like 'Eur. Alc. 729', sigla like 'AS') alone."""
    pieces = re.split(r"(\s+)", text)
    for k, tok in enumerate(pieces):
        if not tok or tok.isspace():
            continue
        lead = ""
        if tok.startswith("(") and len(tok) > 1:      # wrapper paren: (*L
            lead, tok2 = "(", tok[1:]
        else:
            tok2 = tok
        trail = ""
        while tok2 and tok2[-1] in ".,;:·":
            trail = tok2[-1] + trail
            tok2 = tok2[:-1]
        if lead and tok2.endswith(")"):
            trail = ")" + trail
            tok2 = tok2[:-1]
        if not tok2 or not BETA_CORE.match(tok2):
            continue
        if not re.search(r"[A-Z]", tok2) or BETA_SIGLA.match(tok2):
            continue
        if re.fullmatch(r"[A-Z]{1,2}", tok2):          # AS, A, S ...
            continue
        pieces[k] = lead + beta_to_greek(tok2) + trail
    return "".join(pieces)


def match_beta_entry(line):
    """Return the regex match if the line is a Beta Code entry."""
    m = BETA_ENTRY.match(line)
    if not m:
        return None
    lemma = m.group("lemma")
    if not re.search(r"[A-Z]", lemma) or re.search(f"[{GREEK}]", lemma):
        return None
    return m


# ------------------------------ parsing ---------------------------------

def new_entry(page, head, marks, parts, flags="", src=None, kind=""):
    return {"h": head, "m": re.sub(r"\s+", "", marks), "p": page,
            "parts": list(parts), "f": set(flags), "s": set(),
            "src": list(src or []), "k": kind}


def attach(entry, line):
    """Add an original line to an entry (as definition text and as source)."""
    entry["parts"].append(line)
    entry["src"].append(line)


def recover_stray(line, nxt, page, report):
    """Try to read a stray line as the start of a NEW entry.

    Returns (entry, lines_consumed) or None if the line should simply be
    attached to the previous entry.
    """
    marks, rest = split_marks(line)
    nxt_is_head = nxt is not None and parse_headword(nxt) is not None

    # Pattern: <BETA CODE LEMMA>: BETA CODE GLOSS  (Greek typed in Latin letters)
    bm = match_beta_entry(line)
    if bm:
        head = beta_text_to_greek(bm.group("lemma")).strip()
        gloss = beta_text_to_greek(bm.group("gloss")).strip()
        e = new_entry(page, head, bm.group("marks"), [gloss], "b", src=[line])
        report["beta"].append(f"{page}: {line[:70]}   =>   <{head}> {gloss[:60]}")
        return e, 1

    # Pattern: <<lemma>·> gloss, all on one line
    m = LEMMA_AND_GLOSS.match(line)
    if m:
        e = new_entry(page, clean_lemma(m.group("head")), m.group("marks"),
                      [m.group("rest")], "r", src=[line],
                      kind="lemma and gloss on one line")
        report["recovered"].append(f"{page}: <{e['h']}> [lemma and gloss on one line]")
        return e, 1

    if nxt is None or len(line) > MAX_STRAY_LEMMA_LENGTH:
        return None

    depth, low = angle_depths(rest)
    unbalanced = (depth != 0 or low != 0) and ("<" in rest or ">" in rest)

    kind = None
    if unbalanced:
        kind = "stray bracket"
    elif only_bracketed_lemmas(line):
        kind = "several bracketed lemmas"
    elif (rest.startswith("[") or marks.strip().endswith("[")) and "]" not in line \
            and "<" not in line and len(line) <= 40 \
            and "]" in nxt and "[" not in nxt:
        kind = "square-bracketed entry"
    if kind is None:
        return None

    head = clean_lemma(rest if rest else line)
    if not head or looks_like_definition(head):
        return None
    flags = "r"
    parts = []
    consumed = 1
    if nxt_is_head:
        flags += "e"                     # lemma followed by a clean headword
    else:
        parts.append(nxt)
        consumed = 2
    e = new_entry(page, head, marks, parts, flags,
                  src=[line] + parts, kind=kind)
    report["recovered"].append(f"{page}: <{head}> [{kind}]  from «{line[:50]}»")
    return e, consumed


def parse_file(path, report):
    page = page_title_from_filename(path)
    letter = page.split("/", 1)[1] if "/" in page else ""

    lines = [unicodedata.normalize("NFC", l.strip())
             for l in path.read_text(encoding="utf-8").splitlines()]
    paras = [l for l in lines if l and not is_navigation(l)]
    n = len(paras)

    entries = []
    current = None
    seen_letter_heading = False
    skipped_preamble = []
    i = 0

    while i < n:
        line = paras[i]
        hw = parse_headword(line)

        # ---- A clean headword line: start a new entry -------------------
        if hw:
            marks, head = hw
            current = new_entry(page, head, marks, [], src=[line])
            entries.append(current)

            if i + 1 >= n:
                current["f"].add("e")
                report["empty"].append(f"{page}: <{head}>")
                i += 1
                continue

            nxt = paras[i + 1]
            if match_beta_entry(nxt):
                current["f"].add("e")            # Beta entry follows: no definition
                report["empty"].append(f"{page}: <{head}>")
                i += 1
            elif parse_headword(nxt) is None:
                attach(current, nxt)
                i += 2
            else:
                # Next line is also <bracketed>: either this entry's
                # definition, or this entry has none. One line of lookahead
                # decides: a bracketed definition is followed by a new
                # headword; a new entry would be followed by its definition.
                after = paras[i + 2] if i + 2 < n else None
                if after is None or parse_headword(after) is not None:
                    attach(current, nxt)
                    current["s"].add("c")
                    report["bracketed"].append(f"{page}: <{head}> = {nxt}")
                    i += 2
                else:
                    current["f"].add("e")
                    report["empty"].append(f"{page}: <{head}>")
                    i += 1
            continue

        # ---- Before the first entry: page header, or a bare headword ----
        if current is None:
            if plain(line) == "γλωσσαι" or line.startswith("Συγγραφέας"):
                pass
            elif plain(line) == plain(letter) and not seen_letter_heading:
                seen_letter_heading = True
            elif len(line) <= 3 and " " not in line and i + 1 < n:
                current = new_entry(page, line, "", [paras[i + 1]],
                                    src=[line, paras[i + 1]])
                current["s"].add("u")
                entries.append(current)
                report["bare"].append(f"{page}: {line} = {paras[i + 1][:50]}")
                i += 2
                continue
            else:
                skipped_preamble.append(line)
            i += 1
            continue

        # ---- A stray line: new entry in disguise, or an extra paragraph -
        nxt = paras[i + 1] if i + 1 < n else None
        rec = recover_stray(line, nxt, page, report)
        if rec:
            current, used = rec
            entries.append(current)
            i += used
            continue

        attach(current, line)
        current["f"].add("g")
        if POSSIBLE_BETA.search(line):
            report["maybe_beta"].append(f"{page}: <{current['h']}> + «{line[:70]}»")
        report["glued"].append(f"{page}: <{current['h']}> + «{line[:60]}»")
        i += 1

    if skipped_preamble:
        report["preamble"].append(f"{page}: skipped {skipped_preamble}")

    out = []
    for e in entries:
        joins = []
        for part in e["parts"]:
            joins += find_hyphen_joins(prepare(part))
        parts = [tidy(p) for p in e["parts"]]
        text = "\n".join(p for p in parts if p)

        steps = set(e["s"])
        if joins:
            steps.add("h")
            report["hyphens"].append(f"{page}: <{e['h']}>  " + "; ".join(joins))

        entry = {"h": unicodedata.normalize("NFC", e["h"]), "t": text, "p": e["p"]}
        if e["m"]:
            entry["m"] = e["m"]
        if e["f"]:
            entry["f"] = "".join(sorted(e["f"]))
        if "r" in e["f"] and e["k"]:
            entry["k"] = e["k"]
        if steps:
            entry["s"] = "".join(sorted(steps))
        if joins:
            entry["j"] = joins
        if e["f"] or steps:
            entry["o"] = "\n".join(e["src"])
        out.append(entry)
    return out


# -------------------------------- main -----------------------------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    files = sorted(RAW_DIR.glob("Γλώσσαι_*.txt"), key=sort_key)
    if not files:
        sys.exit("No Γλώσσαι_*.txt files found in the 'raw' folder. "
                 "Run fetch_wikisource.py first.")

    report = {"preamble": [], "glued": [], "empty": [], "bracketed": [],
              "bare": [], "recovered": [], "beta": [], "maybe_beta": [],
              "hyphens": []}
    all_entries = []
    for path in files:
        entries = parse_file(path, report)
        print(f"{path.name}: {len(entries):,} entries")
        all_entries.extend(entries)

    flagged = sum(1 for e in all_entries if "f" in e)
    processed = sum(1 for e in all_entries if "s" in e and "f" not in e)

    pages = {}
    manifest_path = RAW_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for title, rec in manifest.get("pages", {}).items():
            pages[title] = {"rev": rec.get("revid"), "fetched": rec.get("fetched")}
    else:
        print("NOTE: raw/manifest.json not found, so links cannot point to an "
              "exact page revision. Run fetch_wikisource.py again to record them.")

    info = {"demo": False, "count": len(all_entries), "flagged": flagged,
            "processed": processed, "built": date.today().isoformat(),
            "pages": pages}
    with OUT_JS.open("w", encoding="utf-8") as f:
        f.write("// Built by build_lexicon.py. Do not edit by hand.\n")
        f.write("const LEXICON_INFO = " + json.dumps(info, ensure_ascii=False) + ";\n")
        f.write("const LEXICON = [\n")
        f.write(",\n".join(json.dumps(e, ensure_ascii=False) for e in all_entries))
        f.write("\n];\n")

    with OUT_REPORT.open("w", encoding="utf-8") as f:
        pct = 100 * flagged / max(1, len(all_entries))
        f.write(f"Total entries: {len(all_entries):,}\n")
        f.write(f"Entries with a possible-error warning: {flagged:,} ({pct:.1f}%)\n")
        f.write(f"Entries edited in routine ways only: {processed:,}\n\n")
        for key, title in [
            ("beta", "Entries converted from Beta Code to Greek (flag b)"),
            ("maybe_beta", "Lines that look like Beta Code but were NOT converted"),
            ("recovered", "Entries reconstructed from irregular formatting (flag r)"),
            ("glued", "Extra lines attached to the previous entry (flag g)"),
            ("empty", "Headwords with no definition after them (flag e)"),
            ("bracketed", "Definitions that are themselves a <bracketed word> (step c)"),
            ("bare", "First entries written without brackets (step u)"),
            ("hyphens", "Line-break hyphens joined (step h)"),
            ("preamble", "Lines skipped above the first entry")]:
            f.write(f"=== {title}: {len(report[key]):,} ===\n")
            for item in report[key][:300]:
                f.write(item + "\n")
            if len(report[key]) > 300:
                f.write(f"... and {len(report[key]) - 300:,} more\n")
            f.write("\n")

    print(f"\nWrote {len(all_entries):,} entries to {OUT_JS.name}")
    print(f"{flagged:,} entries ({pct:.1f}%) carry a possible-error warning, "
          f"and {processed:,} more were edited in routine ways. "
          f"Every one shows its original Wikisource text on the site.")
    print(f"   reconstructed: {len(report['recovered']):,}   "
          f"Beta Code converted: {len(report['beta']):,}   "
          f"extra lines glued: {len(report['glued']):,}   "
          f"no definition: {len(report['empty']):,}")
    print(f"Details are in {OUT_REPORT.name}. Now open index.html in your browser.")

if __name__ == "__main__":
    main()
