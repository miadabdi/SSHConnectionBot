from app.utils.validators import is_valid_host, is_valid_port, is_valid_slug


def test_host_validation() -> None:
    assert is_valid_host("192.168.1.1")
    assert is_valid_host("::1")
    assert is_valid_host("my-server.example.com")
    assert not is_valid_host("")
    assert not is_valid_host("host with spaces")


def test_port_validation() -> None:
    assert is_valid_port("22")
    assert is_valid_port("65535")
    assert not is_valid_port("0")
    assert not is_valid_port("70000")
    assert not is_valid_port("abc")


def test_slug_validation() -> None:
    assert is_valid_slug("prod")
    assert is_valid_slug("staging_01")
    assert is_valid_slug("server-a")
    assert not is_valid_slug("bad slug")
