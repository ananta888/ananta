import pytest

pytestmark = pytest.mark.skip(
    reason="Legacy placeholder test file; imports a non-existent User model and is not part of the current auth architecture."
)
