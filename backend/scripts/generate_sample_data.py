"""Generate synthetic log files (JSONL / JSON / CSV) for upload or offline experiments.

Usage:
    python scripts/generate_sample_data.py --scenario mixed --minutes 120 --out sample_data
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.data.generator import SCENARIOS, generate_logs  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    p.add_argument("--minutes", type=int, default=120)
    p.add_argument("--rpm", type=int, default=40, help="simulated requests per minute")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--end", help="ISO end time (default: now, UTC)")
    p.add_argument("--out", default="sample_data")
    p.add_argument("--formats", default="jsonl,json,csv")
    a = p.parse_args()

    end = datetime.fromisoformat(a.end.replace("Z", "+00:00")) if a.end else datetime.now(timezone.utc)
    logs = generate_logs(a.scenario, a.minutes, a.rpm, a.seed, end)
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"{a.scenario}_logs")
    formats = a.formats.split(",")
    if "jsonl" in formats:
        with open(base + ".jsonl", "w", encoding="utf-8") as f:
            for l in logs:
                f.write(json.dumps(l) + "\n")
    if "json" in formats:
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=1)
    if "csv" in formats:
        with open(base + ".csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["timestamp", "severity", "service", "host", "trace_id", "message",
                                              "metadata"])
            w.writeheader()
            for l in logs:
                w.writerow({**l, "metadata": json.dumps(l["metadata"])})
    print(f"wrote {len(logs)} logs for scenario '{a.scenario}' to {base}.{{{a.formats}}}")


if __name__ == "__main__":
    main()
