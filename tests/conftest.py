"""Zaptec testing configuration file."""

import os

import pytest


@pytest.fixture(scope="session")
def skip_if_in_github_actions() -> None:
    """Check if we are running in Github actions and skip any dependant tests if true."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        pytest.skip("This test doesn't work in Github Actions.")


@pytest.fixture(scope="session")
def skip_if_user_disabled_api_tests() -> None:
    """Check if user has disabled API tests and skip any dependant tests if true."""
    if os.getenv("SKIP_ZAPTEC_API_TEST") == "true":
        pytest.skip("User disabled the tests requiring API login.")


@pytest.fixture(scope="session")
def zaptec_username(skip_if_user_disabled_api_tests, skip_if_in_github_actions) -> str:  # noqa: ANN001 (the inputs are purely to create dependencies to the env-flags above)
    """
    Get the zaptec username stored in env.

    Any test relying on this fixture will be skipped if the test is running
    in Gihub Actions, or the user has disabled tests requiring API login.
    """
    username = os.environ.get("ZAPTEC_USERNAME")
    assert username, (
        "Missing username, either set it with \"export ZAPTEC_USERNAME='username'\" "
        "or run test script with the --skip-api flag"
    )
    return username


@pytest.fixture(scope="session")
def zaptec_password(skip_if_user_disabled_api_tests, skip_if_in_github_actions) -> str:  # noqa: ANN001
    """
    Get the zaptec password stored in env.

    Any test relying on this fixture will be skipped if the test is running
    in Gihub Actions, or the user has disabled tests requiring API login.
    """
    password = os.environ.get("ZAPTEC_PASSWORD")
    assert password, (
        "Missing password, either set it with \"export ZAPTEC_PASSWORD='password'\" "
        "or run test script with the --skip-api flag"
    )
    return password
