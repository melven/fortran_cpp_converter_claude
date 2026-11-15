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
| `files` | modules (`.F90`) → `<module>.hpp` + `<module>.cpp`; pFUnit tests (`.pf`, `.pfunit`, or any module that uses `funit` / `pfunit`) → `<module>.cpp` |
| `-I FILE` | parse only for symbols (types, signatures); no output. For used modules that are translated separately or already exist in C++ |
| `-c TOML` | configuration, see `f2cpp/config.py` for all keys and defaults; may be repeated, later files take precedence (tables and include lists are merged) |
| `-n NS` | C++ namespace |
| `--keep-original` | Fortran statement as `// F:` comment above every translated statement |
| `--report JSON` | construct coverage report: categories, counts and names of unresolved identifiers; **no source text** |

Pass all modules that depend on each other in one call (or via `-I`): types, ranks and
signatures of used modules are needed for a good translation. Unknown modules still work,
with less precise output (calls are passed through unchanged).

## What is translated

| Fortran | C++ |
|---|---|
| module `foo_mod` | `foo_mod.hpp` / `foo_mod.cpp`, `namespace <ns>` (`namespace_map`: child namespaces per module) |
| private module procedures / variables | anonymous namespace in the `.cpp` |
| `type :: xyz_type`, `extends`, `abstract`, `deferred`, `final` | `class xyz`, inheritance, `virtual` / `override` / `= 0`, destructor |
| binding `configure` with `intent(out)` passed object (`constructor_bindings`) | constructor; `call obj%configure(a)` → `obj = xyz(a);` |
| binding `finalize` without further arguments (`destructor_bindings`) | destructor; explicit calls are commented out with a TODO |
| type-bound procedure, `this%x` | member function (const if `intent(in)`), `x`; `this->x` only if a local variable or argument has the same name (`explicit_this = true`: always) |
| `class(t)` dummy / `class(t), allocatable` | `t&` / `std::unique_ptr<t>` |
| `select type` | `typeid` (`type is`) / `dynamic_cast` (`class is`) |
| generic interface / generic binding | overloads |
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
| `optional` | `std::optional<T>` (`intent(in)` values, arrays), `std::optional<std::reference_wrapper<T>>` (out/inout, objects) |
| `a(i)`, `a(i:j, k)`, `a(:, k)`, `a(i, :)` | `a(i - 1)`, `a(Eigen::seq(i - 1, j - 1), k - 1)`, `a.col(k - 1)`, `a.row(i - 1).transpose()` |
| `matmul`, `transpose`, `dot_product`, `sum`, `maxval`, `abs`, `size`, ... | Eigen equivalents |
| elementwise `*`, `/`, `**`, array ± scalar, `where (m) a = b` | `cwiseProduct`, `.array()` expressions, `select` |
| `do`, `do while`, `if`, `select case`, `exit`, `cycle` | `for`, `while`, `if` / `else if` chains, `break`, `continue` |
| `maxloc(x, 1)`, `maxloc(a)` (also `minloc`) | inline lambda around `maxCoeff(&i)`, 1-based result |
| array with lower bound 0 (`c(0:n)`, `c(0:)`, `allocate(c(0:n))`) | 0-based as declared: `c(k)` → `c(k)`; other lower bounds are shifted and get a TODO |
| `lbound(a, d)` / `ubound(a, d)` | Fortran values: declared lower bound / `size + lower - 1` (indices are shifted at each use) |
| `allocate` / `deallocate` / `allocated` | `resize` / `make_unique` / `size() > 0` ... |
| `PROJECT_CHK_IERR(call f(a, ierr), ierr)` | `f(a);` — the `ierr` argument is removed everywhere |
| `PROJECT_ASSERT(c, ierr)`, `PROJECT_CHK_ARGUMENT(c, ierr)` | `if (!(c)) throw std::logic_error(...);` / `std::invalid_argument` (`call_templates`) |
| `PROJECT_ALLOCATE(a(n), ierr)` | `a.resize(n);` |
| other statements using `ierr` | kept as comment (`ierr = 0`, `PROJECT_ASSERT(ierr == 0, ierr)`, `if (ierr /= 0) return`, `PROJECT_CHK_IERR(ierr=ierr, ierr)` are dropped) |
| pFUnit `@test` subroutine `test_x` in module `test_foo_mod` | `TEST(test_foo_mod, test_x)`, outside the project namespace (`using namespace`) |
| `@before` / `@after` | fixture class `test_foo_mod` with `SetUp` / `TearDown`, tests become `TEST_F` |
| other procedures / variables of a test module | `static` functions / anonymous namespace |
| `@assertEqual(e, a, tolerance=t)`, `@assertTrue`, ... | `ASSERT_NEAR(e, a, t)` for scalars and Eigen types (the project must provide the Eigen variant, see `gtest_eigen_near`, `test_includes`), `ASSERT_TRUE`, ... |
| `@assertEqual(0, ierr)` / `@assertTrue(ierr /= 0)` after a call | dropped / `ASSERT_ANY_THROW(call)` |
| comments, doxygen `!>` `!<`, `#ifdef`, `#include` (`include_map`) | preserved |
| file header comment (first comment block) | top of `.hpp` and `.cpp`, file name after `@file` replaced |
| module documentation comment | plain `//` comment before the namespace (not doxygen) |
| type, procedure and binding names | spelling of the definition (`Block_LU_type` → `Block_LU`, `myFunction`); TODO if the source is inconsistent |

## Formatting

`brace_style = "allman"` (default, `{` on its own line) or `"attached"`. Indentation is two
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

A `function_map` entry can also be a template that converts arguments for a C API:
`{i:cstr}` (std::string → `const char*`), `{i:ptr}` (address of an output argument),
`{i:data}` (data pointer of an Eigen array).

`examples/tixi.toml` is such a map for the TIXI XML library, generated from its `tixi.h` by
`tools/make_tixi_config.py` (the Fortran 2003 binding and the C API share names and argument
order). Several configuration files can be combined: `-c f2cpp.toml -c examples/tixi.toml`.

## Existing C++ code (partially translated module hierarchy)

```toml
[type_map]                 # Fortran type -> existing C++ class
legacy_type = "wrappers::Legacy"

[module_headers]           # module that is not translated -> header to include ("" = none)
legacy_mod = "legacy.hpp"
```

Pass the Fortran source of such a module with `-I` so that calls into it get correct
argument handling. Method names are assumed to be identical to the Fortran binding names.

## Source patches

`pre_patches = [[regex, replacement], ...]` are applied to the Fortran text before parsing,
e.g. to flatten kind indirections: `[['real\(kind=real_kind\)', 'real(kind=8)']]`.
Alternatively add named kinds to `kind_map` (`real_kind = "double"`).

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
- **Eigen aliasing**: only `transpose` of the assigned variable is flagged.
- **Loop bounds** are re-evaluated in every iteration in C++ (once in Fortran).
- Module procedures accessing `private` components of a type that are not type-bound need
  `friend` or accessors.
- Passing a slice to a non-`const` `Eigen::Ref` of a *different* storage layout, array-valued
  optional objects, pointers: check by hand.
- Not translated (kept as comment): I/O to units and with numeric edit descriptors (`f10.3`, `es12.4`), `where`/`forall` constructs, implied-do, named
  `exit`/`cycle`, `goto`, substrings, procedure pointers, operator overloading, internal
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
