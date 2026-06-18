from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from .analyze import _apply_criteria
from .store import get_all_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def validate_criteria(criteria: dict, features: list[dict] | None = None) -> dict:
    """Score a criteria dict against detective_features. Returns precision/recall report."""
    if features is None:
        features = get_all_features()

    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]

    tp_rows = [f for f in prime if _apply_criteria(f, criteria)]
    fp_rows = [f for f in control if _apply_criteria(f, criteria)]
    fn_rows = [f for f in prime if not _apply_criteria(f, criteria)]

    tp = len(tp_rows)
    fp = len(fp_rows)
    fn = len(fn_rows)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / len(prime) if prime else 0.0

    fp_by_sector = Counter(f.get("sector") or "Unknown" for f in fp_rows)
    missed = [
        {"date": f["date"], "ticker": f["ticker"], "rsi": f.get("rsi"), "adx": f.get("adx"),
         "price_above_ema50": f.get("price_above_ema50"), "bb_pct_b": f.get("bb_pct_b")}
        for f in fn_rows
    ]

    return {
        "criteria": criteria,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "total_prime": len(prime),
        "fp_by_sector": dict(fp_by_sector.most_common()),
        "missed_primes": sorted(missed, key=lambda r: (r["date"], r["ticker"])),
    }


def print_report(report: dict) -> None:
    c = report["criteria"]
    print(f"\n{'='*60}")
    print(f"Criteria: {json.dumps(c, indent=2)}")
    print(f"\nPrecision : {report['precision']:.1%}  ({report['true_positives']} TP / {report['true_positives'] + report['false_positives']} fired)")
    print(f"Recall    : {report['recall']:.1%}  ({report['true_positives']} / {report['total_prime']} prime tickers caught)")
    print(f"False positives: {report['false_positives']}")
    print(f"Missed primes : {report['false_negatives']}")

    if report["fp_by_sector"]:
        print(f"\nFalse positives by sector:")
        for sector, count in list(report["fp_by_sector"].items())[:10]:
            print(f"  {sector:<30} {count}")

    if report["missed_primes"]:
        print(f"\nMissed prime tickers (first 20):")
        for r in report["missed_primes"][:20]:
            print(f"  {r['date']}  {r['ticker']:<8}  RSI={r['rsi']}  ADX={r['adx']}  EMA50+={r['price_above_ema50']}  BB%B={r['bb_pct_b']}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate criteria against the feature matrix")
    parser.add_argument(
        "--criteria",
        required=True,
        help='JSON string or path to .json file. E.g. \'{"rsi_min": 42, "price_above_ema50": true}\'',
    )
    args = parser.parse_args()

    criteria_input = args.criteria.strip()
    if criteria_input.endswith(".json") and Path(criteria_input).exists():
        criteria = json.loads(Path(criteria_input).read_text())
    else:
        criteria = json.loads(criteria_input)

    report = validate_criteria(criteria)
    print_report(report)
