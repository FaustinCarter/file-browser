from datetime import date

from app import parsing


def test_parse_size():
    assert parsing.parse_size("10 GB") == 10 * 1024**3
    assert parsing.parse_size("1,024 KB") == 1024 * 1024
    assert parsing.parse_size("1.5 MB") == int(1.5 * 1024**2)
    assert parsing.parse_size("512 B") == 512
    assert parsing.parse_size("0 KB") == 0
    assert parsing.parse_size("") is None
    assert parsing.parse_size(None) is None
    assert parsing.parse_size("garbage") is None


def test_parse_percent():
    assert parsing.parse_percent("11.8 %") == 11.8
    assert parsing.parse_percent("100 %") == 100.0
    assert parsing.parse_percent("") is None


def test_parse_date():
    assert parsing.parse_date("03/15/2022") == date(2022, 3, 15)
    assert parsing.parse_date("") is None
    assert parsing.parse_date("bad") is None


def test_is_directory():
    assert parsing.is_directory("C:\\Foo\\Bar\\")
    assert not parsing.is_directory("C:\\Foo\\Bar.txt")


def test_parent_key():
    assert parsing.parent_key("C:\\Foo\\Bar\\Baz.txt") == "c:/foo/bar"
    assert parsing.parent_key("C:\\Foo\\Bar\\") == "c:/foo"
    assert parsing.parent_key("C:\\") is None
