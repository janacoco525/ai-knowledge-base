import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.config import Config


def test_config_has_required_attributes():
    assert hasattr(Config, 'STEP_API_BASE')
    assert hasattr(Config, 'STEP_MODEL')
    assert hasattr(Config, 'STEP_API_KEY')


def test_config_product_version():
    assert hasattr(Config, 'PRODUCT_VERSION')
    assert isinstance(Config.PRODUCT_VERSION, str)


def test_config_has_vector_data_dir():
    assert hasattr(Config, 'VECTOR_DATA_DIR')
