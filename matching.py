"""
Groups PropLine objects from different books into the same real-world
bet (same player, same stat, same map range) and flags discrepancies.

Matching strategy:
  1. Exact match on (player_key, stat_key, map_range) — handles the
     large majority of cases since CS2 books mostly use the player's
     handle, which normalizes consistently.
  2. For anything left unmatched, fuzzy-match player_key within the
     same (stat_key, map_range) bucket across sources (handles minor
     spelling differences like "ZywOo" vs "Zywoo", "device" vs
     "dev1ce" typos, etc.) using a similarity cutoff.
"""
from collections import defaultdict
from difflib import SequenceMatcher

import config
from models import PropLine, Discrepancy


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _map_token(prop: PropLine) -> str:
    return prop.map_range or "full"


def preferred_map_range(group: dict) -> str | None:
    tokens = {_map_token(p) for p in group.values()}
    if "1" in tokens:
        return "1"
    return next(iter(group.values())).map_range


def group_props(all_props: list) -> list:
    """
    Returns a list of groups, where each group is {source: PropLine}
    representing the same bet across the books that offer it.
    """
    exact_groups = defaultdict(dict)  # match_key -> {source: PropLine}
    for prop in all_props:
        key = prop.match_key()
        # If a source somehow lists the same bet twice, keep the first.
        exact_groups[key].setdefault(prop.source, prop)

    groups = list(exact_groups.values())

    # Second pass: fuzzy-merge single-source groups that share a
    # (stat_key, map_range) bucket but weren't caught by exact match.
    by_bucket = defaultdict(list)  # (stat_key, map_range) -> [group_index,...]
    for idx, group in enumerate(groups):
        any_prop = next(iter(group.values()))
        by_bucket[(any_prop.stat_key, any_prop.map_range)].append(idx)

    merged = set()
    for bucket, indices in by_bucket.items():
        for i in range(len(indices)):
            gi = indices[i]
            if gi in merged:
                continue
            for j in range(i + 1, len(indices)):
                gj = indices[j]
                if gj in merged or gi in merged:
                    continue
                group_a, group_b = groups[gi], groups[gj]
                # Only attempt merge if they don't already share a source
                # (that would mean two different players on the same book
                # in the same bucket — not a match).
                if set(group_a) & set(group_b):
                    continue
                name_a = next(iter(group_a.values())).player_key
                name_b = next(iter(group_b.values())).player_key
                if _similar(name_a, name_b) >= config.PLAYER_NAME_FUZZY_CUTOFF:
                    group_a.update(group_b)
                    merged.add(gj)

    # BO1: one book posts "Kills" (no map range) and the other posts
    # "Map 1 Kills". Only merge when that player/stat has no maps 1-2
    # line, so we don't pair a series total with a single map.
    live = [idx for idx, _ in enumerate(groups) if idx not in merged]
    by_player_stat = defaultdict(list)
    for idx in live:
        any_prop = next(iter(groups[idx].values()))
        by_player_stat[(any_prop.player_key, any_prop.stat_key)].append(idx)
    for indices in by_player_stat.values():
        tokens = {_map_token(next(iter(groups[i].values()))) for i in indices}
        if "1-2" in tokens or "1-3" in tokens:
            continue
        ones = [i for i in indices if _map_token(next(iter(groups[i].values()))) == "1"]
        fulls = [i for i in indices if _map_token(next(iter(groups[i].values()))) == "full"]
        for gi in ones:
            if gi in merged:
                continue
            for gj in fulls:
                if gj in merged:
                    continue
                group_a, group_b = groups[gi], groups[gj]
                if set(group_a) & set(group_b):
                    continue
                group_a.update(group_b)
                merged.add(gj)

    return [g for idx, g in enumerate(groups) if idx not in merged]


def find_discrepancies(groups: list, threshold: float = None) -> list:
    threshold = config.DEFAULT_LINE_DIFF_THRESHOLD if threshold is None else threshold
    discrepancies = []

    for group in groups:
        if len(group) < 2:
            continue  # need at least 2 books to compare
        lines = [p.line for p in group.values()]
        spread = max(lines) - min(lines)
        if spread < threshold:
            continue

        any_prop = next(iter(group.values()))
        discrepancies.append(
            Discrepancy(
                player=any_prop.player_raw,
                team=any_prop.team,
                stat=any_prop.stat_key,
                map_range=preferred_map_range(group),
                lines=dict(group),
                spread=spread,
            )
        )

    discrepancies.sort(key=lambda d: d.spread, reverse=True)
    return discrepancies
