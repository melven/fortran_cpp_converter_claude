"""Accessors and member visibility (translated, wrapped, existing classes)."""
import os
import shutil

from f2cpp import config
from f2cpp.cli import translate
from helpers import CORPUS, SUPPORT, EIGEN, cfg, read, cxx


def test_accessors(tmp_path):
    (tmp_path / "a_mod.F90").write_text(
        "module a_mod\nuse sys_mod, only: system_type\nuse legacy_mod, only: legacy_type\ncontains\n"
        "subroutine s(system, obj, n)\ntype(system_type), intent(inout) :: system\n"
        "type(legacy_type), intent(inout) :: obj\ninteger :: n\n"
        "n = size(system%models) + system%count + system%offsets%first\n"
        "system%models(2)%id = n\nobj%value = 2.0 * obj%value\nsystem%coupling_manager%step = n\nend subroutine\nend module\n")
    c = config.load()
    (tmp_path / "f2cpp.toml").write_text(
        '[types]\nSystem_type.accessors = ["models:ref", "offsets:ref", "coupling_manager=couplingManager:ref"]\n'
        'legacy_type.accessors = ["*:ref"]\n')
    c = config.load(str(tmp_path / "f2cpp.toml"))
    translate([str(tmp_path / "a_mod.F90")], c, str(tmp_path), [os.path.join(CORPUS, "legacy_mod.F90")])
    assert "system.couplingManager().step = n;" in (tmp_path / "a_mod.cpp").read_text()
    cpp = (tmp_path / "a_mod.cpp").read_text()
    assert "n = system.models().size() + system.count + system.offsets().first;" in cpp
    assert "system.models()(1).id = n;" in cpp        # accessor of an array: 0-based index
    assert "obj.value() = 2.0 * obj.value();" in cpp


def test_accessors_of_base_class(tmp_path):
    (tmp_path / "d_mod.F90").write_text(
        "module d_mod\nuse base_mod, only: base_type\ntype, extends(base_type) :: derived_type\n"
        "integer :: own = 0\ncontains\nprocedure :: work\nend type\ncontains\n"
        "subroutine work(this, n)\nclass(derived_type), intent(inout) :: this\ninteger, intent(out) :: n\n"
        "n = this%system%count + this%own + this%other\nend subroutine\nend module\n")
    c = config.load()
    c["accessors"] = {"base_type": {"system": (None, "ref")}}
    translate([str(tmp_path / "d_mod.F90")], c, str(tmp_path))
    assert "n = system().count + own + other;" in (tmp_path / "d_mod.cpp").read_text()


def test_accessor_of_pointer_component(tmp_path):
    (tmp_path / "base_mod.F90").write_text(
        "module base_mod\ntype :: sys_type\ninteger :: count = 0\nend type\n"
        "type :: base_type\nclass(sys_type), pointer :: system => null()\n"
        "class(sys_type), pointer :: raw => null()\nend type\nend module\n")
    (tmp_path / "d_mod.F90").write_text(
        "module d_mod\nuse base_mod, only: base_type\ntype, extends(base_type) :: derived_type\n"
        "contains\nprocedure :: work\nend type\ncontains\n"
        "subroutine work(this, n)\nclass(derived_type), intent(inout) :: this\ninteger, intent(out) :: n\n"
        "n = this%system%count + this%raw%count\nend subroutine\nend module\n")
    c = config.load()
    c["accessors"] = {"base_type": {"system": (None, "ref")}}
    translate([str(tmp_path / "d_mod.F90")], c, str(tmp_path), [str(tmp_path / "base_mod.F90")])
    assert "n = system().count + raw->count;" in (tmp_path / "d_mod.cpp").read_text()


