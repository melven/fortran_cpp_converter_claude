"""Config / style files, the converter, macros, the report, options."""
import os
import shutil
import subprocess
import sys

import pytest

from f2cpp import config
from f2cpp.cli import translate
from f2cpp.prepass import prepass
from f2cpp.report import Report
from helpers import HERE, CORPUS, SUPPORT, EIGEN, cfg, read, cxx


@pytest.mark.skipif(not os.environ.get("F2CPP_REQUIRE_TOOLCHAIN"), reason="only where the tool chain is required (CI)")
def test_toolchain_present():
    """CI: the compile / run tests must not be skipped (several of them return early without the tools)"""
    missing = [t for t in ("g++", "gfortran", "valgrind") if not shutil.which(t)]
    missing += [p for p in (EIGEN, "/usr/include/gtest/gtest.h") if not os.path.exists(p)]
    assert not missing, f"missing: {missing}"


def test_prepass_macros():
    c = config.load()
    src = "PROJECT_CHK_IERR(call foo(a, &\n   & b, ierr), ierr)\nPROJECT_ALLOCATE(x(n, m), ierr)\n" \
          "PROJECT_ASSERT(f(a, b) > 0, ierr) ! PROJECT_ASSERT(x, ierr)\n#ifdef X\n@assertEqual(1, n)\n@test\n"
    res = prepass(src, c).split("\n")
    assert res[0] == "call foo(a,  b, ierr)"
    assert res[1] == "allocate(x(n, m))"
    assert res[2] == "call PROJECT_ASSERT(f(a, b) > 0) ! PROJECT_ASSERT(x, ierr)"
    assert res[3] == "!F2CPP_CPP #ifdef X"
    assert res[4] == "call f2cpp_assertEqual(1, n)"
    assert res[5].startswith("!F2CPP_PF test")


def test_report(out):
    files = out[1].files
    todo = [c for name, counters in files.items() if "features" in name for c in counters["todo"]]
    assert "inconsistent-name-case:addValue,addvalue" in todo
    assert [c for c in todo if c.startswith("inconsistent-name-case")] == ["inconsistent-name-case:addValue,addvalue"]
    assert "inconsistently" not in read(out, "test_poly_mod.cpp")      # Setup_Solver vs setup_solver: two symbols
    assert "inconsistent-type-case:Holder_Type,holder_type" in todo
    for name, counters in files.items():
        if "features" in name:
            assert set(counters["untranslated"]) == {"condition:error-arg", "write:format-descriptor",
                                                     "condition:intrinsic:verify"}
        else:
            assert not counters["untranslated"], name


def test_options(tmp_path):
    c = cfg(keep_original=True, namespace="abc", array_args="ref", type_case="camel")
    c["namespace_map"] = [["block_.*", "linalg"]]
    c["constructor_bindings"] = ["configure"]
    c["brace_style"] = "attached"
    c["include_guard"] = "pragma"
    translate([os.path.join(CORPUS, "block_lu_mod.F90")], c, str(tmp_path))
    hpp = (tmp_path / "block_lu_mod.hpp").read_text()
    cpp = (tmp_path / "block_lu_mod.cpp").read_text()
    assert "namespace abc::linalg {" in hpp and "class BlockLu final {" in hpp and "void factorize();" in hpp
    assert "#pragma once" in hpp and "#ifndef" not in hpp
    assert "void solve(Eigen::Ref<vec> x) const;" in hpp
    assert "void init(int nblocks, int bs);" in hpp      # "init" is not a constructor name by default
    assert "// F: bs = this % bs" in cpp


def test_custom_macro_names(tmp_path):
    (tmp_path / "m_mod.F90").write_text(
        "module m_mod\ncontains\nsubroutine s(block_dim, ierr)\ninteger, intent(in) :: block_dim(:)\n"
        "integer, intent(out) :: ierr\nXY_CHK_ARGUMENT(all(block_dim >= 0), ierr)\n"
        "call log_debug('n = ' // str(size(block_dim)))\n"
        "if (.not. any(sys%offsets%mask(ub:ue))) call log_debug('none')\n"
        "call log_debug(str(ext_fn(sys%handle, trim(sys%path), sys%a%b)))\nend subroutine\nend module\n")
    (tmp_path / "c.toml").write_text(
        '[macros.XY_CHK_ARGUMENT]\nfortran = "call XY_CHK_ARGUMENT({0})"\n'
        "cpp = 'if (!({0})) throw std::invalid_argument(\"{0:str}\");'\n"
        "[map.functions]\nstr = \"std::to_string\"\nlog_debug = \"xy_log_debug\"\n")
    translate([str(tmp_path / "m_mod.F90")], config.load(str(tmp_path / "c.toml")), str(tmp_path))
    cpp = (tmp_path / "m_mod.cpp").read_text()
    assert 'if (!((block_dim.array() >= 0).all())) throw std::invalid_argument(' in cpp
    assert 'xy_log_debug("n = " + std::to_string(block_dim.size()));' in cpp
    assert 'if (!sys.offsets.mask(Eigen::seq(ub - 1, ue - 1)).any()) xy_log_debug("none");' in cpp
    assert "xy_log_debug(std::to_string(ext_fn(sys.handle, sys.path, sys.a.b)));" in cpp


