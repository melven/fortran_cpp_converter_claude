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
| `files` | modules (`.F90`) → `<module>.hpp` + `<module>.cpp`; pFUnit tests (`.pf`) → `<module>.cpp` |
| `-I FILE` | parse only for symbols (types, signatures); no output. For used modules that are translated separately or already exist in C++ |
| `-c TOML` | configuration, see `f2cpp/config.py` for all keys and defaults |
| `-n NS` | C++ namespace |
| `--keep-original` | Fortran statement as `// F:` comment above every translated statement |
| `--report JSON` | construct coverage report: categories and counts only, **no source text** |

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
| type-bound procedure, `this%x` | member function (const if `intent(in)`), `this->x` |
| `class(t)` dummy / `class(t), allocatable` | `t&` / `std::unique_ptr<t>` |
| `select type` | `typeid` (`type is`) / `dynamic_cast` (`class is`) |
| generic interface / generic binding | overloads |
| `real` / `real(kind=8)` | `scalar` / `kind_map` entry (`double`) |
| `real` array rank 1 / 2 / 3 | `vec` / `mat` / `std::vector<mat>` (last index = vector index) |
| array dummies | `const mat&` / `mat&`; `Eigen::Ref<mat>` only for out/inout dummies that receive a slice in a translated call (`array_args = "auto"`; alternatives `"plain"`, `"ref"`) |
| `pointer` that is allocated somewhere in the module | `std::unique_ptr<T>` |
| `pointer` dummy | reference |
| local `pointer` associated once (`p => a%b%c`) | `auto&& p = a.b.c;` at the point of association |
| other `pointer` (non-owning member, re-associated local) | `T*` with `TODO(f2cpp)` — decide by hand |
| `optional` | `std::optional<T>` (`intent(in)` values, arrays), `std::optional<std::reference_wrapper<T>>` (out/inout, objects) |
| `a(i)`, `a(i:j, k)`, `a(:, k)`, `a(i, :)` | `a(i - 1)`, `a(Eigen::seq(i - 1, j - 1), k - 1)`, `a.col(k - 1)`, `a.row(i - 1).transpose()` |
| `matmul`, `transpose`, `dot_product`, `sum`, `maxval`, `abs`, `size`, ... | Eigen equivalents |
| elementwise `*`, `/`, `**`, array ± scalar, `where (m) a = b` | `cwiseProduct`, `.array()` expressions, `select` |
| `do`, `do while`, `if`, `select case`, `exit`, `cycle` | `for`, `while`, `if` / `else if` chains, `break`, `continue` |
| `allocate` / `deallocate` / `allocated` | `resize` / `make_unique` / `size() > 0` ... |
| `PROJECT_CHK_IERR(call f(a, ierr), ierr)` | `f(a);` — the `ierr` argument is removed everywhere |
| `PROJECT_ASSERT(c, ierr)`, `PROJECT_CHK_ARGUMENT(c, ierr)` | `if (!(c)) throw std::logic_error(...);` / `std::invalid_argument` (`call_templates`) |
| `PROJECT_ALLOCATE(a(n), ierr)` | `a.resize(n);` |
| other statements using `ierr` | kept as comment (`ierr = 0`, `if (ierr /= 0) return` are dropped) |
| pFUnit `@test`, `@before`/`@after` | `TEST` / `TEST_F` with fixture |
| `@assertEqual(e, a, tolerance=t)`, `@assertTrue`, ... | `ASSERT_NEAR`, `ASSERT_LE((e - a).cwiseAbs().maxCoeff(), t)`, `ASSERT_TRUE`, ... |
| `@assertEqual(0, ierr)` / `@assertTrue(ierr /= 0)` after a call | dropped / `ASSERT_ANY_THROW(call)` |
| comments, doxygen `!>` `!<`, `#ifdef`, `#include` (`include_map`) | preserved |

## Macros

The macro names are configuration; the defaults are placeholders (`PROJECT_...`). No macros
remain on the C++ side:

```toml
[macros]                                   # macro -> Fortran replacement before parsing
MYPROJ_CHK_IERR = "{0}"                    # plain call, exceptions propagate
MYPROJ_ALLOCATE = "allocate({0})"          # resize / make_unique
MYPROJ_ASSERT = "call myproj_assert({0})"
MYPROJ_CHK_ARGUMENT = "call myproj_chk_argument({0})"

[call_templates]                           # call (lower case) -> C++ statement
myproj_assert = 'if (!({0})) throw std::logic_error("assertion failed: {0:str}");'
myproj_chk_argument = 'if (!({0})) throw std::invalid_argument("invalid argument: {0:str}");'

[include_map]
"myproj_macros.h" = ""                     # drop the Fortran macro header
```

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

## Known limitations (manual work)

- **LAPACK / external calls** are passed through with a TODO. `tests/support/lapack_compat.hpp`
  shows thin wrappers that keep such calls working until they are replaced by Eigen decompositions.
- **Eigen aliasing**: only `transpose` of the assigned variable is flagged.
- **Loop bounds** are re-evaluated in every iteration in C++ (once in Fortran).
- Module procedures accessing `private` components of a type that are not type-bound need
  `friend` or accessors.
- Passing a slice to a non-`const` `Eigen::Ref` of a *different* storage layout, array-valued
  optional objects, pointers: check by hand.
- Not translated (kept as comment): I/O, `where`/`forall` constructs, implied-do, named
  `exit`/`cycle`, `goto`, substrings, procedure pointers, operator overloading, internal
  procedures, rank > 3, vector subscripts, most string intrinsics, `program` units.
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
