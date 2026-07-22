from pathlib import Path

from scripts.verify_sfu_distributed_runtime import evaluate


ROOT = Path(__file__).resolve().parents[2]


def test_structure_is_present_but_capabilities_stay_false_without_evidence():
    result = evaluate(
        directory_path=ROOT / "config/sfu_broadcast_cluster_directory.json",
        compose_path=ROOT / "docker-compose.sfu-broadcast.yml",
        redis_config_path=ROOT / "config/redis/sfu-broadcast.conf",
    )
    assert all(result["structural_checks"].values())
    assert result["ready"] is False
    assert result["capabilities"] == {"multi_node": False, "distributed_capacity": False, "rolling_drain": False}
    assert "sfu_distributed_runtime_evidence_missing" in result["reason_codes"]
