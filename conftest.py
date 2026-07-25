"""Repo-root conftest: load pytest-homeassistant-custom-component explicitly.

The plugin autoloads via a pytest11 entry point, but importing it on Windows
fails immediately (`homeassistant.runner` imports `fcntl`, Unix-only) before any
test collects. `-p no:homeassistant` in pyproject.toml blocks that autoload;
this file loads the plugin back explicitly, with Windows compatibility shims
applied first. pytest only honors `pytest_plugins` in the rootdir conftest, so
this cannot live in tests/conftest.py. The shim is a no-op on Linux (CI), where
fcntl/resource exist and the plugin imports natively.
"""

import socket
import sys
import types
from typing import Any

if sys.platform == "win32":
    import pytest_socket

    if "fcntl" not in sys.modules:
        fake_fcntl = types.ModuleType("fcntl")
        fake_fcntl.LOCK_SH = 1
        fake_fcntl.LOCK_EX = 2
        fake_fcntl.LOCK_NB = 4
        fake_fcntl.LOCK_UN = 8
        fake_fcntl.flock = lambda *_args: None
        fake_fcntl.lockf = lambda *_args: None
        fake_fcntl.fcntl = lambda *_args: 0
        fake_fcntl.ioctl = lambda *_args: 0
        sys.modules["fcntl"] = fake_fcntl

    if "resource" not in sys.modules:
        fake_resource = types.ModuleType("resource")
        fake_resource.RLIMIT_NOFILE = 7
        fake_resource.RLIM_INFINITY = -1
        fake_resource.getrlimit = lambda *_args: (8192, 8192)
        fake_resource.setrlimit = lambda *_args: None
        sys.modules["resource"] = fake_resource

    _orig_socketpair = socket.socketpair

    def _shimmed_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        blocked = getattr(socket.socket, "__module__", "") == "pytest_socket"
        if not blocked:
            return _orig_socketpair(*args, **kwargs)

        pytest_socket.enable_socket()
        try:
            return _orig_socketpair(*args, **kwargs)
        finally:
            pytest_socket.socket_allow_hosts(["127.0.0.1"])
            pytest_socket.disable_socket(allow_unix_socket=True)

    socket.socketpair = _shimmed_socketpair

pytest_plugins = "pytest_homeassistant_custom_component.plugins"