def test_internal_errors_differ_from_unsupported_constructs(tmp_path, capsys, monkeypatch):
    """a Python error while translating a statement: report kind "error", a log line with the Fortran and the
    f2cpp location, the statement as Fortran comment; strict (the test configuration): the run stops"""
    from f2cpp.translate import Translator
    (tmp_path / "e_mod.F90").write_text("module e_mod\ncontains\n  subroutine f(x)\n    real, intent(out) :: x\n"
                                        "    x = 1.0\n  end subroutine f\nend module e_mod\n")

    def broken(self, node, ind):
        raise KeyError("boom")
    monkeypatch.setattr(Translator, "s_Assignment_Stmt", broken)
    report, _ = translate([str(tmp_path / "e_mod.F90")], cfg(strict=False), str(tmp_path))
    c = report.files[str(tmp_path / "e_mod.F90")]
    assert c["error"]["internal:KeyError:Assignment_Stmt"] == 1 and not c["untranslated"]
    log = capsys.readouterr().err
    assert "f2cpp: internal error in e_mod::f, line 5: KeyError: 'boom' (f2cpp/" in log
    assert "INTERNAL ERRORS" in report.summary()
    assert "// TODO(f2cpp): not translated (f2cpp internal error: KeyError)" in (tmp_path / "e_mod.cpp").read_text()
    with pytest.raises(KeyError):
        translate([str(tmp_path / "e_mod.F90")], cfg(), str(tmp_path))


def test_config_overrides_are_normalised():
    """keyword overrides are lower-cased like TOML values; unknown keys are errors"""
    assert cfg(wrap_reverse=["StateModel_type"])["wrap_reverse"] == ["statemodel_type"]
    with pytest.raises(TypeError):
        cfg(no_such_option=1)


def test_unused_config_entries(tmp_path):
    """--check-config: config entries that no file of the run uses (or of the wrong kind) are reported (pseudo file
    <config>); off by default (one config serves runs over parts of the code base)"""
    (tmp_path / "x.toml").write_text("""
[map.functions]
no_such_fn = "f"
[types.Missing_type]
cpp = "M"
[types.MySolver_type]
accessors = ["n", "nothere"]
[types.MySolver_type.bindings]
apply.translate = true
gone.template = ""
[types."My*_type".bindings]
scale.template = "{self}.scale({0});"
[modules]
"*solver_mod" = {exclude = ["norm"]}
[procedures]
MySolver_type = {translate = true}
residual_norm = {c_name = "rn"}
""")
    c = config.load(str(tmp_path / "x.toml"), strict=True)
    src = os.path.join(CORPUS, "wrapped", "mysolver_mod.F90")
    assert Report.CONFIG not in translate([], c, str(tmp_path), wrap=[src])[0].files
    c = config.load(str(tmp_path / "x.toml"), strict=True)       # (a run adds the accessors of wrapped classes)
    report, _ = translate([], c, str(tmp_path), wrap=[src], check_config=True)
    info = report.files[Report.CONFIG]["info"]
    assert set(info) == {"config-unused:map.functions.no_such_fn", "config-unused:types.missing_type",
                         "config-unused:types.mysolver_type.accessors.nothere",
                         "config-unused:types.mysolver_type.bindings.gone",
                         "config-wrong-kind:procedures.mysolver_type (a type)"}
    assert "config: 5 entries not used by the files of this run" in report.summary()


