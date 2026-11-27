"""Bidirectional (reverse) types, factories, class(*) in both directions, coding rules."""
import glob
import os
import re
import shutil

from f2cpp import config
from f2cpp.cli import translate
from helpers import CORPUS, SUPPORT, EIGEN, run, run_exe, cfg, flat, read, undocumented


def test_reverse_wrapped_types(tmp_path):
    """abstract Fortran types implemented in C++: Fortran dependents create things through the Fortran
    factory registration, the factory and the things are translated C++ (wrap_reverse, wrap_reverse_factories);
    same output as the pure Fortran program"""
    d, r = str(tmp_path), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[types]\nAbstractThing_type.interop = "bidirectional"\n'
                                     'AbstractThingFactory_type.factory = { register = "register_thing_factory", '
                                     'unregister = "unregister_thing_factory" }\n'
                                     'LinearThingFactory_type.instance = "linear"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    wrapped = [os.path.join(r, f) for f in ("config_mod.F90", "thing_mod.F90", "thing_factory_mod.F90")]
    translate([os.path.join(r, "linear_thing_mod.F90"), os.path.join(r, "test_linear_thing_mod.pf")], c, d, wrap=wrapped)
    th, tc, tb = ((tmp_path / f).read_text() for f in ("thing_mod.hpp", "thing_mod.cpp", "thing_mod_c_binding.F90"))
    fh, fc, fb = ((tmp_path / f).read_text() for f in ("thing_factory_mod.hpp", "thing_factory_mod.cpp",
                                                         "thing_factory_mod_c_binding.F90"))
    tw = (tmp_path / "project_c_wrapper_thing_mod.F90").read_text()          # the Fortran types implemented in C++
    fw = fb             # factories only: their Fortran part is in the binding (no c_wrapper module)
    assert not (tmp_path / "project_c_wrapper_thing_factory_mod.F90").exists()
    # long use statements: "&" also on the continued line, aligned after "only: "
    assert "  use thing_factory_mod, only: AbstractThingFactory_type, register_thing_factory, &\n" \
           "  &                            unregister_thing_factory\n" in fw
    h, lh, lc = ((tmp_path / f).read_text() for f in ("thing_mod.h", "linear_thing_mod.hpp", "linear_thing_mod.cpp"))
    # C struct per C++ object: function pointers (typedefs named like the abstract interfaces), destroy, userData
    assert "typedef struct project_AbstractThing project_AbstractThing;" in flat(h)
    assert "typedef void (*project_AbstractThing_evaluate)(project_AbstractThing*,\n" + \
           "".join(" " * 47 + p + "\n" for p in ("const PROJECT_REAL*,", "int,", "PROJECT_REAL*,", "int,", "int*);")) in h
    assert "typedef void (*project_AbstractThing_weight)(const project_AbstractThing*, PROJECT_REAL*);" in h  # intent(in)
    assert "typedef void (*project_AbstractThing_reset)(project_AbstractThing*);" in h                    # intent(inout)
    assert "  static void weight_wrapper(const project_AbstractThing* self, scalar* w) noexcept;" in th
    assert "  const AbstractThing* thing = static_cast<const AbstractThing*>(self->userData);\n  try\n  {\n" \
           "    *w = thing->weight();" in tc                                      # the object before the try
    assert '    if( y_v.size() != y_n )\n      throw std::length_error("AbstractThing::evaluate: dimension of ' \
           'parameter y changed!");' in tc
    assert "struct project_AbstractThing\n{\n  project_AbstractThing_evaluate evaluate = nullptr;\n" \
           "  project_AbstractThing_weight weight = nullptr;\n  project_AbstractThing_reset reset = nullptr;\n" in flat(h)
    assert "  project_AbstractThing_destroy destroy = nullptr;\n  void* userData = nullptr;\n};" in h and "p_impl" not in h
    assert h.index("typedef void (*project_AbstractThing_destroy)") < h.index("struct project_AbstractThing\n{")
    # one abstract interface block, the typedef before each interface body
    assert tb.count("  abstract interface\n") == 1
    assert "  ! interface declarations\n  abstract interface\n!@c_declaration\n" \
           "!typedef void (*project_AbstractThing_evaluate)(project_AbstractThing*,\n" in tb
    assert "!" + " " * 47 + "int*);\n!@end_c_declaration\n" \
           "    !> AbstractThing::evaluate_wrapper\n    subroutine project_AbstractThing_evaluate(" in tb
    assert "      integer(C_INT), intent(out) :: ierr\n    end subroutine\n\n!@c_declaration\n" \
           "!typedef void (*project_AbstractThing_weight)" in tb                 # only "end subroutine"
    assert "    subroutine project_AbstractThing_evaluate(this, x, x_n, y, y_n, ierr) bind(C)\n" \
           "      import C_PTR, C_REAL, C_INT\n      implicit none\n      type(C_PTR), value :: this\n" \
           "      real(C_REAL), intent(in) :: x(*)\n      integer(C_INT), value :: x_n\n" \
           "      real(C_REAL), intent(out) :: y(*)\n" in tb and "      import C_PTR, C_REAL\n" in tb and "      import\n" not in tb
    # C++ base classes: pure virtual (translation signatures), private static <method>_wrapper
    assert "  virtual void evaluate(const vec& x, vec& y) = 0;" in th and "  virtual scalar weight() const = 0;" in th
    # bindings with a default implementation: virtual, the translated default, forwarded as well
    assert "  virtual void reset();" in th and "  virtual scalar offset() const;" in th
    assert "void AbstractThing::reset()\n{\n}" in tc and "scalar AbstractThing::offset() const\n{\n  scalar o = 0.0;\n  return o;\n}" in tc
    assert "    procedure :: reset\n    procedure :: offset\n" in tw and "  subroutine reset(this)\n" in tw   # binding names
    assert "  static void evaluate_wrapper(project_AbstractThing* self, const scalar* x, int x_n, scalar* y, " \
           "int y_n, int* ierr) noexcept;" in th
    assert "  //! internal wrapper for evaluate\n  static void evaluate_wrapper(" in th
    assert "  //! internal wrapper for the destruction (the object is owned by the caller)\n" \
           "  static void destroy_wrapper(" in th
    assert "  std::unique_ptr<project_AbstractThing> c_;" in th and "#include \"thing_mod.h\"" not in th
    assert "  std::unique_ptr<AbstractThing> owner(static_cast<AbstractThing*>(self->userData));  // deletes it" in tc
    assert '    std::fprintf(stderr, "AbstractThing: encountered exception in evaluate: %s\\n", e.what());' in tc
    assert '    std::fprintf(stderr, "AbstractThing: encountered exception in evaluate: %s\\n", "unknown exception");' in tc
    assert "  explicit AbstractThingFactory(const std::string& name);" in fh
    assert "  virtual void create_thing(const Config& config, std::unique_ptr<AbstractThing>& thing) const = 0;" in fh
    assert "    *thing = thing_u ? AbstractThing_private::toC(*thing_u) : nullptr;\n" \
           "    thing_u.release(); // NOLINT(bugprone-unused-return-value)" in fc
    # Fortran: wrapper types owning / calling the C++ objects
    # (in the c_wrapper module; the binding module has the C struct and the interfaces)
    assert "  type, extends(AbstractThing_type) :: CWrapperThing_type\n" in tw and "    final :: cwrapperthing_final" in tw
    assert ":: CWrapperThing_type" not in tb and "Holder" not in tb + tw and "p_impl" not in tb + tw
    assert "  use thing_mod_c_binding, only: project_AbstractThing_evaluate" in tw
    # private modules, public: types, bind(C) / Fortran-callable procedures, abstract interfaces (one per line)
    assert "  implicit none\n\n!@c_declaration\n!\n!#include <stdbool.h>\n" in tb      # like a use statement
    assert "!@end_c_declaration\n\n  private\n\n  !" + "=" * 80 + "\n  ! public declarations\n  public :: AbstractThing_c\n" in tb and "  public :: project_AbstractThing_evaluate\n" in tb
    assert "  implicit none\n  private\n\n  !" + "=" * 80 + "\n  ! public declarations\n  public :: CWrapperThing_type\n" \
           "  public :: project_AbstractThing_wrap\n\n  !" + "=" * 80 + "\n  ! type declarations\n  !> AbstractThing_type" in tw
    # precision macros before the module; interfaces, then types (each under a section comment)
    assert "#define C_REAL C_DOUBLE\n#endif\n\nmodule thing_mod_c_binding\n" in tb
    assert tb.index("  ! interface declarations\n  abstract interface\n!@c_declaration\n!typedef void (*project_AbstractThing_evaluate)") < \
        tb.index("  ! type declarations\n!@c_declaration\n!struct project_AbstractThing\n") < tb.index("\ncontains\n")
    assert "public :: evaluate" not in tw
    assert "    call c_f_procpointer(this%c%evaluate, f)\n    call f(c_loc(this%c), x, int(size(x), C_INT), y, " \
           "int(size(y), C_INT), ierr)\n" in tw and "_c(:)" not in tw      # same kinds: passed directly
    assert "    if (c_associated(thing_p)) call project_AbstractThing_wrap(thing_p, thing)" in fw
    assert "  use project_c_wrapper_thing_mod, only: project_AbstractThing_wrap" in fw
    assert "    call register_thing_factory(str_array_ptr_to_str(name), f)" in fw
    # translated implementation: name constructor of the factory base, global instance
    assert "class LinearThingFactory final : public AbstractThingFactory\n{\npublic:\n  using AbstractThingFactory::AbstractThingFactory;" in lh
    assert 'namespace LinearThingFactory_private\n{\nLinearThingFactory instance("linear");' in lc
    assert "class Entry" not in fh                                       # private type: not wrapped
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-Wall",
          "-Werror", "-Wno-unused-dummy-argument", "-I" + CORPUS]
    os.makedirs(os.path.join(d, "ref"))
    for f in ("config_mod.F90", "thing_mod.F90", "thing_factory_mod.F90", "linear_thing_mod.F90", "thing_user_mod.F90",
              "ref_main.F90"):
        run(ff + [os.path.join(r, f)], os.path.join(d, "ref"))
    run(["gfortran", "config_mod.o", "thing_mod.o", "thing_factory_mod.o", "linear_thing_mod.o", "thing_user_mod.o",
         "ref_main.o", "-o", "ref"], os.path.join(d, "ref"))
    ref = run_exe(["./ref"], os.path.join(d, "ref"))
    fsrc = [os.path.join(SUPPORT, "project_str.F90")] + wrapped + [os.path.join(r, "thing_user_mod.F90"), "config_mod_c_binding.F90",
            "thing_mod_c_binding.F90", "project_c_wrapper_thing_mod.F90", "thing_factory_mod_c_binding.F90",
            os.path.join(r, "mixed_main.F90")]
    for f in fsrc:
        run(ff + [f], d)
    # the Fortran library references no symbol of the C++ library
    for o in ("thing_mod_c_binding.o", "thing_factory_mod_c_binding.o", "config_mod_c_binding.o",
              "project_c_wrapper_thing_mod.o"):
        assert "_Z" not in run(["nm", "-u", o], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f in ("config_mod", "thing_mod", "thing_factory_mod", "linear_thing_mod"):
        run(gxx + [f + ".cpp", "-o", f + "_cpp.o"], d)
    objs = [os.path.splitext(os.path.basename(f))[0] + ".o" for f in fsrc] + \
        [f + "_cpp.o" for f in ("config_mod", "thing_mod", "thing_factory_mod", "linear_thing_mod")]
    run(["gfortran"] + objs + ["-lstdc++", "-o", "mixed"], d)
    assert run_exe(["./mixed"], d) == ref
    # the translated pFUnit test of the implementation (gtest, linked with the Fortran parts)
    if os.path.exists("/usr/include/gtest/gtest.h"):
        run(gxx + ["test_linear_thing_mod.cpp", "-o", "test_cpp.o"], d)
        run(["g++"] + [o for o in objs if o != "mixed_main.o"] + ["test_cpp.o", "-lgtest", "-lgtest_main", "-lpthread",
                                                                  "-lgfortran", "-o", "gt"], d)
        assert "[  PASSED  ] 1 test" in run_exe(["./gt"], d)


def test_reverse_class_star_is_todo(tmp_path):
    """class(*) arguments of reverse-wrapped methods: only allocatable intent(out) / intent(in) (C++ data);
    Fortran implementations behind the struct: TODO"""
    (tmp_path / "rs_mod.F90").write_text("""module rs_mod
  implicit none
  type, abstract :: AbstractS_type
  contains
    procedure(save_iface), deferred :: save_state
  end type AbstractS_type
  abstract interface
    subroutine save_iface(this, state)
      import :: AbstractS_type
      class(AbstractS_type), intent(in) :: this
      class(*), allocatable, intent(inout) :: state
    end subroutine save_iface
  end interface
end module rs_mod
""")
    translate([], cfg(wrap_reverse=["abstracts_type"]), str(tmp_path), wrap=[str(tmp_path / "rs_mod.F90")])
    b = (tmp_path / "project_c_wrapper_rs_mod.F90").read_text()
    assert "! TODO(f2cpp): argument state: class(*) (C++ data: allocatable intent(out) / intent(in) scalars)" in b
    assert 'error stop "save_state:' in b
    # the other direction (Fortran implementation behind the struct): TODO in the binding, the C++ wrapper throws
    fb, hpp, cpp = ((tmp_path / f).read_text() for f in ("rs_mod_c_binding.F90", "rs_mod_c_wrapper.hpp",
                                                         "rs_mod_c_wrapper.cpp"))
    assert "    ! TODO(f2cpp): save_state not wrapped (argument state: class(*)): C++ CWrapperS::save_state throws\n" in fb
    assert "  virtual void save_state(std::any& state) const override;" in hpp
    assert "void CWrapperS::save_state(std::any& state) const\n{\n  // TODO(f2cpp): argument state: class(*)\n" \
           '  throw std::logic_error("CWrapperS::save_state: not wrapped (argument state: class(*))");' in cpp


def test_reverse_type_fortran_implementation_in_cpp(tmp_path):
    """the other direction of a reverse type: a Fortran implementation behind the C struct
    (create_<Thing>_C_from_Fortran, wrap_<method>), used in C++ through <Abstract>_private::CWrapper<Thing>
    (own files <stem>_c_wrapper.hpp / .cpp)"""
    d, r = str(tmp_path), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[types.AbstractThing_type]\ninterop = "bidirectional"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, d, wrap=[os.path.join(r, "thing_mod.F90")])
    hpp, cpp = (tmp_path / "thing_mod_c_wrapper.hpp").read_text(), (tmp_path / "thing_mod_c_wrapper.cpp").read_text()
    assert "Wrapper" not in (tmp_path / "thing_mod.hpp").read_text().replace("_wrapper", "")
    assert '#include "thing_mod.hpp"\n\n#include "thing_mod.h"  // the C struct (internal header)\n\nnamespace project\n{\n\n' \
           '//! internals of AbstractThing: its implementations behind a C struct\nnamespace AbstractThing_private\n{\n' in hpp
    assert "  project_AbstractThing other_{};  //!< the struct of the implementation (copy)" in hpp
    b, h = (tmp_path / "thing_mod_c_binding.F90").read_text(), (tmp_path / "thing_mod.h").read_text()
    # file headers: original header, @file renamed, @brief prefixed
    assert hpp.startswith("//! @file thing_mod_c_wrapper.hpp\n//! @brief C++ wrapper of C structs: things evaluated")
    import datetime
    today = datetime.date.today().isoformat()       # @date of the file headers: the date of the generation
    assert f"\n//! @date {today}\n" in hpp and f"\n!! @date {today}\n" in (tmp_path / "thing_mod_c_binding.F90").read_text()
    assert f"\n!! @date {today}\n" in (tmp_path / "project_c_wrapper_thing_mod.F90").read_text()
    assert cpp.startswith("//! @file thing_mod_c_wrapper.cpp\n//! @brief C++ wrapper of C structs: things evaluated")
    assert (tmp_path / "project_c_wrapper_thing_mod.F90").read_text().startswith(
        "!> @file project_c_wrapper_thing_mod.F90\n!> @brief Fortran types implemented in C++: things evaluated")
    assert "class CWrapperThing final : public AbstractThing\n{\npublic:\n  CWrapperThing() = default;" in hpp
    assert "  explicit CWrapperThing(const project_AbstractThing& c);  //!< calls the function pointers of c" in hpp
    assert "CWrapperThing::CWrapperThing(const project_AbstractThing& c) : other_(c)\n{\n}" in cpp
    assert "  //! override base class implementation -> call corresponding C function pointer\n" \
           "  virtual void evaluate(const vec& x, vec& y) override;" in hpp
    assert '#include "thing_mod_c_wrapper.hpp"' in cpp and '#include "thing_mod.h"' in hpp     # internal header
    assert "namespace AbstractThing_private\n{\n\nCWrapperThing::CWrapperThing(" in cpp and "\nvoid CWrapperThing::evaluate(" in cpp
    assert "  other_.evaluate(&other_, x.data(), static_cast<int>(x.size()), y.data(), static_cast<int>(y.size()), &ierr);" in cpp
    # no binding label (no global symbol): only the target of a function pointer
    assert "  subroutine wrap_evaluate(this, x, x_n, y, y_n, ierr) bind(C, name=\"\")\n" in b and "wrap_evaluate" not in h
    assert "    call c_f_pointer(this_c%userData, wrapper)\n    call wrapper%ptr%evaluate(x, y, ierr_f)" in b
    assert "  subroutine create_Thing_C_from_Fortran(fthing, cthing, ierr)\n" \
           "    type(AbstractThingWrapper_type), target, intent(in) :: fthing\n" \
           "    type(AbstractThing_c), intent(out) :: cthing\n" in b
    assert b.index("subroutine create_Thing_C_from_Fortran(") < b.index("subroutine wrap_evaluate(")
    assert "  public :: create_Thing_C_from_Fortran\n" in b and "p_impl" not in h + b
    assert "public :: wrap_" not in b                                  # only used through the function pointers
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""program drv
  use, intrinsic :: iso_c_binding
  use thing_mod_c_binding, only: AbstractThing_c, AbstractThingWrapper_type, create_Thing_C_from_Fortran
  use const_thing_mod, only: ConstThing_type
  implicit none
  interface
    subroutine run(c) bind(C, name="run")
      import :: C_PTR
      type(C_PTR), value :: c
    end subroutine
  end interface
  type(ConstThing_type), target :: thing
  type(AbstractThingWrapper_type), target :: fthing
  type(AbstractThing_c), target :: cthing
  integer :: ierr
  thing%c = 2.0
  fthing%ptr => thing
  call create_Thing_C_from_Fortran(fthing, cthing, ierr)
  call run(c_loc(cthing))
