"""Wrapped modules (-W, -w): binding, glue, hierarchies, constants, owned objects, listeners."""
import os
import re
import shutil
import subprocess

import pytest

from f2cpp import config
from f2cpp.cli import translate
from helpers import CORPUS, SUPPORT, EIGEN, needs_cxx, run, run_exe, cfg, flat, read, cxx


@needs_cxx
@pytest.mark.skipif(not shutil.which("gfortran"), reason="gfortran missing")
@pytest.mark.parametrize("translated", [False, True])
def test_wrapped_module_end_to_end(tmp_path, translated):
    """mysolver_mod stays in Fortran (bind(C) layer + C++ glue), solver_user_mod is translated and uses it;
    the result equals the pure Fortran program. translated: norm / residual_norm implemented in C++
    (wrap_translate), the header is the same"""
    d = str(tmp_path)
    w = os.path.join(CORPUS, "wrapped")
    consts = {"mysolver_type": ["n", "diag", "weights", "params", "label"]}
    wt = ["mysolver_type%norm", "residual_norm"] if translated else []
    translate([os.path.join(w, "solver_user_mod.F90")], cfg(wrap_size_checks=True, wrap_constants=consts,
                                                            wrap_translate=wt), d,
              wrap=[os.path.join(w, "mysolver_mod.F90")])
    if translated:
        translate([], cfg(wrap_size_checks=True, wrap_constants=consts), os.path.join(d, "plain"),
                  wrap=[os.path.join(w, "mysolver_mod.F90")])
        assert read((d,), "mysolver_mod.hpp") == read((d, "plain"), "mysolver_mod.hpp")     # same declarations
    hpp, cpp, h = (read((d,), "mysolver_mod.hpp"), read((d,), "mysolver_mod.cpp"), read((d,), "mysolver_mod.h"))
    binding = read((d,), "mysolver_mod_c_binding.F90")
    assert "void project_MySolver_apply(const project_MySolver* mySolver, const PROJECT_REAL* x, PROJECT_REAL* y, " \
           "int* ierr);" in flat(h)
    assert "void project_MySolver_scale(project_MySolver* mySolver, PROJECT_REAL factor, int* ierr);" in flat(h)
    assert "struct project_MySolver\n{\n  void* p_impl = nullptr;\n  int n = 0;\n  PROJECT_REAL* diag = nullptr;\n" \
           "  int diag_size = 0;\n  PROJECT_REAL weights[3] = {};\n  project_SolverParams params;\n};" in flat(h)
    assert "struct project_SolverParams\n{\n  int maxit = 0;\n  PROJECT_REAL tol = 0;\n  bool restart = false;\n" \
           "  PROJECT_REAL w[2] = {};\n};" in flat(h)
    assert "!typedef struct project_MySolver project_MySolver;\n" in binding and "!struct project_MySolver\n!{\n!  void* p_impl = nullptr;\n" in binding
    assert "  type, bind(C) :: MySolver_c\n    type(C_PTR) :: p_impl = C_NULL_PTR   !< the Fortran object\n" \
           "    integer(C_INT) :: n = 0\n    type(C_PTR) :: diag = C_NULL_PTR\n    integer(C_INT) :: diag_size = 0\n" \
           "    real(C_REAL) :: weights(3) = 0\n    type(SolverParams_c) :: params\n  end type MySolver_c" in binding
    assert "  type, bind(C) :: SolverParams_c\n    integer(C_INT) :: maxit = 0\n" in binding
    # setup / destroy (Fortran only): C struct with the constants, const getters only
    assert "  function project_MySolver_setup(obj) result(handle)\n    type(MySolver_type), target :: obj\n" in binding
    assert "    allocate(this_c)\n    this_c%p_impl = c_loc(obj)\n    this_c%n = obj%n\n    this_c%diag = C_NULL_PTR\n    this_c%diag_size = 0\n    if (allocated(obj%diag)) then\n" \
           "      this_c%diag = c_loc(obj%diag)\n      this_c%diag_size = size(obj%diag)\n    end if\n" \
           "    this_c%weights = obj%weights\n    this_c%params%maxit = obj%params%maxit\n" \
           "    this_c%params%tol = obj%params%tol\n    this_c%params%restart = logical(obj%params%restart, C_BOOL)\n" \
           "    this_c%params%w = obj%params%w\n    handle = c_loc(this_c)" in binding
    assert "  subroutine project_MySolver_destroy(handle)" in binding and "    deallocate(this_c)\n" in binding
    assert "set_constants" not in binding + cpp
    # struct constant: C++ copy of the class, member of the wrapper filled in the constructor
    assert "class SolverParams final\n{\npublic:\n  int maxit = 10;  //!< maximum number of iterations\n" in hpp
    assert "  //! iteration parameters\n  const SolverParams& params() const { return params_; }" in hpp
    assert "  explicit MySolver(project_MySolver& c);" in hpp and "  SolverParams params_;" in hpp
    assert "MySolver::MySolver(project_MySolver& c) : handle_(&c)\n{\n  params_.maxit = c.params.maxit;\n" in cpp
    assert "  params_.w = Eigen::Map<const vec>(c.params.w, 2);" in cpp
    assert "struct project_SolverParams;" not in hpp
    assert "  //! problem size\n  int n() const;\n  //! diagonal\n  Eigen::Map<const vec> diag() const;\n" in hpp
    assert "//!< constant" not in hpp
    assert "Eigen::Map<const vec> MySolver::weights() const\n{\n  return Eigen::Map<const vec>(handle_->weights, 3);" in cpp
    assert "Eigen::Map<const vec> MySolver::diag() const\n{\n  return Eigen::Map<const vec>(handle_->diag, handle_->diag_size);" in cpp
    assert "  // TODO(f2cpp): constant label not wrapped: character" in hpp
    assert "int& n()" not in hpp and "Eigen::Map<vec> diag()" not in hpp
    assert "    call c_f_pointer(mySolver, this_c)\n    call c_f_pointer(this_c%p_impl, obj)" in binding
    assert "namespace MySolver_private\n{\nstd::unique_ptr<MySolver> wrapFromC(project_MySolver& c);" in hpp
    assert "public:\n  MySolver() = default;  //!< empty object\n" in hpp
    assert "protected:\n  explicit MySolver(project_MySolver& c);  //!< the object created in Fortran\n" \
           "  friend std::unique_ptr<MySolver> MySolver_private::wrapFromC(project_MySolver& c);\n" \
           "  project_MySolver* handle_ = nullptr;" in hpp
    assert "std::unique_ptr<MySolver> MySolver_private::wrapFromC(project_MySolver& c)\n{\n" \
           "  return std::unique_ptr<MySolver>(new MySolver(c));  // new: protected constructor, make_unique has no access" in cpp
    assert '#ifdef DLLEXPORT\n  DLLEXPORT :: project_MySolver_apply\n#endif' in binding
    # the C interface is only included in the glue; the header declares the handle type itself
    assert '#include "mysolver_mod.h"' not in hpp and '#include "mysolver_mod.h"' in cpp
    assert "struct project_MySolver;  //!< C handle, see mysolver_mod.h" in hpp
    assert "  subroutine project_MySolver_apply(mySolver, x, y, ierr) bind(C, name=\"project_MySolver_apply\")" in binding
    assert "    real(C_REAL), intent(in), target :: x(*)\n" in binding and "    real, pointer, contiguous :: x_f(:)\n" in binding
    assert "    x_f => x(1:obj%n)\n    y_f => y(1:(obj%n))\n    call obj%apply(x_f, y_f, ierr)" in binding
    assert "    a_2d(1:obj%n, 1:2) => a(1:(obj%n) * (2))" in binding
    assert "    call obj%set_label(str_array_ptr_to_str(label), ierr)" in binding
    assert "  void apply(const vec& x, vec& y) const;" in hpp and "  //! spectral shift\n  scalar& shift();" in hpp
    assert "  //! spectral shift\n  scalar& shift();\n  //! spectral shift (const version)\n" in hpp
    assert "  //! component verbose\n  bool verbose() const;\n  //! component verbose (setter)\n  void verbose(bool value);\n" \
           "  //! component coupling\n  Eigen::Map<mat> coupling();\n" in hpp
    assert "void project_MySolver_verbose_set(project_MySolver* mySolver, bool value);" in flat(h)
    assert "    obj%verbose = value\n  end subroutine project_MySolver_verbose_set" in binding
    assert "void MySolver::verbose(bool value)\n{\n  project_MySolver_verbose_set(handle_, value);\n}" in cpp
    assert "  Eigen::Map<mat> coupling();\n  //! component coupling (const version)\n  Eigen::Map<const mat> coupling() const;" in hpp and "void stats(scalar& mn, scalar& mx, " \
           "std::optional<scalar> tol = std::nullopt) const;" in hpp
    assert '  if( x.size() != n() )\n    throw std::invalid_argument("MySolver::apply: wrong dimension of parameter x!");\n' \
           '  y.resize(x.size());\n  int ierr = 0;\n  project_MySolver_apply(handle_, x.data(), y.data(), &ierr);\n' \
           '  if( ierr != 0 )\n    throw std::runtime_error("Error: calling C function project_MySolver_apply in ' \
           'MySolver::apply!");' in cpp
    # module procedures
    assert "scalar residual_norm(const vec& r, const vec& b, int n);" in hpp
    if translated:                      # C++ implementations, no binding
        assert "project_residual_norm" not in h + binding and "project_MySolver_norm" not in h + binding
        assert "scalar residual_norm(const vec& r, const vec& b, int n)\n{\n  scalar res = (r - b).cwiseAbs().maxCoeff();" in cpp
        assert "scalar MySolver::norm(const vec& x) const\n{\n" in cpp and "diag()" in cpp
    else:
        assert "PROJECT_REAL project_residual_norm(const PROJECT_REAL* r, const PROJECT_REAL* b, int n);" in flat(h)
        assert "    r_f => r(1:n)\n    b_f => b(1:n)\n    res = residual_norm(r_f, b_f, n)" in binding
    # size unknown in Fortran (x(:) without check): passed from C++
    assert "void project_scale_vector(PROJECT_REAL* x, int x_n1, PROJECT_REAL factor, int* ierr);" in flat(h)
    assert "  project_scale_vector(x.data(), static_cast<int>(x.size()), factor, &ierr);" in cpp
    assert "    integer(C_INT), value :: x_n1\n" in binding
    # optional string: NULL pointer
    assert "int project_tagged_length(const char* msg, const char* tag);" in flat(h)
    assert "int tagged_length(const std::string& msg, const std::optional<std::string>& tag = std::nullopt);" in hpp
    assert "  return project_tagged_length(msg.c_str(), tag ? tag->c_str() : nullptr);" in cpp
    assert "    if (c_associated(tag)) allocate(tag_f, source=str_array_ptr_to_str(tag))\n" \
           "    res = tagged_length(str_array_ptr_to_str(msg), tag_f)" in binding
    user = read((d,), "solver_user_mod.cpp")
    assert "s.shift() = 0.25;" in user and "s.diag()(0)" in user and "r = r + residual_norm(y, x, x.size());" in user
    os.makedirs(os.path.join(d, "f"))
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS, "-J", "f"]
    for src, obj in ((os.path.join(SUPPORT, "project_str.F90"), "f/str.o"),
                     (os.path.join(w, "mysolver_mod.F90"), "f/mysolver.o"),
                     ("mysolver_mod_c_binding.F90", "f/binding.o"),
                     (os.path.join(SUPPORT, "mysolver_factory.F90"), "f/factory.o")):
        run(ff + [src, "-o", obj] + (["-Werror=realloc-lhs-all"] if "binding" in src else []), d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for src, obj in (("mysolver_mod.cpp", "glue.o"), ("solver_user_mod.cpp", "user.o"),
                     (os.path.join(SUPPORT, "wrap_driver.cpp"), "drv.o")):
        run(gxx + [src, "-o", obj], d)
    run(["g++", "glue.o", "user.o", "drv.o", "f/str.o", "f/mysolver.o", "f/binding.o", "f/factory.o",
         "-lgfortran", "-o", "cdrv"], d)
    res = run_exe(["./cdrv"], d).split("\n")
    run(ff + [os.path.join(w, "solver_user_mod.F90"), "-o", "ref_user.o"], d)
    run(["gfortran", "-fdefault-real-8", "-If", os.path.join(w, "ref_driver.F90"), "f/str.o", "f/mysolver.o",
         "ref_user.o", "-o", "fdrv"], d)
    ref = run_exe(["./fdrv"], d).split()
    vals = " ".join(res[:9]).split()
    ref, ref_flag = ref[:-1], ref[-1]
    assert len(vals) == len(ref) == 20
    for a, b in zip(vals, ref, strict=True):
        assert float(a) == pytest.approx(float(b), rel=1e-12, abs=1e-12)
    assert res[9] == "1" and ref_flag == "1"                         # logical setter
    assert res[10] == "MySolver::apply: wrong dimension of parameter x!"
    # logical arrays: bool copies; optional: NULL = absent (unallocated)
    assert "PROJECT_REAL project_MySolver_masked_sum(const project_MySolver* mySolver, const PROJECT_REAL* x, " \
           "const bool* modelmask, int modelmask_n1);" in flat(h)
    assert "    logical(C_BOOL), pointer, contiguous :: modelmask_c(:)\n    logical, allocatable :: modelmask_f(:)\n" in binding
    assert "    if (c_associated(modelmask)) then\n      call c_f_pointer(modelmask, modelmask_c, [modelmask_n1])\n" \
           "      allocate(modelmask_f(modelmask_n1))\n      modelmask_f(:) = modelmask_c\n    end if\n" in binding
    assert "modelMask.size() > 0 ? modelMask.data() : nullptr, modelMask.size() > 0 ? " \
           "static_cast<int>(modelMask.size()) : 0);" in cpp
    assert "      allocate(pos_f(obj%n))\n    end if\n    call obj%positive(x_f, pos_f)\n" \
           "    if (allocated(pos_f)) pos_c = logical(pos_f, C_BOOL)" in binding
    # optional arrays: NULL = absent
    assert "PROJECT_REAL project_MySolver_weighted(const project_MySolver* mySolver, const PROJECT_REAL* x, " \
           "PROJECT_REAL* r, const PROJECT_REAL* w);" in flat(h)
    assert "    type(C_PTR), value :: r\n" in binding and "    real, pointer, contiguous :: r_f(:)\n" in binding \
        and "=> null()\n    real" not in binding
    assert "    nullify(r_f)\n    if (c_associated(r)) call c_f_pointer(r, r_f, [obj%n])\n" in binding
    assert "res = obj%weighted(x_f, r_f, w_f)" in binding
    assert "  if( r )\n    r->get().resize(n());\n" in cpp
    assert "project_MySolver_weighted(handle_, x.data(), r ? r->get().data() : nullptr, " \
           "w.size() > 0 ? w.data() : nullptr);" in cpp


def test_wrapped_module_without_size_checks(tmp_path):
    w = os.path.join(CORPUS, "wrapped")
    translate([], cfg(), str(tmp_path), wrap=[os.path.join(w, "mysolver_mod.F90")])
    cpp = (tmp_path / "mysolver_mod.cpp").read_text()
    assert "invalid_argument" not in cpp and "  y.resize(x.size());\n  int ierr = 0;\n" in cpp
    hpp = (tmp_path / "mysolver_mod.hpp").read_text()
    assert "  explicit MySolver(project_MySolver& c) : handle_(&c) {}  //!< the object created in Fortran" in hpp


def test_c_names_follow_the_namespace(tmp_path):
    w = os.path.join(CORPUS, "wrapped")
    translate([], cfg(namespace="Flow::detail"), str(tmp_path), wrap=[os.path.join(w, "mysolver_mod.F90")])
    h, binding = (tmp_path / "mysolver_mod.h").read_text(), (tmp_path / "mysolver_mod_c_binding.F90").read_text()
    assert "typedef struct flow_MySolver flow_MySolver;\n" in h and "struct flow_MySolver\n{" in h and "#define FLOW_REAL double" in flat(h)
    assert "void flow_MySolver_apply(const flow_MySolver* mySolver, const FLOW_REAL* x, FLOW_REAL* y, int* ierr);" in flat(h)
    assert "#ifdef FLOW_SINGLE_PRECISION" in binding and "  use flow_str, only: str_array_ptr_to_str" in binding


def test_exclude_parts_of_a_module(tmp_path):
    from f2cpp.cli import main
    (tmp_path / "rec_mod.F90").write_text("""module rec_mod
  implicit none
  type :: record_type
    integer :: id = 0
    real :: value = 0.0
  contains
    procedure :: show
  end type
  type :: internal_type
    integer :: k
  end type
  integer, parameter :: max_count = 10
  real :: scratch(3)
contains
  subroutine show(this)
    class(record_type), intent(in) :: this
  end subroutine show
  subroutine read_records(r)
    type(record_type), intent(out) :: r(:)
  end subroutine read_records
  subroutine write_records(r)
    type(record_type), intent(in) :: r(:)
  end subroutine write_records
  function keep_me() result(k)
    integer :: k
    k = 1
  end function keep_me
end module rec_mod
""")
    (tmp_path / "x.toml").write_text('[modules]\nrec_mod.exclude = ["*_records", "internal_type"]\n'
                                     '"*".exclude = ["record_type%show"]\n')
    main([str(tmp_path / "rec_mod.F90"), "-o", str(tmp_path / "out"), "-c", str(tmp_path / "x.toml"),
          "-x", "rec_mod:scratch"])
    hpp, cpp = (tmp_path / "out" / "rec_mod.hpp").read_text(), (tmp_path / "out" / "rec_mod.cpp").read_text()
    assert "class record final" in hpp and "inline const int max_count = 10;" in hpp
    # a data type without (emitted) methods keeps its members public
    assert "class record final\n{\npublic:\n\n  int id = 0;\n  scalar value = 0.0;\n};" in hpp
    assert "int keep_me();" in hpp and "_records" not in hpp + cpp
    assert "internal" not in hpp and "scratch" not in hpp + cpp and "show" not in hpp + cpp


def test_includes_only_where_needed(tmp_path):
    (tmp_path / "a_mod.F90").write_text("module a_mod\ntype :: pt_type\nreal :: x\nend type\n"
                                        "integer, parameter :: na = 2\nend module\n")
    (tmp_path / "b_mod.F90").write_text("module b_mod\ncontains\nfunction twice(k) result(r)\ninteger :: k, r\n"
                                        "r = 2 * k\nend function\nend module\n")
    (tmp_path / "c_mod.F90").write_text("module c_mod\nend module\n")
    (tmp_path / "u_mod.F90").write_text(
        "module u_mod\nuse a_mod, only: pt_type, na\nuse b_mod, only: twice\nuse c_mod\n"
        "contains\nfunction f(p) result(r)\ntype(pt_type), intent(in) :: p\nreal :: r\n"
        "r = p%x * twice(na)\nend function\n"
        "subroutine g()\nuse c_mod\nend subroutine\nend module\n")
    translate([str(tmp_path / f) for f in ("u_mod.F90",)], config.load(), str(tmp_path),
              [str(tmp_path / f) for f in ("a_mod.F90", "b_mod.F90", "c_mod.F90")])
    hpp, cpp = (tmp_path / "u_mod.hpp").read_text(), (tmp_path / "u_mod.cpp").read_text()
    assert '#include "a_mod.hpp"' in hpp                               # pt in the signature
    assert '#include "b_mod.hpp"' in cpp and "b_mod" not in hpp        # twice only in the body
    assert '#include "c_mod.hpp"' in hpp                               # no only-list: kept


@needs_cxx
@pytest.mark.skipif(not shutil.which("gfortran"), reason="gfortran missing")
def test_wrapped_class_hierarchy(tmp_path):
    """root (abstract, deferred method) and derived class in two wrapped modules"""
    import math
    d = str(tmp_path)
    w = os.path.join(CORPUS, "wrapped")
    translate([], cfg(), d, wrap=[os.path.join(w, "body_mod.F90"), os.path.join(w, "sphere_mod.F90")])
    bh, sh = read((d,), "body_mod.hpp"), read((d,), "sphere_mod.hpp")
    sb = read((d,), "sphere_mod_c_binding.F90")
    assert "class body\n{" in bh and "  scalar volume() const;" in bh and "  project_body* handle_ = nullptr;" in bh
    assert "class sphere final : public body\n{" in sh and "  using body::body;" in sh
    assert "  friend std::unique_ptr<sphere> sphere_private::wrapFromC(project_body& c);" in sh
    assert "explicit sphere" not in sh                                 # body(project_body&) is inherited
    assert "volume" not in sh and "handle_" not in sh                  # overrides dispatch in the base class
    assert "  type :: bodyWrapper_type\n    class(body_type), pointer :: ptr => null()" in \
        read((d,), "body_mod_c_binding.F90")
    assert "  use body_mod_c_binding, only: bodyWrapper_type, body_c" in sb
    assert "    call c_f_pointer(sphere, this_c)\n    call c_f_pointer(this_c%p_impl, wrapper)\n" in sb and '#include "body_mod.h"' in read((d,), "sphere_mod.h")
    assert "    select type (obj => wrapper%ptr)\n    class is (sphere_type)\n      call obj%grow(dr, ierr)\n" \
           "    class default\n      ierr = -1\n      return\n    end select" in sb
    # file headers: original header, @file renamed, @brief prefixed
    assert read((d,), "body_mod_c_binding.F90").startswith(
        "!> @file body_mod_c_binding.F90\n!> @brief C binding: Abstract bodies")
    assert read((d,), "body_mod.cpp").startswith("//! @file body_mod.cpp\n//! @brief C++ wrapper: Abstract bodies")
    assert bh.startswith("//! @file body_mod.hpp\n//! @brief Abstract bodies")
    os.makedirs(os.path.join(d, "f"))
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS, "-J", "f"]
    objs = []
    for src in (os.path.join(w, "body_mod.F90"), os.path.join(w, "sphere_mod.F90"), "body_mod_c_binding.F90",
                "sphere_mod_c_binding.F90", os.path.join(SUPPORT, "body_factory.F90")):
        objs.append("f/" + os.path.basename(src) + ".o")
        run(ff + [src, "-o", objs[-1]], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for src in ("body_mod.cpp", "sphere_mod.cpp", os.path.join(SUPPORT, "body_driver.cpp")):
        objs.append(os.path.basename(src) + ".o")
        run(gxx + [src, "-o", objs[-1]], d)
    run(["g++"] + objs + ["-lgfortran", "-o", "drv"], d)
    lines = run_exe(["./drv"], d).split("\n")
    vol = 4.0 / 3.0 * math.pi * 8.0
    got = [float(x) for x in (lines[0] + " " + lines[1]).split()]
    vol2 = 4.0 / 3.0 * math.pi * 2.5 ** 3
    for a, b in zip(got, [vol, 3.0 / vol, 4.0 * math.pi * 4.0, 2.5, 6.0, 6.0 / vol2], strict=True):
        assert a == pytest.approx(b, rel=1e-12)
    assert lines[2] == "Error: calling C function project_sphere_grow in sphere::grow!"


def test_wrapped_class_with_hand_wrapped_base(tmp_path):
    """only the derived class is wrapped; the base module (wrapped by hand) is given with -I"""
    w = os.path.join(CORPUS, "wrapped")
    c = cfg()
    c["module_headers"] = {"body_mod": "body_wrapper.hpp"}
    translate([], c, str(tmp_path), [os.path.join(w, "body_mod.F90")], wrap=[os.path.join(w, "sphere_mod.F90")])
    sh = (tmp_path / "sphere_mod.hpp").read_text()
    assert '#include "body_wrapper.hpp"' in sh and "class sphere final : public body" in sh
    assert "  use body_mod_c_binding, only: bodyWrapper_type, body_c" in \
        (tmp_path / "sphere_mod_c_binding.F90").read_text()


def test_wrapped_header_forward_declarations(tmp_path):
    (tmp_path / "pair_mod.F90").write_text("""module pair_mod
  implicit none
  type :: node_type
    integer :: id = 0
  contains
    procedure :: link
  end type
  type :: edge_type
    integer :: weight = 1
  end type
  abstract interface
    function cost_iface(e) result(c)
      import :: edge_type
      type(edge_type), intent(in) :: e
      real :: c
    end function
  end interface
contains
  subroutine link(this, e, f)
    class(node_type), intent(inout) :: this
    type(edge_type), intent(in) :: e
    procedure(cost_iface) :: f
  end subroutine
end module pair_mod
""")
    translate([], cfg(), str(tmp_path), wrap=[str(tmp_path / "pair_mod.F90")])
    hpp = (tmp_path / "pair_mod.hpp").read_text()
    assert "class node;\nclass edge;\n" in hpp and hpp.index("class edge;") < hpp.index("class node final")
    assert "using cost_iface = std::function<scalar(const edge& e)>;" in hpp and "#include <functional>" in hpp
    assert "void link(const edge& e, const cost_iface& f);" in hpp       # not wrapped (objects): declared


def test_wrapped_hierarchy_below_an_independent_base(tmp_path):
    """A exists in Fortran and C++ (not wrapped), B is wrapped (by hand: -I), C is wrapped now"""
    w = os.path.join(CORPUS, "wrapped")
    c = cfg()
    c["wrap_independent_bases"] = ["a_type"]
    c["module_headers"] = {"a_mod": "serialization/a.hpp", "b_mod": "b_wrapper.hpp"}
    translate([], c, str(tmp_path), [os.path.join(w, "a_mod.F90"), os.path.join(w, "b_mod.F90")],
              wrap=[os.path.join(w, "c_mod.F90")])
    hpp, b = (tmp_path / "c_mod.hpp").read_text(), (tmp_path / "c_mod_c_binding.F90").read_text()
    assert "struct project_B;" in hpp and "class C final : public B" in hpp
    assert "  using B::B;" in hpp and "write_state" not in hpp
    assert "  use b_mod_c_binding, only: BWrapper_type, B_c" in b and "project_A" not in b + hpp
    # B wrapped as well: B is the root, its override of A's method is wrapped in B
    translate([], c, str(tmp_path), [os.path.join(w, "a_mod.F90")],
              wrap=[os.path.join(w, "b_mod.F90"), os.path.join(w, "c_mod.F90")])
    bh, bb = (tmp_path / "b_wrapper.hpp").read_text(), (tmp_path / "b_wrapper_c_binding.F90").read_text()
    assert "class B : public A" in bh and "explicit B(project_B& c) : handle_(&c) {}" in bh
    assert "  void write_state() const;" in bh and "  friend std::unique_ptr<B> B_private::wrapFromC(project_B& c);\n  project_B* handle_ = nullptr;" in bh
    assert "  int& someInteger();\n  //! component someInteger (const version)\n  const int& someInteger() const;" in bh and "scale" not in bh.replace("rescale", "")
    assert "res = c_loc(obj%someinteger)" in bb
    assert "  // TODO(f2cpp): no getter for component listOfOther (type(A_type), rank 1, allocatable;" in bh
    assert "  // TODO(f2cpp): binding version not wrapped: nopass" in bh
    assert "// TODO(f2cpp): module variable b_count not wrapped" in bh
    assert "type :: BWrapper_type\n    class(B_type), pointer :: ptr => null()" in bb
    assert "    obj => wrapper%ptr\n    call obj%write_state(ierr)" in bb


def test_wrapped_constant_of_a_translated_type(tmp_path):
    """struct constant whose type is in another (translated) module: its C++ class is the copy"""
    w = os.path.join(CORPUS, "wrapped")
    translate([os.path.join(w, "offsets_mod.F90")], cfg(wrap_constants={"holder_type": ["offsets"]},
                                                       exclude=["offsets_type%tag"]), str(tmp_path),
              wrap=[os.path.join(w, "holder_mod.F90")])
    hpp, cpp = (tmp_path / "holder_mod.hpp").read_text(), (tmp_path / "holder_mod.cpp").read_text()
    h, b = (tmp_path / "holder_mod.h").read_text(), (tmp_path / "holder_mod_c_binding.F90").read_text()
    assert '#include "offsets_mod.hpp"' in hpp and "class Offsets" not in hpp
    assert "  //! position of the block\n  const Offsets& offsets() const { return offsets_; }" in hpp
    assert "struct project_Offsets\n{\n  int row = 0;\n  int col = 0;\n  PROJECT_REAL w[2] = {};\n" \
           "  PROJECT_REAL* scale = nullptr;\n  int scale_size = 0;\n  int* idx = nullptr;\n  int idx_size = 0;\n" \
           "  bool mask[2] = {};\n  bool* active = nullptr;\n  int active_size = 0;\n};" in flat(h)
    # logical arrays: copied into logical(C_BOOL) (the buffer is released in destroy)
    assert "    this_c%offsets%mask = logical(obj%offsets%mask, C_BOOL)\n" in b
    assert "      allocate(buf1(size(obj%offsets%active)))\n      buf1 = logical(obj%offsets%active, C_BOOL)\n" in b
    assert "      call c_f_pointer(this_c%offsets%active, buf1, [this_c%offsets%active_size])\n" \
           "      deallocate(buf1)" in b
    assert "  offsets_.active = Eigen::Map<const Eigen::Array<bool, Eigen::Dynamic, 1>>(c.offsets.active, " \
           "c.offsets.active_size);" in cpp
    assert "tag" not in h + cpp and "TODO(f2cpp):" not in hpp
    assert "    if (allocated(obj%offsets%scale)) then\n      this_c%offsets%scale = c_loc(obj%offsets%scale)\n" in b
    assert "    this_c%offsets%row = obj%offsets%row\n" in b
    assert "  offsets_.row = c.offsets.row;\n  offsets_.col = c.offsets.col;\n" \
           "  offsets_.w = Eigen::Map<const vec>(c.offsets.w, 2);\n" \
           "  offsets_.scale = Eigen::Map<const vec>(c.offsets.scale, c.offsets.scale_size);\n" in cpp
    assert "  offsets_.idx = Eigen::Map<const Eigen::VectorXi>(c.offsets.idx, c.offsets.idx_size);" in cpp
    if shutil.which("g++") and os.path.isdir(EIGEN):
        run(["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-fsyntax-only",
             "holder_mod.cpp"], str(tmp_path))
    if shutil.which("gfortran"):
        ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS]
        for src in (os.path.join(w, "offsets_mod.F90"), os.path.join(w, "holder_mod.F90"), "holder_mod_c_binding.F90"):
            run(ff + [src], str(tmp_path))


