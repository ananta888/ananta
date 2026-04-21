from agent.services.demo_mode_service import DemoModeService


def test_demo_mode_preview_is_read_only_and_use_case_oriented():
    preview = DemoModeService().preview()

    assert preview["mode"] == "preview"
    assert preview["isolated"] is True
    assert len(preview["examples"]) >= 3
    assert {example["id"] for example in preview["examples"]} >= {
        "repo-analysis",
        "bugfix-plan",
        "compose-diagnosis",
        "change-review",
        "guided-first-run",
    }
    assert all(example["goal"] and example["tasks"] for example in preview["examples"])
    assert all(example["starter_context"] for example in preview["examples"])


def test_demo_preview_route_requires_auth_and_returns_isolated_preview(client, auth_header):
    assert client.get("/api/demo/preview").status_code == 401

    response = client.get("/api/demo/preview", headers=auth_header)

    assert response.status_code == 200
    assert response.json["data"]["isolated"] is True
    assert response.json["data"]["examples"][0]["title"]
