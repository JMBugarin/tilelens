import tilelens
from tilelens.app import main


def test_version():
    assert tilelens.__version__


def test_version_flag(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--version"])
    assert "tilelens" in capsys.readouterr().out
