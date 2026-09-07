"""Selected generations require both a real update observation and matching hydration."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_avatar_renewal_observer import AvatarRenewalObserver


def setup(monkeypatch):
    monkeypatch.setattr("worker.meet_media.dialog_avatar_presentation.DialogAvatarPresentation.update", Mock())
    monkeypatch.setattr("worker.meet_media.dialog_avatar_image_client.HubAvatarImageClient.fetch", Mock())
    return AvatarRenewalObserver(monkeypatch)


@pytest.mark.parametrize("generation", [2, 3, 4])
def test_selected_generation_must_match_both_control_and_image_observation(monkeypatch, generation):
    observer = setup(monkeypatch)
    observer.generations.update(range(1, generation + 1))
    observer.hydrations.extend({"generation": value, "sha256": "a" * 64} for value in range(1, generation))
    condition = Mock()
    condition.__enter__ = Mock(return_value=condition)
    condition.__exit__ = Mock(return_value=False)
    condition.wait_for.side_effect = lambda predicate, **kwargs: predicate()
    observer.condition = condition
    with pytest.raises(AssertionError):
        observer.require_renewed("a" * 64, generation=generation)
    observer.hydrations.append({"generation": generation, "sha256": "wrong"})
    with pytest.raises(AssertionError):
        observer.require_renewed("a" * 64, generation=generation)
    observer.hydrations.append({"generation": generation, "sha256": "a" * 64})
    observer.require_renewed("a" * 64, generation=generation)
    assert condition.wait_for.call_args.kwargs == {"timeout": 75}


@pytest.mark.parametrize("generation", [True, None, 1, 5, "2"])
def test_invalid_generation_never_waits(monkeypatch, generation):
    observer = setup(monkeypatch)
    observer.condition = SimpleNamespace(wait_for=Mock())
    with pytest.raises(ValueError, match="generation_invalid"):
        observer.require_renewed("a" * 64, generation=generation)
    observer.condition.wait_for.assert_not_called()


def test_hydration_history_is_bounded_and_default_generation_remains_compatible(monkeypatch):
    observer = setup(monkeypatch)
    observer.generations.update({1, 2})
    for _ in range(20):
        observer.hydrations.append({"generation": 2, "sha256": "a" * 64})
    assert len(observer.hydrations) == 16
    assert isinstance(observer.condition, threading.Condition)
    observer.require_renewed("a" * 64)
    assert observer.report() == {"lease_generations": [1, 2], "hydrated_generations": [2] * 16}
