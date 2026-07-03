from agent.services.augment.retrieval_benchmark import (
    RetrievalBenchmarkRunner, RetrievalMode, GoldSet, RetrievedResult,
    DiscardedResult, DiscardReason
)
import pytest


def _gold(files=["src/auth.py", "src/user.py"]):
    return GoldSet(gold_id="g1", query="find auth", expected_files=files)


def _runner():
    return RetrievalBenchmarkRunner()


def test_make_audit_report_no_gold():
    runner = _runner()
    report = runner.make_audit_report(query="q", provider="cc", mode="codecompass_only", retrieved=[])
    assert report.gold_comparison is None


def test_make_audit_report_with_gold():
    runner = _runner()
    gold = _gold()
    retrieved = [RetrievedResult(path="src/auth.py", score=0.9, provider="cc")]
    report = runner.make_audit_report(query="q", provider="cc", mode="cc", retrieved=retrieved, gold=gold)
    assert report.gold_comparison is not None
    assert report.gold_comparison["hits"] == 1
    assert "src/user.py" in report.gold_comparison["missed_files"]


def test_recall_perfect():
    runner = _runner()
    gold = _gold(["a.py"])
    retrieved = [RetrievedResult(path="a.py", score=0.9, provider="cc")]
    report = runner.make_audit_report(query="q", provider="cc", mode="cc", retrieved=retrieved, gold=gold)
    assert report.recall(gold) == 1.0


def test_recall_zero():
    runner = _runner()
    gold = _gold(["a.py"])
    report = runner.make_audit_report(query="q", provider="cc", mode="cc", retrieved=[], gold=gold)
    assert report.recall(gold) == 0.0


def test_to_cli_text_contains_query():
    runner = _runner()
    report = runner.make_audit_report(query="find auth", provider="cc", mode="cc", retrieved=[])
    text = report.to_cli_text()
    assert "find auth" in text


def test_discarded_appear_in_cli_text():
    runner = _runner()
    discarded = [DiscardedResult(path=".env", score=0.9, provider="cc",
                                 reason=DiscardReason.DENIED_PATH, detail="secret file")]
    report = runner.make_audit_report(query="q", provider="cc", mode="cc", retrieved=[], discarded=discarded)
    text = report.to_cli_text()
    assert "denied_path" in text


def test_run_comparison_with_fake_provider():
    runner = _runner()
    gold = _gold(["src/auth.py"])
    runner.register_gold_set(gold)

    def fake_factory(mode, query):
        if mode == "codecompass_only":
            return [RetrievedResult(path="src/auth.py", score=0.9, provider="cc")]
        return []

    cmp = runner.run_comparison(
        "find auth",
        modes=[RetrievalMode.CODECOMPASS_ONLY, RetrievalMode.AUGMENT_ONLY],
        provider_factory=fake_factory,
        gold_id="g1",
    )
    assert cmp.winner == "codecompass_only"


def test_comparison_to_markdown():
    runner = _runner()
    gold = _gold()
    runner.register_gold_set(gold)

    def fake_factory(mode, query):
        return [RetrievedResult(path="src/auth.py", score=0.8, provider="cc")]

    cmp = runner.run_comparison(
        "find auth",
        modes=[RetrievalMode.CODECOMPASS_ONLY],
        provider_factory=fake_factory,
        gold_id="g1",
    )
    md = cmp.to_markdown()
    assert "find auth" in md
    assert "|" in md
