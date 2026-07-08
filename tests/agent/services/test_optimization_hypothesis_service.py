from agent.services.optimization_hypothesis_service import OptimizationHypothesisService


def test_optimization_hypothesis_service_requires_evidence_and_classifies_bus_bound():
    hypotheses = OptimizationHypothesisService().generate(
        hotspot_report={
            "hotspots": [
                {"hotspot_id": "h1", "symbol": "gpu_copy_time", "score": 1.0, "affected_files": ["x.py"]}
            ]
        }
    )
    assert hypotheses[0]["schema"] == "optimization_hypothesis_artifact.v1"
    assert hypotheses[0]["suspected_bottleneck"] == "bus_bound"
    assert hypotheses[0]["hotspot_refs"]
