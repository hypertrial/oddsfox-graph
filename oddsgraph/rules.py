from __future__ import annotations

from .queries import q


STAGE_RULES = (
    ("^Will (.*) win the 2026 FIFA World Cup\\?$", 5),
    ("^Will (.*) reach the 2026 FIFA World Cup final\\?$", 4),
    ("^Will (.*) reach the Semifinals at the 2026 FIFA World Cup\\?$", 3),
    ("^Will (.*) reach the Quarterfinals at the 2026 FIFA World Cup\\?$", 2),
    ("^Will (.*) reach the Round of 16 at the 2026 FIFA World Cup\\?$", 1),
)

SINGLE_WINNER_SLUGS = (
    "world-cup-winner",
    "world-cup-golden-boot-winner",
    "which-continent-will-win-the-world-cup",
    "world-cup-bronze-ball-winner-20260603194938828",
    "world-cup-bronze-boot-winner-20260603200444388",
    "world-cup-fair-play-award-winner-20260603201520240",
    "world-cup-golden-ball-winner-20260603194031758",
    "world-cup-golden-glove-winner-20260603195306910",
    "world-cup-silver-ball-winner-20260603194459107",
    "world-cup-silver-boot-winner-20260603195826159",
    "world-cup-young-player-award-winner-20260602160649063",
)

SINGLE_WINNER_SLUG_PATTERNS = ("world-cup-group-%-winner",)


def stage_rules_values_sql() -> str:
    return ",\n".join(f"('{q(pattern)}', {rank})" for pattern, rank in STAGE_RULES)


def single_winner_values_sql() -> str:
    return ",\n".join(f"('{q(slug)}')" for slug in SINGLE_WINNER_SLUGS)


def single_winner_pattern_sql(column: str) -> str:
    return " OR ".join(f"{column} LIKE '{q(pattern)}'" for pattern in SINGLE_WINNER_SLUG_PATTERNS)