def test_config_file_format(tmp_path):
    """sections of the config file -> internal keys; derived macros (prefix, include); errors for unknown / old keys"""
    (tmp_path / "x.toml").write_text("""
[project]
namespace = "Flow::detail"
[macros]
include = "flow_macros.inc"
FLOW_ASSERT_MSG = {fortran = "call flow_assert_msg({0}, {1})", cpp = "if (!({0})) throw std::logic_error({1});"}
[modules]
flow_env = {header = ""}
flow_io = {header = "io.hpp", exclude = ["dump*"]}
[types.Solver_type]
cpp = "Eigen::PartialPivLU<mat>"
bindings.solve.template = "{1} = {self}.solve({0});"
bindings.apply = {translate = true, sizes = {x = "this%n"}}
[types.Model_type]
interop = "bidirectional"
constants = ["n"]
translate = true
[procedures.helper]
c_name = "flow_helper"
""")
    c = config.load(str(tmp_path / "x.toml"))
    assert c["macros"]["FLOW_CHK_IERR"] == "{0}" and c["macros"]["FLOW_ASSERT_MSG"] == "call flow_assert_msg({0}, {1})"
    assert c["call_templates"]["flow_assert_msg"] == "if (!({0})) throw std::logic_error({1});"
    assert c["call_templates"]["flow_chk_argument"].startswith("if (!({0})) throw std::invalid_argument(")
    assert c["wrap_chk_ierr"] == ["FLOW_CHK_IERR", "flow_macros.inc"] and c["include_map"]["flow_macros.inc"] == ""
    assert c["wrap_fail"] == ["FLOW_ASSERT(.false., {ierr})", "flow_macros.inc"]
    assert "flow_env" in c["ignore_modules"] and c["module_headers"]["flow_io"] == "io.hpp"
    assert c["exclude"] == ["flow_io:dump*"] and c["type_map"]["solver_type"] == "Eigen::PartialPivLU<mat>"
    assert c["method_templates"] == {"solver_type%solve": "{1} = {self}.solve({0});"}
    assert c["wrap_sizes"] == {"solver_type%apply": {"x": "this%n"}} and c["c_procedure_names"] == {"helper": "flow_helper"}
    assert c["wrap_translate"] == ["solver_type%apply", "model_type%*"] and c["wrap_reverse"] == ["model_type"]
    assert c["c_prefix"] == "flow" and c["wrap_constants"] == {"model_type": ["n"]}
    # -n changes the C++ namespace, not the macros of the Fortran source
    assert "FLOW_CHK_IERR" in config.load(str(tmp_path / "x.toml"), namespace="other")["macros"]
    for text, msg in (("namespace = 'x'\n", "old config format"), ("[project]\nnamspace = 'x'\n", "namspace"),
                      ("[types.T]\ninterop = 'reverse'\n", "interop"), ("[typs.T]\n", "unknown config section"),
                      ("[layout]\nline_width = 100\n", "move it to the file given by style"),
                      ("style = 's.toml'\n", "s.toml")):
        (tmp_path / "y.toml").write_text(text)
        (tmp_path / "s.toml").write_text("[members]\ngetter = '{typ} {name}() const'\n")
        with pytest.raises((SystemExit, FileNotFoundError), match=msg if msg != "s.toml" else "typ"):
            config.load(str(tmp_path / "y.toml"))


