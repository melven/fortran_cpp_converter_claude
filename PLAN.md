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

1. **Coverage of the real code base is unknown.** Development uses a synthetic corpus and construct reports;
   real code can contain constructs, macro usages and naming patterns the corpus lacks. The report shows what was
   not translated, not what was translated *wrongly*.
2. **Silent mistranslation** (highest impact): 0-based index shifts, lower bounds, integer division / `mod`
   semantics, Eigen aliasing and lazy evaluation, column-major vs. nested `std::vector`, optional arguments,
   intent(out) sizes. Only the corpus programs are checked numerically.
3. **Hidden internal errors**: `Translator.stmt` turns Python exceptions into `TODO` comments
   (`internal:<Exception>` in the report); a bug looks like an unsupported construct.
4. **Lifetime / ownership across C and Fortran** (wrap mode): pointers into Fortran objects held by C++, owned
   objects, `class(*)` holders (no copy hook in Fortran: copies double-free), registration of factories (copy
   stored). Tested on small cases, valgrind not automated.
5. **Brittleness of the generated Fortran**: post-processing by regular expressions (`finish_module`); order
   dependent; long lines with macros are not split.
6. **Maintainability**: very long functions (`WrapEmitter.generate`, `method`, `ReverseMixin.forward`,
   `ModuleEmitter.generate`), state in emitter attributes, two `class(*)` mechanisms, listeners vs. reverse types,
   one 2,600-line test file with exact-text assertions (style changes touch many tests).
7. **Tool chain**: fparser2 version specifics (node shapes; quirks documented in `CLAUDE.md`); compiler-specific
   assumptions (`integer(C_INT)` = default `integer`, `-fdefault-real-8`, C++17).
8. **Environment**: no CI; compile / run tests are skipped without the tool chain (some `return` silently).

## Plan

Priorities: tests first (they protect every later step), then correctness, then structure for extensions.

### 1. Tests (short term)
- CI (GitHub Actions or the project's CI): pytest with gfortran, g++, Eigen, googletest, LAPACK, valgrind; fail
  when compile tests are skipped there.
- Corpus-wide guard: no `internal:` category in the report of any corpus run (translate and wrap).
- Valgrind in the run tests (`valgrind --error-exitcode=1` when available) for all wrap / reverse executables.
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
- Make internal errors visible: `--strict` (re-raise in `Translator.stmt`), used by the tests.
- Normalise keyword overrides in `config.load` like TOML values (or reject mixed case).
- Fix: output directory creation for `_c_binding.F90`; no `.f2cpp_prepass` file next to the input (write it to the
  output directory); `!` inside strings in `expand_macros`; declared spelling for reverse wrapper parameter names;
  report noise (`unlimited-polymorphic` once per argument).
- Runtime checks in generated C++ behind a macro (`F2CPP_CHECKS`): index bounds of shifted indices, sizes of
  intent(out) arrays, optional presence.
- Document and test the assumptions of the C binding (kinds, `integer(C_INT)` vs. default `integer`) with a
  compile-time check in the generated Fortran (`static_assert`-like `c_sizeof` comparison at setup).

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
