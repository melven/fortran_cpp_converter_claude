# f2cpp package: map for development

Pipeline (`cli.translate`): `prepass` (text) → fparser2 (f2008) → `model.collect_module` → `Program.finish`
(cross-module analysis) → analysis pass (`ModuleEmitter.generate` of every module, output discarded) →
`Program.decide_int_vectors` → output pass: `ModuleEmitter` (translated) or `WrapEmitter` (wrapped, `-W`).
`Translator` (one per procedure) does expressions and statements; emitters do declarations, classes, files.

Sizes: translate.py 2640, wrap.py 1800, emit.py 1310, reverse.py 1100, model.py 800 lines.

## Files

| File | Responsibility |
|---|---|
| `__init__.py`, `__main__.py` | package doc; `python -m f2cpp` → `cli.main` |
| `cli.py` | argument parsing, parse all files, two passes, write all output files (incl. `.h` from the binding) |
| `config.py` | config file (sections → internal flat keys), style file (`style.toml`: defaults), `load`, derived defaults |
| `prepass.py` | text level: `pre_patches`, macro expansion, cpp lines / pFUnit `@` lines / blank lines → marker comments |
| `model.py` | symbol table (`Var`, `Proc`, `Binding`, `TypeDef`, `Module`, `Program`) from the parse tree; pointer classification, callbacks, unwrap types, named elements, int vectors |
| `translate.py` | `Translator`: expressions (`x_<Node>` → `X`), statements (`s_<Node>`), intrinsics (`i_*`), error-flag removal, pFUnit asserts |
| `emit.py` | `ModuleEmitter`: declarations, signatures, classes, procedures, lambdas, includes, header/source assembly, pFUnit → gtest |
| `wrap.py` | `WrapEmitter` (`-W`): C++ wrapper classes + glue, Fortran C binding module, C header; Fortran text post-processing |
| `reverse.py` | `ReverseMixin` of `WrapEmitter`: listeners, reverse types/factories (C++ implements Fortran abstract types), Fortran implementations used in C++, `class(*)` ↔ `std::any` |
| `report.py` | `Report`: counters per file (translated / untranslated / todo / info / missing), summary, JSON |

## Functions

### cli.py
- `parse_file`: prepass + parse one file → modules (marks pFUnit modules).
- `translate`: whole run (parse, `Program.finish`, analysis pass, emit, write files); returns (report, written files). `prog.translated`: modules translated in the run; `prog.wrapped`: wrapped (`-W`) and wrapped interface (`-w`) modules.
- `break_long_lines`: C++ lines over style `line_width` through `clang-format --lines` (only those lines).
- `main`: command line → `config.load` → `translate` → summary / JSON report.

### config.py
- `DEFAULTS`: internal keys (the code uses `cfg["key"]`); file formats: README "Configuration", `style.toml`.
- `STYLE_KEYS` / `PLACEHOLDERS` / `CHOICES`: style file → internal keys, template placeholders, allowed values.
- `SECTION_KEYS`, `TYPE_KEYS`, ...: config sections → internal keys; `OLD_KEYS`: old format is an error.
- `load_style`, `load_config` / `load_type`: one file each (unknown keys are errors); `load`: built-in style,
  files, keyword overrides (internal keys), `normalise` (lower case, `<PREFIX>_*` macros, macro roles from
  `macro_include`, `c_prefix` / `c_real` / ... from the namespace). Macro prefix: from the files' namespace, not `-n`.
- `subst(template, **values)`: style templates (`{name}`, no brace escaping).
- `unused_entries(cfg, words, prog)`: `--check-config` (entries whose names do not occur in the sources; globs match words).
- `tools/convert_config.py`: old flat format → config + style file (one-time).

### prepass.py
- `_find_close`: matching parenthesis (strings respected). `split_args`: top-level macro arguments.
- `_join_continuations`: joins `&` continuation lines inside macro arguments.
- `expand_macros`: replaces configured macros (`macros`) by Fortran text, nested, not in comments / cpp lines.
- `cpp_condition` / `resolve_conditionals`: `#if` blocks of known macros (`[prepass] defined / undefined`) resolved; decided if the value is the same for all values of the unknown macros (`defined(A) || defined(B)` with `A` defined: true).
- `prepass`: patches, macros, marker comments for `#` lines (`CPP_MARK`), pFUnit `@` directives (`PF_MARK`, asserts → calls), blank lines (`BLANK_MARK`).

