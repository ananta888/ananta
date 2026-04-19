import scripts.check_service_boundaries as boundaries


def test_current_service_boundaries_have_no_new_route_imports():
    assert boundaries.check_service_boundaries() == []


def test_service_boundary_checker_reports_unlisted_route_import(tmp_path):
    services = tmp_path / "agent" / "services"
    services.mkdir(parents=True)
    service_file = services / "bad_service.py"
    service_file.write_text("from agent.routes.tasks import management\n", encoding="utf-8")

    violations = boundaries.check_service_boundaries(root=services)

    assert violations == ["agent.services.bad_service imports agent.routes.tasks"]


def test_service_boundary_checker_allows_documented_exception(tmp_path):
    services = tmp_path / "agent" / "services"
    services.mkdir(parents=True)
    service_file = services / "task_orchestration_service.py"
    service_file.write_text("from agent.routes.tasks.orchestration_policy import routing\n", encoding="utf-8")

    assert boundaries.check_service_boundaries(root=services) == []
