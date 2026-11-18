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
| `-c TOML` | configuration, see `f2cpp/config.py` for all keys and defaults; may be repeated, later files take precedence (tables and include lists are merged) |
| `-n NS` | C++ namespace |
| `--keep-original` | Fortran statement as `// F:` comment above every translated statement |
| `-x MODULE:NAME` | do not emit `NAME` (module procedure, type, module variable, `type%binding`; glob patterns, e.g. `io_mod:*_records`); in the config file `[exclude]` with `module = ["name", ...]` (`"*" = [...]` for all modules). Applies to translated and wrapped modules |
| `--report JSON` | construct coverage report: categories, counts and names of unresolved identifiers; **no source text** |

Pass all modules that depend on each other in one call (or via `-I`): types, ranks and
signatures of used modules are needed for a good translation. Unknown modules still work,
with less precise output (calls are passed through unchanged).

## What is translated

| Fortran | C++ |
|---|---|
| module `foo_mod` | `foo_mod.hpp` / `foo_mod.cpp`, `namespace <ns>` (`namespace_map`: child namespaces per module) |
| private module procedures / variables | anonymous namespace in the `.cpp` |
| `type :: xyz_type`, `extends`, `abstract`, `deferred`, `final` | `class xyz`, inheritance, `override` / `= 0`, destructor |
| type without derived type in the run | `class xyz final` (`closed_hierarchy`) |
| binding that is overridden in a derived type / root of a hierarchy | `virtual` method / virtual destructor; other methods are not virtual |
| binding `configure` with `intent(out)` passed object (`constructor_bindings`) | constructor; `call obj%configure(a)` → `obj = xyz(a);` |
| binding `finalize` without further arguments (`destructor_bindings`) | destructor; explicit calls are commented out with a TODO |
| components | data types without (emitted) methods: `public:`; otherwise `private:` by default; `protected:` if used by methods of derived classes; `public:` if used anywhere else in the run (free functions, other classes, tests, structure constructors). `member_access = "fortran"`: as declared in Fortran |
| type-bound procedure, `this%x` | member function (const if `intent(in)`), `x`; `this->x` only if a local variable or argument has the same name (`explicit_this = true`: always) |
| `class(t)` dummy / `class(t), allocatable` | `t&` / `std::unique_ptr<t>` |
| `select type` | `typeid` (`type is`) / `dynamic_cast` (`class is`) |
| generic interface / generic binding (`generic :: solve => solve_a, solve_b`) | overloads named `solve`; a call is matched to the specific procedure by number, keywords, rank and type of the arguments |
| `real` / `real(kind=8)` | `scalar` / `kind_map` entry (`double`) |
| `real` array rank 1 / 2 | `vec` / `mat` |
| integer array rank 1 | `std::vector<int>` if it is only indexed, sized, filled, copied or passed on; `Eigen::VectorXi` if it is used in array expressions, sections, as vector subscript, or passed to unknown / untranslated (`-I`) procedures (`int_arrays = "eigen"`: always) |
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
| array dummies | `const mat&` / `mat&`; `Eigen::Ref<mat>` only for out/inout dummies that receive a slice in a translated call (`array_args = "auto"`; alternatives `"plain"`, `"ref"`) |
| `pointer` that is allocated somewhere in the module | `std::unique_ptr<T>` |
| array `pointer` component that is only allocated / deallocated, never `=>` a target | `mutable vec x;` (the Fortran idiom for a member modified through an `intent(in)` object) |
| `pointer` dummy | reference |
| local `pointer` associated once (`p => a%b%c`) | `auto&& p = a.b.c;` at the point of association |
| other `pointer` (non-owning member, re-associated local) | `T*` with `TODO(f2cpp)` — decide by hand |
| `optional` | `intent(in)` arrays: `const vec& w = vec()`, `present(w)` → `w.size() > 0` (`optional_arrays = "optional"`: `std::optional`); other `intent(in)` values: `std::optional<T>`; out/inout and objects: `std::optional<std::reference_wrapper<T>>` |
| `a(i)`, `a(i:j, k)`, `a(:, k)`, `a(i, :)` | `a(i - 1)`, `a(Eigen::seq(i - 1, j - 1), k - 1)`, `a.col(k - 1)`, `a.row(i - 1).transpose()` |
| `matmul`, `transpose`, `dot_product`, `sum`, `maxval`, `abs`, `size`, ... | Eigen equivalents |
| elementwise `*`, `/`, `**`, array ± scalar, `where (m) a = b` | `cwiseProduct`, `.array()` expressions, `select` |
| `do`, `do while`, `if`, `select case`, `exit`, `cycle` | `for`, `while`, `if` / `else if` chains, `break`, `continue` |
| `any([a, b, c])`, `all([a, b])` | `a \|\| b \|\| c`, `a && b` |
| `maxloc(x, 1)`, `maxloc(a)` (also `minloc`) | inline lambda around `maxCoeff(&i)`, 1-based result |
| array with a constant lower bound (`c(0:n)`, `c(-1:)`, `allocate(c(-1:n))`) | index shifted by the bound: `c(k)` → `c(k)` resp. `c(k + 1)`, also for vector subscripts: `c(idx)` → `c((idx.array() + 1))`; variable lower bounds get a TODO |
| `lbound(a, d)` / `ubound(a, d)` | Fortran values: declared lower bound / `size + lower - 1` (indices are shifted at each use) |
| local variables | declared before the first top-level statement that uses it; merged with a defining assignment (`int m = A.rows();`), `allocate` (`mat As(m, m);`) or constructor call (`block_lu tmp(1, n);`). Loop variables used only in their loops: `for (int i = ...)` (`declare_at_first_use`) |
| `do i = 1, n` whose variable is mostly an array index (lower bound 1) | `for (int i = 0; i < n; ++i)` with `a(i)` → `a(i)` and other uses `i + 1` (`zero_based_loops`) |
| internal procedures (`contains` inside a procedure) | lambdas `auto f = [&](...) -> T { ... };` at the start of the host body (host association by reference capture), ordered by their dependencies; recursive ones stay Fortran comments |
| `allocate` / `deallocate` / `allocated` | `resize` / `make_unique` / `size() > 0` ... |
| `PROJECT_CHK_IERR(call f(a, ierr), ierr)` | `f(a);` — the `ierr` argument is removed everywhere |
| `PROJECT_ASSERT(c, ierr)`, `PROJECT_CHK_ARGUMENT(c, ierr)` | `if (!(c)) throw std::logic_error(...);` / `std::invalid_argument` (`call_templates`) |
| `PROJECT_ALLOCATE(a(n), ierr)` | `a.resize(n);` |
| `if` whose condition cannot be translated (e.g. `if (ierr /= 0) then`) | `if (false /* TODO(f2cpp): ... */)` with the translated body |
| other statements using `ierr` | kept as comment (`ierr = 0`, `PROJECT_ASSERT(ierr == 0, ierr)`, `if (ierr /= 0) return`, `PROJECT_CHK_IERR(ierr=ierr, ierr)` are dropped) |
| pFUnit `@test` subroutine `test_x` in module `test_foo_mod` | `TEST(test_foo_mod, test_x)`, outside the project namespace (`using namespace`) |
| `@before` / `@after` | fixture class `test_foo_mod` with `SetUp` / `TearDown`, tests become `TEST_F` |
| other procedures / variables of a test module | `static` functions / anonymous namespace |
| test module that is used by another module (shared test helpers) | additionally `<module>.hpp` with its types, constants and helper prototypes; the helpers are then not `static` |
| `@assertEqual(e, a, tolerance=t)`, `@assertTrue`, ... | `ASSERT_NEAR(e, a, t)` for scalars and Eigen types (the project must provide the Eigen variant, see `gtest_eigen_near`, `test_includes`), `ASSERT_TRUE`, ... |
| `@assertEqual(0, ierr)` / `@assertTrue(ierr /= 0)` after a call | dropped / `ASSERT_ANY_THROW(call)` |
| comments, `#ifdef`, `#include` (`include_map`) | preserved |
| doxygen `!>` / `!<` | `//!` / `//!<` (`doxygen = "///"` for the other style) |
| `integer, intent(in) :: arg !< text` (also `!>` lines before the declaration) | `//! @param[in] arg text` in the documentation of the function in the header; result: `@return` |
| file header comment (first comment block) | top of `.hpp` and `.cpp`, file name after `@file` replaced |
| module documentation comment | plain `//` comment before the namespace (not doxygen) |
| type, procedure and binding names | spelling of the definition (`Block_LU_type` → `Block_LU`, `myFunction`); TODO if the source is inconsistent |