def test_style_file(tmp_path):
    """style file: templates of member accessors, member names, messages; only the given entries change"""
    (tmp_path / "s.toml").write_text("""
[members]
member = "m_{name}"
getter_name = "get_{name}"
setter_name = "set_{name}"
getter = "{type} {name}() const { return {member}; }  // getter"
[errors]
call_message = "{where} failed ({cfunc})"
""")
    (tmp_path / "x.toml").write_text('style = "s.toml"\n[types.solver_type]\naccessors = ["n", "tol:set"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    assert c["eigen_placeholders"] == "Eigen" and c["doxygen"] == "//!"           # tests style + defaults
    d = str(tmp_path)
    translate([os.path.join(CORPUS, "accessors_mod.F90")], c, d)
    hpp, cpp = read((d,), "accessors_mod.hpp"), read((d,), "accessors_mod.cpp")
    assert "  int get_n() const { return m_n; }  // getter\n" in hpp and "  int m_n = 0;" in hpp
    assert "  void set_tol(scalar value) { m_tol = value; }" in hpp
    assert "  s.set_tol(0.5);" in cpp and "s.count = s.count + s.get_n();" in cpp and "  m_n = n;" in cpp
    if shutil.which("g++") and os.path.isdir(EIGEN):
        cxx((d,), "accessors_mod")
    translate([], c, d, wrap=[os.path.join(CORPUS, "wrapped", "mysolver_mod.F90")])
    assert 'throw std::runtime_error("MySolver::apply failed (project_MySolver_apply)");' in read((d,), "mysolver_mod.cpp")


def test_convert_old_config(tmp_path):
    """tools/convert_config.py: old flat config -> config + style file; derived entries dropped"""
    import tomllib
    (tmp_path / "old.toml").write_text("""
namespace = "MYPROJECT"
destructor_bindings = ["finalize"]
ignore_modules = ["myproject_env"]
log_error = "myproject_log_error"
log_error_include = '"myproject_logging.h"'
wrap_reverse = ["Model_type"]
wrap_listeners = ["Listener_type"]
wrap_independent_bases = ["Persistable_type"]
wrap_translate = ["System_type%get_state", "System_type%*", "Wrapper_type", "helper"]
wrap_chk_ierr = ["MYPROJECT_CHK_IERR", "myproject_macros.inc"]
wrap_fail = "MYPROJECT_ASSERT(.false., {ierr})"
eigen_placeholders = "Eigen"
member_suffix = "_m"
closed_hierarchy = false
[include_map]
"myproject_macros.inc" = ""
[macros]
MYPROJECT_CHK_IERR = "{0}"
MYPROJECT_ASSERT_MSG = "call myproj_assert_msg({0}, {1})"
[call_templates]
MYPROJECT_ASSERT = 'if (!({0})) throw std::logic_error("assertion failed: {0:str}");'
MYPROJ_ASSERT_MSG = 'if (!({0})) throw std::logic_error({1});'
sort_rank = "x({0});"
[exclude]
"myproject_*_factory" = ["generate_*"]
[type_map]
Persistable_type = "Persistable::AbstractPersistable"
[method_templates]
"Solver_type%solve" = "{1} = {self}.solve({0});"
[wrap_sizes]
"Solver_type%apply" = {x = "this%n"}
[named_elements]
Variable_type = ""
[named_element_lists]
Variable_type = "VariableVector"
[accessors]
System_type = ["models", "coupling_manager=couplingManager"]
[member_accessors]
System_type = ["tol:set"]
""")
    tool = os.path.join(HERE, "..", "tools", "convert_config.py")
    r = subprocess.run([sys.executable, tool, str(tmp_path / "old.toml"), str(tmp_path / "new.toml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "closed_hierarchy = False dropped" in r.stderr and "'Wrapper_type' is a procedure name" in r.stderr
    new = tomllib.loads((tmp_path / "new.toml").read_text())
    style = tomllib.loads((tmp_path / "new_style.toml").read_text())
    assert new["style"] == "new_style.toml" and new["project"] == {"namespace": "MYPROJECT"}
    assert new["macros"] == {"include": "myproject_macros.inc", "MYPROJECT_ASSERT_MSG": {
        "fortran": "call myproj_assert_msg({0}, {1})", "cpp": "if (!({0})) throw std::logic_error({1});"}}
    assert new["map"] == {"calls": {"sort_rank": "x({0});"}}
    assert new["modules"] == {"myproject_env": {"header": ""}, "myproject_*_factory": {"exclude": ["generate_*"]}}
    t = new["types"]
    assert t["Persistable_type"] == {"cpp": "Persistable::AbstractPersistable", "independent": True}
    assert t["Model_type"] == {"interop": "bidirectional"} and t["Listener_type"] == {"interop": "listener"}
    assert t["System_type"] == {"bindings": {"get_state": {"translate": True}}, "translate": True,
                                "accessors": ["models:ref", "coupling_manager=couplingManager:ref", "tol:set"]}
    assert t["Solver_type"]["bindings"] == {"solve": {"template": "{1} = {self}.solve({0});"},
                                            "apply": {"sizes": {"x": "this%n"}}}
    assert t["Variable_type"] == {"named_elements": True, "element_list": "VariableVector"}
    assert new["procedures"] == {"Wrapper_type": {"translate": True}, "helper": {"translate": True}}
    assert style == {"conventions": {"eigen_placeholders": "Eigen"}, "members": {"member": "{name}_m"},
                     "errors": {"log": 'myproject_log_error("{class}", ("{message}: " + std::string({what})).c_str());',
                                "log_includes": ["<string>", '"myproject_logging.h"']}}
    c = config.load(str(tmp_path / "new.toml"))
    assert c["wrap_fail"] == ["MYPROJECT_ASSERT(.false., {ierr})", "myproject_macros.inc"]
    assert c["call_templates"]["myproj_assert_msg"] == "if (!({0})) throw std::logic_error({1});"
