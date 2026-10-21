import tilelens
from tilelens.app import main


def test_version():
    assert tilelens.__version__


def test_main_runs():
    assert main([]) == 0
