import pytest
from pathlib import Path
from src.config import PDF_DIR


@pytest.fixture
def english_pdf():
    # 项目早期架构搭建时的 parser/chunker 夹具。所需 PDF 不随 git 同步（data/ 已 gitignore），
    # 换机/重建 data/ 后会缺失 → 文件不在则 skip，把这些 PDF 放回 data/pdfs/ 即自动启用。
    path = PDF_DIR / "Parallel-Reservoir-Computing-Using-Optical-Amplifiers.pdf"
    if not path.exists():
        pytest.skip(f"缺少测试 PDF（data/ 不随 git 同步）：{path.name}")
    return str(path)


@pytest.fixture
def chinese_pdf():
    path = PDF_DIR / "不同类型胶原蛋白在皮肤衰老中的作用及其研究进展.pdf"
    if not path.exists():
        pytest.skip(f"缺少测试 PDF（data/ 不随 git 同步）：{path.name}")
    return str(path)


@pytest.fixture
def test_user_id():
    return "test_user"


@pytest.fixture
def sample_doc_id():
    return "test_doc_id_m2_001"


# 注册 --live 命令行参数
def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run live tests that make real network requests",
    )


# 注册 live 标记，消除 Pytest 的 UnknownMarkWarning 警告
def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test to run only with --live flag")


# 根据是否传入 --live 参数，决定是否跳过带 @pytest.mark.live 的测试
def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        skip_live = pytest.mark.skip(reason="需要传入 --live 参数才运行真实网络测试")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)
