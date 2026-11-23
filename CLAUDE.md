# f2cpp

Rule-based Fortran 2003 (parsed as 2008) → C++ (Eigen / googletest) translator. The real target code base is
confidential and never available here: develop against the synthetic corpus in `tests/corpus`
and against construct reports (`--report`, no source text) supplied by the user.

## Commands

- Tests: `python3 -m pytest -q` (needs `fparser`, `pytest`; compile/numerics tests additionally
  need `g++`, `gfortran`, `libeigen3-dev`, `libgtest-dev`, `liblapack-dev`, otherwise skipped)
- Run: `python3 -m f2cpp tests/corpus/*.F90 tests/corpus/*.pf -I tests/corpus/legacy_mod.F90 -c tests/support/f2cpp.toml -o /tmp/out`

## Layout

- `f2cpp/prepass.py` — text level: macros (`PROJECT_CHK_IERR` ...), cpp directives, pFUnit `@` directives → parsable Fortran + marker comments
- `f2cpp/model.py` — symbol table (`Var`, `Proc`, `TypeDef`, `Module`, `Program`) from the fparser2 tree
- `f2cpp/translate.py` — `Translator`: expressions (`x_<NodeClass>`), statements (`s_<NodeClass>`), intrinsic table
- `f2cpp/emit.py` — module → `.hpp`/`.cpp`, classes, signatures, pFUnit module → gtest
- `f2cpp/wrap.py` — wrapped modules (`-W`): C binding module, C header, C++ wrapper classes and glue
- `f2cpp/reverse.py` — listeners, reverse types (C++ implements Fortran abstract types), `class(*)` ↔ `std::any`
- `f2cpp/config.py` — all options with defaults; `f2cpp/report.py` — coverage report
- `tests/corpus` — synthetic Fortran; `tests/support` — stand-ins for project headers, hand-written C++ driver

Details: [`f2cpp/CLAUDE.md`](f2cpp/CLAUDE.md) (pipeline, every function in one line, known problems),
[`tests/CLAUDE.md`](tests/CLAUDE.md) (tests, corpus, pitfalls), [`PLAN.md`](PLAN.md) (state, risks, plan).

## Conventions

- Never crash on input: raise `Untranslatable(category)`; the statement is then emitted as
  commented Fortran with `TODO(f2cpp)` and counted in the report. Dubious but translated
  code gets `self.note(category, msg)`.
- Expression results are `X` objects (code, rank, base type, precedence, Matrix vs. Array);
  use `par()` for parenthesisation and `as_array*()` / `matrix()` to cross Eigen worlds.
- All Fortran names are handled lower case; C++ output uses the spelling of the declaration.
- The error argument (`ierr`) is removed everywhere; C++ uses exceptions.
- New feature = synthetic example in `tests/corpus` (must compile with gfortran) + assertion in
  `tests/test_f2cpp.py`; generated C++ must compile with `-Wall -Werror`.
- fparser quirks: comments preceding a construct are children of that construct; comments in
  derived types sit between `Component_Part` nodes; `foo(x)` may be `Part_Ref`,
  `Function_Reference` or `Structure_Constructor`.
- Keep answers and docs short and factual.
