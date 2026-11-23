# f2cpp: state, risks, improvement plan

As of 2026-11-23 (branch `claude/peaceful-pascal-jeib0p`): ~7,800 lines of Python in `f2cpp/`, 87 tests
(~90 s), synthetic corpus of 44 Fortran / pFUnit files. Function map and detailed findings: `f2cpp/CLAUDE.md`, tests:
`tests/CLAUDE.md`.

## State

Two modes, usable together in one run:
- **Translate** (default): Fortran modules / pFUnit tests → C++ (Eigen, googletest). Covers OOP (types,
  bindings, polymorphism, generics), arrays (0-based, lower bounds, slices, aliasing), pointers, callbacks with
  user data, internal procedures (lambdas), `class(*)` (`std::any`), error flag → exceptions, configurable
  mappings (functions, calls, methods, types, accessors, macros). Not translatable statements stay as commented
  Fortran (`TODO(f2cpp)`) and are counted in the report.
- **Wrap** (`-W`, bottom-up migration): the Fortran module stays; C binding module + C header + C++ wrapper classes
  with the same interface as a translation. Extensions: constants (also struct constants), owned objects,
  unwrapped types, listeners, `wrap_translate` (single methods in C++), reverse types / factories (abstract Fortran
  types implemented in C++, also Fortran implementations used from C++), `class(*)` state in both directions,
  sizes from checks / `wrap_sizes` / documentation, generated Fortran in the project's style (sections, macros,
  line limits).

Verification: string assertions on the generated code; compile checks (`-Wall -Werror`, gfortran `-Wall
-Werror`); run tests that compare C++ / mixed builds with the pure Fortran program (numerics, wrapped modules,
reverse types, `class(*)`); valgrind by hand on selected executables. No CI.

## Risks

Assessment (with the user, 2026-11-23): status in brackets.

1. **Coverage of the real code base**: the real code base has nearly complete unit and system tests; translated
   code is checked there. [low, user testing]
2. **Silent mistranslation** (0-based shifts, lower bounds, integer division / `mod`, Eigen aliasing, sizes):
   annoying, but detectable by the real tests. [medium; corpus numerics + differential tests below]
3. **Internal errors**: now distinct from unsupported constructs: report kind `error`, a log line with the Fortran
   location (module::procedure, line) and the f2cpp location, exit code 2, `--strict` stops (used by the tests).
   [done]
4. **Lifetime / ownership across C and Fortran**: known; `class(*)` holders: `move_alloc` only (Fortran has no copy
   hook), valgrind on the run tests by hand. [known; automate valgrind]
5. **Regular expressions on the generated Fortran** (in f2cpp, not in the user's code): `WrapEmitter.finish_module`
   rewrites the generated binding text (sections, public list, only-lists, short names, separators, line
   continuation). Each step parses Fortran lines with patterns; the order matters and a new generated construct
   can be misclassified. Covered by the compile tests of the generated Fortran. [medium; block IR below]
6. **Maintainability**: long functions, emitter state in attributes, two `class(*)` mechanisms, listeners vs.
   reverse types, one large test file with exact-text assertions. [room for improvement, section 3]
7. **Tool chain**: fparser2 node shapes; compiler assumptions (`integer(C_INT)` = default `integer`, Fortran `real`
   = `C_REAL` by compiler flags: a mismatch is a compile error at the C interface). [CI added]

Small defects (from the review): fixed: internal errors, keyword config overrides normalised, prepass dump into
the output directory, declared spelling of reverse wrapper arguments, notes counted once per procedure, Python 3.11
syntax (f-strings). Fixed later: output subdirectory for `_c_binding.F90`. Not fixed (uncritical for the user, CMake build):
`!` inside a string before a macro, same object file name for `x_mod.F90` / `x_mod.cpp` in simple builds.

## Plan

Priorities: tests first (they protect every later step), then correctness, then structure for extensions.

### 1. Tests (short term)
- [done] CI (`.github/workflows/ci.yml`): ruff (pyflakes, syntax), pytest with coverage on Python 3.11 / 3.13,
  full tool chain, `F2CPP_REQUIRE_TOOLCHAIN` fails when compile tools are missing. Line coverage 93%.
- [done] Internal errors fail the tests (`strict = true` in `tests/support/f2cpp.toml`).
- [done] Ruff bugbear (`B`): unused loop variables, `zip(..., strict=True)` where the lengths must match (a
  mismatch is now an internal error instead of silent truncation). [done] line length 132 (E501; not for the test file: long assertions of generated code).
- [done] Valgrind in the run tests (`run_exe`: memory errors and definite leaks fail) for all compiled programs.
- Split `test_f2cpp.py` by area (`test_translate_*.py`, `test_wrap_*.py`, `test_reverse_*.py`, `conftest.py`
  with `run`, `cfg`, `read`, `flat`, compiler flags, the build helpers that are now repeated in each run test).
- Golden files for a few representative outputs (reviewed diffs instead of many substring assertions), keep
  targeted assertions for semantics.
- Differential numerics: for every translated corpus module, a generated Fortran ↔ C++ comparison harness
  (same inputs, compare outputs with tolerance), including edge cases (lower bounds, empty arrays, zero-size
  slices, negative integer division).
- Property-style tests for the expression layer: random index expressions / slices → C++ evaluated vs. Fortran.
- Report-driven corpus growth: every construct category from the user's `--report` that is untranslated or
  noted gets a synthetic example.

### 2. Correctness (short / middle term)
- [done] internal errors visible, `--strict`; keyword overrides normalised; prepass dump; parameter spelling;
  report noise.
- [dropped] `wrap_constants` with character components for forward-wrapped types (not needed; reverse types: done,
  with `destroy_<Thing>_C_from_Fortran`).
- Runtime checks in generated C++ behind a macro (`F2CPP_CHECKS`): index bounds of shifted indices, sizes of
  intent(out) arrays, optional presence.
- [dropped] Explicit kind check in the generated Fortran: a kind mismatch already gives compiler errors at the C
  interface (real / integer passed directly to the `bind(C)` dummies).

### 3. Structure and extensibility (middle term)
- One `class(*)` mechanism: the `project_<module>_Any` struct (destroy + userData) for forward wrapping too;
  remove `FortranState` / `project_FortranAny`.
- Listeners as reverse types with a non-owning bridge (one implementation of function-pointer types).
- Argument kinds as small classes (scalar, logical, array, optional array, string, owned, any, created object)
  with `fortran()`, `c()`, `cpp()` parts, shared by `WrapEmitter.method`, `ReverseMixin.forward` and
  `fortran_method_c`; this removes most of the duplicated per-direction code and makes new kinds local changes.
- Generated Fortran as a small block IR (c_declaration, interface, type, procedure, use, public) instead of
  regex post-processing; layout (sections, separators, line limits) as a final printer.
- Split `translate.py` (expressions, statements, calls, intrinsics, error flag) and `wrap.py` (names/config,
  binding, glue, constants, Fortran printer) into modules/mixins without behaviour change (golden files first).
- Declarative config normalisation (key → type / normaliser), generated option documentation (README table from
  `DEFAULTS` comments).
- Emitter state: one context object per module (`ModuleState`) instead of attributes set in `generate`.

### 4. Later
- Submodules, programs, external procedures (now: report `no-module`).
- Incremental runs / caching of parsed modules for large code bases.
- Optional clang-format pass on the output.
