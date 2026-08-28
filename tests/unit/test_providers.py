import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.providers import list_providers, match_provider_by_base_url


def test_list_providers_returns_list():
    result = list_providers()
    assert isinstance(result, list)
    assert len(result) > 0


def test_list_providers_has_required_fields():
    providers = list_providers()
    for p in providers:
        assert 'id' in p
        assert 'name' in p
        assert 'base_url' in p
        assert 'models' in p


def test_match_provider_by_base_url():
    providers = list_providers()
    if providers:
        url = providers[0]['base_url']
        matched = match_provider_by_base_url(url)
        assert matched is not None
