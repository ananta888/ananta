import pytest
from agent.services.action_pack_service import get_action_pack_service
from agent.services.platform_governance_service import get_platform_governance_service

def test_action_pack_service_crud(app):
    with app.app_context():
        service = get_action_pack_service()

        # Initialisierung pruefen
        service.initialize_action_packs()
        packs = service.get_all_action_packs()
        assert len(packs) >= 5

        # Erstellen
        new_pack = service.create_action_pack(name="test_pack", description="Test", capabilities=["cap1"])
        assert new_pack.name == "test_pack"

        # Abrufen
        retrieved = service.get_action_pack_by_name("test_pack")
        assert retrieved.id == new_pack.id

        # Update
        updated = service.update_action_pack(new_pack.id, description="Updated")
        assert updated.description == "Updated"

        # Toggle
        service.toggle_action_pack(new_pack.id, False)
        toggled = service.get_action_pack_by_id(new_pack.id)
        assert toggled.enabled is False

def test_platform_governance_action_pack_integration():
    service = get_platform_governance_service()

    # Defaults
    packs = service.resolve_action_packs({})
    names = [p["name"] for p in packs]
    assert "file" in names
    assert "shell" in names

    # Access check (default)
    assert service.evaluate_action_pack_access("file", {}) is True
    assert service.evaluate_action_pack_access("shell", {}) is False

    # Access check (override in config)
    cfg = {"action_packs": {"shell": {"enabled": True}}}
    assert service.evaluate_action_pack_access("shell", cfg) is True

    # Read model
    policy = service.build_policy_read_model(cfg)
    assert "action_packs" in policy
    assert policy["decisions"]["action_pack_shell"]["allowed"] is True
