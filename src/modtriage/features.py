"""Cheap, deterministic text/metadata signals.

Used by (a) the rule-based metadata agent, and (b) the offline mock provider
that stands in for the LLM agents in tests, CI and key-less demos. These are
NOT meant to be a good classifier on their own - that's the point of the eval.
"""

from __future__ import annotations

import re

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
WORD_RE = re.compile(r"[a-z']+")

# Deliberately small, mild lexicons (no slurs are stored in this repo).
LEXICON: dict[str, dict[str, float]] = {
    "harassment": {
        "idiot": 0.8,
        "idiots": 0.8,
        "stupid": 0.6,
        "moron": 0.85,
        "morons": 0.85,
        "dumb": 0.5,
        "loser": 0.7,
        "losers": 0.7,
        "pathetic": 0.6,
        "clown": 0.5,
        "fool": 0.5,
        "fools": 0.5,
        "imbecile": 0.8,
        "ignorant": 0.4,
        "trash": 0.5,
        "worthless": 0.7,
        "shut up": 0.6,
        "brain-dead": 0.8,
        "hypocrite": 0.4,
        "liar": 0.4,
        "scum": 0.8,
        "disgusting": 0.4,
        "crazy": 0.3,
        "jerk": 0.6,
    },
    "violence": {
        "kill": 0.6,
        "shoot": 0.6,
        "hang": 0.5,
        "beat you": 0.8,
        "hurt you": 0.8,
        "find where you live": 0.95,
        "find out where you live": 0.95,
        "you'll regret": 0.7,
        "watch your back": 0.85,
        "burn": 0.3,
        "die": 0.4,
        "should be shot": 0.95,
        "put a bullet": 0.95,
        "lynch": 0.9,
    },
    "sexual": {
        "sex": 0.35,
        "sexy": 0.4,
        "porn": 0.8,
        "nude": 0.6,
        "naked": 0.5,
        "orgasm": 0.8,
        "genitals": 0.7,
        "horny": 0.7,
    },
    "profanity": {
        "damn": 0.2,
        "hell": 0.15,
        "crap": 0.3,
        "shit": 0.6,
        "fuck": 0.8,
        "fucking": 0.8,
        "ass": 0.5,
        "bastard": 0.6,
        "bitch": 0.75,
        "piss": 0.4,
    },
    "spam": {
        "buy now": 0.8,
        "click here": 0.8,
        "free money": 0.9,
        "work from home": 0.6,
        "discount": 0.4,
        "subscribe": 0.3,
        "promo code": 0.8,
        "dm me": 0.4,
    },
}

IDENTITY_TERMS = {
    "muslims",
    "muslim",
    "jews",
    "jewish",
    "christians",
    "immigrants",
    "gays",
    "gay",
    "women",
    "men",
    "blacks",
    "whites",
    "asians",
    "mexicans",
    "latinos",
    "trans",
    "transgender",
    "atheists",
    "catholics",
    "refugees",
    "disabled",
    "lesbians",
}
HATE_PATTERNS = [
    re.compile(r"\ball (\w+ )?(" + "|".join(sorted(IDENTITY_TERMS)) + r") (are|should)\b", re.I),
    re.compile(
        r"\b("
        + "|".join(sorted(IDENTITY_TERMS))
        + r") (are|is) (all )?(animals|vermin|subhuman|criminals|the problem)\b",
        re.I,
    ),
    re.compile(r"\bgo back to (your|their) (own )?country\b", re.I),
    re.compile(r"\b(deport|ban) (all|the) (" + "|".join(sorted(IDENTITY_TERMS)) + r")\b", re.I),
]
SECOND_PERSON = re.compile(r"\b(you|you're|youre|your|u|ur)\b", re.I)
QUOTE_CUES = re.compile(r"(\"[^\"]{6,}\"|\bhe said\b|\bshe said\b|\bthey said\b|\bcalled (me|him|her|them)\b)", re.I)
COUNTER_CUES = re.compile(
    r"\b(not okay|not ok|unacceptable|don't call|stop calling|no need to insult|that's racist|that is racist|be respectful|reported)\b",
    re.I,
)


def lexicon_hits(text: str) -> dict[str, list[tuple[str, float]]]:
    low = text.lower()
    words = set(WORD_RE.findall(low))
    hits: dict[str, list[tuple[str, float]]] = {}
    for cat, lex in LEXICON.items():
        for term, w in lex.items():
            found = (term in low) if " " in term or "-" in term else (term in words)
            if found:
                hits.setdefault(cat, []).append((term, w))
    for pat in HATE_PATTERNS:
        m = pat.search(text)
        if m:
            hits.setdefault("hate", []).append((m.group(0), 0.85))
    return hits


def category_scores(text: str) -> dict[str, float]:
    """Noisy-OR of lexicon weights per category, in [0, 1]."""
    out: dict[str, float] = {}
    for cat, items in lexicon_hits(text).items():
        p_none = 1.0
        for _, w in items:
            p_none *= 1 - w
        out[cat] = round(1 - p_none, 4)
    return out


def style_features(text: str) -> dict[str, float]:
    letters = [c for c in text if c.isalpha()]
    caps = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
    return {
        "length": float(len(text)),
        "caps_ratio": round(caps, 3),
        "exclamations": float(text.count("!")),
        "urls": float(len(URL_RE.findall(text))),
        "repeated_chars": float(bool(re.search(r"(.)\1{4,}", text))),
        "second_person": float(bool(SECOND_PERSON.search(text))),
        "quote_cue": float(bool(QUOTE_CUES.search(text))),
        "counter_cue": float(bool(COUNTER_CUES.search(text))),
    }
