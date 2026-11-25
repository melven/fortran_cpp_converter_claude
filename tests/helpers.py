"""Shared helpers of the tests: paths, subprocess / compiler helpers, the test config."""
import os
import re
import shutil
import subprocess

import pytest

from f2cpp import config

HERE = os.path.dirname(__file__)
CORPUS = os.path.join(HERE, "corpus")
SUPPORT = os.path.join(HERE, "support")
EIGEN = "/usr/include/eigen3"
MODULES = ["expm_mod", "block_lu_mod", "shape_mod", "features_mod", "poly_mod", "solver_mod", "generic_mod", "problem_mod"]

needs_cxx = pytest.mark.skipif(not (shutil.which("g++") and os.path.isdir(EIGEN)), reason="g++/Eigen missing")


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}"
    return r.stdout


VALGRIND = ["valgrind", "-q", "--error-exitcode=99", "--leak-check=full", "--errors-for-leak-kinds=definite"]


def run_exe(cmd, cwd):
    """run a compiled test program; under valgrind if available (memory errors and definite leaks fail)"""
    return run((VALGRIND if shutil.which("valgrind") else []) + cmd, cwd)


def cfg(**kw):
    return config.load(os.path.join(SUPPORT, "f2cpp.toml"), **kw)


def flat(text):
    """C declarations with one parameter per line -> one line"""
    return re.sub(r",\n[ !]*", ", ", text)


def read(out, name):
    with open(os.path.join(out[0], name)) as f:
        return f.read()


def cxx(out, name):
    run(["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c", name + ".cpp"],
        out[0])
