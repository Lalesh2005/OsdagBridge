"""Shared pytest setup for the unit tests."""
import os
import sys

import pytest

# Qt must never need a display for the test run.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_sessionfinish(session, exitstatus):
    session.config._exit_status = int(exitstatus)


def pytest_unconfigure(config):
    """
    Tests that build the real template page (``CustomWindow``) set
    ``config._qt_main_window_built``.  Tearing that window down at interpreter
    exit segfaults in headless runs (it does so with the app code untouched too),
    which would turn a green run into exit code 139.  ``pytest_unconfigure`` runs
    after the summary has been printed, so leave right away with the real status.
    """
    if getattr(config, "_qt_main_window_built", False):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(getattr(config, "_exit_status", 0))
