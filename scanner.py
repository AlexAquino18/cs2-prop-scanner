"""
CS2 player prop line discrepancy scanner.

Compares CS2 player props across PrizePicks, Underdog, and (once
available) Chalkboard, and reports where the lines disagree.

Usage:
    python scanner.py                          # table output, default threshold
    python scanner.py --threshold 1.0           # only report gaps >= 1.0
    python scanner.py --sources prizepicks,underdog
    python scanner.py --json out.json           # also write JSON
    python scanner.py --csv out.csv              # also write CSV
    python scanner.py --dump-raw underdog        # print parsed Underdog CS2 items and exit
"""
import argparse
import csv
import json
import sys

import requests

import config
import matching
from sources import prizepicks, underdog, chalkboard

SOURCE_FETCHERS = {
    "prizepicks": prizepicks.fetch,
    "underdog": underdog.fetch,
    "chalkboard": chalkboard.fetch,
}


def fetch_all(source_names: list, session: requests.Session):
    all_props = []
    for name in source_names:
        fetcher = SOURCE_FETCHERS[name]
        try:
            props = fetcher(session)
        except NotImplementedError as e:
            print(f"[{name}] skipped: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"[{name}] fetch failed: {e}", file=sys.stderr)
            continue
        print(f"[{name}] fetched {len(props)} CS2 props", file=sys.stderr)
        all_props.extend(props)
    return all_props


def print_table(discrepancies: list):
    if not discrepancies:
        print("No discrepancies found at or above the threshold.")
        return

    headers = ["Player", "Team", "Stat", "Map", "Spread", "Lines"]
    rows = []
    for d in discrepancies:
        row = d.as_row()
        lines_str = " | ".join(
            f"{src}: {line}" for src, line in sorted(row["all_lines"].items())
        )
        rows.append([
            row["player"], row["team"], row["stat"], row["map_range"],
            f"{row['spread']:.1f}", lines_str,
        ])

    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*r))


def write_json(discrepancies: list, path: str):
    rows = [d.as_row() for d in discrepancies]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"Wrote {len(rows)} discrepancies to {path}", file=sys.stderr)


def write_csv(discrepancies: list, path: str):
    rows = [d.as_row() for d in discrepancies]
    if not rows:
        print("No discrepancies to write to CSV.", file=sys.stderr)
        return
    fieldnames = ["player", "team", "stat", "map_range", "spread",
                  "low_book", "low_line", "high_book", "high_line", "all_lines"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row = dict(row)
            row["all_lines"] = json.dumps(row["all_lines"])
            writer.writerow(row)
    print(f"Wrote {len(rows)} discrepancies to {path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--sources", default="prizepicks,underdog",
        help="Comma-separated list of sources to check (default: prizepicks,underdog)",
    )
    parser.add_argument(
        "--threshold", type=float, default=config.DEFAULT_LINE_DIFF_THRESHOLD,
        help=f"Minimum line spread to report (default: {config.DEFAULT_LINE_DIFF_THRESHOLD})",
    )
    parser.add_argument("--json", metavar="PATH", help="Write results to a JSON file")
    parser.add_argument("--csv", metavar="PATH", help="Write results to a CSV file")
    parser.add_argument(
        "--dump-raw", metavar="SOURCE", choices=["underdog"],
        help="Print parsed CS2 items from a source and exit — useful for inspecting field mappings",
    )
    args = parser.parse_args()

    if args.dump_raw:
        items = underdog.fetch(dump_raw=True)
        print(json.dumps(items[:5], indent=2))  # first 5 is plenty to see the shape
        print(f"\n({len(items)} total items)", file=sys.stderr)
        return

    source_names = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(source_names) - set(SOURCE_FETCHERS)
    if unknown:
        parser.error(f"Unknown source(s): {', '.join(unknown)}. Choose from: {', '.join(SOURCE_FETCHERS)}")

    session = requests.Session()
    all_props = fetch_all(source_names, session)

    if not all_props:
        print("No props fetched from any source — nothing to compare.", file=sys.stderr)
        return

    groups = matching.group_props(all_props)
    discrepancies = matching.find_discrepancies(groups, threshold=args.threshold)

    print_table(discrepancies)

    if args.json:
        write_json(discrepancies, args.json)
    if args.csv:
        write_csv(discrepancies, args.csv)


if __name__ == "__main__":
    main()
