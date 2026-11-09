"""Seeded blind sampling and transparent AI/human evaluation of real reports.

Run from the repository root: python -m scripts.review_traffy --help
"""

import argparse
import hashlib
import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

from src.ingest_traffy import records_hash
from src.traffy_filter import RULE_VERSION, classify_flood

LABELS = ("flood", "non_flood", "uncertain")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_population(data_dir, start, end):
    reports, partitions = {}, []
    for path in sorted((Path(data_dir) / "incoming/traffy").glob("dt=*/traffy_*.json")):
        # Multiyear backfills can contain thousands of daily files. Do not
        # load unrelated years to review a three-day study snapshot.
        path_date = path.parent.name.removeprefix("dt=")
        if not start <= path_date <= end:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date_bkk") != path_date:
            raise ValueError("Partition path and envelope Bangkok date disagree")
        if not start <= payload["date_bkk"] <= end:
            continue
        if payload.get("is_mock") is not False:
            raise ValueError("Review evidence requires real source records, not fixtures")
        digest = records_hash(payload["data"])
        if digest != payload.get("records_sha256"):
            raise ValueError(f"Partition hash mismatch: {path}")
        partitions.append({"date_bkk": payload["date_bkk"], "record_count": len(payload["data"]),
                           "records_sha256": digest})
        for row in payload["data"]:
            if row["ticket_id"] in reports:
                raise ValueError("Duplicate report ID across source partitions")
            reports[row["ticket_id"]] = row
    expected_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if len(partitions) != expected_days or expected_days < 1:
        raise ValueError("Every requested Bangkok date must have exactly one ingested partition")
    return reports, partitions


def sample(data_dir, start, end, count, seed, output):
    reports, partitions = load_population(data_dir, start, end)
    ids = sorted(reports)
    if count < 1 or count > len(ids):
        raise ValueError("Sample size must be between 1 and the population size")
    selected = random.Random(seed).sample(ids, count)
    # No predictions/categories in this file: label the descriptions first.
    payload = {
        "method": "simple_random_without_replacement", "seed": seed,
        "sample_size": count, "population_count": len(ids),
        "start_date_bkk": start, "end_date_bkk": end,
        "population_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
        "source_partitions": partitions,
        "reports": [{"ticket_id": key, "description": reports[key]["description"]}
                    for key in selected],
    }
    output = Path(output)
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous != payload:
            raise ValueError("Existing sample is a different snapshot; choose a new --output path to preserve review work")
    else:
        write_json(output, payload)
    human_path = Path(output).with_name("human_review_100.json")
    if not human_path.exists():
        write_json(human_path, {
            "reviewer_type": "human", "reviewer_name": "", "status": "pending",
            "reports": [{"ticket_id": key, "label": None, "rationale": ""} for key in selected],
        })
    return {"sample": str(output), "human_template": str(human_path),
            "population_count": len(ids), "sample_size": count, "seed": seed}


def flood_precision(matrix):
    """Precision over every predicted positive, exposing unresolved references.

    When some predicted positives have uncertain reference labels, return bounds
    instead of quietly deleting them from the denominator of the headline metric.
    """
    tp, fp = matrix["flood"]["flood"], matrix["non_flood"]["flood"]
    unresolved = matrix["uncertain"]["flood"]
    denominator = tp + fp + unresolved
    value = tp / denominator if denominator and not unresolved else None
    return {
        "precision": value,
        "precision_status": "no_predicted_positives" if not denominator else (
            "unresolved_positive_labels" if unresolved else "scored"),
        "precision_counts": {"true_positives": tp, "false_positives": fp,
                             "unresolved_predicted_positives": unresolved,
                             "all_predicted_positives": denominator},
        "precision_bounds": {"lower": tp / denominator if denominator else None,
                             "upper": (tp + unresolved) / denominator if denominator else None},
        "resolved_positive_coverage": (tp + fp) / denominator if denominator else None,
    }