### model.py
- `cls`, `lname`, `items_of`, `cname`: node class name, lower-case name, list children, spelling.
- `Var` (`has`, `opt_kind`: `std::optional` of value / reference, `ptr_like`), `Proc`, `Binding`, `TypeDef`, `Module` (`is_public`): data classes.
- `parse_type_spec`, `parse_array_spec`, `parse_decl`, `parse_proc_decl`: declarations → `Var`.
- `collect_param_docs`: `!<` / `!>` documentation of dummies. `collect_proc`, `collect_type`, `collect_module`: parse tree → symbols.
- `_target_name`, `classify_pointers`: Fortran pointers → owned (`unique_ptr`) / references / raw.
- `leading_doc`: comment lines before a type statement (`TypeDef.doc`).
- `Program`: `add`, `finish` (cross-module analysis), `inconsistent_case`, `classify_callbacks` (user-data callbacks), `unwrap_types` (wrapper types → wrapped type + companions), `mark_named_elements`, `file_stem`, `decide_int_vectors` (interface of a module: Eigen; inside: by uses and links), `visible`, `find_type`, `ancestors`, `polymorphic`, `find_component` (incl. inherited), `find_binding`, `binding_proc`, `overrides`, `descendants`, `overridden`, `type_name` (C++ name incl. namespace / type_map), `namespace`.

### translate.py
- `Untranslatable(category)`: raised for anything not translated (statement becomes a `TODO(f2cpp)` comment).
- `X`: translated expression (code, rank, base type, precedence, Matrix/Array world, variable, flags). `Seq`: Eigen slice code. `par`: parenthesise by precedence. `cstring`: C string literal.
- `Translator` core: `lookup`, `lookup_proc`, `pick_specific` (generic resolution), `find_type`, `note` (review note), `elem_type`, `find_iface`, `proc_type`, `expansion` / `expanded_variables` / `expanded_name` (`expand_args`), `dropped` / `is_dropped` (`drop_args`), `scope_chain`, `method_proc`.
- Types: `storage` (container kind), `extents`, `nest`, `leaf_type`, `nested_ctor`, `eigen_type`, `cpp_type`, `param_type`.
- Expressions: `expr`, `int_array`, `touch`, `link`, `ref`; `x_Parenthesis`, `x_*_Literal_Constant`, `x_Name`, `var_ref`, `x_Part_Ref`, `x_Function_Reference`, `result_x`, `ref_or_call` (array element vs call), `mapped_call` / `fill_template` / `method_template` / `call_template` / `declares_self` (configured mappings), `unknown_ref`, `member`, `accessor` (getter / setter / mode of a component), `own_member` (translated class: the data member in its methods), `used_member` / `used_binding` (callers: C++ visibility), `x_Proc_Component_Ref`, `x_Data_Ref` (component chains, getters, unwrapped companions).
- Indexing: `cancel_shift` (identical shifts in comparisons), `linear`, `minus` (constant folding), `index0` (0-based), `lower_bounds`, `sub_code`, `slice_length` (term cancellation), `named_count` / `named_element` / `new_named_element` (`named_elements`), `subscript`, `subscript_`.
- Operators: `unary`, `as_array`, `binary`, `as_array_p`, `power`, `matrix`, `ac_items`, `x_Array_Constructor`.
- Calls: `split_arg`, `is_err`, `plain_args`, `bound_args` (actuals in dummy order, optionals, error flag removed), `spelled`, `callback_lambda`, `call_code`, `method_call`, `call_on`, `lifetime_call` (ctor/dtor procedures), `intrinsic`.
- Output: `emit`, `fortran_comment`, `comment_text`, `comment`, `block`.
- Error flag: `err_uses`, `names`, `only_err_names`, `err_stmt` (drop bookkeeping), `err_assert` (pFUnit asserts on ierr).
- Statements: `stmt` (dispatch, keeps going on errors), `s_Assignment_Stmt`, `designator`, `aliasing` / `disjoint` (Eigen aliasing), `s_Pointer_Assignment_Stmt`, `s_Call_Stmt`, `pf_assert`, `s_Return/Exit/Cycle/Continue/Stop_Stmt`, `return_code`, `alloc_dims`, `s_Allocate_Stmt`, `s_Deallocate_Stmt`, `companion_code`, `s_Nullify_Stmt`, `s_If_Stmt`, `io_pieces` / `io_emit` / `s_Print_Stmt` / `s_Write_Stmt`, `s_Where_Stmt`, `condition`, `s_If_Construct`, `index_uses`, `s_Block_Nonlabel_Do_Construct` (0-based loops where possible), `s_Case_Construct`, `callback_cast`, `s_Select_Type_Construct`, `guard_type`, `guard_var`, `in_const_method`.
- Helpers: `last_line`, `_Trimmed` (construct without leading comments), `match_name` (glob tables), `postfix_only`, `is_zero`, `_args`.
- Intrinsics (`INTRINSICS` table): `i_size`, `i_matmul`, `i_method`, `i_transpose`, `i_reduce` (sum/product/maxval/...), `i_dot`, `i_abs`, `i_math1`, `i_std`, `i_minmax`, `i_mod`, `i_cast`, `i_present`, `i_allocated`, `i_limits`, `i_merge`, `i_reshape`, `i_sign`, `i_len`, `i_identity`, `i_bound`, `i_loc`, `i_null`.

