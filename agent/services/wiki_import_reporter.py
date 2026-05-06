from __future__ import annotations

from collections import Counter
from typing import Any


def build_wiki_import_stats(
    *,
    input_pages: int,
    input_docs: int,
    processed_items: int,
    issues: list[dict[str, Any]],
    normalized_records: int,
) -> dict[str, Any]:
    issue_counter = Counter(str(issue.get("error") or "unknown") for issue in issues)
    return {
        "input_pages": int(input_pages),
        "input_docs": int(input_docs),
        "processed_items": int(processed_items),
        "normalized_records": int(normalized_records),
        "issues": len(issues),
        "top_issue_reasons": [
            {"reason": reason, "count": count}
            for reason, count in issue_counter.most_common(5)
        ],
    }

