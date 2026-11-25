# tests: map for development

`python3 -m pytest -q` (~140 s; `--durations=8` shows the slow ones). Compile / run tests need `g++`, `gfortran`,
Eigen (`/usr/include/eigen3`), googletest, LAPACK; without them they return early or are skipped (silently for
the tests that `return`).

## Layout

| Path | Content |
|---|---|
| `test_f2cpp.py` | all tests (one file, ~3200 lines); helpers `run` (subprocess, asserts rc 0), `cfg` (test config with keyword overrides of internal keys), `out` (module fixture: whole corpus translated once), `flat` (joins one-parameter-per-line C declarations), `read`, `cxx` (`g++ -Wall -Werror -c`) |
| `corpus/*.F90`, `*.pf` | translated modules and pFUnit tests (`MODULES`); `driver.F90` (Fortran reference program) |
| `corpus/wrapped/` | modules wrapped with `-W` (Fortran stays, C++ interface) and their users |
| `corpus/reverse/` | abstract types implemented in C++ (reverse types, factories, constants, `class(*)` state) |
| `corpus/unwrap/`, `corpus/named/` | `unwrap_types`, `named_elements` |
| `corpus/project_macros.h` | stand-in for the project's error macros (`PROJECT_CHK_IERR`, `PROJECT_CHK_ARGUMENT`, ...) |
| `support/` | `f2cpp.toml` + `f2cpp_style.toml` (test config / style), stand-ins for project headers (`project_math.hpp`, `gtest_eigen.hpp`, `lapack_compat.hpp`, `legacy.hpp`, `project_log.hpp`, `project_str.F90`), hand-written C++ drivers (`driver.cpp`, `wrap_driver.cpp`, `body_driver.cpp`) and Fortran factories |

Corpus files (first comment line): `expm_mod` matrix exponential; `block_lu_mod` block-tridiagonal LU;
`shape_mod` OOP hierarchy; `features_mod` language features (compile-checked); `poly_mod` lower bound 0;
`solver_mod` / `problem_mod` callbacks with user data; `generic_mod` generic bindings; `legacy_mod` existing C++
(`-I`); `wrapped/mysolver_mod` wrapped solver; `wrapped/body_mod` + `sphere_mod` wrapped hierarchy;
`a_mod`/`b_mod`/`c_mod` hierarchy with hand-wrapped / independent bases; `state_mod` `wrap_owned`;
`fstate_mod` `class(*)` state; `listen_mod` listener; `holder_mod` / `offsets_mod` struct constants;
`shape_mod` documented sizes; `reverse/thing_mod` + `thing_factory_mod` + `linear_thing_mod` + `config_mod`
reverse types / factories; `const_thing_mod` Fortran implementation used in C++; `convergence_mod` +
`model_base_mod` reverse constants and known sizes; `state_model_mod` `class(*)` both directions;
`unwrap/blocks_mod` unwrapped multi-component type; `named/var_mod` named elements.

## Tests (one line each)