end program drv
""")
    (tmp_path / "run.cpp").write_text(r"""#include "thing_mod_c_wrapper.hpp"
#include "thing_mod.h"
#include <cstdio>
#include <stdexcept>
static project::scalar use(project::AbstractThing& t)       // any implementation
{
  project::vec x(2), y(2);
  x << 1.0, 3.0;
  t.evaluate(x, y);
  return y(1) + t.weight() + t.offset();
}
extern "C" void run(project_AbstractThing* c)
{
  project::AbstractThing_private::CWrapperThing w(*c);                   // the Fortran implementation in C++
  std::printf("%g\n", use(w));                  // 3 + 2, 10 * 2, Fortran default offset 0
  project::vec x(2), y(3);
  try
  {
    w.evaluate(x, y);                            // Fortran: ierr = 1 -> exception
  }
  catch (const std::exception&)
  {
    std::printf("caught\n");
  }
  try
  {
    project::AbstractThing_private::CWrapperThing empty;
    empty.weight();
  }
  catch (const std::logic_error&)
  {
    std::printf("empty\n");
  }
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-ffree-line-length-none", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (os.path.join(r, "thing_mod.F90"), "thing_mod_c_binding.F90", os.path.join(r, "const_thing_mod.F90"),
              "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f, o in (("thing_mod.cpp", "glue.o"), ("thing_mod_c_wrapper.cpp", "cw.o"), ("run.cpp", "run.o")):
        run(gxx + [f, "-o", o], d)
    run(["gfortran", "thing_mod.o", "thing_mod_c_binding.o", "const_thing_mod.o", "drv.o", "glue.o", "cw.o", "run.o",
         "-lstdc++", "-o", "m"], d)
    assert run_exe(["./m"], d).split() == ["25", "caught", "empty"]


def test_reverse_type_constants(tmp_path):
    """wrap_constants of a reverse type: set by the C++ constructor, in the C struct, const getters in C++ (used by
    the translated default implementation), copied into the Fortran object (project_<Type>_wrap)"""
    d, src = str(tmp_path), os.path.join(CORPUS, "reverse", "convergence_mod.F90")
    (tmp_path / "x.toml").write_text('[macros]\ninclude = "project_macros.h"\n'
                                     '[types.Thing_type]\ninterop = "bidirectional"\n'
                                     'constants = ["stateSize", "inputSize", "label"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    base = os.path.join(CORPUS, "reverse", "model_base_mod.F90")      # stateSize: inherited (not wrapped)
    translate([], c, d, wrap=[src], interface_only=[base])
    hpp, cpp, h = read((d,), "convergence_mod.hpp"), read((d,), "convergence_mod.cpp"), read((d,), "convergence_mod.h")
    b, cw = read((d,), "convergence_mod_c_binding.F90"), read((d,), "project_c_wrapper_convergence_mod.F90")
    assert "  explicit Thing(int stateSize, int inputSize, const std::string& label);  " \
           "//!< creates its C struct (constants: stateSize, inputSize, label)" in hpp
    # character constants: std::string in the C++ object, const char* in the struct; Fortran: C string copies
    assert "  //! name of the model\n  const std::string& label() const;" in hpp and "  std::string label_;" in hpp
    assert "  c_->label = label_.c_str();" in cpp and "  const char* label = nullptr;" in h
    assert "      allocate(x%label, source=trim(str_array_ptr_to_str(x%c%label)))" in cw     # no realloc-lhs
    assert "      PROJECT_CHK_IERR(label_c => f2cpp_copy_string(fthing%ptr%label, ierr), ierr)\n" in b  # wrap_chk_ierr
    assert "  subroutine destroy_Thing_C_from_Fortran(cthing, ierr)\n" in b and \
        "    call f2cpp_delete_string(cthing%label)\n    cthing = Thing_c()\n" in b
    assert "  public :: destroy_Thing_C_from_Fortran\n" in b
    assert "  //! size of the state\n  int stateSize() const;" in hpp
    assert "int Thing::stateSize() const\n{\n  return c_->stateSize;\n}" in cpp and "  c_->stateSize = stateSize;" in cpp
    assert "if (state.size() != stateSize())" in cpp                   # translated default implementation
    assert "struct project_Thing\n{\n  int stateSize = 0;\n  int inputSize = 0;\n  const char* label = nullptr;\n" \
           "  project_Thing_check_convergence check_convergence = nullptr;\n" in h      # constants first
    assert "  type, bind(C) :: Thing_c\n    integer(C_INT) :: stateSize = 0\n    integer(C_INT) :: inputSize = 0\n" \
           "    type(C_PTR) :: label = C_NULL_PTR  !< C string\n    type(C_FUNPTR) :: check_convergence = C_NULL_FUNPTR\n" in b
    assert "    integer(C_INT) :: stateSize = 0\n" in b and "    cthing%stateSize = fthing%ptr%stateSize" in b
    assert "      call c_f_pointer(c, x%c)\n      x%stateSize = x%c%stateSize\n      x%inputSize = x%c%inputSize" in cw
    cwc = read((d,), "convergence_mod_c_wrapper.cpp")
    assert 'CWrapperThing::CWrapperThing(const project_Thing& c) : Thing(c.stateSize, c.inputSize, ' \
           'std::string(c.label != nullptr ? c.label : "")), other_(c)' in cwc
    # sizes known on both sides (constants): no size argument, checked in Fortran and in C++
    assert "typedef void (*project_Thing_check_convergence)(project_Thing*,\n" in h and "_n" not in flat(h)
    assert "  Thing* thing = static_cast<Thing*>(self->userData);\n  const int state_n = thing->stateSize();" in cpp
    assert "bool* needAnotherIteration, int* ierr) noexcept" in cpp       # declared spelling
    assert "    ierr = 0\n    PROJECT_CHK_ARGUMENT(size(state) == this%stateSize, ierr)\n" in cw   # wrap_chk_argument
    # include right after the module statement (it may contain use statements), precision macros before it
    assert '#endif\n\nmodule project_c_wrapper_convergence_mod\n#include "project_macros.h"\n  use' in cw and \
        '#endif\n\nmodule convergence_mod_c_binding\n#include "project_macros.h"\n  use' in b         # both macros used
    # wrap_<method>: pointer views (sizes: constants of the struct), positional arguments, PROJECT_CHK_IERR
    assert "  !" + "=" * 80 + "\n  !> check_convergence of a Fortran implementation" in b
    assert "    real(C_REAL), intent(in), target :: state(*)\n" in b and "    real, pointer, contiguous :: state_f(:)\n" in b
    assert "    state_f => state(1:this_c%stateSize)\n" in b
    sep = "    !" + "-" * 70 + "\n"                 # after the dummies and after the locals
    assert "    integer(C_INT), intent(out) :: ierr\n" + sep + "    type(Thing_c), pointer :: this_c\n" in b
    assert "    logical :: needAnotherIteration_f\n" + sep + "    call c_f_pointer(this, this_c)\n" in b
    assert "    real, contiguous, target, intent(in) :: state(:)  !< new state, dimension (this\\%stateSize)\n" in cw
    assert "    PROJECT_CHK_IERR(call f(c_loc(this%c), state, input, time, reset_c, needAnotherIteration_c, ierr), " \
           "ierr)\n" in cw
    assert "    integer, intent(out) :: ierr  !< error flag\n" + sep + "    procedure(project_Thing_check_convergence), pointer :: f\n" in cw
    assert "    PROJECT_CHK_IERR(call wrapper%ptr%check_convergence(state_f, input_f, time, reset_f, " \
           "needAnotherIteration_f, ierr), ierr)\n" in b and "ierr_f" not in b
    assert '  if( state.size() != stateSize() )\n    throw std::invalid_argument("CWrapperThing::check_convergence: ' \
           'wrong dimension of parameter state!");' in cwc
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""module drv_mod
  use, intrinsic :: iso_c_binding, only: C_PTR, C_INT, c_loc
  use convergence_mod, only: Thing_type
  use project_c_wrapper_convergence_mod, only: project_Thing_wrap
  use convergence_mod_c_binding, only: Thing_c, ThingWrapper_type, create_Thing_C_from_Fortran, &
    destroy_Thing_C_from_Fortran
  implicit none
  type, extends(Thing_type) :: FThing_type      ! Fortran implementation (default check_convergence)
  end type FThing_type
  interface
    function run_cpp(c) result(code) bind(C, name="run_cpp")
      import :: C_PTR, C_INT
      type(C_PTR), value :: c
      integer(C_INT) :: code
    end function run_cpp
  end interface
contains
  subroutine fortran_side() bind(C, name="fortran_side")
    type(FThing_type), target :: ft
    type(ThingWrapper_type), target :: fw
    type(Thing_c), target :: ct
    integer :: ierr
    ft%stateSize = 3
    allocate(ft%label, source="fortran")             ! (-fno-realloc-lhs)
    fw%ptr => ft
    call create_Thing_C_from_Fortran(fw, ct, ierr)
    print '(i5)', run_cpp(c_loc(ct))
    call destroy_Thing_C_from_Fortran(ct, ierr)      ! frees the C string of the label
  end subroutine fortran_side

  subroutine use_thing(c) bind(C, name="use_thing")
    type(C_PTR), value :: c
    class(Thing_type), allocatable :: x
    logical :: again
    integer :: ierr
    call project_Thing_wrap(c, x)            ! owns the C++ object
    print '(2i3, 1x, a)', x%stateSize, x%inputSize, x%label
    call x%check_convergence([1.0, 2.0], [3.0], 0.0, .false., again, ierr)
    print '(i3, l2)', ierr, again
    call x%check_convergence([1.0], [3.0], 0.0, .false., again, ierr)
    print '(i3)', ierr
  end subroutine use_thing
end module drv_mod
""")
    (tmp_path / "run.cpp").write_text(r"""#include "convergence_mod_c_wrapper.hpp"
#include <memory>
#include <stdexcept>
#include <string>
extern "C" void use_thing(project_Thing* c);
extern "C" void fortran_side();
extern "C" int run_cpp(project_Thing* c)       // the Fortran implementation in C++: 100 * stateSize + checks
{
  project::Thing_private::CWrapperThing w(*c);
  bool again = true;
  int code = 100 * w.stateSize() + (w.label() == "fortran" ? 1000 : 0);
  w.check_convergence(project::vec::Zero(3), project::vec::Zero(1), 0.0, false, again);
  code += again ? 0 : 10;
  try
  {
    w.check_convergence(project::vec::Zero(2), project::vec::Zero(1), 0.0, false, again);
  }
  catch (const std::invalid_argument&)
  {
    code += 1;
  }
  return code;
}
class MyThing final : public project::Thing
{
public:
  MyThing() : Thing(2, 1, "cpp") {}
};
int main()
{
  auto t = std::make_unique<MyThing>();
  use_thing(project::Thing_private::toC(*t));
  t.release(); // NOLINT(bugprone-unused-return-value)
  fortran_side();
  return 0;
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (os.path.join(SUPPORT, "project_str.F90"), base, src, "convergence_mod_c_binding.F90",
              "project_c_wrapper_convergence_mod.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f in ("convergence_mod.cpp", "convergence_mod_c_wrapper.cpp", "run.cpp"):
        run(gxx + [f, "-o", f.replace(".cpp", "_cxx.o")], d)       # convergence_mod.o: the Fortran module
    run(["g++", "project_str.o", "model_base_mod.o", "convergence_mod.o", "convergence_mod_c_binding.o", "project_c_wrapper_convergence_mod.o", "drv.o",
         "convergence_mod_cxx.o", "convergence_mod_c_wrapper_cxx.o", "run_cxx.o", "-lgfortran", "-o", "m"], d)
    assert run_exe(["./m"], d).split("\n")[:4] == ["  2  1 cpp", "  0 F", " -2", " 1311"]     # -2: PROJECT_CHK_ARGUMENT


def test_reverse_class_star_cpp_data(tmp_path):
    """class(*) arguments of reverse types: C++ data (std::any) held by Fortran in a CWrapperAny_type (owns it,
    move_alloc only), passed back to C++ unchanged, Fortran data rejected (ierr); the other direction (Fortran
    implementation used in C++): Fortran data in a std::any (FortranData), C++ data rejected"""
    d, src = str(tmp_path), os.path.join(CORPUS, "reverse", "state_model_mod.F90")
    translate([], cfg(wrap_reverse=["statemodel_type"]), d, wrap=[src])
    cpp, h, cw = read((d,), "state_model_mod.cpp"), read((d,), "state_model_mod.h"), \
        read((d,), "project_c_wrapper_state_model_mod.F90")
    assert "struct project_state_model_mod_Any\n{\n  project_state_model_mod_Any_destroy destroy = nullptr;\n" \
           "  void* userData = nullptr;\n};" in h
    assert "    *state = any_to_c(std::move(state_v));" in cpp and "set_internal_state(any_from_c(state));" in cpp
    assert "  c->userData = new std::any(std::move(a));  // owned by c (destroy_any)" in cpp
    assert "    if (c_associated(state_p)) then      ! NULL: unallocated\n      allocate(CWrapperAny_type :: state)\n" \
           "      select type (state)\n      type is (CWrapperAny_type)\n        call c_f_pointer(state_p, state%c)\n" \
           "      end select\n    end if" in cw
    assert "  if( !a.has_value() )\n    return nullptr;  // unallocated in Fortran" in cpp
    assert "    type is (CWrapperAny_type)\n      if (associated(state%c)) state_p = c_loc(state%c)\n" \
           "    class default\n      ierr = -1\n      return\n    end select" in cw
    assert "public :: CWrapperAny_type" not in cw and "    final :: cwrapperany_final" in cw
    b, cwc = read((d,), "state_model_mod_c_binding.F90"), read((d,), "state_model_mod_c_wrapper.cpp")
    assert "    call wrapper%ptr%get_internal_state(state_f, ierr_f)\n    ierr = ierr_f\n" \
           "    state = f2cpp_any_new(state_f)" in b
    assert "    call move_alloc(state_h%p, state_f)\n    call wrapper%ptr%set_internal_state(state_f, ierr_f)\n" \
           "    ierr = ierr_f\n    call move_alloc(state_f, state_h%p)" in b and "public :: f2cpp_any" not in b
    assert "  state = state_model_mod_private::any_from_fortran(state_c);\n  if( ierr != 0 )\n    throw std::runtime_error(\"Error: calling C " \
           "project_StateModel::get_internal_state function pointer in CWrapperStateModel::get_internal_state!\");" in cwc
    assert "  other_.set_internal_state(&other_, state_model_mod_private::any_to_fortran(state), &ierr);" in cwc and "TODO(f2cpp):" not in cwc
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""module drv_mod
  use, intrinsic :: iso_c_binding, only: C_PTR, C_INT, c_loc
  use state_model_mod, only: StateModel_type
  use project_c_wrapper_state_model_mod, only: project_StateModel_wrap
  use state_model_mod_c_binding, only: StateModel_c, StateModelWrapper_type, create_StateModel_C_from_Fortran
  implicit none
  type, extends(StateModel_type) :: FCounter_type     ! Fortran implementation
    integer :: n = 0
  contains
    procedure :: get_internal_state => fc_get
    procedure :: set_internal_state => fc_set
    procedure :: step => fstep
    procedure :: value => fvalue
    procedure :: has_state => fhas
  end type FCounter_type
  interface
    function run_cpp(c) result(code) bind(C, name="run_cpp")
      import :: C_PTR, C_INT
      type(C_PTR), value :: c
      integer(C_INT) :: code
    end function run_cpp
  end interface
contains
  subroutine fc_get(this, state, ierr)
    class(FCounter_type), intent(in) :: this
    class(*), allocatable, intent(out) :: state
    integer, intent(out) :: ierr
    if (this%n /= 0) allocate(state, source=this%n)     ! n == 0: no state (unallocated)
    ierr = 0
  end subroutine fc_get
  subroutine fc_set(this, state, ierr)
    class(FCounter_type), intent(inout) :: this
    class(*), intent(in) :: state
    integer, intent(out) :: ierr
    ierr = -3
    select type (state)
    type is (integer)
      this%n = state
      ierr = 0
    end select
  end subroutine fc_set
  subroutine fstep(this)
    class(FCounter_type), intent(inout) :: this
    this%n = this%n + 1
  end subroutine fstep
  subroutine fhas(this, state, given)
    class(FCounter_type), intent(in) :: this
    class(*), allocatable, intent(in) :: state
    logical, intent(out) :: given
    given = allocated(state)
  end subroutine fhas
  function fvalue(this) result(v)
    class(FCounter_type), intent(in) :: this
    integer :: v
    v = this%n
  end function fvalue
  subroutine fortran_side() bind(C, name="fortran_side")
    type(FCounter_type), target :: fc
    type(StateModelWrapper_type), target :: fw
    type(StateModel_c), target :: ct
    integer :: ierr
    fw%ptr => fc
    call create_StateModel_C_from_Fortran(fw, ct, ierr)
    print '(i6)', run_cpp(c_loc(ct))
  end subroutine fortran_side
  subroutine use_model(c) bind(C, name="use_model")
    type(C_PTR), value :: c
    class(StateModel_type), allocatable :: m
    class(*), allocatable :: s, s2
    integer :: ierr
    class(*), allocatable :: s0
    logical :: given
    call project_StateModel_wrap(c, m)
    call m%get_internal_state(s0, ierr)        ! C++: empty std::any -> unallocated
    call m%has_state(s0, given)                ! unallocated -> empty std::any
    print '(2l2)', allocated(s0), given
    call m%step()
    call m%get_internal_state(s, ierr)        ! C++ data (value 1)
    call m%step()
    call m%step()
    print '(2i3)', ierr, m%value()
    call m%set_internal_state(s, ierr)
    print '(2i3)', ierr, m%value()
    call move_alloc(s, s2)                     ! moved, not copied
    call m%step()
    call m%set_internal_state(s2, ierr)
    print '(2i3)', ierr, m%value()
    call m%set_internal_state(42, ierr)        ! Fortran data: rejected
    print '(i3)', ierr
    call m%has_state(s2, given)                ! C++ data -> a non-empty std::any
    print '(l2)', given
    deallocate(s2)                             ! deletes the C++ data
  end subroutine use_model
end module drv_mod
""")
    (tmp_path / "run.cpp").write_text(r"""#include "state_model_mod_c_wrapper.hpp"
#include <any>
#include <memory>
#include <stdexcept>
extern "C" void use_model(project_StateModel* c);
extern "C" void fortran_side();
extern "C" int run_cpp(project_StateModel* c)   // the Fortran implementation in C++
{
  project::StateModel_private::CWrapperStateModel w(*c);
  std::any s;
  bool given = true;
  w.get_internal_state(s);                       // Fortran: unallocated -> empty std::any
  int empty = s.has_value() ? 0 : 1;
  w.has_state(std::any(), given);                // empty -> unallocated (allocatable dummy)
  empty += given ? 0 : 1;
  try
  {
    w.set_internal_state(std::any());            // empty for a non-allocatable dummy: error
  }
  catch (const std::runtime_error&)
  {
    empty += 1;
  }
  w.step();
  w.get_internal_state(s);                       // Fortran data (value 1)
  std::any shared = s;                           // copies share it
  w.step();
  w.step();
  int code = 100 * w.value();
  w.set_internal_state(shared);
  code += 10 * w.value();
  w.has_state(shared, given);                    // Fortran data -> allocated
  code += given ? 10000 * empty : 0;
  try
  {
    w.set_internal_state(std::any(5));           // C++ data: rejected
  }
  catch (const std::invalid_argument&)
  {
    code += 1;
  }
  return code;
}
class Counter final : public project::StateModel
{
public:
  void get_internal_state(std::any& state) const override
  {
    if( n_ != 0 )                                // 0: no state (empty)
      state = n_;
  }
  void has_state(const std::any& state, bool& given) const override { given = state.has_value(); }
  void set_internal_state(const std::any& state) override { n_ = std::any_cast<int>(state); }
  void step() override { ++n_; }
  int value() const override { return n_; }

private:
  int n_ = 0;
};
int main()
{
  auto m = std::make_unique<Counter>();
  use_model(project::StateModel_private::toC(*m));
  m.release(); // NOLINT(bugprone-unused-return-value)
  fortran_side();
  return 0;
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (src, "state_model_mod_c_binding.F90", "project_c_wrapper_state_model_mod.F90", "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f in ("state_model_mod.cpp", "state_model_mod_c_wrapper.cpp", "run.cpp"):
        run(gxx + [f, "-o", f.replace(".cpp", "_cxx.o")], d)
    run(["g++", "state_model_mod.o", "state_model_mod_c_binding.o", "project_c_wrapper_state_model_mod.o", "drv.o",
         "state_model_mod_cxx.o", "state_model_mod_c_wrapper_cxx.o", "run_cxx.o", "-lgfortran", "-o", "m"], d)
    assert run_exe(["./m"], d).split("\n")[:7] == [" F F", "  0  3", "  0  1", "  0  1", " -1", " T", " 30311"]


def test_reverse_any_name_of_a_prefixed_module(tmp_path):
    """module project_thing: C names project_thing_Any (not project_project_thing_Any)"""
    (tmp_path / "project_thing.F90").write_text("""module project_thing
  implicit none
  type, abstract :: Saver_type
  contains
    procedure(save_iface), deferred :: save_state
  end type Saver_type
  abstract interface
    subroutine save_iface(this, state)
      import :: Saver_type
      class(Saver_type), intent(in) :: this
      class(*), allocatable, intent(out) :: state
    end subroutine save_iface
  end interface
end module project_thing
""")
    translate([], cfg(wrap_reverse=["saver_type"]), str(tmp_path), wrap=[str(tmp_path / "project_thing.F90")])
    h = (tmp_path / "project_thing.h").read_text()
    assert "struct project_thing_Any\n" in h and "project_project" not in h


def test_reverse_string_constants_with_project_helpers(tmp_path):
    """wrap_string_out: the project's helpers create / free the C strings of character constants"""
    d, r = str(tmp_path), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[wrap]\nstring_out = ["project_str", "copyString", "deleteString"]\n'
                                     '[types.Thing_type]\ninterop = "bidirectional"\n'
                                     'constants = ["stateSize", "inputSize", "label"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    base = os.path.join(r, "model_base_mod.F90")
    translate([], c, d, wrap=[os.path.join(r, "convergence_mod.F90")], interface_only=[base])
    b = read((d,), "convergence_mod_c_binding.F90")
    assert "  use project_str, only: copyString, deleteString\n" in b and "f2cpp_" not in b
    assert "    if (allocated(fthing%ptr%label)) then\n      label_c => copyString(fthing%ptr%label, ierr)\n" \
           "      if (ierr /= 0) return\n      cthing%label = c_loc(label_c)\n    end if\n" in b
    assert "    call deleteString(cthing%label)\n" in b
    if shutil.which("gfortran"):
        ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
              "-Wno-unused-dummy-argument", "-I" + CORPUS]
        for f in (os.path.join(SUPPORT, "project_str.F90"), base, os.path.join(r, "convergence_mod.F90"),
                  "convergence_mod_c_binding.F90"):
            run(ff + [f], d)


def test_cwrapper_procedure_names(tmp_path):
    """c_wrapper module: procedures named like the bindings; prefixed for a second type with the same binding
    name and for names of intrinsics"""
    (tmp_path / "two_mod.F90").write_text("""module two_mod
  implicit none
  type, abstract :: A_type
  contains
    procedure(run_a), deferred :: run
    procedure(size_a), deferred :: size
  end type A_type
  type, abstract :: B_type
  contains
    procedure(run_b), deferred :: run
  end type B_type
  abstract interface
    subroutine run_a(this)
      import :: A_type
      class(A_type), intent(inout) :: this
    end subroutine run_a
    function size_a(this) result(n)
      import :: A_type
      class(A_type), intent(in) :: this
      integer :: n
    end function size_a
    subroutine run_b(this)
      import :: B_type
      class(B_type), intent(inout) :: this
    end subroutine run_b
  end interface
end module two_mod
""")
    d = str(tmp_path)
    translate([], cfg(wrap_reverse=["a_type", "b_type"]), d, wrap=[str(tmp_path / "two_mod.F90")])
    cw = (tmp_path / "project_c_wrapper_two_mod.F90").read_text()
    assert "    procedure :: run\n    procedure :: size => cwrappera_size\n" in cw
    assert "    procedure :: run => cwrapperb_run\n" in cw
    if shutil.which("gfortran"):
        ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-Wall", "-Werror", "-Wno-unused-dummy-argument"]
        for f in ("two_mod.F90", "two_mod_c_binding.F90", "project_c_wrapper_two_mod.F90"):
            run(ff + [f], d)


def class_rule_violations(texts):
    """C++ coding rules of generated classes: final or a virtual destructor; rule of five (none or all of the
    special members declared)"""
    bad = []
    for fname, txt in texts:
        for m in re.finditer(r"^(\s*)class (\w+)( final)?\s*(:[^\n{]*)?\s*\{(.*?)\n\1\};", txt, re.M | re.S):
            name, final, body = m.group(2), m.group(3), m.group(5)
            n = re.escape(name)
            k = sum(bool(re.search(p, body)) for p in (
                rf"~{n}\s*\(", rf"\b{n}\s*\(\s*const {n}\s*&", rf"operator=\s*\(\s*const {n}\s*&",
                rf"\b{n}\s*\(\s*{n}\s*&&", rf"operator=\s*\(\s*{n}\s*&&"))
            if not final and not re.search(rf"virtual ~{n}", body):
                bad.append(f"{fname}: {name}: neither final nor a virtual destructor")
            if 0 < k < 5:
                bad.append(f"{fname}: {name}: {k} of the 5 special members")
    return bad


def test_generated_classes_follow_the_coding_rules(out, tmp_path):
    """every generated class (translated, wrapped, reverse, wrappers, tests) is final or has a virtual destructor
    and declares none or all special members"""
    w, r = os.path.join(CORPUS, "wrapped"), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[types]\nAbstractListener_type.interop = "listener"\n'
                                     'AbstractThing_type.interop = "bidirectional"\n'
                                     'StateModel_type.interop = "bidirectional"\nThing_type.interop = "bidirectional"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, str(tmp_path), wrap=[os.path.join(w, f) for f in ("mysolver_mod.F90", "body_mod.F90",
                                                                         "sphere_mod.F90", "listen_mod.F90",
                                                                         "fstate_mod.F90")] +
              [os.path.join(r, f) for f in ("thing_mod.F90", "state_model_mod.F90", "convergence_mod.F90")],
              interface_only=[os.path.join(r, "model_base_mod.F90")])
    files = glob.glob(os.path.join(out[0], "*.[ch]pp")) + glob.glob(str(tmp_path / "*.[ch]pp"))
    assert len(files) > 20
    assert not class_rule_violations([(os.path.basename(f), open(f).read()) for f in files])
    assert len(class_rule_violations([("x.hpp", "class A\n{\npublic:\n  A(const A&) = delete;\n};")])) == 2  # the check


def test_reverse_cpp_only_and_non_overridable(tmp_path):
    """interop = "cpp": only the C++ implementation used from Fortran (no create_<Thing>_C_from_Fortran, no C++
    CWrapper<Thing>); a non_overridable binding is not forwarded (Fortran keeps it, C++: not virtual); stored
    argument: the C++ object keeps the wrapper (std::unique_ptr), the Fortran bridge the handle (in the C struct,
    replaced by the next call, released by the final after the C++ object); the caller owns the Fortran object"""
    d, r = str(tmp_path), os.path.join(CORPUS, "reverse")
    src, counter = os.path.join(r, "sealed_thing_mod.F90"), os.path.join(r, "counter_mod.F90")
    settings = os.path.join(r, "settings_mod.F90")      # bind(C) type, hand-wrapped C struct
    options = os.path.join(r, "options_mod.F90")        # Fortran type, C struct made by a hand-written helper
    (tmp_path / "x.toml").write_text('[types.SealedThing_type]\ninterop = "cpp"\nbindings.attach.stored = ["counter"]\n'
                                     '[types.ProgramSettings_type]\ncpp = "project_ProgramSettings"\n'
                                     '[modules.settings_mod]\nheader = "project_settings.h"\n'
                                     '[types.Options_type]\ncpp = "project_Options"\n'
                                     'c_struct = {setup = "CWrapperOptions_setup", wrapper = "CWrapperOptions_type", '
                                     'ptr = "cPtr()"}\n[modules.options_mod]\nheader = "project_options.h"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, d, [settings, options], wrap=[counter, src])
    hpp, b = read((d,), "sealed_thing_mod.hpp"), read((d,), "sealed_thing_mod_c_binding.F90")
    cw = read((d,), "sealed_thing_mod_c_binding.F90")      # interop = "cpp": no c_wrapper module
    assert not (tmp_path / "project_c_wrapper_sealed_thing_mod.F90").exists()
    assert "  virtual scalar value(scalar x) const = 0;" in hpp and "  scalar twice(scalar x) const;" in hpp
    assert "C_from_Fortran" not in b + cw and not os.path.exists(tmp_path / "sealed_thing_mod_c_wrapper.hpp")
    assert "procedure :: value" in cw and "twice" not in cw
    # stored argument
    assert "  virtual void attach(std::unique_ptr<Counter> counter) = 0;" in hpp
    # C struct data: passed by reference (C pointer to the bind(C) object), the C header includes its header
    assert "  virtual void apply_settings(const project_ProgramSettings& settings) = 0;" in hpp
    assert '#include "project_settings.h"' in read((d,), "sealed_thing_mod.h")
    assert "    call f(c_loc(this%c), settings)\n" in cw
    # C struct of a Fortran helper (c_struct): the wrapper is local (freed after the call), C++ gets the struct
    assert "  virtual void apply_options(const project_Options& options) = 0;" in hpp
    assert "    type(CWrapperOptions_type), target :: options_w      ! its C struct (freed with it)\n" in cw
    assert "    call CWrapperOptions_setup(options_w, options, options_ierr)\n" in cw and \
        "    call f(c_loc(this%c), options_w%cPtr())\n" in cw
    assert "    sealedThing->attach(std::move(counter_w));" in read((d,), "sealed_thing_mod.cpp")
    assert "  void* attach_counter = nullptr;  //!< Fortran: handle of the stored counter" in read((d,), "sealed_thing_mod.h")
    assert "    counter_old = this%c%attach_counter\n    this%c%attach_counter = project_Counter_setup(counter)" in cw
    assert "    if (c_associated(counter_old)) call project_Counter_destroy(counter_old)" in cw
    assert "    attach_counter = this%c%attach_counter" in cw and \
        "    end if\n    if (c_associated(attach_counter)) call project_Counter_destroy(attach_counter)\n" in cw
    # not stored: the reason in the TODO (here: the module of the stored type only given with -I)
    translate([], c, str(tmp_path / "i"), [counter, settings, options], wrap=[src])
    assert "TODO(f2cpp): stored argument counter: counter_type is not wrapped (give its module with -W or -w)" in \
        read((str(tmp_path / "i"),), "sealed_thing_mod_c_binding.F90")
    if not (shutil.which("gfortran") and shutil.which("g++") and os.path.isdir(EIGEN)):
        return
    (tmp_path / "drv.F90").write_text("""module drv_mod
  use, intrinsic :: iso_c_binding, only: C_PTR
  use counter_mod, only: Counter_type
  use settings_mod, only: ProgramSettings_type
  use options_mod, only: Options_type
  use sealed_thing_mod, only: SealedThing_type
  use sealed_thing_mod_c_binding, only: project_SealedThing_wrap
  implicit none
  interface
    function make_cpp() result(c) bind(C, name="make_cpp")
      import :: C_PTR
      type(C_PTR) :: c
    end function make_cpp
  end interface
contains
  subroutine run_fortran() bind(C, name="run_fortran")
    type(Counter_type), pointer :: counter
    type(ProgramSettings_type) :: settings
    type(Options_type) :: opts
    class(SealedThing_type), allocatable :: thing
    real :: y
    allocate(counter)                            ! owned by the caller
    call project_SealedThing_wrap(make_cpp(), thing)
    call thing%attach(counter)
    call thing%attach(counter)                   ! replaces the stored handle
    settings%level = 3
    call thing%apply_settings(settings)          ! C struct data: counter%add(level) in C++
    allocate(opts%name, source="fast")
    opts%level = 2
    call thing%apply_options(opts)               ! C struct of a helper: counter%add(level + 10 for "fast")
    y = thing%twice(1.5)                         ! Fortran twice -> C++ value -> counter%add
    print '(f6.2, i3)', y, counter%count
    deallocate(thing)                            ! the C++ object, then the handle
    print '(i3)', counter%count
    deallocate(counter)
  end subroutine run_fortran
end module drv_mod
""")
    (tmp_path / "run.cpp").write_text(r"""#include "sealed_thing_mod.hpp"
#include <memory>
#include <string>
class MySealed final : public project::SealedThing
{
public:
  project::scalar value(project::scalar x) const override { counter_->add(1); return x + 1.0; }
  void attach(std::unique_ptr<project::Counter> counter) override { counter_ = std::move(counter); }
  void apply_settings(const project_ProgramSettings& settings) override { counter_->add(settings.level); }
  void apply_options(const project_Options& options) override
  {
    counter_->add(options.level + (std::string(options.name) == "fast" ? 10 : 0));
  }

private:
  std::unique_ptr<project::Counter> counter_;
};
extern "C" project_SealedThing* make_cpp()
{
  auto t = std::make_unique<MySealed>();
  project_SealedThing* c = project::SealedThing_private::toC(*t);
  t.release(); // NOLINT(bugprone-unused-return-value): owned by the Fortran object
  return c;
}
extern "C" void run_fortran();
int main()
{
  run_fortran();
  return 0;
}
""")
    ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
          "-Wno-unused-dummy-argument", "-I" + CORPUS]
    for f in (settings, options, counter, src, "counter_mod_c_binding.F90", "sealed_thing_mod_c_binding.F90",
              "drv.F90"):
        run(ff + [f], d)
    gxx = ["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c"]
    for f in ("counter_mod.cpp", "sealed_thing_mod.cpp", "run.cpp"):
        run(gxx + [f, "-o", f.replace(".cpp", "_cxx.o")], d)
    run(["g++", "settings_mod.o", "options_mod.o", "counter_mod.o", "sealed_thing_mod.o", "counter_mod_c_binding.o", "sealed_thing_mod_c_binding.o",
         "drv.o", "counter_mod_cxx.o", "sealed_thing_mod_cxx.o", "run_cxx.o",
         "-lgfortran", "-o", "m"], d)
    assert run_exe(["./m"], d).split("\n")[:2] == ["  5.00 16", " 16"]


def test_reverse_factory_function_with_fortran_base(tmp_path):
    """factory methods as functions (class(...), allocatable result -> std::unique_ptr result), one with an
    expanded argument (its components: C parameters, C++ parameters as in translated code); a Fortran-only base
    type of the factory (dictionary entry) does not appear in C++"""
    d, r = str(tmp_path), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[types]\nAbstractThing_type.interop = "cpp"\n'
                                     'ThingMaker_type.factory = {register = "register_maker", unregister = "unregister_maker"}\n'
                                     'ConfigNode_type.expand = ["int tixiHandle", "const std::string& xml_path"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    srcs = [os.path.join(r, f) for f in ("config_mod.F90", "thing_mod.F90", "fn_factory_mod.F90")]
    report, _ = translate([], c, d, [os.path.join(r, "dict_mod.F90"), os.path.join(r, "node_mod.F90")], wrap=srcs)
    hpp, h = read((d,), "fn_factory_mod.hpp"), read((d,), "fn_factory_mod.h")
    cw = read((d,), "fn_factory_mod_c_binding.F90")      # factories only: no c_wrapper module
    assert "class ThingMaker\n{" in hpp and "DictEntry" not in hpp
    assert "  virtual std::unique_ptr<AbstractThing> make(const Config& config) const = 0;" in hpp
    assert "  project_ThingMaker_make make = nullptr;\n  project_ThingMaker_makeFromNode makeFromNode = nullptr;\n" in h
    assert "  virtual std::unique_ptr<AbstractThing> makeFromNode(int tixiHandle, const std::string& xml_path) const = 0;" in hpp
    assert "typedef void (*project_ThingMaker_makeFromNode)(const project_ThingMaker*, int, const char*, " \
           "project_AbstractThing**);" in flat(h)
    assert "    allocate(xml_path_c, source=trim(node%xml_path) // C_NULL_CHAR)\n" in cw and \
        "    call f(c_loc(this%c), node%tixiHandle, xml_path_c, thing_p)\n" in cw
    assert "thingMaker->makeFromNode(tixiHandle, std::string(xml_path));" in read((d,), "fn_factory_mod.cpp")
    assert "  static void makeFromNode_wrapper(" in hpp          # C / C++ names: declared spelling of the binding
    b = read((d,), "fn_factory_mod_c_binding.F90")
    assert "register_maker" not in hpp and "project_unregister_maker" not in b and "project_register_maker" not in b
    assert "subroutine project_ThingMaker_register(" in cw and "call unregister_maker(" in cw
    assert "    std::unique_ptr<AbstractThing> thing_u = thingMaker->make(*config_w);\n" \
           "    *thing = thing_u ? AbstractThing_private::toC(*thing_u) : nullptr;" in read((d,), "fn_factory_mod.cpp")
    assert "    if (c_associated(thing_p)) call project_AbstractThing_wrap(thing_p, thing)\n  end function make" in cw
    assert "  use thing_mod_c_binding, only: project_AbstractThing_wrap" in cw      # interop = "cpp": merged
    assert not any(report.files[f]["todo"] for f in srcs)
    if shutil.which("gfortran"):
        ff = ["gfortran", "-c", "-cpp", "-fdefault-real-8", "-fno-realloc-lhs", "-Wall", "-Werror",
              "-Wno-unused-dummy-argument", "-I" + CORPUS]
        for f in [os.path.join(SUPPORT, "project_str.F90"), os.path.join(r, "dict_mod.F90"),
                  os.path.join(r, "node_mod.F90")] + srcs + [
                "config_mod_c_binding.F90", "thing_mod_c_binding.F90",
                                                               "fn_factory_mod_c_binding.F90"]:
            run(ff + [f], d)
    if shutil.which("g++") and os.path.isdir(EIGEN):
        for f in ("config_mod", "thing_mod", "fn_factory_mod"):
            run(["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c", f + ".cpp",
                 "-o", f + "_cxx.o"], d)


def test_generated_declarations_are_documented(tmp_path):
    """every declaration that f2cpp generates for wrapped and bidirectional modules (classes, _private namespaces,
    toC / wrapFromC, methods, members, factories, listeners, CWrapper classes) has a doxygen comment; class
    comments: the documentation of the Fortran type, then the role of the class"""
    w, r = os.path.join(CORPUS, "wrapped"), os.path.join(CORPUS, "reverse")
    (tmp_path / "x.toml").write_text('[types]\nAbstractListener_type.interop = "listener"\n'
                                     'AbstractThing_type.interop = "bidirectional"\nStateModel_type.interop = "bidirectional"\n'
                                     'ThingMaker_type.factory = {register = "register_maker", unregister = "unregister_maker"}\n'
                                     'MySolver_type.constants = ["n", "params"]\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, str(tmp_path), [os.path.join(r, "dict_mod.F90"), os.path.join(r, "node_mod.F90")],
              wrap=[os.path.join(w, f) for f in ("mysolver_mod.F90", "body_mod.F90", "sphere_mod.F90", "listen_mod.F90",
                                                 "fstate_mod.F90")] +
              [os.path.join(r, f) for f in ("config_mod.F90", "thing_mod.F90", "state_model_mod.F90", "fn_factory_mod.F90")])
    files = glob.glob(str(tmp_path / "*.hpp"))
    assert len(files) >= 9
    missing = {os.path.basename(f): undocumented(open(f).read()) for f in files}
    assert not {f: m for f, m in missing.items() if m}
    fn = read((str(tmp_path),), "fn_factory_mod.hpp")
    assert "//! makes things; an entry of the dictionary of makers\n//! factory implemented in C++" in fn
    assert "//! internals of ThingMaker: its C struct\nnamespace ThingMaker_private" in fn
    assert "//! iteration parameters\n//! C++ copy of the Fortran type SolverParams_type" in read((str(tmp_path),), "mysolver_mod.hpp")


def test_reverse_type_integer_arrays(tmp_path):
    """integer arrays of a bidirectional type: std::vector<int> in the C++ virtual; the glue copies from the
    Fortran memory (intent(in)) and back with a size check (inout); compiles"""
    (tmp_path / "x.toml").write_text('[types]\nAbstractPicker_type.interop = "bidirectional"\n')
    c = config.load([os.path.join(SUPPORT, "f2cpp.toml"), str(tmp_path / "x.toml")])
    translate([], c, str(tmp_path), wrap=[os.path.join(CORPUS, "reverse", "picker_mod.F90")])
    h, cpp = (tmp_path / "picker_mod.hpp").read_text(), (tmp_path / "picker_mod.cpp").read_text()
    assert "virtual void pick(const std::vector<int>& idx, std::vector<int>& marks) = 0;" in h
    assert "std::vector<int> marks_v(marks, marks + marks_n);\n" \
           "    picker->pick(std::vector<int>(idx, idx + idx_n), marks_v);" in cpp
    assert "std::copy(marks_v.begin(), marks_v.end(), marks);" in cpp
    if shutil.which("g++") and os.path.isdir(EIGEN):
        for f in ("picker_mod", "picker_mod_c_wrapper"):
            run(["g++", "-std=c++17", "-Wall", "-Werror", "-I.", "-I" + SUPPORT, "-I" + EIGEN, "-c", f + ".cpp",
                 "-o", f + "_cxx.o"], str(tmp_path))