## Eigen version

Sections use `Eigen::seq` with `Eigen::placeholders::all` / `last` (Eigen ≥ 3.5 / master). For
Eigen 3.4, where these are deprecated in favour of `Eigen::all` / `Eigen::last`, set
`eigen_placeholders = "Eigen"`.

## Formatting

`brace_style = "allman"` (default, `{` on its own line) or `"attached"`.
`include_guard = "ifndef"` (default, `#ifndef FILE_NAME_HPP`) or `"pragma"` (`#pragma once`). Indentation is two
spaces; run your formatter (clang-format) over the output for anything else.

## Macros

The macro names are configuration; the defaults are placeholders (`PROJECT_...`). No macros
remain on the C++ side:

```toml
[macros]                                   # macro -> Fortran replacement before parsing
MYPROJ_CHK_IERR = "{0}"                    # plain call, exceptions propagate
MYPROJ_ALLOCATE = "allocate({0})"          # resize / make_unique
MYPROJ_ASSERT = "call myproj_assert({0})"
MYPROJ_CHK_ARGUMENT = "call myproj_chk_argument({0})"
MYPROJ_CHK_ARGUMENT_MSG = "call myproj_chk_argument_msg({0}, {1})"   # with message

[call_templates]                           # call (lower case) -> C++ statement
myproj_assert = 'if (!({0})) throw std::logic_error("assertion failed: {0:str}");'
myproj_chk_argument = 'if (!({0})) throw std::invalid_argument("invalid argument: {0:str}");'
myproj_chk_argument_msg = 'if (!({0})) throw std::invalid_argument({1});'

[include_map]
"myproj_macros.h" = ""                     # drop the Fortran macro header
```

