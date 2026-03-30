import pytest
from pathlib import Path
from src.config import PDF_DIR

@pytest.fixture
def english_pdf():
    return str(PDF_DIR / "Parallel-Reservoir-Computing-Using-Optical-Amplifiers.pdf")

@pytest.fixture
def chinese_pdf():
    return str(PDF_DIR / "不同类型胶原蛋白在皮肤衰老中的作用及其研究进展.pdf")

@pytest.fixture
def test_user_id():
    return "test_user"

@pytest.fixture
def sample_doc_id():
    return "test_doc_id_m2_001"
