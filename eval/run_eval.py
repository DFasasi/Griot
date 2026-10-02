"""Compare Griot with baselines. Usage:
    uv run python eval/run_eval.py [catalog.jsonl] [--pairs 50] [--n 8] [--out eval/results.md]
Without a catalog it runs on the synthetic catalog (pipeline sanity check, not evidence).
"""

import argparse
from pathlib import Path

from griot_core.evaluate import run, table
from griot_core.schema import TrackFeatures
from griot_core.synthetic import make_tracks

ap = argparse.ArgumentParser()
ap.add_argument("catalog", nargs="?")
ap.add_argument("--pairs", type=int, default=50)
ap.add_argument("--n", type=int, default=8)
ap.add_argument("--out", default="eval/results.md")
args = ap.parse_args()

if args.catalog:
    lines = Path(args.catalog).read_text().splitlines()
    tracks = [TrackFeatures.model_validate_json(x) for x in lines if x.strip()]
    source = f"`{args.catalog}` ({len(tracks)} tracks)"
else:
    tracks = make_tracks(n=3000, seed=42)
    source = "synthetic catalog (3000 tracks) — sanity check only"

md = f"# Bridge evaluation\n\nCatalog: {source}. "
md += f"{args.pairs} random waypoint pairs, {args.n} bridge tracks.\n\n"
md += table(run(tracks, pairs=args.pairs, n=args.n)) + "\n"
Path(args.out).write_text(md)
print(md)