### emit.py
- `ModuleEmitter`: `tr` (new Translator), `var_decl`, `decl_stmt`, `warn_lower_bounds`, `signature` (C++ signature of a procedure), `proto`, `warn_name`, `doc` (doxygen from Fortran comments), `emit_type` (class: members, methods, ctors, virtuals; private bindings moved into their section), `binding_level` (section of a private binding), `component_docs`, `member_docs`, `internal_order` / `emit_lambdas` (internal procedures → lambdas), `excluded_names` / `excluded` / `excluded_proc` (`exclude`), `declaration_plan` / `emit_statement` (declaration at first use), `result_decl`, `balance_preprocessor`, `emit_proc`, `drop_unused_locals` (intrinsic-type locals not used in the body), `includes`, `cpp_names`, `std_includes`, `name_includes` (`[map.headers]`: includes by use), `open_ns`, `using_other_ns`, `generate` (header + source of a module or test), `missing_modules`, `guard`, `braces` (Allman), `file_header`, `assemble_test` (gtest file).
- `collapse_identical_branches` (identical `#if` / `#else` branches, empty `#if` blocks; in `braces`, the final pass).
- `MappedNames` (configured names, glob aware), `loop_var`, `_One`, `plain_comment`, `pf_role`, `strip_blank`, `squeeze`.

### wrap.py
- `cw_in_binding` (`ReverseMixin.cw_merged`): the c_wrapper part in the binding module (factories; `interop = "cpp"` without other public types); `wrap_module`: where `project_<Thing>_wrap` is.
- Docs: `type_doc` (class comment of generated classes: Fortran type doc + role); every generated declaration is documented (test `test_generated_declarations_are_documented`).
- Names: `lower_camel`, `c_class_name`, `c_class`, `root`, `is_root`, `overrides_wrapped`, `poly`, `wrapper_type`, `c_type_name` (C handle / struct name), `struct_type`, `handle_member`, `c_name`, `proc_c_name`, `dllexport`.
- Module level: `use_only` (only-lists), `wrapped_types`, `owned` / `owned_type` (`wrap_owned`), `wrap_from_c` (`<Class>_private::wrapFromC`), `object_access` (Fortran object from the handle).
- Sizes: `doc_sizes` (`dimension (...)` in docs), `array_sizes` (size checks, allocate, explicit shapes, docs), `fortran_size` (`wrap_sizes` first, as binding code).
- Arguments: `wrappable` (argument kinds or reason), `c_type`, `f_type`.
- Components / constants: `getters`, `constants` / `_constants`, `plain_kind`, `value_type(s)` (struct constants), `constants_struct`, `plain_consts`, `copy_constants`, `setup_destroy` (`project_<Class>_setup/_destroy`), `constant_getter`, `component_kind` (reason text).
- Methods: `method` (binding + declaration + glue of one method; ~230 lines), `owned_array`, `tr` (with `via_getters`), `translated_here` / `translated_method` (`wrap_translate`), `doc` (fallback doxygen), `not_wrapped`, `getter`.
- Files: `header_comments`, `brief`, `log_includes`, `cpp_file_header`, `fortran_file_header`, `generate` (all outputs of a wrapped module; ~300 lines), `finish_module` (C declarations in column 1, only-lists, private/public, precision macros before `module`, includes right after it (they may contain `use`), sections, separators, line splitting).
- Text post-processing: `fortran_continue` (132 columns), `split_params` (long C declarations), `procedure_separators`, `section`, `spec_sections` (public / interface / type sections, one abstract interface block), `public_list`, `short_names` (63-character limit), `iso_c_only`, `use_line`, `c_header` (`.h` from `!@c_declaration`).
- `FORTRAN_SEPARATORS` (translate.py): the separator lines of generated Fortran; such comment lines are dropped in C++.
- Global: `comp_public`, `register_accessors` (accessors of wrapped types: listed / implicit `*:ref`, logical `set`, constants `get`; reverse types: only constants), `check_dependencies`.