- `test_config_file_format`, `test_style_file`, `test_convert_old_config`: config / style formats, `tools/convert_config.py`; `test_unused_config_entries`: `--check-config`.
- `test_prepass_macros`, `test_custom_macro_names`: macro expansion. `test_report`: report contents, no source text.
- `test_expressions`, `test_classes`, `test_features`, `test_pfunit`, `test_options`: string checks of the translated corpus.
- `test_compiles[name]`: every translated corpus module and test compiles (`-Wall -Werror`).
- `test_same_results_as_fortran`: translated C++ driver prints the same numbers as the Fortran driver.
- `test_translated_unit_tests_pass`: translated pFUnit tests pass as gtest.
- `test_eigen_placeholders_default`, `test_missing_modules`, `test_private_names_of_other_modules_are_not_used`, `test_pfunit_file_extension`, `test_interface_bodies_of_external_procedures`, `test_tixi_function_map`, `test_int_arrays_eigen_option`, `test_int_arrays_same_with_interface_only`, `test_includes_by_name`, `test_module_header_rename`: options and edge cases.
- `test_accessors*`, `test_accessor_of_pointer_component`, `test_member_access`: accessors of existing C++ classes; `test_accessors_same_for_wrapped_and_translated`: one list, same callers; `test_wrapped_interface`: `-w`; `test_private_bindings`: `corpus/visibility_mod.F90`.
- `test_constant_map`, `test_callbacks_with_user_data`, `test_procedure_map_and_callback_implementation`, `test_callback_implementations_become_methods`, `test_method_templates_for_eigen_solvers`, `test_method_template_declaring_the_object`, `test_call_templates_with_glob_patterns`, `test_generic_bindings`, `test_to_string_of_eigen_types`: mappings and calls.
- `test_vector_subscript_of_components_with_lower_bounds`, `test_internal_procedures_become_lambdas`, `test_lambda_inside_method_sees_the_host`, `test_declarations_at_first_use_and_zero_based_loops`, `test_aliasing`, `test_slices_segment_block`, `test_optional_arrays_are_empty_by_default`, `test_output_file_names_keep_the_module_spelling`, `test_module_given_twice_and_long_expressions`, `test_preprocessor_lines_*`, `test_expanded_arguments`, `test_expanded_variables_in_tests`, `test_named_elements`, `test_unlimited_polymorphic_as_any`: translation details.
- `test_unwrapped_*`: `unwrap_types` (companions, mutable wrapper, existing classes).
- `test_wrapped_module_end_to_end[False/True]`: wrapped solver + translated user, compiled and run against the pure Fortran program; `True`: two methods implemented in C++ (`wrap_translate`), same header.
- `test_wrapped_module_without_size_checks`, `test_wrapped_out_array_size_only_in_fortran`, `test_wrap_sizes_from_documentation`: array sizes of wrapped methods.
- `test_c_names_follow_the_namespace`, `test_exclude_parts_of_a_module`, `test_includes_only_where_needed`, `test_wrapped_header_forward_declarations`, `test_wrapped_long_names` (63/132 limits), `test_wrapped_handle_member_name`: wrapped module details.
- `test_wrapped_class_hierarchy`, `test_wrapped_class_with_hand_wrapped_base`, `test_wrapped_hierarchy_below_an_independent_base`: class hierarchies across modules.
- `test_wrapped_constant_of_a_translated_type`, `test_wrapped_constants_of_unwrapped_companions`: `wrap_constants`.
- `test_wrapped_owned_objects_in_arrays`, `test_wrapped_unlimited_polymorphic_state`: `wrap_owned`, `class(*)` through C++.
- `test_wrapped_listener`: listener (compiled, run, `nm` check of the Fortran objects).
- `test_wrap_translate_guards`: `wrap_translate` (translated methods of wrapped classes).
- `test_reverse_wrapped_types`: reverse types + factories; pure Fortran vs mixed build give identical output.
- `test_reverse_type_fortran_implementation_in_cpp`: Fortran implementation used in C++ (`CWrapperThing`).
- `test_reverse_type_constants`: reverse constants (inherited), known sizes, macros in generated Fortran (`[macros] include`), both directions, run.
- `test_reverse_class_star_is_todo`, `test_reverse_class_star_cpp_data`: `class(*)` of reverse types (TODO cases; both directions, run).

## Conventions and pitfalls

- New feature: corpus file (compiles with gfortran) + assertions + compile (`-Wall -Werror`), ideally a run that
  compares with Fortran. Generated Fortran is compiled with `-fdefault-real-8 -fno-realloc-lhs -Wall -Werror`
  (and `-Wno-unused-dummy-argument` where stubs are generated).
- Assertions compare generated text exactly: style changes touch many tests; use `flat()` for C declarations.
- Objects of Fortran and C++ files with the same stem collide (`x_mod.o`): compile C++ with `-o x_mod_cxx.o`.
- Output of Fortran (`print`) and C (`printf`) in one program are buffered separately: let one side print.
- Compiled programs run under valgrind when installed (`run_exe`: memory errors and definite leaks fail).
