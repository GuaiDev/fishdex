"""The sentences signals use to explain themselves.

Every reason and summary a person reads about their own profile is built here,
so a wrong guess is worded the same wherever it surfaces. Plain statements of
what the angler has done; none of them says anything about fish.
"""

WATER_TYPE_PLURAL: dict[str, str] = {
    "river": "rivers",
    "creek": "creeks",
    "lake": "lakes",
    "pond": "ponds",
    "great_lake": "the Great Lakes",
}

EARLY_READ = "Early read: {sentence}"

WATER_TYPE_MOSTLY = "you mostly fish {water}"
WATER_TYPE_ALSO = "you fish {water} too"
WATER_TYPE_MIX = "you fish a mix of {first} and {second}"
WATER_TYPE_NONE = "not enough logged trips on mapped water to tell what water you prefer yet"

EXPLORE_EXPLORER = "you tend to try new water"
EXPLORE_REGULAR = "you tend to go back to the same water"
EXPLORE_MIXED = "you mix new water with places you know"
EXPLORE_DECLARED = " (you set this)"

REASON_UNVISITED = "a place you haven't fished, and you tend to try new water"
REASON_FAVOURITE = "one of your regular spots, and you tend to go back to the same water"
REASON_VISITED_ONCE = "you've fished near here once"

NOT_PERSONALISED = (
    "New to me needs a little to go on. Log a few trips, or set how much you like trying "
    "new water, and it will start following the water you fish."
)


def water_plural(water_type: str) -> str:
    return WATER_TYPE_PLURAL.get(water_type, water_type.replace("_", " ") + "s")