`function_map` renames project functions and subroutines with a direct C++ counterpart
(function references and `call` statements; arguments unchanged, `ierr` removed), e.g.
`str = "std::to_string"`. `call_templates` covers everything else, e.g. `sort_rank`.
See `f2cpp.example.toml`; there are no project-specific defaults except the `PROJECT_*`
placeholders.

A function mapped to a `std::` function is emitted without `std::` if an argument is an
Eigen array, so that an overload of the project is found: `str(v)` → `to_string(v)`,
`str(n)` → `std::to_string(n)`.

`constant_map` replaces named constants: `snapshotmode_read = "SnapshotMode::READ"`
(a local variable of the same name takes precedence).

A `function_map` entry can also be a template that converts arguments for a C API:
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
| `M.hpp` | the classes with the interface of the translation (`MySolver(project_MySolver* handle)` from the Fortran object, empty default constructor, no copy, the handle as protected member); of the C interface only the forward declaration `struct project_MySolver;`, `M.h` is included in `M.cpp` |
| `M.cpp` | glue: `resize` of `intent(out)` arrays, call, `ierr != 0` → `std::runtime_error`; size checks of the other arrays with `wrap_size_checks = true` |

Translated modules use the wrapped classes like translated ones; public components are getters
(`n()` → `int&`, arrays → `Eigen::Map<vec>`). Handle: by default a pointer to the C struct `project_<Class>` = bind(C) type `<Class>_c` (in the
binding module) whose member `p_impl` points to the Fortran object, for class hierarchies to
`<Root>Wrapper_type`. `wrap_handle = "direct"` (or per root type `wrap_direct_handles`) omits the
struct: the handle points to the object / wrapper itself.

