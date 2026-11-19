import pytest

from sidadr.synthetic.generator import GeneratedDataset, GeneratorConfig, generate

SMALL_ROWS = 1500


@pytest.fixture(scope="session")
def small_config() -> GeneratorConfig:
    return GeneratorConfig(rows=SMALL_ROWS, seed=7)


@pytest.fixture(scope="session")
def small_dataset(small_config: GeneratorConfig) -> GeneratedDataset:
    return generate(small_config)