def test_wrapped_constants_of_unwrapped_companions(tmp_path):
    """wrapped type with an array of an unwrapped type: the companions obj%arr(:)%offset are gathered into
    C buffers by setup (const getters only)"""
    d = str(tmp_path)
    (tmp_path / "x.toml").write_text('[types.Block_type]\nunwrap = ["p", "offset1", "offset2"]\n'
                                     '[types.Blocks_type]\nconstants = ["arr_offset1", "arr_offset2"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    src = os.path.join(CORPUS, "unwrap", "blocks_mod.F90")
    translate([os.path.join(CORPUS, "unwrap", "blocks_user_mod.F90")], c, d, wrap=[src])
    hpp = (tmp_path / "blocks_mod.hpp").read_text()
    user = (tmp_path / "blocks_user_mod.cpp").read_text()       # translated code using the wrapped class
    assert "for (int i = 0; i < b.arr_offset1().size(); ++i)" in user
    assert "sum_offsets = sum_offsets + b.arr_offset1()[i];" in user
    b, h = (tmp_path / "blocks_mod_c_binding.F90").read_text(), (tmp_path / "blocks_mod.h").read_text()
    assert "  int* arr_offset1 = nullptr;\n  int arr_offset1_size = 0;\n" in flat(h)
    assert "    if (allocated(obj%arr)) then\n      allocate(buf1(size(obj%arr)))\n      buf1 = obj%arr(:)%offset1\n" \
           "      if (size(buf1) > 0) this_c%arr_offset1 = c_loc(buf1(1))\n      this_c%arr_offset1_size = size(obj%arr)" in b
    assert "obj%arr_offset" not in b and "  //! first row\n  Eigen::Map<const Eigen::VectorXi> arr_offset1() const;" in hpp
    assert "Eigen::Map<Eigen::VectorXi> arr_offset1()" not in hpp
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""program drv
  use, intrinsic :: iso_c_binding
  use blocks_mod
  use blocks_mod_c_binding, only: project_Blocks_setup, project_Blocks_destroy
  implicit none
  interface
    subroutine print_offsets(h) bind(C, name="print_offsets")
      import :: C_PTR
      type(C_PTR), value :: h
    end subroutine
  end interface
  type(Blocks_type), target :: bl
  type(C_PTR) :: h
  call bl%init(3)
  h = project_Blocks_setup(bl)
  call print_offsets(h)
  call project_Blocks_destroy(h)
end program drv
""")
    (tmp_path / "print.cpp").write_text('#include "blocks_mod.hpp"\n#include "blocks_mod.h"\n#include "blocks_user_mod.hpp"\n'
                                        '#include <cstdio>\n'
                                        'extern "C" void print_offsets(project_Blocks* h)\n{\n'
                                        '  auto b = project::Blocks_private::wrapFromC(*h);\n'
                                        '  for (int i = 0; i < b->arr_offset1().size(); ++i)\n'
                                        '    std::printf("%d %d\\n", b->arr_offset1()(i), b->arr_offset2()(i));\n'
                                        '  std::printf("%d\\n", project::sum_offsets(*b));\n}\n')
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS]
    for f in (src, "blocks_mod_c_binding.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f, o in (("blocks_mod.cpp", "glue.o"), ("print.cpp", "print.o"), ("blocks_user_mod.cpp", "user.o")):
        run(gxx + [f, "-o", o], d)
    run(["gfortran", "blocks_mod.o", "blocks_mod_c_binding.o", "drv.o", "glue.o", "print.o", "user.o", "-lstdc++",
         "-o", "m"], d)
    assert run_exe(["./m"], d).split() == ["10", "11", "20", "21", "30", "31", "60"]


def test_wrapped_out_array_size_only_in_fortran(tmp_path):
    """intent(out) array with a size from wrap_sizes (Fortran text): no silent missing resize"""
    (tmp_path / "q_mod.F90").write_text("""module q_mod
  implicit none
  type :: Q_type
    integer :: m = 0
  contains
    procedure :: fill => q_fill
  end type Q_type
contains
  subroutine q_fill(this, y)
    class(Q_type), intent(in) :: this
    real, intent(out) :: y(:)
    y = 1.0
  end subroutine q_fill
end module q_mod
""")
    translate([], cfg(wrap_sizes={"q_type%fill": {"y": "this%m"}}), str(tmp_path), wrap=[str(tmp_path / "q_mod.F90")])
    cpp, b = (tmp_path / "q_mod.cpp").read_text(), (tmp_path / "q_mod_c_binding.F90").read_text()
    assert "y_f => y(1:obj%m)\n    call obj%fill(y_f)" in b
    assert "  // TODO(f2cpp): size of y (obj%m in Fortran) not translatable: resize y before the call" in cpp


def test_wrapped_owned_objects_in_arrays(tmp_path):
    """black-box objects (wrap_owned): std::vector of C++ objects that own their Fortran object"""
    d, w = str(tmp_path), os.path.join(CORPUS, "wrapped")
    (tmp_path / "x.toml").write_text('[types.InternalStateWrapper_type]\nowned = true\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    src = os.path.join(w, "state_mod.F90")
    translate([], c, d, wrap=[src])
    hpp, cpp = (tmp_path / "state_mod.hpp").read_text(), (tmp_path / "state_mod.cpp").read_text()
    b, h = (tmp_path / "state_mod_c_binding.F90").read_text(), (tmp_path / "state_mod.h").read_text()
    assert "  ~InternalStateWrapper();  //!< frees the Fortran object\n" \
           "  InternalStateWrapper(InternalStateWrapper&& o) noexcept;  //!< takes over the Fortran object of o\n" in hpp
    assert "  //! Fortran model_save (undocumented)\n  void save(std::vector<InternalStateWrapper>& checkpoints) const;\n" \
           "  //! Fortran model_restore (undocumented)\n  void restore(const std::vector<InternalStateWrapper>& checkpoints);" in hpp
    assert "void project_Model_save(const project_Model* model, project_InternalStateWrapper** checkpoints, int* ierr);" in flat(h)
    assert "void project_InternalStateWrapper_free(project_InternalStateWrapper* internalStateWrapper);" in flat(h)
    assert "        allocate(checkpoints_e, source=checkpoints_f(checkpoints_i))\n" \
           "        checkpoints(checkpoints_i) = project_InternalStateWrapper_setup(checkpoints_e)" in b
    assert "      checkpoints_f(checkpoints_i) = checkpoints_e" in b
    assert "  std::vector<project_InternalStateWrapper*> checkpoints_h(nCheckpoints(), nullptr);" in cpp
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""program drv
  use, intrinsic :: iso_c_binding
  use state_mod
  use state_mod_c_binding, only: project_Model_setup, project_Model_destroy
  implicit none
  interface
    subroutine run(h) bind(C, name="run")
      import :: C_PTR
      type(C_PTR), value :: h
    end subroutine
  end interface
  type(Model_type), target :: m
  type(C_PTR) :: h
  m%x = 1.5
  h = project_Model_setup(m)
  call run(h)
  call project_Model_destroy(h)
  print '(f6.2)', m%x
end program drv
""")
    (tmp_path / "run.cpp").write_text(
        '#include "state_mod.hpp"\n#include "state_mod.h"\n#include <cstdio>\n'
        'extern "C" void run(project_Model* h)\n{\n'
        '  auto m = project::Model_private::wrapFromC(*h);\n'
        '  std::vector<project::InternalStateWrapper> cp;\n'
        '  m->save(cp);\n'
        '  std::printf("%d\\n", static_cast<int>(cp.size()));\n'
        '  auto moved = std::move(cp);                 // owned: moving keeps one owner\n'
        '  m->x() = 7.0;\n'
        '  m->restore(moved);                          // last checkpoint: 2 * 1.5\n'
        '}\n')
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS]
    for f in (src, "state_mod_c_binding.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f, o in (("state_mod.cpp", "glue.o"), ("run.cpp", "run.o")):
        run(gxx + [f, "-o", o], d)
    run(["gfortran", "state_mod.o", "state_mod_c_binding.o", "drv.o", "glue.o", "run.o", "-lstdc++", "-o", "m"], d)
    assert run_exe(["./m"], d).split() == ["2", "3.00"]


def test_wrapped_listener(tmp_path):
    """abstract listener type implemented in C++: Fortran bridge type calling private static trampolines of the
    C++ class through function pointers (no C++ symbol referenced by the Fortran library)"""
    d, w = str(tmp_path), os.path.join(CORPUS, "wrapped")
    (tmp_path / "x.toml").write_text('style = "s.toml"\n[types.AbstractListener_type]\ninterop = "listener"\n')
    (tmp_path / "s.toml").write_text('[errors]\nlog = \'project_log_error("{class}", ("{message}: " + '
                                     'std::string({what})).c_str());\'\nlog_includes = ["<string>", \'"project_log.hpp"\']\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    src = os.path.join(w, "listen_mod.F90")
    translate([], c, d, wrap=[src])
    hpp, cpp = (tmp_path / "listen_mod.hpp").read_text(), (tmp_path / "listen_mod.cpp").read_text()
    b, h = (tmp_path / "listen_mod_c_binding.F90").read_text(), (tmp_path / "listen_mod.h").read_text()
    assert "  virtual void state_changed(const Thing& thing, scalar old, const vec& history) = 0;" in hpp
    assert "private:\n  //! internal wrapper for state_changed\n" \
           "  static void state_changed_wrapper(void* userData, void* thing, scalar old, const scalar* history, " \
           "int history_n, int* ierr) noexcept;" in hpp
    assert "  //! internal wrapper for state_changed\n  static void state_changed_wrapper(" in hpp
    assert "  void set_listener(std::optional<std::reference_wrapper<AbstractListener>> listener = std::nullopt);" in hpp
    assert "typedef void (*project_AbstractListener_state_changed)(void*, void*, PROJECT_REAL, const PROJECT_REAL*, " \
           "int, int*);" in flat(h)
    assert "project_AbstractListener* project_AbstractListener_create(void* userData, " \
           "project_AbstractListener_state_changed state_changed_fn);" in flat(h)
    assert "  abstract interface\n!@c_declaration\n!typedef void (*project_AbstractListener_state_changed)(" in b
    assert "!@end_c_declaration\n    !> AbstractListener::state_changed_wrapper\n" \
           "    subroutine project_AbstractListener_state_changed(userData, thing, old, history, history_n, ierr) bind(C)" in b
    assert "AbstractListener::AbstractListener() : fortran_(project_AbstractListener_create(this, " \
           "&AbstractListener::state_changed_wrapper))" in cpp
    assert "    type(C_FUNPTR) :: state_changed_fn = C_NULL_FUNPTR" in b
    assert "    call c_f_procpointer(this%state_changed_fn, f)\n" \
           "    call f(this%userData, this%user, old, history, int(size(history), C_INT), ierr)\n" in b
    assert "    if (.not. listener_same(this%owner, thing)) then\n      ierr = -1\n      return\n    end if" in b
    assert "extern" not in cpp and "    *ierr = -1;" in cpp
    # error logging (log_error): instead of std::fprintf
    assert '    project_log_error("AbstractListener", ("encountered exception in state_changed: " + ' \
           'std::string(e.what())).c_str());' in cpp and "fprintf" not in cpp
    assert '  catch (...)\n  {\n    project_log_error("AbstractListener", ("encountered exception in state_changed: " + ' \
           'std::string("unknown exception")).c_str());\n' \
           '    *ierr = -1;' in cpp and "#include <string>" in cpp
    assert '#include "project_log.hpp"' in cpp and "<cstdio>" not in cpp
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""program drv
  use, intrinsic :: iso_c_binding
  use listen_mod
  use listen_mod_c_binding, only: project_Thing_setup, project_Thing_destroy
  implicit none
  interface
    subroutine run(h1, h2) bind(C, name="run")
      import :: C_PTR
      type(C_PTR), value :: h1, h2
    end subroutine
  end interface
  type(Thing_type), target :: t1, t2
  type(C_PTR) :: h1, h2
  h1 = project_Thing_setup(t1)
  h2 = project_Thing_setup(t2)
  call run(h1, h2)
  call project_Thing_destroy(h1)
  call project_Thing_destroy(h2)
  print '(l1)', associated(t1%listener) .or. associated(t2%listener)
end program drv
""")
    (tmp_path / "run.cpp").write_text(r"""#include "listen_mod.hpp"
#include "listen_mod.h"
#include <cstdio>
#include <stdexcept>
struct L : project::AbstractListener
{
  const project::Thing* expected = nullptr;
  void state_changed(const project::Thing& thing, project::scalar old, const project::vec& history) override
  {
    std::printf("%g %g %g %d\n", thing.value(), old, history(1), &thing == expected ? 1 : 0);
    if (thing.value() > 3.5) throw std::runtime_error("too large");
  }
};
extern "C" void run(project_Thing* h1, project_Thing* h2)
{
  auto t1 = project::Thing_private::wrapFromC(*h1);
  auto t2 = project::Thing_private::wrapFromC(*h2);
  L l;
  l.expected = t1.get();
  t1->set_listener(std::ref(l));
  t1->set_value(2.0);                 // the owner itself: 2 0 2 1
  try
  {
    t1->set_value(4.0);               // the listener throws -> ierr -> exception
  }
  catch (const std::exception&)
  {
    std::printf("caught\n");
  }
  t2->set_listener(std::ref(l));      // owner now t2: a change of t1 is an error
  try
  {
    t1->set_value(3.0);
  }
  catch (const std::exception&)
  {
    std::printf("other\n");
  }
  t1->set_listener(std::nullopt);
  t2->set_listener(std::nullopt);
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-I" + CORPUS]
    for f in (src, "listen_mod_c_binding.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f, o in (("listen_mod.cpp", "glue.o"), ("run.cpp", "run.o")):
        run(gxx + [f, "-o", o], d)
    # the Fortran objects alone: no undefined C++ symbols
    undefined = run(["nm", "-u", "listen_mod_c_binding.o"], d)
    assert "AbstractListener" not in undefined.replace("project_AbstractListener", "")
    run(["gfortran", "listen_mod.o", "listen_mod_c_binding.o", "drv.o", "glue.o", "run.o", "-lstdc++", "-o", "m"], d)
    assert run_exe(["./m"], d).split("\n")[:5] == ["2 0 2 1", "4 2 4 1", "caught", "other", "F"]


def test_wrapped_handle_member_name(tmp_path):
    """name of the handle member per root type (hand-written base classes); derived classes use it"""
    (tmp_path / "x.toml").write_text('[types.body_type]\nhandle_member = "body_c_"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    w = os.path.join(CORPUS, "wrapped")
    translate([], c, str(tmp_path), wrap=[os.path.join(w, "body_mod.F90"), os.path.join(w, "sphere_mod.F90")])
    bh, bc = (tmp_path / "body_mod.hpp").read_text(), (tmp_path / "body_mod.cpp").read_text()
    sc = (tmp_path / "sphere_mod.cpp").read_text()
    assert "  explicit body(project_body& c) : body_c_(&c) {}" in bh and "  project_body* body_c_ = nullptr;" in bh
    assert "handle_" not in bh + bc + sc and "(body_c_" in sc
    if shutil.which("g++") and os.path.isdir(EIGEN):
        for f in ("body_mod.cpp", "sphere_mod.cpp"):
            run(["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-fsyntax-only", f],
                str(tmp_path))


def test_wrapped_long_names(tmp_path):
    """bind(C) routines whose C name is longer than 63 characters: shortened Fortran name, full label"""
    (tmp_path / "lng_mod.F90").write_text("""module lng_mod
  implicit none
  type :: VeryLongClassNameForTesting_type
    real :: x = 1.0
  contains
    procedure :: compute_something_with_a_rather_long_name => lng_compute
  end type VeryLongClassNameForTesting_type
contains
  real function lng_compute(this)
    class(VeryLongClassNameForTesting_type), intent(in) :: this
    lng_compute = 2.0 * this%x
  end function lng_compute
end module lng_mod
""")
    translate([], cfg(), str(tmp_path), wrap=[str(tmp_path / "lng_mod.F90")])
    b = (tmp_path / "lng_mod_c_binding.F90").read_text()
    cname = "project_VeryLongClassNameForTesting_compute_something_with_a_rather_long_name"
    assert len(cname) > 63 and f'bind(C, name="{cname}")' in b and f"\n!  PROJECT_REAL {cname}(\n!      const project_VeryLongClassNameForTesting* " in b and "\n!@c_declaration\n" in b and "  !@c_declaration" not in b
    names = re.findall(r"^\s*(?:end )?(?:subroutine|function)\s+(\w+)", b, flags=re.M)
    assert names and max(len(n) for n in names) <= 63
    # free form line limit (comments included): long code lines continued, C declarations one parameter per line
    assert max(len(line) for line in b.split("\n")) <= 132
    assert ' &\n      & bind(C, name="project_VeryLongClassNameForTesting_compute_something_with_a_rather_long_name")' in b
    if shutil.which("gfortran"):                # default line length (132)
        run(["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-I" + CORPUS,
             str(tmp_path / "lng_mod.F90"), "lng_mod_c_binding.F90"], str(tmp_path))


def test_wrapped_unlimited_polymorphic_state(tmp_path):
    """class(*) state of a wrapped class: Fortran data in a std::any (FortranState) through C++ and back"""
    d, w = str(tmp_path), os.path.join(CORPUS, "wrapped")
    src = os.path.join(w, "fstate_mod.F90")
    translate([], cfg(), d, wrap=[src])
    hpp, cpp = (tmp_path / "fstate_mod.hpp").read_text(), (tmp_path / "fstate_mod.cpp").read_text()
    b = (tmp_path / "fstate_mod_c_binding.F90").read_text()
    assert "  void get_state(std::any& state) const;" in hpp and "  void set_state(const std::any& state);" in hpp
    h = (tmp_path / "fstate_mod.h").read_text()
    # one class(*) mechanism: the struct project_<module>_Any (Fortran data: AnyHolder_type, deleted in Fortran)
    assert "struct project_fstate_mod_Any;" in hpp and "FortranState" not in hpp + cpp and "FortranAny" not in h + b
    assert "std::any any_from_fortran(project_fstate_mod_Any* c);" in hpp
    assert "void project_Counter_get_state(const project_Counter* counter, project_fstate_mod_Any** state, " \
           "int* ierr);" in flat(h)
    assert "void project_Counter_set_state(project_Counter* counter, const project_fstate_mod_Any* state, " \
           "int* ierr);" in flat(h)
    assert "  state = fstate_mod_private::any_from_fortran(state_h);" in cpp
    assert "project_Counter_set_state(handle_, fstate_mod_private::any_to_fortran(state), &ierr);" in cpp
    assert "    if (f2cpp_any_holder(state, state_h)) call move_alloc(state_h%p, state_f)\n" \
           "    call obj%set_state(state_f, ierr)\n    if (associated(state_h)) call move_alloc(state_f, state_h%p)" in b
    assert "    state = C_NULL_PTR\n" in b and "    state = f2cpp_any_new(state_f)\n" in b
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""program drv
  use, intrinsic :: iso_c_binding
  use fstate_mod
  use fstate_mod_c_binding, only: project_Counter_setup, project_Counter_destroy
  implicit none
  interface
    subroutine run(h) bind(C, name="run")
      import :: C_PTR
      type(C_PTR), value :: h
    end subroutine
  end interface
  type(Counter_type), target :: c
  type(C_PTR) :: h
  c%n = 5
  h = project_Counter_setup(c)
  call run(h)
  call project_Counter_destroy(h)
  print '(i0)', c%n
end program drv
""")
    (tmp_path / "run.cpp").write_text(r"""#include "fstate_mod.hpp"
#include "fstate_mod.h"
#include <cstdio>
extern "C" void run(project_Counter* h)
{
  auto c = project::Counter_private::wrapFromC(*h);
  std::any st;
  c->get_state(st);                   // Fortran data: FortranState in the std::any
  std::any copy = st;                 // copies share it
  c->n() = 99;
  c->set_state(copy);                 // back to Fortran: 5
  std::printf("%d\n", c->n());
  try
  {
    c->set_state(std::any(3));        // C++ data cannot be passed to Fortran -> std::invalid_argument
  }
  catch (const std::exception&)
  {
    std::printf("caught\n");
  }
  std::any none;
  c->empty_state(none);               // unallocated -> empty std::any
  std::printf("%s\n", none.has_value() ? "value" : "empty");
  try
  {
    c->set_state(none);               // empty -> unallocated in Fortran -> ierr -> exception
  }
  catch (const std::runtime_error&)
  {
    std::printf("caught empty\n");
  }
  std::any r;
  c->real_state(r);
  for (int k = 0; k < 2; ++k)         // rejected by Fortran (ierr): the data goes back to its holder each time
  {
    try
    {
      c->set_state(r);
    }
    catch (const std::runtime_error&)
    {
      std::printf("caught real\n");
    }
  }
  c->set_state(copy);                 // the first state is still intact
  std::printf("%d\n", c->n());
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (src, "fstate_mod_c_binding.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f, o in (("fstate_mod.cpp", "glue.o"), ("run.cpp", "run.o")):
        run(gxx + [f, "-o", o], d)
    run(["gfortran", "fstate_mod.o", "fstate_mod_c_binding.o", "drv.o", "glue.o", "run.o", "-lstdc++", "-o", "m"], d)
    assert run_exe(["./m"], d).split("\n")[:8] == ["5", "caught", "empty", "caught empty", "caught real", "caught real",
                                                  "5", "5"]
    if shutil.which("valgrind"):
        out = subprocess.run(["valgrind", "--error-exitcode=3", "--leak-check=full", "./m"], cwd=d,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr                      # no errors, no leaks (counted as errors)


def test_wrap_translate_guards(tmp_path):
    """translated methods of a wrapped class: no allocation / size change through the getters (Eigen::Map)"""
    (tmp_path / "g_mod.F90").write_text("""module g_mod
  implicit none
  type :: G_type
    real, allocatable :: v(:)
    integer :: n = 0
  contains
    procedure :: grow => g_grow
    procedure :: total => g_total
  end type G_type
contains
  subroutine g_grow(this, m, w)
    class(G_type), intent(inout) :: this
    integer, intent(in) :: m
    real, intent(in) :: w(:)
    if (allocated(this%v)) deallocate(this%v)
    allocate(this%v(m))
    this%v = 1.0
    this%v = w
    this%v(1) = 2.0
    this%n = m
  end subroutine g_grow
  real function g_total(this)
    class(G_type), intent(in) :: this
    g_total = sum(this%v) + this%n
  end function g_total
end module g_mod
""")
    translate([], cfg(wrap_translate=["g_type%*"]), str(tmp_path), wrap=[str(tmp_path / "g_mod.F90")])
    cpp, b = (tmp_path / "g_mod.cpp").read_text(), (tmp_path / "g_mod_c_binding.F90").read_text()
    assert "scalar G::total() const\n{\n  scalar g_total = v().sum() + n();" in cpp
    assert cpp.count("not translated (wrapped-component-allocate)") == 2           # deallocate (one-line if), allocate
    assert cpp.count("not translated (wrapped-component-resize)") == 1             # this%v = w
    assert "  v().setConstant(1.0);" in cpp and "  v()(0) = 2.0;" in cpp
    assert "project_G_grow" not in b and "project_G_total" not in b


def test_wrap_sizes_from_documentation(tmp_path):
    """sizes of intent(out) arrays without size check (deferred bindings, default implementations):
    from the documentation "dimension (...)" (lowest precedence, reported as info)"""
    d = str(tmp_path)
    report, _ = translate([], cfg(), d, wrap=[os.path.join(CORPUS, "wrapped", "shape_mod.F90")])
    cpp, b = read((d,), "shape_mod.cpp"), read((d,), "shape_mod_c_binding.F90")
    assert "void shape::areas(vec& a) const\n{\n  a.resize(nSurface());\n" in cpp
    assert "void shape::normals(mat& nrm) const\n{\n  nrm.resize(3, nSurface());\n" in cpp
    assert "    a_f => a(1:obj%nSurface)\n    call obj%areas(a_f, ierr)" in b and "a_n1" not in b and "TODO(f2cpp):" not in b + cpp
    info = report.files[os.path.join(CORPUS, "wrapped", "shape_mod.F90")]["info"]
    assert info["wrap-size-from-doc:shape_type%areas:a"] == 1 and info["wrap-size-from-doc:shape_type%normals:nrm"] == 1
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (os.path.join(CORPUS, "wrapped", "shape_mod.F90"), "shape_mod_c_binding.F90"):
        run(ff + [f], d)
    cxx((d,), "shape_mod")


def test_wrapped_interface(tmp_path):
    """-w: a module wrapped in its own run, given for its symbols only: its classes have the accessors of a wrapped
    class (here implicit: all public components), so the callers are those of a run that wraps it; -I: translated"""
    w = os.path.join(CORPUS, "wrapped")
    user, solver = os.path.join(w, "solver_user_mod.F90"), os.path.join(w, "mysolver_mod.F90")
    translate([user], cfg(), str(tmp_path / "w"), wrap=[solver])
    translate([user], cfg(), str(tmp_path / "iw"), wrapped_interface=[solver])
    translate([user], cfg(), str(tmp_path / "i"), [solver])
    both = read((str(tmp_path / "w"),), "solver_user_mod.cpp")
    assert both == read((str(tmp_path / "iw"),), "solver_user_mod.cpp") and "s.n() + s.diag()(0)" in both
    assert "s.n + s.diag(0)" in read((str(tmp_path / "i"),), "solver_user_mod.cpp")
    assert not os.path.exists(tmp_path / "iw" / "mysolver_mod.hpp")        # no output for -w


def test_wrapped_module_in_a_subdirectory(tmp_path):
    """module_headers with a directory: all outputs of a wrapped module go there (created)"""
    (tmp_path / "x.toml").write_text('[modules.mysolver_mod]\nheader = "solvers/MySolver.hpp"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, str(tmp_path / "out"), wrap=[os.path.join(CORPUS, "wrapped", "mysolver_mod.F90")])
    for f in ("MySolver.hpp", "MySolver.cpp", "MySolver.h", "MySolver_c_binding.F90"):
        assert (tmp_path / "out" / "solvers" / f).exists(), f


def test_wrapped_calls_with_error_macro(tmp_path):
    """wrap_chk_ierr: binding calls of wrapped methods inside PROJECT_CHK_IERR (also in the select type of a derived
    class); positional actual arguments"""
    d, w = str(tmp_path), os.path.join(CORPUS, "wrapped")
    translate([], cfg(macro_include="project_macros.h"), d,
              wrap=[os.path.join(w, "body_mod.F90"), os.path.join(w, "sphere_mod.F90")])
    sb = read((d,), "sphere_mod_c_binding.F90")
    assert "    select type (obj => wrapper%ptr)\n    class is (sphere_type)\n" \
           "      PROJECT_CHK_IERR(call obj%grow(dr, ierr), ierr)\n    class default\n" \
           "      PROJECT_ASSERT(.false., ierr)\n    end select" in sb        # wrap_fail
    assert '#include "project_macros.h"\n' in sb
    if shutil.which("gfortran"):
        ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
              "-Wno-unused-dummy-argument", "-I" + CORPUS]
        for f in (os.path.join(w, "body_mod.F90"), "body_mod_c_binding.F90", os.path.join(w, "sphere_mod.F90"),
                  "sphere_mod_c_binding.F90"):
            run(ff + [f], d)
