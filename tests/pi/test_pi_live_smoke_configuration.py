"""Missing opt-in never calls models; explicit malformed selection is a bounded error."""

import pytest

from tests.pi.pi_live_smoke_configuration import live_smoke_selection


def configured(tmp_path, kind='local'):
    node, sdk = tmp_path / 'node', tmp_path / 'index.js'
    node.write_text('synthetic executable marker')
    sdk.write_text('synthetic SDK marker')
    prefix = 'ANANTA_TEST_PI_LIVE_' + kind.upper()
    return {prefix: '1', 'ANANTA_TEST_NODE_BINARY': str(node), 'ANANTA_TEST_PI_SDK': str(sdk),
        prefix + '_MODEL': 'explicit-model', prefix + '_BASE_URL': 'http://127.0.0.1:18080/v1',
        prefix + '_API_KEY': 'synthetic-selected-secret'}


@pytest.mark.parametrize('kind', ['local', 'external'])
def test_no_opt_in_does_not_require_resources_or_discover_provider(kind):
    assert live_smoke_selection(kind, {}) is None


def test_explicit_selection_is_bound_without_leaking_auth_in_metadata(tmp_path):
    selection = live_smoke_selection('local', configured(tmp_path))
    assert selection.target.model == 'explicit-model'
    assert selection.target.api_key == 'synthetic-selected-secret'
    assert 'synthetic-selected-secret' not in str(selection.target.public_metadata())


@pytest.mark.parametrize('key', ['ANANTA_TEST_NODE_BINARY', 'ANANTA_TEST_PI_SDK',
                               'ANANTA_TEST_PI_LIVE_LOCAL_MODEL', 'ANANTA_TEST_PI_LIVE_LOCAL_BASE_URL',
                               'ANANTA_TEST_PI_LIVE_LOCAL_API_KEY'])
def test_opt_in_with_missing_required_data_fails_without_input_or_inference(tmp_path, key):
    environment = configured(tmp_path)
    environment.pop(key)
    with pytest.raises(ValueError):
        live_smoke_selection('local', environment)


def test_external_selection_requires_https_and_never_uses_local_configuration(tmp_path):
    environment = configured(tmp_path, 'external')
    with pytest.raises(ValueError, match='pi_endpoint_invalid'):
        live_smoke_selection('external', environment)
    environment['ANANTA_TEST_PI_LIVE_EXTERNAL_BASE_URL'] = 'https://synthetic-provider.example/v1'
    assert live_smoke_selection('external', environment).target.provider_id == 'openrouter'


@pytest.mark.parametrize('flag', ['true', '', 'yes'])
def test_misspelled_opt_in_is_not_silently_a_skip(flag):
    with pytest.raises(ValueError, match='pi_live_smoke_opt_in_invalid'):
        live_smoke_selection('local', {'ANANTA_TEST_PI_LIVE_LOCAL': flag})
