"""Card database loading and deck parsing."""
import json, os, re

from .keywords import parse_printed_keywords, residual_prose

# Cards missing from the master JSON get invented stats here. Currently empty:
# every card in both decks resolves against the master JSON. If a future
# decklist references a card the JSON lacks, add a stats dict here (see the
# Card fields in the class below) with "_PLACEHOLDER": True to flag it loudly.
PLACEHOLDER_CARDS = {}


class Card:
    __slots__ = ("name", "base_name", "version", "cost", "inkable", "ink_type",
                 "ink_types",
                 "card_type", "classifications", "strength", "willpower", "lore",
                 "move_cost", "text", "placeholder",
                 "keywords", "residual", "schema_abilities")

    def __init__(self, name, raw):
        self.name = name
        parts = name.split(" - ", 1)
        self.base_name = parts[0]
        self.version = parts[1] if len(parts) > 1 else ""
        self.cost = int(raw["Cost Ink"])
        self.inkable = raw.get("InkwellIcononCard", "No") == "Yes"
        _raw_ink = raw.get("InkType", "") or ""
        # A card may list multiple inks separated by ';' (e.g. "Amber;Ruby").
        # Store them as a set for correct membership/legality tests, and keep
        # the raw string for display and backward compatibility.
        self.ink_types = frozenset(i.strip() for i in _raw_ink.split(";") if i.strip())
        self.ink_type = _raw_ink
        self.card_type = raw["CardType"]              # Character / Action / Item / Location
        cls = raw.get("Classification", "") or ""
        self.classifications = set(c.strip() for c in cls.split(";") if c.strip())
        self.strength = int(raw["Strength"]) if raw.get("Strength") not in (None, "",) else None
        self.willpower = int(raw["Willpower"]) if raw.get("Willpower") not in (None, "",) else None
        self.lore = int(raw["Lore Value"]) if raw.get("Lore Value") not in (None, "",) else 0
        self.move_cost = int(raw["Move Cost"]) if raw.get("Move Cost") not in (None, "",) else None
        self.text = re.sub(r"<[^>]+>", "", raw.get("Description", "") or "")
        self.placeholder = raw.get("_PLACEHOLDER", False)
        # Phase 1: printed keywords parsed once at load
        desc = raw.get("Description", "") or ""
        self.keywords = parse_printed_keywords(desc)
        self.residual = residual_prose(desc)
        # Phase 2: schema abilities attached by abilities_data loader (or None)
        self.schema_abilities = None

    # -- keyword accessors ------------------------------------------------
    def kw(self, name):
        """True/False for boolean keywords; int or None for scaling ones."""
        return self.keywords.get(name)

    @property
    def shift_ink(self):        # Shift N -> N, else None
        v = self.keywords.get("Shift")
        return v if isinstance(v, int) else None

    @property
    def singer_value(self):     # value this character counts as when singing
        v = self.keywords.get("Singer")
        return max(self.cost, v) if isinstance(v, int) else self.cost

    @property
    def sing_together_cost(self):  # Sing Together N -> N, else None
        v = self.keywords.get("Sing Together")
        return v if isinstance(v, int) else None

    @property
    def is_character(self): return self.card_type == "Character"
    @property
    def is_action(self): return self.card_type == "Action"
    @property
    def is_location(self): return self.card_type == "Location"
    @property
    def is_item(self): return self.card_type == "Item"
    @property
    def is_song(self): return self.is_action and "sing this song" in self.text.lower()
    @property
    def is_toy(self): return "Toy" in self.classifications

    def __repr__(self):
        return f"<{self.name}>"


class CardDB:
    def __init__(self, json_path):
        with open(json_path) as f:
            raw = json.load(f)
        raw.update(PLACEHOLDER_CARDS)
        self.cards = {}
        self._lower = {}
        for name, data in raw.items():
            try:
                c = Card(name, data)
            except (KeyError, ValueError):
                continue  # skip malformed entries
            self.cards[name] = c
            self._lower[name.lower()] = name

    def get(self, name):
        key = self._lower.get(name.strip().lower())
        return self.cards[key] if key else None


def parse_decklist(path, db):
    """Returns (list_of_Card_with_duplicates, errors, warnings)."""
    cards, errors, warnings = [], [], []
    with open(path) as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                qty_s, name = line.split(" ", 1)
                qty = int(qty_s)
            except ValueError:
                errors.append(f"line {ln}: cannot parse '{line}'")
                continue
            card = db.get(name)
            if card is None:
                errors.append(f"line {ln}: card not found in DB: '{name}'")
                continue
            if card.placeholder:
                warnings.append(f"'{card.name}' uses PLACEHOLDER stats -- results unreliable until real data supplied")
            if qty > 4:
                warnings.append(f"line {ln}: {qty} copies of '{name}' exceeds the 4-copy limit")
            cards.extend([card] * qty)
    total = len(cards)
    if total != 60:
        warnings.append(f"deck has {total} cards (expected 60)")
    inks = set().union(*(c.ink_types for c in cards)) if cards else set()
    if len(inks) > 2:
        warnings.append(f"deck has {len(inks)} ink colors: {sorted(inks)}")
    return cards, errors, warnings
