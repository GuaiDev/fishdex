"""Is this the same watercourse? Name identity that works for any jurisdiction's data.

A gauge network names its stations ("CREDIT RIVER AT ERINDALE") and a hydro
network names its flowlines ("Credit River"). Neither carries an ID the other
shares, so the join is the water's own name. Two names are the same water when
they match after case, accents, punctuation and the generic word for the kind
of water are folded together. "River", "Rivière" and "Rio" fold to one word,
"Creek" and "Ruisseau" to another (a leading kind word moves to the end, so
"Rivière Rouge" meets "Rouge River"), and the two words are not folded into each
other: a Credit Creek and a Credit River are different waters.

Add a language's words to `_KIND_WORDS` as its jurisdictions arrive.
"""

import re
import unicodedata

# Folded word -> the words that mean it. Only kinds of flowing water.
_KIND_WORDS: dict[str, tuple[str, ...]] = {
    "river": ("river", "riviere", "rio", "fleuve", "r"),
    "creek": ("creek", "cr", "ruisseau", "ruis", "arroyo"),
    "brook": ("brook", "br"),
    "stream": ("stream",),
}
_FOLD = {word: kind for kind, words in _KIND_WORDS.items() for word in words}
_ARTICLES = {"the", "la", "le", "les", "de", "du", "des", "of"}


def normalize_water_name(name: str | None) -> str | None:
    """A comparable form of a water's name, or None when there is nothing to compare."""
    if not name:
        return None
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\(.*?\)", " ", text)
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in _ARTICLES]
    words = [_FOLD.get(w, w) for w in words]
    if len(words) > 1 and words[0] in _KIND_WORDS:
        words = words[1:] + words[:1]  # French order: "Rivière Rouge" is the Rouge River
    return " ".join(words) or None


def same_water(a: str | None, b: str | None) -> bool:
    na, nb = normalize_water_name(a), normalize_water_name(b)
    return na is not None and na == nb
