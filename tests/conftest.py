"""Fixtures shared by the test files."""
import os

import pytest

from f2cpp.cli import translate
from helpers import CORPUS, MODULES, cfg


@pytest.fixture(scope="session")
def out(tmp_path_factory):
    """the corpus translated once (read only)"""
    d = str(tmp_path_factory.mktemp("out"))
    files = [os.path.join(CORPUS, m + ".F90") for m in MODULES] + \
        [os.path.join(CORPUS, t + ".pf") for t in ("test_expm_mod", "test_poly_mod", "test_helpers_mod", "test_poly_shared_mod")]
    report, _ = translate(files, cfg(), d, interface_only=[os.path.join(CORPUS, "legacy_mod.F90")])
    return d, report
