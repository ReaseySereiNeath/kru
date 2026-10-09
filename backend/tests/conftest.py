import pytest

from tests.samples.make_samples import make_all


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    """Fresh sample.pdf / sample.epub / sample.txt, built once per test run."""
    return make_all(tmp_path_factory.mktemp("samples"))