def evaluate(data_dir, sample_path, labels_path, output_dir):
    sampled = json.loads(Path(sample_path).read_text(encoding="utf-8"))
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    reviewer_type = labels.get("reviewer_type")
    if reviewer_type not in ("ai", "human") or not str(labels.get("reviewer_name") or "").strip():
        raise ValueError("Declare reviewer_type (ai/human) and a nonempty reviewer_name")
    if reviewer_type == "human" and labels.get("status") != "completed":
        raise ValueError("Human review must explicitly set status=completed after checking every report")
    population, partitions = load_population(data_dir, sampled["start_date_bkk"], sampled["end_date_bkk"])
    if partitions != sampled["source_partitions"]:
        raise ValueError("Population changed after sampling; sample and label a consistent snapshot")
    ids = sorted(population)
    if (sampled.get("population_count") != len(ids)
            or sampled.get("population_ids_sha256") != hashlib.sha256("\n".join(ids).encode()).hexdigest()):
        raise ValueError("Sample population metadata does not match the source snapshot")
    sample_rows = sampled["reports"]
    if (len(sample_rows) != sampled["sample_size"]
            or len({r["ticket_id"] for r in sample_rows}) != len(sample_rows)
            or [r["ticket_id"] for r in sample_rows] != random.Random(sampled["seed"]).sample(ids, len(sample_rows))):
        raise ValueError("Sample must match the declared deterministic selection without duplicates")
    truth = {}
    for row in labels["reports"]:
        if row.get("label") not in LABELS or not row.get("rationale"):
            raise ValueError("Each reviewed report needs a valid label and written rationale")
        if row["ticket_id"] in truth:
            raise ValueError("Duplicate review label")
        truth[row["ticket_id"]] = row
    if set(truth) != {r["ticket_id"] for r in sampled["reports"]}:
        raise ValueError("Review labels must cover the exact sample, no missing or extra IDs")
    matrix = {expected: {predicted: 0 for predicted in LABELS} for expected in LABELS}
    reviewed = []
    for item in sampled["reports"]:
        key = item["ticket_id"]
        source = population[key]
        if source["description"] != item["description"]:
            raise ValueError("Sample text differs from the frozen source")
        prediction = classify_flood(source["description"], source["source_categories"])
        expected, predicted = truth[key]["label"], prediction["flood_classification"]
        matrix[expected][predicted] += 1
        reviewed.append({
            "ticket_id": key,
            "reference_label": expected, "predicted_label": predicted,
            "agrees": expected == predicted, "rationale": truth[key]["rationale"],
            "prediction_reason": prediction["flood_reason"],
            "matched_terms": prediction["flood_matched_terms"],
        })
    tp, fp = matrix["flood"]["flood"], matrix["non_flood"]["flood"]
    positives = sum(matrix["flood"].values())
    scorable = sum(sum(matrix[x].values()) for x in ("flood", "non_flood"))
    decided_correct = tp + matrix["non_flood"]["non_flood"]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rule_version": RULE_VERSION, "reviewer_type": reviewer_type,
        "reviewer_name": labels["reviewer_name"],
        "human_verified": reviewer_type == "human",
        "reference_status": "human_review" if reviewer_type == "human" else "AI provisional labels; human confirmation pending",
        **flood_precision(matrix),
        "sampling": {k: v for k, v in sampled.items() if k != "reports"},
        "confusion_matrix_rows_reference_columns_prediction": matrix,
        "three_class_agreement": sum(matrix[x][x] for x in LABELS) / len(reviewed),
        "scorable_binary_reference_count": scorable,
        "binary_agreement_including_abstentions_as_incorrect": decided_correct / scorable if scorable else None,
        "flood_precision_excluding_uncertain_reference": tp / (tp + fp) if tp + fp else None,
        "flood_recall_including_abstentions_as_missed": tp / positives if positives else None,
        "reports": reviewed,
    }
    out = Path(output_dir)
    write_json(out / "review_100.json", summary)
    lines = ["# Traffy 100-report review", "",
             f"Reviewer: {reviewer_type} / {labels['reviewer_name']}", "",
             "**These are provisional AI labels, not human ground truth.**" if reviewer_type == "ai"
             else "Labels were supplied by the named human reviewer.", "",
             f"Population: {sampled['population_count']}; sample: {len(reviewed)}; seed: {sampled['seed']}.",
             f"Rule version: `{RULE_VERSION}`. Each sample was selected without seeing predictions.", "",
             "| Reference \\ Predicted | flood | non_flood | uncertain |",
             "|---|---:|---:|---:|"]
    for label in LABELS:
        lines.append(f"| {label} | " + " | ".join(str(matrix[label][x]) for x in LABELS) + " |")
    lines.extend(["", f"Three-class agreement: {summary['three_class_agreement']:.1%}.",
                  "This agreement is a different metric from precision.", "",
                  "## Precision over all predicted positives", "",
                  f"TP={tp}; FP={fp}; unresolved predicted positives={summary['precision_counts']['unresolved_predicted_positives']}; "
                  f"all predicted positives={summary['precision_counts']['all_predicted_positives']}.", ""])
    if summary["precision"] is not None:
        lines.append(f"Precision = TP / (TP + FP) = **{summary['precision']:.2%}** against the declared reference labels.")
    elif summary["precision_status"] == "unresolved_positive_labels":
        bounds = summary["precision_bounds"]
        lines.append(f"**A single precision value is unresolved. Bounds: {bounds['lower']:.2%} to {bounds['upper']:.2%}.**")
        lines.append("Bounds assume the unresolved predicted positives are all false or all true, respectively.")
    else:
        lines.append("Precision is undefined because there are no predicted positives.")
    lines.extend(["", "The legacy resolved-only precision field excludes unknown references and is conditional; "
                  "it is not the headline precision over the whole sample.",
                  "AI-reference scores/bounds are provisional and must not be presented as human-verified field accuracy.",
                  "The sample is uniform across all reports, not balanced by flood class. Metrics describe this period only.", "",
                  "## Disagreements", ""])
    for row in reviewed:
        if not row["agrees"]:
            lines.append(f"- `{row['ticket_id']}`: reference={row['reference_label']}, "
                         f"predicted={row['predicted_label']}; {row['rationale']}")
    if all(x["agrees"] for x in reviewed):
        lines.append("None in this sample. This does not establish production accuracy.")
    lines.extend(["", "## Human confirmation", "",
                  "Read the full local `sample_100.json`, fill every label/rationale in `human_review_100.json`, "
                  "set reviewer_name and status=completed, and rerun evaluate with those labels.",
                  "Repository evidence contains IDs, labels and rationales; full descriptions stay in the local ignored sample.", ""])
    (out / "review_summary.md").write_text("\n".join(lines), encoding="utf-8")
    return {k: v for k, v in summary.items() if k not in ("reports", "sampling")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("sample")
    select.add_argument("--data-dir", default="data")
    select.add_argument("--start", required=True)
    select.add_argument("--end", required=True)
    select.add_argument("--count", type=int, default=100)
    select.add_argument("--seed", type=int, default=341)
    select.add_argument("--output", default="data/review/traffy/sample_100.json")
    score = sub.add_parser("evaluate")
    score.add_argument("--data-dir", default="data")
    score.add_argument("--sample", default="data/review/traffy/sample_100.json")
    score.add_argument("--labels", required=True)
    score.add_argument("--output-dir", default="evidence/traffy")
    args = parser.parse_args()
    if args.command == "sample":
        result = sample(args.data_dir, args.start, args.end, args.count, args.seed, args.output)
    else:
        result = evaluate(args.data_dir, args.sample, args.labels, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