Class hierarchies: the object pointer is `<Root>Wrapper_type` (`class(Root_type), pointer :: ptr`,
defined in the root's binding module; one C struct `project_<Root>` per hierarchy, passed as pointer, `const` for `intent(in)` objects). The C++
classes keep their inheritance (`class sphere final : public body`); methods of the root dispatch
in Fortran (also deferred ones), methods added in derived classes use `select type` (`ierr = -1`
or `error stop` for another dynamic type). The base may be wrapped in the same run or by hand
(its module given with `-I`, its C++ header with `module_headers`). The binding and the glue keep
the original file header with `@file` renamed and `@brief` prefixed by `C binding: ` /
`C++ wrapper: `.

Supported so far: classes and class hierarchies, public module procedures (C name
`project_<procedure>`, `c_procedure_names` for others), methods
with scalar, rank-1/2 array (sizes from `CHK_ARGUMENT(size(x) == this%n)`, explicit shapes or
`wrap_sizes`), input string (`str_array_ptr_to_str`), optional scalar and optional string (`NULL`) arguments, scalar
function results. Other methods are declared and throw `std::logic_error` (TODO). Configuration:
`c_prefix` (default: the namespace in lower case, `project_MySolver_apply`), `c_class_names`
(shorter class names in C names), `c_real` / `c_single_precision` (default `PROJECT_REAL` /
`PROJECT_SINGLE_PRECISION` from the namespace), `wrap_string_in` (default `project_str`), `wrap_sizes`, `wrap_dllexport` (macro of the DLL export
attribute, default `DLLEXPORT`; `""` for none). A wrapped module may use translated modules as long as their Fortran
originals are still built (noted as `wrap-uses-translated` in the report).

## Classes replaced by library classes with another interface

`method_templates` rewrites calls of type-bound procedures of a type that is mapped with
`type_map`: `"lu_type%solve" = "{1} = {self}.solve({0});"` turns `call lu%solve(b, x, ierr)`
into `x = lu.solve(b);`. `{self}` is the object, `{0}`, `{1}` are the arguments (without the
error flag); a template may contain several statements. An empty template (`""`) drops the call. `examples/eigen_solvers.toml` shows
an eigenvalue solver (`Eigen::SelfAdjointEigenSolver` / `Eigen::EigenSolver`), a linear solver
(`Eigen::PartialPivLU`) and a least-squares solver (`Eigen::ColPivHouseholderQR`).

## Wrapper types

`unwrap_types` replaces a wrapper type around a single component by that component:

```toml
[unwrap_types]
mutablerealarray1d_type = "v"     # type with `real, pointer, contiguous :: v(:)`
```

`type(MutableRealArray1d_type) :: tmp` becomes `mutable vec tmp;` in a class (a plain `vec` for
locals and arguments); `this%tmp%v(i)` → `tmp(i - 1)`, `allocate(this%tmp%v(n))` →
`tmp.resize(n)`, `associated(this%tmp%v)` → `tmp.size() > 0`. The wrapper type itself is not
emitted.

## Callbacks

| Fortran | C++ |
|---|---|
| `abstract interface` body `NonLinearFunction` (not used by a deferred binding) | `using NonLinearFunction = std::function<void(const vec& x, vec& f)>;` |
| `procedure(NonLinearFunction) :: fun` dummy / `procedure(...), pointer, nopass :: f` component | `const NonLinearFunction& fun` / member `NonLinearFunction f;` |
| `call fun(userData, x, f, ierr)`, `associated(this%f)` | `fun(x, f);`, `f` |

`procedure_map` replaces the generated alias by an existing type
(`nonlinearfunction = "std::function<void(const vec&, vec&)>"`).

`drop_args = ["userData"]` removes dummies of that name that are declared `class(*)` from all
signatures and calls, like the error flag: the user data is captured by the lambda in C++.
Callback implementations of the form `subroutine cb(userData, x, f, ierr)` whose user data
is cast with `select type (userData)` to exactly one derived type `T` become methods of `T`
(`void T::cb(const vec& x, vec& f) const`); the cast and its `class default` disappear.
Where such a callback is passed together with its user data, a lambda binds it:
`call solve(cb, this, x, ierr)` → `solve([&](const vec& x, vec& f) { cb(x, f); }, x);`.
If `T` is an existing C++ class (`type_map`), `cb` stays a free function with a `const T&`
parameter. Other uses of the dropped argument are kept as commented Fortran.

## Existing C++ code (partially translated module hierarchy)

```toml
[type_map]                 # Fortran type -> existing C++ class
legacy_type = "wrappers::Legacy"

[module_headers]           # module that is not translated -> header to include ("" = none)
legacy_mod = "legacy.hpp"
```

Components that are accessor functions in the C++ class:

```toml
[accessors]                # Fortran type -> components; ["*"] = all components
system_type = ["models", "offsets"]     # system%models -> system.models()
other_type = ["coupling_manager=couplingManager"]   # renamed: x%coupling_manager -> x.couplingManager()
```

An accessor is assumed to return a reference, also if the Fortran component is a pointer:
`this%system%count` → `system().count`. Accessors of a base type apply in derived classes.

`module_headers` is also the way to rename: `use project_global_thing` becomes
`#include "project_thing.hpp"`, and if that module is translated in the same call its output
files are `project_thing.hpp` / `project_thing.cpp`. (`include_map` is only for preprocessor
`#include "x.h"` lines of the Fortran source.)

Pass the Fortran source of such a module with `-I` so that calls into it get correct
argument handling. Method names are assumed to be identical to the Fortran binding names.

## Source patches

`pre_patches = [[regex, replacement], ...]` are applied to the Fortran text before parsing,
e.g. to flatten kind indirections: `[['real\(kind=real_kind\)', 'real(kind=8)']]`.
Alternatively add named kinds to `kind_map` (`real_kind = "double"`).

A `use m, only: a, b` produces no `#include` if all imported names are replaced by
`function_map`, `call_templates`, `constant_map`, `kind_map` or `error_success`. A module that is obsolete in
C++ altogether (e.g. the error codes) goes into `ignore_modules`: no include, no
"missing module" warning.

Includes: a module used with an `only:` list is included in the header if one of its names appears
there, in the source if it appears only there, otherwise not at all; modules used without `only:`
are included in the header.

## Missing modules

The summary and the report list used modules that are neither translated in the same call nor
given with `-I`, if they are needed: with an `only` list, the names that are not covered by
`function_map`, `call_templates`, `type_map` or `kind_map`; without one, the names in the
file that could not be resolved.

## Parse errors

The parser accepts Fortran 2008. If a file cannot be parsed, the fparser message is printed
and the pre-processed text is written to `<file>.f2cpp_prepass`; the reported line number
refers to that file. Work around unsupported syntax with `pre_patches`.

## Known limitations (manual work)

- **LAPACK / external calls** are passed through with a TODO. `tests/support/lapack_compat.hpp`
  shows thin wrappers that keep such calls working until they are replaced by Eigen decompositions.
- **Eigen aliasing**: an assignment whose right-hand side reads the assigned array in another
  section or rearranged (`transpose`, `reshape`, ...) gets `.eval()`, `a = transpose(a)` becomes
  `a.transposeInPlace()`; products, reductions and function results count as new objects. Not
  detected: aliasing through different names (pointers, two arguments referring to the same array).
- **Loop bounds** are re-evaluated in every iteration in C++ (once in Fortran).
- `closed_hierarchy = true` (default) assumes that the run sees every derived type (files to
  translate and `-I`). Otherwise a base class wrongly becomes `final` / non-virtual (compile
  error in the derived class); set it to `false` for partial runs.
- `member_access = "auto"` only sees uses in the files of the run: translate a class together with
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
