# f2cpp — Fortran 2003 → C++ (Eigen, googletest) translator

Rule-based source-to-source translator for module-based, object-oriented Fortran 2003 code.
It produces a *rough but compilable-as-far-as-possible* C++ translation; everything it cannot
translate is kept as commented Fortran with a `TODO(f2cpp)` marker for manual work.

Built on [fparser2](https://github.com/stfc/fparser) (parse tree) plus an own symbol table.

## Usage

```bash
pip install -e .[test]
```

```bash
f2cpp src/expm_mod.F90 src/block_lu_mod.F90 test/test_expm_mod.pf \
      -I src/already_ported_mod.F90 -c f2cpp.toml -o cpp_out --report report.json
```

| Option | Meaning |
|---|---|
| `files` | modules (`.F90`) → `<Module>.hpp` + `<Module>.cpp` (spelling of the `module` statement; a note on stderr if that differs from the file name, a warning for files without a module); pFUnit tests (`.pf`, `.pfunit`, or any module that uses `funit` / `pfunit`) → `<module>.cpp` |
| `-I FILE` | parse only for symbols (types, signatures); no output. For used modules that are translated separately or already exist in C++ |
| `-w FILE` | like `-I`, for a module that is wrapped (`-W`) in its own run: its classes are wrapper classes (accessors of wrapped classes, wrapped object arguments) |
| `-c TOML` | configuration (see [Configuration](#configuration)); may be repeated, later files take precedence (tables and include lists are merged) |
| `-n NS` | C++ namespace (not the macro prefix) |
| `--keep-original` | Fortran statement as `// F:` comment above every translated statement |
| `-x MODULE:NAME` | do not emit `NAME` (module procedure, type, module variable, `type%binding`, `type%component` of wrapped types / struct constants; glob patterns, e.g. `io_mod:*_records`); in the config file `[modules.M] exclude = ["name", ...]` (`[modules."*"]` for all modules). Applies to translated and wrapped modules |
| `--check-config` | report config entries that no given file uses (`config-unused:types.x_type.bindings.solve`, ...) or of the wrong kind (`config-wrong-kind:procedures.x_type (a type)`): for a run over the whole code base (other modules as `-I`), to find leftovers and typos |
| `--report JSON` | construct coverage report: categories, counts and names of unresolved identifiers; **no source text** |

Pass all modules that depend on each other in one call (or via `-I`): types, ranks and
signatures of used modules are needed for a good translation, and the run must see every derived type
(classes without derived class are `final`). Unknown modules still work, with less precise output (calls are
passed through unchanged).

## Configuration

Two TOML files: the **config** (`-c`) says what is translated or wrapped and how Fortran names map to C++;
the **style** (`style = "file.toml"` in the config, relative to it) says how the generated code looks. The style
defaults are `f2cpp/style.toml` (documented there); a project style file lists only the differences. Old (flat)
configs: `python3 tools/convert_config.py old.toml new.toml` (also writes `new_style.toml`; dropped and ambiguous
entries are printed).

| Config section | Keys |
|---|---|
| `[project]` | `namespace` (default `project`), `namespace_map` (`[[module-regex, "child"], ...]`: child namespaces), `includes` (in every header; must provide `scalar`, `vec`, `mat`), `constructor_bindings` (`["configure"]`), `destructor_bindings` (`["finalize"]`), `error_arg` (`"ierr"`), `error_success` (`["0"]`), `drop_args` (names of `class(*)` user data dummies), `strict` (internal errors stop the run) |
| `[macros]` | `prefix` (default: the outer namespace in upper case), `include` (the Fortran include file of the macros: generated Fortran then uses `<P>_CHK_IERR`, `<P>_CHK_ARGUMENT`, `<P>_ASSERT(.false., ierr)`; dropped in C++); `[macros.NAME]`: `fortran` (replacement before parsing), `cpp` (C++ statement of the call it becomes) |
| `[prepass]` | `patches = [[regex, replacement], ...]` |
| `[map]` | `kinds`, `constants`, `includes` (Fortran `#include` → C++, `""`: dropped), `functions` (function / subroutine → C++ function or template), `calls` (call → C++ statement; glob patterns), `interfaces` (abstract interface → C++ type), `headers` (C++ name → include, added to the `.hpp` / `.cpp` whose code uses the name, also from templates; whole words: `Logger = '"project_logging.hpp"'`) |
| `[tests]` | `extensions` (`[".pf", ".pfunit"]`), `modules` (test framework modules), `includes` |
| `[wrap]` | `string_in`, `string_out` (string helpers of the project) |
| `[modules.M]` | `header` (header of a module that is not translated, or output name of a translated one; `""`: no include, no missing-module warning), `exclude` (names not emitted, glob patterns; `M` may be a pattern) |
| `[types.T]` | `cpp` (existing C++ class), `accessors` (see below), `unwrap`, `expand`, `named_elements` (`true` or the C++ class), `element_list`; wrapped modules: `constants`, `owned`, `independent`, `handle_member`, `c_name`, `translate` (all methods in C++), `interop` (`"bidirectional"`: implemented in C++, Fortran implementations also usable in C++; `"cpp"`: only implemented in C++, used from Fortran; `"listener"`), `factory = {register, unregister}`, `instance` |
| `[types.T.bindings.B]` | `template` (C++ for calls of the binding), `translate`, `sizes` (`{x = "this%n"}`), `stored` (`interop = "cpp"`: wrapped object arguments that the C++ object keeps, see below) |
| `[procedures.P]` | `c_name`, `translate`, `sizes` |

Names are case-insensitive; `T` may be a glob pattern for `template` (`[types."*_config_type".bindings]`).

Accessors: `accessors = ["n", "tol:set", "x:ref", "coupling_manager=couplingManager:ref", "*"]`, each entry
`component[=name][:mode]` (`*`: all components; name: default style `getter_name` / `setter_name`). Modes: `get`
(default) `T n() const` (scalars by value, others `const T&`), writes from other code are TODOs (also through
the returned object); `set`: + `void tol(T)`, `obj%tol = v` → `obj.tol(v)` (whole member); `ref`: + `T& x()`, writes
through the reference. The same list serves every origin of the class, so its callers do not change when a type
moves from wrapped to translated:

| Class | Listed components | Not listed |
|---|---|---|
| translated | data member renamed (style `member`: `n_`, protected; private in final classes), accessors generated; own and derived methods of the run use the member, other code the accessors | plain member |
| wrapped (`-W`; `-w`: called only) | accessors generated (glue into the Fortran object; `logical`: `set` instead of `ref`) | public components `ref` if the type has no list (report info `accessors-implicit`), else none |
| `-I` (translated before), existing C++ (`cpp`) | called only | plain member |
| reverse type | only `constants` (`get`) | – |

Constants (`constants`) always have `get` accessors.
Short entries fit on one line: `[types] legacy_type = {cpp = "wrappers::Legacy"}`, `[types.T.bindings]
solve.template = "..."`.

| Style section | Content |
|---|---|
| `[names]` | type suffix / case, file extensions, handle member of wrapped classes, C names (`c_prefix`, `c_real`) |
| `[layout]` | include guard, doxygen prefix, brace style, width of C declarations |
| `[types]` | `scalar`, `vec`, `mat`, `ivec`, `imat`, `int` |
| `[conventions]` | member access, optional / array / integer array arguments, Eigen placeholders, size checks of wrapped methods, gtest asserts |
| `[members]` | templates of the data member name, the accessor names and the accessors |
| `[errors]` | logging in catch blocks (and its includes), exception texts of the wrapper glue |

Templates use `{placeholders}` (no brace escaping); unknown placeholders are errors.

## What is translated

| Fortran | C++ |
|---|---|
| module `foo_mod` | `foo_mod.hpp` / `foo_mod.cpp`, `namespace <ns>` (`project.namespace_map`: child namespaces per module) |
| private module procedures / variables | anonymous namespace in the `.cpp` |
| `type :: xyz_type`, `extends`, `abstract`, `deferred`, `final` | `class xyz`, inheritance, `override` / `= 0`, destructor |
| type without derived type in the run | `class xyz final` |
| binding that is overridden in a derived type / root of a hierarchy | `virtual` method / virtual destructor; other methods are not virtual |
| `procedure, non_overridable` | `virtual ... override final` if it overrides a base binding, else not virtual; bidirectional types / listeners: not forwarded (the Fortran implementation stays, C++: the translated default, not virtual) |
| `procedure, private :: f`, `private` after `contains` (module-private) | `private:` if only methods of the type call it, `protected:` if methods of derived types do, else public (style `member_access = "fortran"`: `private:`); constructors / destructors stay public |
| binding `configure` with `intent(out)` passed object (`project.constructor_bindings`) | constructor; `call obj%configure(a)` → `obj = xyz(a);` |
| binding `finalize` without further arguments (`project.destructor_bindings`) | destructor; explicit calls are commented out with a TODO |
| components | data types without (emitted) methods: `public:`; otherwise `private:` by default; `protected:` if used by methods of derived classes; `public:` if used anywhere else in the run (free functions, other classes, tests, structure constructors). style `member_access = "fortran"`: as declared in Fortran |
| type-bound procedure, `this%x` | member function (const if `intent(in)`), `x`; `this->x` only if a local variable or argument has the same name |
| `class(t)` dummy / `class(t), allocatable` | `t&` / `std::unique_ptr<t>` |
| `select type` | `typeid` (`type is`) / `dynamic_cast` (`class is`) |
| generic interface / generic binding (`generic :: solve => solve_a, solve_b`) | overloads named `solve`; a call is matched to the specific procedure by number, keywords, rank and type of the arguments |
| `real` / `real(kind=8)` | `scalar` / `map.kinds` entry (`double`) |
| `real` array rank 1 / 2 | `vec` / `mat` |
| integer array rank 1 | `std::vector<int>` if it is only indexed, sized, filled, copied or passed on; `Eigen::VectorXi` if it is used in array expressions, sections, as vector subscript, passed to unknown procedures, or visible to other modules (dummies / results of public procedures and of methods of public types, public components, public module variables: the same with and without `-I`) (style `int_arrays = "eigen"`: always) |
| numeric array rank 3 | `std::vector<mat>`: `a(:, :, i)` → `a[i - 1]` |
| array of objects, rank 1 / 2 | `std::vector<T>` / `Eigen::Array<T, Dynamic, Dynamic>`: `b(i, j)%m(k, l)` → `b(i - 1, j - 1).m(k - 1, l - 1)` |
| any array of rank ≥ 4 (objects: ≥ 3) | flat `vec` / `std::vector<T>` with explicit column-major index and a TODO; extents from the declaration or an `allocate` in the module |
| vector subscript `v(iperm)` | `v((iperm.array() - 1))` |
| `call random_number(x)` | `x = (x.setRandom().array() + 1.0) / 2.0;` (uniform in [0, 1] as in Fortran); scalar: `std::rand() / RAND_MAX` |
| `print *, ...`, `write(*, fmt) ...` | `std::cout << ... << std::endl;` (formats with only `a`, `i0`, `g0`, `x`, `/` and literals) |
| `write(str, fmt) ...` (internal write to a string) | `str = ... + std::to_string(n) + ...;` |
| `strings = ''` (array of strings / objects = scalar) | `std::fill(v.begin(), v.end(), value);` |
| `p => null()`, `p => q` for owned pointers | `p.reset();`, `p = std::move(q);` with TODO |
| `call move_alloc(a, b)` | `b = std::move(a);` (+ `a.resize(0)`) |
| array dummies | `const mat&` / `mat&`; `Eigen::Ref<mat>` only for out/inout dummies that receive a slice in a translated call (style `array_args = "auto"`; alternatives `"plain"`, `"ref"`) |
| `pointer` that is allocated somewhere in the module | `std::unique_ptr<T>` |
| array `pointer` component that is only allocated / deallocated, never `=>` a target | `mutable vec x;` (the Fortran idiom for a member modified through an `intent(in)` object) |
| `pointer` dummy | reference |
| local `pointer` associated once (`p => a%b%c`) | `auto&& p = a.b.c;` at the point of association |
| other `pointer` (non-owning member, re-associated local) | `T*` with `TODO(f2cpp)` — decide by hand |
| `optional` | `intent(in)` arrays: `const vec& w = vec()`, `present(w)` → `w.size() > 0` (style `optional_arrays = "optional"`: `std::optional`); other `intent(in)` values: `std::optional<T>`; out/inout and objects: `std::optional<std::reference_wrapper<T>>` |
| `a(i)`, `a(i:j, k)`, `a(:, k)`, `a(i, :)` | `a(i - 1)`, `a(Eigen::seq(i - 1, j - 1), k - 1)`, `a.col(k - 1)`, `a.row(i - 1).transpose()` |
| `x(o+1:o+n)`, `a(o:o+n-1, k:k+m-1)` (length from cancelling identical `+`/`-` terms) | `x.segment(o, n)`, `a.block(o - 1, k - 1, n, m)` (also `col(..).segment`, `middleRows` / `middleCols`); otherwise `Eigen::seq` |
| `matmul`, `transpose`, `dot_product`, `sum`, `maxval`, `abs`, `size`, ... | Eigen equivalents |
| elementwise `*`, `/`, `**`, array ± scalar, `where (m) a = b` | `cwiseProduct`, `.array()` expressions, `select` |
| `do`, `do while`, `if`, `select case`, `exit`, `cycle` | `for`, `while`, `if` / `else if` chains, `break`, `continue` |
| `any([a, b, c])`, `all([a, b])` | `a \|\| b \|\| c`, `a && b` |
| `maxloc(x, 1)`, `maxloc(a)` (also `minloc`) | inline lambda around `maxCoeff(&i)`, 1-based result |
| array with a constant lower bound (`c(0:n)`, `c(-1:)`, `allocate(c(-1:n))`) | index shifted by the bound: `c(k)` → `c(k)` resp. `c(k + 1)`, also for vector subscripts: `c(idx)` → `c((idx.array() + 1))`; variable lower bounds get a TODO |
| `lbound(a, d)` / `ubound(a, d)` | Fortran values: declared lower bound / `size + lower - 1` (indices are shifted at each use) |
| members with `accessors` (`[types.solver_type] accessors = ["n", "tol:set", "x:ref"]`) | protected `n_` (private in final classes) with public `int n() const`, `void tol(scalar value)`, `vec& x()`; own and derived methods use `n_`, other code the accessors (see [Configuration](#configuration)) |
| local variables | declared before the first top-level statement that uses it; merged with a defining assignment (`int m = A.rows();`), `allocate` (`mat As(m, m);`) or constructor call (`block_lu tmp(1, n);`). Loop variables used only in their loops: `for (int i = ...)`; scalars used only in one loop body or if / select case branch and assigned first there: declared there (`int a = 2 * i;`) |
| `do i = 1, n` whose variable is mostly an array index (lower bound 1) | `for (int i = 0; i < n; ++i)` with `a(i)` → `a(i)` and other uses `i + 1` |
| internal procedures (`contains` inside a procedure) | lambdas `auto f = [&](...) -> T { ... };` at the start of the host body (host association by reference capture), ordered by their dependencies; recursive ones stay Fortran comments |
| `allocate` / `deallocate` / `allocated` | `resize` / `make_unique` / `size() > 0` ... |
| `PROJECT_CHK_IERR(call f(a, ierr), ierr)` | `f(a);` — the `ierr` argument is removed everywhere |
| `PROJECT_ASSERT(c, ierr)`, `PROJECT_CHK_ARGUMENT(c, ierr)` | `if (!(c)) throw std::logic_error(...);` / `std::invalid_argument` (`cpp` of the macro) |
| `PROJECT_ALLOCATE(a(n), ierr)` | `a.resize(n);` |
| `if` whose condition cannot be translated (e.g. `if (ierr /= 0) then`) | `if (false /* TODO(f2cpp): ... */)` with the translated body |
| other statements using `ierr` | kept as comment (`ierr = 0`, `PROJECT_ASSERT(ierr == 0, ierr)`, `if (ierr /= 0) return`, `PROJECT_CHK_IERR(ierr=ierr, ierr)` are dropped) |
| pFUnit `@test` subroutine `test_x` in module `test_foo_mod` | `TEST(test_foo_mod, test_x)`, outside the project namespace (`using namespace`) |
| `@before` / `@after` | fixture class `test_foo_mod` with `SetUp` / `TearDown`, tests become `TEST_F` |
| other procedures / variables of a test module | `static` functions / anonymous namespace |
| test module that is used by another module (shared test helpers) | additionally `<module>.hpp` with its types, constants and helper prototypes; the helpers are then not `static` |
| `@assertEqual(e, a, tolerance=t)`, `@assertTrue`, ... | `ASSERT_NEAR(e, a, t)` for scalars and Eigen types (the project must provide the Eigen variant, see style `gtest_eigen_near`, `tests.includes`), `ASSERT_TRUE`, ... |
| `@assertEqual(0, ierr)` / `@assertTrue(ierr /= 0)` after a call | dropped / `ASSERT_ANY_THROW(call)` |
| comments, `#ifdef`, `#include` (`map.includes`) | preserved; the separator lines of the generated Fortran (`!` + 80 `=`, `!` + 70 `-`) are dropped |
| doxygen `!>` / `!<` | `//!` / `//!<` (style `doxygen = "///"` for the other style) |
| `integer, intent(in) :: arg !< text` (also `!>` lines before the declaration) | `//! @param[in] arg text` in the documentation of the function in the header; result: `@return` |
| file header comment (first comment block) | top of `.hpp` and `.cpp` (and of generated bindings), file name after `@file` replaced, `@date` set to the date of the generation |
| module documentation comment | plain `//` comment before the namespace (not doxygen) |
| type, procedure and binding names | spelling of the definition (`Block_LU_type` → `Block_LU`, `myFunction`); TODO if the source is inconsistent |

## Eigen version

Sections use `Eigen::seq` with `Eigen::placeholders::all` / `last` (Eigen 5.0 / master, where only these
exist). For Eigen 3.4, where these are deprecated in favour of `Eigen::all` / `Eigen::last`, set
`eigen_placeholders = "Eigen"` in the style (`[conventions]`).

## Formatting

Style `[layout]`: `brace_style = "allman"` (default, `{` on its own line) or `"attached"`;
`include_guard = "ifndef"` (default, `#ifndef FILE_NAME_HPP`) or `"pragma"` (`#pragma once`). Indentation is two
spaces; run your formatter (clang-format) over the output for anything else.

## Macros

The macros are named after the prefix (`[macros] prefix`, default: the outer namespace in upper case,
`PROJECT` for `project`). No macros remain on the C++ side. Defaults (`P` = prefix):

| Macro | Fortran before parsing | C++ |
|---|---|---|
| `P_CHK_IERR(stmt, ierr)` | `stmt` | plain call, exceptions propagate |
| `P_ALLOCATE(a(n), ierr)` | `allocate(a(n))` | `resize` / `make_unique` |
| `P_ASSERT(c, ierr)` | `call P_ASSERT(c)` | `if (!(c)) throw std::logic_error("assertion failed: c");` |
| `P_CHK_ARGUMENT(c, ierr)` | `call P_CHK_ARGUMENT(c)` | `std::invalid_argument("invalid argument: c")` |
| `P_ASSERT_MSG(c, msg, ierr)`, `P_CHK_ARGUMENT_MSG` | `call P_ASSERT_MSG(c, msg)` | the same with `msg` as text |

Other or changed macros:

```toml
[macros]
include = "myproj_macros.h"                # Fortran header of the macros: dropped in C++, used by generated Fortran
[macros.MYPROJ_CHK_ARGUMENT_MSG]
fortran = "call myproj_chk_argument_msg({0}, {1})"            # {0}, {1}: macro arguments (without ierr)
cpp = 'if (!({0})) throw std::invalid_argument({1});'         # C++ of the call; {0:str}: argument as text
```

`[map.calls]` gives the C++ statement of other calls; keys may be glob patterns (`"log_*" = ""` drops all such
calls); an exact name takes precedence over a pattern.

`[map.functions]` renames project functions and subroutines with a direct C++ counterpart
(function references and `call` statements; arguments unchanged, `ierr` removed), e.g.
`str = "std::to_string"`. `[map.calls]` covers everything else, e.g. `sort_rank`.

A function mapped to a `std::` function is emitted without `std::` if an argument is an
Eigen array, so that an overload of the project is found: `str(v)` → `to_string(v)`,
`str(n)` → `std::to_string(n)`.

`[map.constants]` replaces named constants: `snapshotmode_read = "SnapshotMode::READ"`
(a local variable of the same name takes precedence).

A `[map.functions]` entry can also be a template that converts arguments for a C API:
`{i:cstr}` (std::string → `const char*`), `{i:ptr}` (address of an output argument),
`{i:data}` (data pointer of an Eigen array).

`examples/tixi.toml` is such a map for the TIXI XML library, generated from its `tixi.h` by
`tools/make_tixi_config.py` (the Fortran 2003 binding and the C API share names and argument
order). Several configuration files can be combined: `-c f2cpp.toml -c examples/tixi.toml`.

## Wrapped modules (stay in Fortran)

`-W FILE` / `--wrap FILE` keeps a module in Fortran and generates

| File | Content |
|---|---|
| `M_c_binding.F90` | `bind(C)` routines `project_<Class>_<method>` with `!@c_declaration` blocks |
| `M.h` | the C declarations of these blocks (normally generated by the build) |
| `M.hpp` | the classes with the interface of the translation (empty default constructor, no copy, protected `explicit MySolver(project_MySolver& c)` and handle member (style `handle_member`, default `handle_`; per root type `[types.model_type] handle_member = "model_c_"`), no other members; derived classes `using Base::Base;`); of the C interface only the forward declaration `struct project_MySolver;`, `M.h` is included in `M.cpp` |
| `M.cpp` | glue: `resize` of `intent(out)` arrays, call, `ierr != 0` → `std::runtime_error`; size checks of the other arrays with style `size_checks = true`; exception texts: style `[errors]` |

Translated modules use the wrapped classes like translated ones; components have the accessors of `accessors`
(documented like the component; `get`: `int n() const`, arrays `Eigen::Map<const vec> d() const`; `ref`: + `int& n()`
/ `Eigen::Map<vec> d()`, the const one with " (const version)"; `set`: + `void n(int)`; `logical`: `bool x() const`
by value, setter `void x(bool)` for `set` / `ref`). Types without `accessors`: all public components `ref`. Handle: by default a pointer to the C struct `project_<Class>` = bind(C) type `<Class>_c` (in the
binding module) whose member `p_impl` points to the Fortran object, for class hierarchies to
`<Root>Wrapper_type`.

Class hierarchies: the object pointer is `<Root>Wrapper_type` (`class(Root_type), pointer :: ptr`,
defined in the root's binding module; one C struct `project_<Root>` per hierarchy, passed as pointer, `const` for `intent(in)` objects). The C++
classes keep their inheritance (`class sphere final : public body`); methods of the root dispatch
in Fortran (also deferred ones), methods added in derived classes use `select type` (`ierr = -1`
or `error stop` for another dynamic type). Base types that exist independently in Fortran and C++ (`[types.T] independent = true`, e.g. a
serialization base) are not part of the hierarchy: the next derived type is its root. The base may be
wrapped in the same run or by hand
(its module given with `-I`, its C++ header with `[modules.M] header`). The binding and the glue keep
the original file header with `@file` renamed and `@brief` prefixed by `C binding: ` /
`C++ wrapper: `.

Construction from the C object: `std::unique_ptr<X> X_private::wrapFromC(project_<Root>& c)` (defined
in `M.cpp`, friend of `X`) calls the protected constructor `X(project_<Root>& c)`; derived classes
inherit it from the root. A hand-written parent class must provide this protected constructor (in its
`.cpp`, reading its members from `c`). Derived classes add no members. Moving to pure C++: delete
`wrapFromC`, the protected constructor and `handle_`; the public interface stays.

Handles are created in Fortran by the binding module's `project_<Class>_setup(obj)` (C struct, for
hierarchies also the wrapper; copies the constants) and released by `project_<Class>_destroy(handle)`
(not the object, which must outlive the handle). Both are plain Fortran procedures, not `bind(C)`.

Constants: `[types.mysolver_type] constants = ["n", "diag", "weights", "params"]` (root types): members of the C struct, filled by `setup`, only const getters in C++:

| Fortran | C struct | C++ |
|---|---|---|
| `integer :: n` | `int n;` | `int n() const` (reads the struct) |
| `real, allocatable :: diag(:)` | `PROJECT_REAL* diag; int diag_size;` (into the Fortran object) | `Eigen::Map<const vec> diag() const` |
| `real :: weights(3)` | `PROJECT_REAL weights[3];` | `Eigen::Map<const vec> weights() const` |
| `type(SolverParams_type) :: params` (plain scalars, rank-1 fixed-size / allocatable arrays; `logical` arrays are copied to `bool` in `setup`, buffer freed in `destroy`) | `project_SolverParams params;` | C++ copy of the class `SolverParams` (type of another module: its translation, give the module as input or with `-I`), member `params_` filled in the constructor, `const SolverParams& params() const` |

Changes in Fortran after `setup` are not seen. Companion variables of `unwrap` types in a wrapped type (`arr_offset1` for
`type(Block_type), allocatable :: arr(:)`) are only available as constants: `setup` gathers
`obj%arr(:)%offset1` into a buffer (freed in `destroy`), C++ `Eigen::Map<const Eigen::VectorXi> arr_offset1() const`.

Owned objects: `[types.InternalStateWrapper_type] owned = true` (root type without extensions): the C++
object owns its Fortran object (destructor `project_<Class>_free`, move-only). Array arguments of the
type are `std::vector<InternalStateWrapper>`: `intent(out)` (size from a check, an `allocate` in the procedure or `sizes`) creates
one owned object per element (`allocate(e, source=arr(i))` + `setup`), `intent(in)` copies the objects
into a Fortran array (`X_private::toC` gives the handles).

Listeners: `[types.AbstractListener_type] interop = "listener"` (abstract type with deferred bindings, `intent(in)`
arguments: the owner object, real / integer / logical scalars, real / integer rank-1 arrays; error flag). C++:
class `AbstractListener` with pure virtual methods (no error argument) and private static trampolines
`state_changed_wrapper(..., int* ierr)` (exceptions -> `*ierr = -1`, message by style `errors.log`, default
`std::fprintf(stderr, ...)`, e.g. `log = 'project_log_error("{class}", ("{message}: " + std::string({what})).c_str());'`
with `log_includes = ["<string>", '"project_log.hpp"']`; without error flag: terminate); non-copyable (its Fortran object knows its address). The binding defines
`AbstractListener_cpp` extending the Fortran type: `userData` (the C++ listener), `user` / `owner` (the C++ /
Fortran object it was set on) and one `type(C_FUNPTR)` per method, passed by the C++ constructor to
`project_AbstractListener_create`; its methods call through them (`c_f_procpointer`), so the Fortran
library references no symbol of the C++ library. Object arguments must be the owner (else `ierr = -1`).
`set_listener(std::optional<std::reference_wrapper<AbstractListener>>)`; unset it before the listener
is destroyed.

Fortran names longer than 63 characters (C names of long class / method names, `setup` / `destroy`)
are shortened to 54 characters + `_` + 8 hex digits of a hash; `bind(C, name=...)` keeps the full C name.

Abstract types implemented in C++ (Fortran dependents stay, the implementations are translated; `interop = "cpp"`: without the
other direction, i.e. no `create_<Thing>_C_from_Fortran` / C++ `CWrapper<Thing>`):

```toml
[types]
AbstractThing_type.interop = "bidirectional"
AbstractThingFactory_type.factory = { register = "register_thing_factory", unregister = "unregister_thing_factory" }
LinearThingFactory_type.instance = "linear"    # translated factories: global instance with its name
```

- abstract C struct `project_AbstractThing`: one function pointer per binding (the struct is the first
  argument), `destroy`, `userData` (the implementation: C++ or Fortran object); for C++ objects set by the
  C++ constructor
- `constants` (`[types.Thing_type] constants = ["stateSize"]`, real / integer / logical / character scalars, also inherited components;
  first in the C struct): C struct members set by the
  C++ constructor (`explicit Thing(int stateSize)`), const getters `stateSize()` (used by translated defaults),
  copied into the Fortran object by `project_Thing_wrap`; Fortran implementations: by `create_Thing_C_from_Fortran`;
  character constants: `std::string` member in C++ (`c_str()` in the struct), C string copies allocated by
  `create_Thing_C_from_Fortran` and freed by `destroy_Thing_C_from_Fortran(cthing, ierr)` (resets the struct);
  `[wrap] string_out = [module, copy, delete]` (`copy(fStr, ierr)` → C_CHAR pointer array, `delete(cStr_ptr)`): the project's helpers
- array sizes that both sides know (integer literals and constants, from `sizes`, a size check or the
  documentation `dimension (this\%stateSize)`): no `_n` argument; Fortran checks `size(x)` (`ierr = -1`, or
  with `[macros] include = "project_macros.h"`: `PROJECT_CHK_ARGUMENT(size(x) == this%n, ierr)`
  with the include right after the `module` statement (it may contain `use` statements); without error flag `error stop`), C++ maps with `stateSize()`; `CWrapperThing` checks inputs (`std::invalid_argument`)
  and resizes outputs; otherwise the size is passed (`x, x_n`)
- stored arguments (`[types.T.bindings.attach] stored = ["counter"]`, deferred bindings of `interop = "cpp"` types,
  wrapped root types): C++ gets `std::unique_ptr<Counter> counter` (the wrapper, to keep; not the Fortran object,
  which the caller owns and which must outlive the C++ object); the bridge keeps the handle in the C struct
  (`attach_counter`, Fortran only), releases the previous one after the next call and the last one in the final,
  after the C++ object
- the other direction: a Fortran implementation behind the struct for C / C++:
  `create_Thing_C_from_Fortran(fthing, cthing, ierr)` (`fthing`: `AbstractThingWrapper_type` with
  `class(AbstractThing_type), pointer :: ptr`; `cthing` intent(out), stored by the caller) sets the function
  pointers to the private `bind(C, name="")` routines `wrap_<method>` (arrays of known size: pointer views
  `x_f => x(1:this_c%n)`; positional actual arguments; with the macro include:
  `PROJECT_CHK_IERR(call wrapper%ptr%f(..., ierr), ierr)`, also for the calls of forward-wrapped methods,
  and `PROJECT_ASSERT(.false., ierr)` as the error statement of generated Fortran
  (wrong object type, wrong `class(*)` data; default `ierr = -1` + `return`); no global symbol; plain `bind(C)`
  would give clashing global symbols `wrap_<method>` across modules); C++
  `AbstractThing_private::CWrapperThing(project_AbstractThing&)` (own files `<stem>_c_wrapper.hpp/.cpp`) derives
  from `AbstractThing` and calls them (the struct outlives it; the default constructor gives an empty object);
  methods that cannot be passed (e.g. class(*)): TODO in the binding, the C++ override throws
- all bindings are forwarded (Fortran bindings are overridable): deferred ones are pure virtual in C++,
  those with a default implementation `virtual` with the translated default as definition
- C++ `AbstractThing`: pure virtual methods (translation signatures), private static `<method>_wrapper`
  (`noexcept`; arguments from C, exceptions -> `*ierr = -1`), `destroy_wrapper` (back into a `unique_ptr`),
  `std::unique_ptr<project_AbstractThing> c_`; the C header stays out of the `.hpp`
- C / C++ names of the methods (struct members, function pointer typedefs, `<method>_wrapper`): the declared spelling of
  the binding
- the binding module has what C sees (C struct `AbstractThing_c`, interfaces of the functions, each with a C
  function pointer typedef of the same name, e.g. `project_AbstractThing_evaluate`, used by the struct); the
  module `project_c_wrapper_<module>` (file of the same name; procedures named like the bindings,
  `procedure :: evaluate`, prefixed for a second type with the same name or names of intrinsics) has
  `CWrapperThing_type`: extends the abstract
  type, calls the function pointers, owns the C++ object (final -> `destroy`); `project_AbstractThing_wrap(c, x)`
  makes one; factories: `CWrapperThingFactory_type` and register / unregister. No c_wrapper module (this part in the
  binding module, all C declarations of the module from one file) for modules whose bidirectional types are all
  factories, or all factories / `interop = "cpp"` types without other public types in the module (else a dependency
  cycle is possible: other modules use the bindings of forward-wrapped types); `project_<Thing>_wrap` is then in
  `<module>_c_binding`
- factories: the `register` / `unregister` procedures of `factory = {...}` are not wrapped themselves (only for the generated
  registration); C++ constructor `AbstractThingFactory(name)` registers via `project_AbstractThingFactory_register`
  in the existing Fortran registration (`CWrapperThingFactory_type`, a copy is stored), destructor
  unregisters; `create_thing(..., std::unique_ptr<AbstractThing>& thing)` (Fortran `intent(out)` dummy) or
  `std::unique_ptr<AbstractThing> create_thing(...)` (Fortran function with an allocatable `class(...)` result): `release()` to
  Fortran; a Fortran-only base type of the factory (e.g. a dictionary entry) does not appear in C++
- translated factories get `using Base::Base;` and `<Factory>_private::instance("name")`
- arguments: wrapped objects (temporary handles), expanded arguments (`expand`, `intent(in)`: their integer / real /
  logical / character components as C parameters, C++ parameters as in translated code), real / integer / logical scalars (in / out / inout),
  real / integer rank-1 arrays (copied; the size must not change), created objects, scalar results, `ierr`
- the Fortran library references no symbol of the C++ library (registration at run time); a global
  factory instance in a static library is only constructed if its object file is linked

The binding module is `private`; one `public ::` statement per name for the types, the `bind(C)`
procedures, `setup` / `destroy` / `wrap` and the abstract interfaces (for tests in Fortran). Layout of the
binding and c_wrapper modules: precision macros (`C_REAL`) before `module`; the C prologue (`stdbool.h`,
macros, forward declarations) between `implicit none` and `private`; sections `public declarations`,
`interface declarations` (one `abstract interface` block; bodies with explicit `import` list and `implicit none`),
C header: one forward declaration per struct (`typedef struct X X;`) at the top, definitions as
`struct X { ... };`, no `struct` keyword in parameters (C and C++); a `!====...` line before each module procedure, a `!----...` line after the dummy and after the local declarations; the C pointer argument of the binding is `this`; Fortran-side
locals have the Fortran kind (`real`): only `bind(C)` arguments use `C_REAL` / `C_INT` (a kind mismatch is a
compile error), `type declarations` (comment line `!====...`).
C declarations longer than style `c_decl_width` (100): one parameter per line; code lines over 132 characters are
continued (`&` at the end and at the start of the continued line); long `use ..., only:` lists are continued
after about 100 characters, aligned after the `only:`. C++ declarations without Fortran
documentation get a generated doxygen comment (`//! Fortran <name> (undocumented)`, `//! component <name>`).

Mixing: `translate = true` (`[types.mysolver_type.bindings.norm]`, `[procedures.residual_norm]`, or
`[types.T]` for all methods) implements methods / procedures of a wrapped module in C++: same declaration (the header does not change), the
translation as definition, no binding. Components are getters into the Fortran object; `allocate` /
`deallocate` of components and array assignments that may change their size are TODOs (`Eigen::Map`),
constants are noted (copies from `setup`). Private components, private procedures and
module variables are not available (compile errors / TODOs). Fortran callers keep using the Fortran
implementation (`wrapped-translated:` in the report). Reverse types: listed bindings with a default
implementation get no `_wrapper` (C++ only; Fortran keeps its default).

Not wrapped (each with `// TODO(f2cpp): ...` and the reason in `M.hpp`, counted in the report):
methods / procedures with unsupported arguments (declared, the definition throws
`std::logic_error`), `nopass` bindings, components without getter, public module variables and
constants.

Supported so far: classes and class hierarchies, public module procedures (C name
`project_<procedure>`, `[procedures.P] c_name` for others), methods
with scalar, rank-1/2 array (sizes from `sizes`, `CHK_ARGUMENT(size(x) == this%n)`, `allocate`, explicit
shapes; for `intent(out)` finally from the documentation `!< ..., dimension (this\%n)`, report info
`wrap-size-from-doc`), input string (`str_array_ptr_to_str`), optional scalar, optional string and optional array (`NULL`; binding: disassociated pointer = absent) arguments, logical arrays (copied between `bool` and `logical`; optional: unallocated = absent), `intent(in)` / `inout` arrays of unknown size (extra C parameter `int x_n1` from `x.size()`), scalar
function results. Other methods are declared and throw `std::logic_error` (TODO). Configuration:
style `c_prefix` (default: the namespace in lower case, `project_MySolver_apply`), `[types.T] c_name`
(shorter class names in C names), style `c_real` / `c_single_precision` (default `PROJECT_REAL` /
`PROJECT_SINGLE_PRECISION` from the namespace), `[wrap] string_in` (default `project_str`), `sizes`. DLL export:
`#ifdef DLLEXPORT` / `DLLEXPORT :: name` / `#endif`. A wrapped module may use translated modules as long as their Fortran
originals are still built (noted as `wrap-uses-translated` in the report).

## Classes replaced by library classes with another interface

`[types.T.bindings.B] template` rewrites calls of type-bound procedures of a type that is mapped with
`cpp`: `[types.lu_type.bindings] solve.template = "{1} = {self}.solve({0});"` turns `call lu%solve(b, x, ierr)`
into `x = lu.solve(b);`. `{self}` is the object, `{0}`, `{1}` are the arguments (without the
error flag); a template may contain several statements. An empty template (`""`) drops the call.
Type names may be glob patterns (`[types."*_config_type".bindings]`); `{module}` / `{module_base}` give the module name
with / without the prefix `<namespace>_`; an expanded argument (`expand`) gives all its
parameters. A template that declares the object (`"Type {self}(...);"`) replaces its declaration:
`call config%read(abstractConfig, ierr)` with
`read.template = "project::config::{module_base}Config_wrapper {self}({0});"` becomes
`project::config::solverXConfig_wrapper config(tixiHandle, xml_path);`. `examples/eigen_solvers.toml` shows
an eigenvalue solver (`Eigen::SelfAdjointEigenSolver` / `Eigen::EigenSolver`), a linear solver
(`Eigen::PartialPivLU`) and a least-squares solver (`Eigen::ColPivHouseholderQR`).

## Unlimited polymorphic objects

`class(*), allocatable :: p` (black-box data) is translated as `std::any p;` (noted as
`unlimited-polymorphic`): `allocate(p, source=v)` → `p = v;`, `select type (q => p)` / `type is (real)` →
`p.type() == typeid(scalar)` / `std::any_cast<const scalar&>(p)` (read access). In a wrapped module (`-W`)
such a type is a wrapped class instead (`owned` for arrays of it passed to / from C++).

`class(*), allocatable` arguments (`intent(out)` / `intent(in)`) of wrapped methods: Fortran data held by
C++ and passed back, moved and never copied. C: the struct `project_<module>_Any` (`destroy`, `userData`: a
Fortran holder, NULL: unallocated); C++: `std::any` with a shared `FortranData` (copies share it, the last one
deletes the data in Fortran: `destroy`), helpers `<module>_private::any_from_fortran` / `any_to_fortran`. C++
data in a `std::any` cannot be passed to Fortran (`std::invalid_argument`).
`class(*)` arguments of reverse-wrapped methods (`allocatable, intent(out)` / `intent(in)`): data owned by
the other side, per module in the C struct `project_<module>_Any` (binding: `Any_c`; `destroy`, `userData`); only
its creator unpacks it. C++ implementations: `userData` is a `std::any`; in Fortran a private `CWrapperAny_type`
in the `class(*)` (owns it, final -> `destroy`; `move_alloc` only, never copy); passed back unchanged, Fortran
data: `ierr = -1`. Fortran implementations used in C++ (`CWrapper<Thing>`): `userData` is an `AnyHolder_type`
(`f2cpp_any_new`, moved, deleted by `destroy` in Fortran); in C++ a `std::any` with a shared `FortranData`;
passed back by moving the data out of its holder for the call; C++ data: `std::invalid_argument`.

## Objects that are neither copyable nor movable

`[types.variable_type] named_elements = true` (or the C++ class name): local arrays of the type become one
variable per element. `inputs(1) = Variable_type('a', '', rank=0)` → `Variable inputs_1("a", "", 0);` (each assignment declares the next variable `inputs_<n>`, counted per array; uses refer to the current one), array constructors in its arguments as braced lists (`(/1, 3/)` → `{1, 3}`, `(/'a'/)` → `{"a"}`),
`inputs(2)%rank` → `inputs_2.rank`, the whole array as argument → `{inputs_1, inputs_2}` (`{}` for
size 0). With `element_list = "VariableVector"` the list is typed:
`VariableVector{inputs_1, inputs_2}` (needed where arguments are forwarded, e.g. `std::make_unique<T>(...)`). Sizes from the declaration or a literal `allocate` (then no code; `deallocate`: nothing), literal indices; the
right-hand side a structure constructor or a constructor function (generic interface named like the type); other uses (loops, copies, `size`, reassignment) are TODOs.

## Wrapper types

`unwrap` replaces a wrapper type around a single component by that component:

```toml
[types]
mutablerealarray1d_type = {unwrap = "v"}     # type with `real, pointer, contiguous :: v(:)`
```

`type(MutableRealArray1d_type) :: tmp` becomes `mutable vec tmp;` in a class (a plain `vec` for
locals and arguments); `this%tmp%v(i)` → `tmp(i - 1)`, `allocate(this%tmp%v(n))` →
`tmp.resize(n)`, `associated(this%tmp%v)` → `tmp.size() > 0`. The wrapper type itself is not
emitted.


Several components: `unwrap = ["p", "offset1", "offset2"]`. The first replaces the variable, the
others (integer / real / logical scalars, e.g. metadata) become companion variables
`<variable>_<component>` of the same shape. `type(Block_type), allocatable :: arr(:)` with
`class(Op_type), allocatable :: p` → `std::vector<std::unique_ptr<Op>> arr; std::vector<int>
arr_offset1, arr_offset2;`, `arr(i)%offset1` → `arr_offset1[i - 1]`, `arr(i)%p%f()` → `arr[i - 1]->f()`,
`allocate(arr(n))` / `deallocate(arr)` resize / clear all of them. Not for dummy arguments; whole
elements (`arr(i)` as an object) are not translated. Classes that stay in Fortran or exist in C++: `b%arr(i)%offset1` → `b.arr_offset1()[i - 1]`
(getter from `accessors` or the wrapped class); for a wrapped class without getter of `arr`,
`size(b%arr)` uses the first companion (`b.arr_offset1().size()`).
## Arguments that are several parameters in C++

`expand` turns a dummy argument of a (not translated) type into several C++ parameters:

```toml
[types.confignode_type]
expand = ["int tixiHandle = tixiHandle", "const std::string& xpath = xml_path"]
```

Each entry is `C++ type parameter = component`; `= component` can be omitted if the component
has the parameter's name (`"int tixiHandle"`). `type(ConfigNode_type) :: configNode` becomes `int tixiHandle, const std::string& xpath`;
`configNode%xml_path` → `xpath`; passing the argument on (`call f(configNode, x)`) → `f(tixiHandle,
xpath, x)`. Other uses of the object are kept as commented Fortran (`expanded-argument`).
Local and module variables of the type (e.g. a pFUnit fixture) become the variables
`int tixiHandle; std::string xpath;` (without `const` / `&`; locals `{}`-initialised), prefixed by the
variable name (`other_xpath`) if another variable of the type is visible. Derived-type components
are not expanded.

## Callbacks

| Fortran | C++ |
|---|---|
| `abstract interface` body `NonLinearFunction` (not used by a deferred binding) | `using NonLinearFunction = std::function<void(const vec& x, vec& f)>;` |
| `procedure(NonLinearFunction) :: fun` dummy / `procedure(...), pointer, nopass :: f` component | `const NonLinearFunction& fun` / member `NonLinearFunction f;` |
| `call fun(userData, x, f, ierr)`, `associated(this%f)` | `fun(x, f);`, `f` |

`[map.interfaces]` replaces the generated alias by an existing type
(`nonlinearfunction = "std::function<void(const vec&, vec&)>"`).

`[project] drop_args = ["userData"]` removes dummies of that name that are declared `class(*)` from all
signatures and calls, like the error flag: the user data is captured by the lambda in C++.
Callback implementations of the form `subroutine cb(userData, x, f, ierr)` whose user data
is cast with `select type (userData)` to exactly one derived type `T` become methods of `T`
(`void T::cb(const vec& x, vec& f) const`); the cast and its `class default` disappear.
Where such a callback is passed together with its user data, a lambda binds it:
`call solve(cb, this, x, ierr)` → `solve([&](const vec& x, vec& f) { cb(x, f); }, x);`.
If `T` is an existing C++ class (`cpp`), `cb` stays a free function with a `const T&`
parameter. Other uses of the dropped argument are kept as commented Fortran.

## Existing C++ code (partially translated module hierarchy)

```toml
[types]                    # Fortran type -> existing C++ class
legacy_type = {cpp = "wrappers::Legacy"}

[modules]                  # module that is not translated -> header to include ("" = none)
legacy_mod = {header = "legacy.hpp"}
```

Components that are accessor functions in the C++ class:

```toml
[types]                    # components; ["*"] = all components
system_type.accessors = ["models:ref", "offsets"]     # system%models -> system.models()
other_type.accessors = ["coupling_manager=couplingManager:ref"]   # renamed: x%coupling_manager -> x.couplingManager()
```

`ref`: the accessor returns a reference (also for pointer components: `this%system%count` → `system().count`);
`get`: a const reference, writes are TODOs. Accessors of a base type apply in derived classes.

`header` is also the way to rename: `use project_global_thing` becomes
`#include "project_thing.hpp"`, and if that module is translated in the same call its output
files are `project_thing.hpp` / `project_thing.cpp`. (`[map.includes]` is only for preprocessor
`#include "x.h"` lines of the Fortran source.)

Pass the Fortran source of such a module with `-I` so that calls into it get correct
argument handling. Method names are assumed to be identical to the Fortran binding names.

## Source patches

`[prepass] patches = [[regex, replacement], ...]` are applied to the Fortran text before parsing,
e.g. to flatten kind indirections: `[['real\(kind=real_kind\)', 'real(kind=8)']]`.
Alternatively add named kinds to `[map.kinds]` (`real_kind = "double"`).

A `use m, only: a, b` produces no `#include` if all imported names are replaced by
`[map]` (`functions`, `calls`, `constants`, `kinds`) or `error_success`. A module that is obsolete in
C++ altogether (e.g. the error codes) gets `[modules.M] header = ""`: no include, no
"missing module" warning.

Includes: a module used with an `only:` list is included in the header if one of its names appears
there, in the source if it appears only there, otherwise not at all; modules used without `only:`
are included in the header.

## Missing modules

The summary and the report list used modules that are neither translated in the same call nor
given with `-I`, if they are needed: with an `only` list, the names that are not covered by
`[map]` or `cpp` of a type; without one, the names in the
file that could not be resolved.

## Parse errors

The parser accepts Fortran 2008. If a file cannot be parsed, the fparser message is printed
and the pre-processed text is written to `<outdir>/<file>.f2cpp_prepass`; the reported line number
refers to that file. Work around unsupported syntax with `[prepass] patches`.

## Known limitations (manual work)

- **LAPACK / external calls** are passed through with a TODO. `tests/support/lapack_compat.hpp`
  shows thin wrappers that keep such calls working until they are replaced by Eigen decompositions.
- **Eigen aliasing**: an assignment whose right-hand side reads the assigned array in another
  section or rearranged (`transpose`, `reshape`, ...) gets `.eval()`, `a = transpose(a)` becomes
  `a.transposeInPlace()`; products, reductions and function results count as new objects. Not
  detected: aliasing through different names (pointers, two arguments referring to the same array).
- **Loop bounds** are re-evaluated in every iteration in C++ (once in Fortran).
- The run must see every derived type (files to translate and `-I`). Otherwise a base class wrongly becomes
  `final` / non-virtual (compile error in the derived class).
- style `member_access = "auto"` only sees uses in the files of the run: translate a class together with
  its users and unit tests, otherwise members used elsewhere stay private (compile error there).
- Passing a slice to a non-`const` `Eigen::Ref` of a *different* storage layout, array-valued
  optional objects, pointers: check by hand.
- Not translated (kept as comment): I/O to units and with numeric edit descriptors (`f10.3`, `es12.4`), `where`/`forall` constructs, implied-do, named
  `exit`/`cycle`, `goto`, substrings, procedure pointers, operator overloading, recursive internal
  procedures, slices of flat (rank ≥ 4) arrays and over the vector index of `std::vector<mat>`, most string intrinsics, `program` units.
- **Name lookup across modules** is simpler than in Fortran: a name is searched in the module
  itself and then in all (transitively) used modules, public names only. `only:` lists and
  renames (`a => b`) are not evaluated, so if two used modules define the same public name,
  the first one found is taken.
- Fortran is case-insensitive: the spelling of the declaration is used. Names differing only
  in case from other C++ names in scope are not detected.

- **Listeners and reverse types** are two variants of the same mechanism (possible simplification):
  listeners could become reverse types without `destroy` (C struct in the C++ object, `set_listener`
  passes it, Fortran wraps it non-owning).
## Development

```bash
python3 -m pytest -q
```

`tests/corpus` is a synthetic code base following the conventions of the real (non-public)
code: matrix exponential + moments, block-tridiagonal LU with LAPACK, a polymorphic type
hierarchy, pFUnit tests. The tests translate it, compile it (`g++`, Eigen, googletest) and
compare the numerical results with the `gfortran` build of the original.

Workflow for unsupported constructs in the real code: run with `--report`, look at the
`untranslated` categories with the highest counts, reproduce with a small synthetic example
in `tests/corpus`, add a rule.