### reverse.py (`ReverseMixin`)
- `forward`: one method of a listener / reverse type: Fortran procedure calling the function pointer, abstract interface + C typedef, C++ `<method>_wrapper` (exceptions → `ierr`); arrays (known sizes: no `_n`), logicals, created objects, `class(*)` as C++ data, wrapped objects (temporary handle; `stored`: handle in the C struct, `std::unique_ptr` wrapper moved to C++), expanded arguments (components; strings as `const char*`), created objects also as function results, C struct data (`bind(C)` types with `cpp`: by reference; `c_struct`: a local wrapper made by the configured Fortran helper, its C pointer).
- `abstract_interface` (with C typedef), `imported` (explicit `import` list), `known_size` (constant size expression for both sides), `fortran_decl`, `private_ns`, `use_binding`, `methods_of`, `emit_lines`.
- `class(*)` (one mechanism for forward wrapping and reverse types): `any_struct` (per-module `project_<module>_Any`: destroy, userData), `cpp_any` (C++ data in Fortran: `CWrapperAny_type`), `fortran_any_data` (Fortran holder `AnyHolder_type`, `f2cpp_any_new/destroy/holder`), `any_ns`, `fortran_any_decl` / `fortran_any_cpp` (`<module>_private::any_from_fortran` / `any_to_fortran`, `FortranData` in `std::any`), `cpp_any_helpers` (`any_to_c` / `any_from_c`).
- Listeners: `bridge_name`, `listener`; shared: `pure_virtuals`, `default_methods`.
- Reverse types: `reverse_constants`, `reverse_types`, `reverse_type` (C struct, C++ base, Fortran `CWrapper<Thing>_type`, factories), `fortran_behind` (Fortran implementation for C++: `wrap_<method>`, `create_<Thing>_C_from_Fortran`, C++ `CWrapper<Thing>`; not for factories and `interop = "cpp"`); `non_overridable` bindings are not forwarded, `fortran_method_c`, `cwrapper_module`.

### report.py
- `Report`: `start` (current file; `Report.CONFIG`: pseudo file of `--check-config`), `add(kind, category)`, `summary`, `write_json`.

## Findings (problems, simplifications, inconsistencies)

Correctness
- `Translator.stmt` catches every exception: an internal error (report kind `error`, exit code 2); the tests run with
  `strict = true`, so any internal error fails them.
- Broad `except Exception` also in `pick_specific` (argument of unknown kind: "assume it fits"), `known_size`,
  `doc_sizes`: silent fallbacks.
- `prepass.expand_macros` skips a macro if a `!` precedes it on the line, also inside a string literal.
- Generated C++ for Fortran and C++ sources of the same stem: `x_mod.F90` and `x_mod.cpp` both compile to `x_mod.o`
  in simple builds (tests use `-o *_cxx.o`).
- `PROJECT_CHK_IERR` mode passes `integer(C_INT)` as default `integer` (same kind on the supported compilers only).
- `class(*)` data held across the C interface: Fortran has no copy hook; an intrinsic copy of a `class(*)` holding
  `CWrapperAny_type` double-frees (rule: `move_alloc` only, not enforceable).

Structure / simplifications
- Listeners and bidirectional types are two implementations of function pointers + user data (kept: C++ owns the
  listener, owner arguments).
- Very long functions: `WrapEmitter.generate` (~300 lines), `WrapEmitter.method` (~230), `ReverseMixin.forward`
  (~220), `ModuleEmitter.generate` (~220), `emit_type` (~170), `reverse_type` (~150). Each mixes Fortran, C and
  C++ generation of one feature; candidates: one small object per argument kind with `fortran()`, `c()`, `cpp()`.
- Generated Fortran is post-processed by regular expressions (`finish_module`: sections, public list, only-lists,
  short names, line splitting). The order of these steps matters; a small line-based IR (blocks with kind:
  c_declaration / interface / type / procedure) would make it robust.
- Emitter state lives in attributes set in `generate` (`bridges`, `cw`, `cwc`, `cppany`, `wrap_routines`,
  `public_extra`, `extra_uses`, ...) and is read by the mixin; caches in `self.__dict__` (`constants_cache`,
  `reverse_constants_cache`), `getattr(self, ..., default)` patterns, `fortran_any_done` not reset in `generate`
  (fine only because there is one emitter per module).
- `constants` means two things: copied at setup (wrapped types, C struct filled by Fortran) and set by the C++
  constructor (bidirectional types).
- `translate.py` mixes expression, statement, intrinsic and error-flag logic in one 2500-line class; split along
  these lines (mixins or modules) without changing behaviour.
