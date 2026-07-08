from agent.performance.profilers.llama_cpp_log import LlamaCppLogProfilerParser
from agent.performance.profilers.pytest_benchmark import PytestBenchmarkProfilerParser
from agent.performance.profilers.python_time import PythonTimeProfilerParser


def test_python_time_parser_extracts_hotspot():
    obs = PythonTimeProfilerParser().parse("slow_func: 1.25 seconds")
    assert obs.status == "completed"
    assert obs.hotspots[0]["symbol"] == "slow_func"


def test_pytest_benchmark_parser_degrades_unknown_format():
    obs = PytestBenchmarkProfilerParser().parse("no benchmark rows")
    assert obs.status == "degraded"


def test_llama_cpp_parser_extracts_tokens_per_second():
    log = "llama_print_timings: eval time = 1 ms / 10 runs ( 25.50 tokens per second)"
    obs = LlamaCppLogProfilerParser().parse(log)
    assert obs.status == "completed"
    assert "eval_tokens_per_second" in obs.metrics
