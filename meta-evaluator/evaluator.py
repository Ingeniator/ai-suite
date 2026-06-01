"""
Meta-evaluation: compare AI evaluator verdicts against human annotation labels.

Input
-----
assignments : list of assignment dicts from GET /api/v0/assignments?task_id=...
  Each assignment has:
    asgn["marker_name"]  — annotator name
    asgn["items"][]
      item["id"]                    — item id
      item["result"]["label"]       — AGREE | DISAGREE | PARTIAL
      item["result"]["confidence"]  — 0.0–1.0
      item["data"]                  — original trace dict; expected fields:
                                        "verdict"  : PASS | FAIL  (AI evaluator output)
                                        "score"    : float        (AI score 0–1)
                                        "trace_id" : str

Output
------
{
  total, agree, disagree, partial,
  agreement_rate, disagreement_rate, partial_rate,

  # only present when traces carry a "verdict" field:
  tp, fp, tn, fn, classified, unclassified,
  accuracy, precision, recall, f1, cohens_kappa,
}
"""

from collections import Counter


def compute_metrics(assignments: list) -> dict:
    if not assignments:
        return {"total": 0}

    # Collect votes per item across all annotators
    item_votes: dict[str, list[str]] = {}
    item_data:  dict[str, dict]      = {}

    for asgn in assignments:
        for item in asgn.get("items", []):
            iid = item["id"]
            if iid not in item_votes:
                item_votes[iid] = []
                item_data[iid]  = item.get("data") or {}
            item_votes[iid].append(item["result"]["label"])

    total = len(item_votes)
    agree = disagree = partial = 0

    # Confusion matrix: AI verdict (PASS/FAIL) vs human majority verdict
    # tp = AI=PASS, human confirms  | fp = AI=PASS, human rejects
    # tn = AI=FAIL, human confirms  | fn = AI=FAIL, human rejects
    tp = fp = tn = fn = skip = 0

    for iid, votes in item_votes.items():
        majority = Counter(votes).most_common(1)[0][0]

        if majority == "AGREE":
            agree += 1
        elif majority == "DISAGREE":
            disagree += 1
        else:
            partial += 1

        verdict = str(item_data[iid].get("verdict", "")).upper()
        if verdict not in ("PASS", "FAIL"):
            skip += 1
            continue

        human_confirms = (majority == "AGREE")
        if verdict == "PASS":
            if human_confirms: tp += 1
            else:              fp += 1
        else:  # FAIL
            if human_confirms: tn += 1
            else:              fn += 1

    result: dict = {
        "total":             total,
        "agree":             agree,
        "disagree":          disagree,
        "partial":           partial,
        "agreement_rate":    round(agree    / total, 4),
        "disagreement_rate": round(disagree / total, 4),
        "partial_rate":      round(partial  / total, 4),
    }

    classified = tp + fp + tn + fn
    if classified > 0:
        n = classified

        # Standard classification metrics
        accuracy  = (tp + tn) / n
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (2 * precision * recall / (precision + recall)
                     if (precision + recall) > 0 else 0.0)

        # Cohen's κ  (standard 2×2 formula)
        #   rows: AI verdict (PASS / FAIL)  — marginals: (tp+fp)/n, (fn+tn)/n
        #   cols: human verdict (PASS / FAIL) — marginals: (tp+fn)/n, (fp+tn)/n
        po = (tp + tn) / n                             # observed agreement
        p_ai_pos  = (tp + fp) / n                      # AI P(PASS)
        p_hum_pos = (tp + fn) / n                      # human P(PASS)
        pe = p_ai_pos * p_hum_pos + (1 - p_ai_pos) * (1 - p_hum_pos)
        kappa = (po - pe) / (1 - pe) if (1 - pe) != 0 else 0.0

        result.update({
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "classified":   classified,
            "unclassified": skip,
            "accuracy":     round(accuracy,  4),
            "precision":    round(precision, 4),
            "recall":       round(recall,    4),
            "f1":           round(f1,        4),
            "cohens_kappa": round(kappa,     4),
        })

    return result
