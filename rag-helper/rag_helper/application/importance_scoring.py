from __future__ import annotations


def score_index_records(records: list[dict], mode: str) -> None:
    if mode == "off":
        return
    for record in records:
        record["importance_score"] = compute_importance_score(record)


def compute_importance_score(record: dict) -> float:
    score = 1.0
    kind = record.get("kind", "")
    role_labels = set(record.get("role_labels", []) or [])

    if kind == "java_file":
        score += 0.5
    if kind == "java_type":
        score += 1.0
    if kind == "adoc_section":
        score += 0.8
    if kind == "xsd_complex_type":
        score += 1.1
    if kind == "xsd_root_element":
        score += 0.7

    if "controller" in role_labels:
        score += 2.5
    if "service" in role_labels:
        score += 2.0
    if "repository" in role_labels:
        score += 1.7
    if "config" in role_labels:
        score += 1.4
    if "client" in role_labels or "facade" in role_labels:
        score += 1.2

    title = str(record.get("title") or record.get("name") or "").lower()
    section_path = " ".join(record.get("section_path", []) or []).lower()
    joined_text = f"{title} {section_path}"
    if kind == "adoc_section" and any(token in joined_text for token in ("architecture", "overview", "design", "system")):
        score += 2.2

    if kind == "xsd_complex_type":
        name = str(record.get("name") or "").lower()
        if any(token in name for token in ("request", "response", "order", "customer", "invoice")):
            score += 1.2

    if record.get("generated_code"):
        score -= 0.8

    return round(max(score, 0.1), 2)