def test_member_access(out, tmp_path):
    shape, feat, poly = read(out, "shape_mod.hpp"), read(out, "features_mod.hpp"), read(out, "poly_mod.hpp")
    # used only by the own methods: private; used by free functions / other classes / tests: public
    assert "  virtual void scale(scalar factor) override;\n\nprivate:\n  scalar width = 1.0;\n  scalar height = 1.0;\n};" in shape
    assert '\n\n  std::string name = "shape";\n};' in shape                  # set in make_shape
    assert "  ~stats();\n\n  scalar total = 0.0;\n  vec samples;\n\nprivate:\n  int n = 0;\n" in feat
    assert "  vec coeff;  //!< coefficients 0..degree\n\nprivate:\n  int degree = -1;\n};" in poly    # coeff: unit test
    # derived classes: protected; "fortran": as declared
    (tmp_path / "p_mod.F90").write_text(
        "module p_mod\ntype :: base_type\ninteger :: a = 0, b = 0, c = 0\ncontains\nprocedure :: get\nend type\n"
        "type, extends(base_type) :: child_type\ncontains\nprocedure :: twice\nend type\ncontains\n"
        "function get(this) result(k)\nclass(base_type), intent(in) :: this\ninteger :: k\nk = this%a\nend function\n"
        "function twice(this) result(k)\nclass(child_type), intent(in) :: this\ninteger :: k\nk = 2 * this%b\n"
        "end function\nfunction free(x) result(k)\ntype(child_type), intent(in) :: x\ninteger :: k\nk = x%c\n"
        "end function\nend module\n")
    translate([str(tmp_path / "p_mod.F90")], config.load(), str(tmp_path))
    hpp = (tmp_path / "p_mod.hpp").read_text()
    assert "  int get() const;\n\n  int c = 0;\n\nprotected:\n  int b = 0;\n\nprivate:\n  int a = 0;\n};" in hpp
    c = config.load()
    c["member_access"] = "fortran"
    translate([str(tmp_path / "p_mod.F90")], c, str(tmp_path))
    assert "  int get() const;\n\n  int a = 0;\n  int b = 0;\n  int c = 0;\n};" in (tmp_path / "p_mod.hpp").read_text()


def test_accessors_of_unknown_types_index_from_zero(tmp_path):
    (tmp_path / "u_mod.F90").write_text(
        "module u_mod\nuse sys_mod, only: system_type\ncontains\nsubroutine s(system)\n"
        "type(system_type), intent(inout) :: system\ninteger :: i\n"
        "do i = 1, system%n\n  system%x(i) = 2.0 * system%x(i)\nend do\nsystem%x(1) = system%m(2, i)\n"
        "end subroutine\nend module\n")
    c = config.load()
    c["accessors"] = {"system_type": {"x": (None, "ref"), "n": (None, "get")}}
    translate([str(tmp_path / "u_mod.F90")], c, str(tmp_path))
    cpp = (tmp_path / "u_mod.cpp").read_text()
    assert "system.x()(i - 1) = 2.0 * system.x()(i - 1);" in cpp   # accessor (array, lower bound 1)
    assert "system.x()(0) = system.m(2, i);" in cpp                  # unknown component: passed through


def test_accessors_same_for_wrapped_and_translated(tmp_path):
    """one accessors list: the callers of a class are the same whether it is wrapped or translated; get: const
    getter only, set: + setter (wrapped scalars through the pointer getter), ref: + reference; wrapped classes
    without a list: all public components (ref), noted in the report"""
    w = os.path.join(CORPUS, "wrapped")
    (tmp_path / "x.toml").write_text('[types.MySolver_type]\n'
                                     'accessors = ["n", "diag:ref", "coupling:ref", "shift:set", "verbose:set"]\n')
    c = lambda: config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")], strict=False)  # noqa: E731
    t, wd = str(tmp_path / "t"), str(tmp_path / "w")
    translate([os.path.join(w, "solver_user_mod.F90"), os.path.join(w, "mysolver_mod.F90")], c(), t)
    report, _ = translate([os.path.join(w, "solver_user_mod.F90")], c(), wd, wrap=[os.path.join(w, "mysolver_mod.F90")])
    user = read((wd,), "solver_user_mod.cpp")
    assert user == read((t,), "solver_user_mod.cpp")
    assert "s.n() + s.diag()(0)" in user and "  s.shift(0.25);" in user and "s.coupling()(i - 1, 1)" in user
    th, wh = read((t,), "mysolver_mod.hpp"), read((wd,), "mysolver_mod.hpp")
    assert "  int n() const { return n_; }" in th and "  void shift(scalar value) { shift_ = value; }" in th
    assert "  int n() const;\n" in wh and "int& n()" not in wh and "  void shift(scalar value);" in wh
    assert "  Eigen::Map<vec> diag();" in wh and "weights()" not in wh     # not listed
    assert "void MySolver::shift(scalar value)\n{\n  *project_MySolver_shift(handle_) = value;\n}" in \
        read((wd,), "mysolver_mod.cpp")
    assert "accessors-implicit:mysolver_type" not in str(report.files)
    report, _ = translate([], cfg(), str(tmp_path / "i"), wrap=[os.path.join(w, "mysolver_mod.F90")])
    assert "accessors-implicit:mysolver_type" in str(report.files)
    if shutil.which("g++") and os.path.isdir(EIGEN):
        for d in (t, wd):
            cxx((d,), "solver_user_mod")
        cxx((wd,), "mysolver_mod")


