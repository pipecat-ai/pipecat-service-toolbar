import pytest
from tts_server.backends import BACKEND_NAMES, get_backend
from ._macos_catalog import macos_catalog


@pytest.mark.parametrize("name", BACKEND_NAMES)
def test_swift_model_repo_equals_registry_default(name):
    (entry,) = [e for e in macos_catalog().values() if e.name == name and not e.is_variant]
    assert entry.model_repo == get_backend(name).default_model()
