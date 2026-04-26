"""
Compute per-test precision/recall/F1 vs ground truth.

Usage:
  python backend/evaluate.py --name test_001
  python backend/evaluate.py --all                # evaluates test_001..test_005
"""

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GT_DIR = PROJECT_ROOT / "test" / "ground_truth"
OUTPUT_DIR = PROJECT_ROOT / "output"


def in_intervals(t, intervals):
    return any(s <= t < e for s, e in intervals)


def evaluate(name):
    gt_path = GT_DIR / f"{name}.json"
    pred_path = OUTPUT_DIR / name / "segments.json"

    if not pred_path.exists():
        return None

    gt = json.loads(gt_path.read_text())
    pred = json.loads(pred_path.read_text())

    gt_ads = [
        (a["final_video_ad_start_seconds"], a["final_video_ad_end_seconds"])
        for a in gt["inserted_ads"]
    ]
    gt_dur = gt["output_duration_seconds"]
    nc_intervals = [
        (s["start"], s["end"]) for s in pred["segments"] if s["type"] == "non_content"
    ]
    pred_dur = pred["duration_seconds"]
    total = int(min(gt_dur, pred_dur))

    tp = fp = fn = tn = 0
    for t in range(total):
        is_ad = in_intervals(t + 0.5, gt_ads)
        is_nc = in_intervals(t + 0.5, nc_intervals)
        if is_ad and is_nc:
            tp += 1
        elif not is_ad and is_nc:
            fp += 1
        elif is_ad and not is_nc:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    accuracy = (tp + tn) / total

    per_ad = []
    for gs, ge in gt_ads:
        overlap = sum(max(0, min(ge, pe) - max(gs, ps)) for ps, pe in nc_intervals)
        per_ad.append({
            "start": gs, "end": ge, "duration": ge - gs,
            "overlap": overlap, "recall_pct": overlap / (ge - gs) * 100 if ge > gs else 0,
        })

    return {
        "name": name,
        "duration": pred_dur,
        "n_ads_gt": len(gt_ads),
        "n_segments": len(pred["segments"]),
        "n_non_content": len(nc_intervals),
        "non_content_total": sum(e - s for s, e in nc_intervals),
        "ad_total_gt": gt["total_ads_duration_seconds"],
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1,
        "per_ad": per_ad,
    }


def print_report(r):
    print(f"\n=== {r['name']} (duration {r['duration']:.0f}s, {r['n_ads_gt']} ads) ===")
    print(f"  Final segments: {r['n_segments']} ({r['n_non_content']} non_content, {r['non_content_total']:.1f}s)")
    print(f"  Accuracy:  {r['accuracy']*100:5.1f}%   Precision: {r['precision']*100:5.1f}%")
    print(f"  Recall:    {r['recall']*100:5.1f}%   F1:        {r['f1']*100:5.1f}%")
    for i, ad in enumerate(r["per_ad"]):
        bar = "█" * int(ad["recall_pct"] / 5) + "░" * (20 - int(ad["recall_pct"] / 5))
        print(f"  Ad {i+1} [{ad['start']:7.1f}-{ad['end']:7.1f}] {bar} {ad['recall_pct']:5.1f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="single test name (e.g. test_001)")
    parser.add_argument("--all", action="store_true", help="evaluate all 5 tests")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    names = [f"test_{n}" for n in ("001", "002", "003", "004", "005")] if args.all else [args.name]
    results = [r for r in (evaluate(n) for n in names) if r]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print_report(r)
        if len(results) > 1:
            avg_p = sum(r["precision"] for r in results) / len(results)
            avg_r = sum(r["recall"] for r in results) / len(results)
            avg_f = sum(r["f1"] for r in results) / len(results)
            avg_a = sum(r["accuracy"] for r in results) / len(results)
            print(f"\n=== AVERAGE across {len(results)} tests ===")
            print(f"  Accuracy: {avg_a*100:.1f}%  Precision: {avg_p*100:.1f}%  Recall: {avg_r*100:.1f}%  F1: {avg_f*100:.1f}%")


if __name__ == "__main__":
    main()
