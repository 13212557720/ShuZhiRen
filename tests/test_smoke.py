from web_app import safe_filename


def test_package_version() -> None:
    assert safe_filename("test") == "test"
