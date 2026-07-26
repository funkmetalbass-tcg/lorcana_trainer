"""Phase-1 keyword layer.

Extracts PRINTED keywords from card text so vanilla + keyword-only cards work
engine-wide with no per-card code.

The crucial distinction: a keyword is only "printed" if it appears in a pure
keyword line -- i.e. a sentence, before any named ability ([SOME NAME] ...),
that contains nothing except keyword tokens. This prevents false positives:

  - "Rush (reminder)[DYNAMIC MANEUVER] ..."      -> printed Rush            OK
  - "[CHART YOUR OWN COURSE] ... gains Resist +1 for each location"
                                                  -> NOT printed Resist
                                                     (dynamic; ability code)
  - "Chosen character gains Evasive this turn."   -> NOT printed Evasive
  - "Puppy Shift 3 (...)"                         -> NOT plain Shift
                                                     ('Puppy' residue rejects)

Run directly for a pool coverage report:
    python3 -m lorcana.keywords master_legal_cardlist.json
"""
import re

# keyword -> pattern. Groups capture scaling numbers where relevant.
_PATTERNS = {
    "Bodyguard":     re.compile(r"\bBodyguard\b"),
    "Evasive":       re.compile(r"\bEvasive\b"),
    "Alert":         re.compile(r"\bAlert\b"),
    "Rush":          re.compile(r"\bRush\b"),
    "Ward":          re.compile(r"\bWard\b"),
    "Reckless":      re.compile(r"\bReckless\b"),
    "Support":       re.compile(r"\bSupport\b"),
    "Vanish":        re.compile(r"\bVanish\b"),
    "Resist":        re.compile(r"\bResist\b\s*\+?(\d+)"),
    "Challenger":    re.compile(r"\bChallenger\b\s*\+?(\d+)"),
    "Singer":        re.compile(r"\bSinger\b\s*(\d+)"),
    "Shift":         re.compile(r"\bShift\b\s*(\d+)\s*(?:Ink)?"),
    "Sing Together": re.compile(r"\bSing Together\b\s*(\d+)"),
}

_REMINDER = re.compile(r"\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")
_BRACKET_NAME = re.compile(r"\[[^\]]*\]")


def clean_text(raw):
    return _HTML.sub("", raw or "")


def _keyword_prefix(text):
    """Text before the first named ability [NAME]; keywords print there.
    Reminder parentheticals are stripped FIRST because they may themselves
    contain brackets (e.g. the [Exert] symbol inside Sing Together's reminder)."""
    t = _REMINDER.sub("", clean_text(text))
    idx = t.find("[")
    return t if idx < 0 else t[:idx]


def _keyword_candidate_lines(text):
    """Sentences that may print keywords.

    Historically only the pre-[NAME] prefix was scanned, but keywords are also
    printed AFTER a named ability (e.g. Liquidator: "[UNDERDOG] ...\nReckless
    (...)"). We therefore scan the prefix plus every line of the remainder with
    the [NAME] labels and reminder text stripped. The purity check below is what
    prevents false positives, so widening the candidate set is safe.
    """
    t = _REMINDER.sub("", clean_text(text))
    prefix = _keyword_prefix(text)
    rest = _BRACKET_NAME.sub("\n", t[len(_keyword_prefix(text)):]) if t.startswith(prefix) \
        else _BRACKET_NAME.sub("\n", t)
    return re.split(r"[.\n]", prefix) + re.split(r"[.\n]", rest)


def parse_printed_keywords(text):
    """Return dict of printed keyword -> True | int.

    Only pure keyword sentences qualify: after stripping reminder
    parentheticals and recognized keyword tokens, nothing may remain in the
    sentence (bare {} ink/lore symbols are ignored -- they are notation, not
    prose, e.g. "Shift 3{}")."""
    out = {}
    for sentence in _keyword_candidate_lines(text):
        found = {}
        rest = sentence
        for kw, pat in _PATTERNS.items():
            m = pat.search(rest)
            if not m:
                continue
            if pat.groups:
                try:
                    found[kw] = int(m.group(1))
                except (IndexError, ValueError):
                    found[kw] = True
            else:
                found[kw] = True
            rest = pat.sub("", rest)
        # purity check: sentence must contain nothing but keywords/punct.
        # {} is the ink/lore symbol (notation), not prose -- strip it too.
        if found and not re.sub(r"[\s,;:+\-]+", "", rest.replace("{}", "")):
            out.update(found)
    return out


def residual_prose(text):
    """Ability text remaining after removing reminder text, printed keyword
    lines, and named-ability markers. Empty => keywords fully cover the card.
    Non-empty => needs a schema entry or hand-written logic."""
    t = _REMINDER.sub("", clean_text(text))
    t = _BRACKET_NAME.sub(" ", t)
    printed = parse_printed_keywords(text)
    for kw in printed:
        t = _PATTERNS[kw].sub("", t, count=1)
    t = re.sub(r"[\s,.;:+\-]+", " ", t).strip()
    return t


# Backwards-compatible aliases
parse_keywords = parse_printed_keywords
residual_text = residual_prose


if __name__ == "__main__":
    import json, sys
    from collections import Counter
    path = sys.argv[1] if len(sys.argv) > 1 else "master_legal_cardlist.json"
    db = json.load(open(path))
    kw_count = Counter()
    vanilla = kw_only = needs_logic = 0
    for c in db.values():
        desc = c.get("Description", "")
        if not clean_text(desc).strip():
            vanilla += 1
            continue
        kws = parse_printed_keywords(desc)
        for k in kws:
            kw_count[k] += 1
        if not residual_prose(desc):
            kw_only += 1
        else:
            needs_logic += 1
    total = len(db)
    print(f"Total cards: {total}")
    print(f"  vanilla (no text):        {vanilla:4d}  ({100*vanilla/total:.0f}%)")
    print(f"  keyword-only (0 code):    {kw_only:4d}  ({100*kw_only/total:.0f}%)")
    print(f"  needs schema/hand code:   {needs_logic:4d}  ({100*needs_logic/total:.0f}%)")
    print("\nPrinted keyword frequency across pool:")
    for k, v in kw_count.most_common():
        print(f"  {k:15s} {v}")