def test_private_bindings(tmp_path):
    """procedure, private / a private statement after contains: Fortran private is module-private, so the C++
    section follows the callers (own methods: private, derived types: protected, other procedures: public)"""
    d, src = str(tmp_path), os.path.join(CORPUS, "visibility_mod.F90")
    translate([src], cfg(), d)
    hpp = read((d,), "visibility_mod.hpp")
    assert "  void add(int n);\n  void clear();  //!< also a module procedure: public\n\nprotected:\n" \
           "  void bump(int n);  //!< also derived types: protected\n\nprivate:\n" \
           "  bool check(int n) const;  //!< own methods only: private\n  int total = 0;\n};" in hpp
    assert "public:\n  // default for the following bindings\n  void add_scaled(int n);\n\nprivate:\n" \
           "  void scaled_add(int n);  //!< own methods only: private\n  int factor = 2;\n};" in hpp
    assert "//---" not in hpp + read((d,), "visibility_mod.cpp") and "//===" not in hpp     # Fortran separators
    assert "  //! adds n if it is valid" in hpp
    if shutil.which("g++") and os.path.isdir(EIGEN):
        cxx((d,), "visibility_mod")
    c = cfg(member_access="fortran")            # as declared
    translate([src], c, str(tmp_path / "f"))
    hpp = read((str(tmp_path / "f"),), "visibility_mod.hpp")
    assert "private:\n  bool check(int n) const;  //!< own methods only: private\n" \
           "  void bump(int n);  //!< also derived types: protected\n  void clear();" in hpp


def test_member_accessors(tmp_path):
    """member_accessors: listed members become protected <name>_ with public accessors (get: const getter, scalars
    by value; set: + setter overload; ref: + non-const reference); own methods and derived classes use n_; other
    code the accessors; writes to get-only members are TODOs; unlisted members and types stay as they are"""
    d = str(tmp_path)
    (tmp_path / "x.toml").write_text('[types.solver_type]\naccessors = ["n", "tol:set", "x:ref"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    report, _ = translate([os.path.join(CORPUS, "accessors_mod.F90")], c, d)
    hpp, cpp = read((d,), "accessors_mod.hpp"), read((d,), "accessors_mod.cpp")
    assert "  //! problem size\n  int n() const { return n_; }\n" in hpp
    assert "  scalar tol() const { return tol_; }\n  //! tolerance (setter)\n  void tol(scalar value) { tol_ = value; }" in hpp
    assert "  vec& x() { return x_; }\n  //! state (const version)\n  const vec& x() const { return x_; }" in hpp
    assert "protected:\n  int n_ = 0;  //!< problem size\n  scalar tol_ = 1.0e-6;  //!< tolerance\n  vec x_;" in hpp
    assert "  int count = 0;  //!< not listed: plain member" in hpp
    assert "  n_ = n;\n  x_.resize(n);" in cpp and "  x_ = f * x_;" in cpp          # own class, derived class
    assert "  s.tol(0.5);" in cpp and "  s.x()(0) = 2.0;" in cpp and "s.count = s.count + s.n();" in cpp
    assert "// TODO(f2cpp): not translated (member-read-only)" in cpp
    assert report.files[os.path.join(CORPUS, "accessors_mod.F90")]["untranslated"]["member-read-only"] == 1
    if shutil.which("g++") and os.path.isdir(EIGEN):
        cxx((d,), "accessors_mod")
    # not configured: unchanged
    translate([os.path.join(CORPUS, "accessors_mod.F90")], cfg(), str(tmp_path / "plain"))
    assert "  int n = 0;" in (tmp_path / "plain" / "accessors_mod.hpp").read_text()
