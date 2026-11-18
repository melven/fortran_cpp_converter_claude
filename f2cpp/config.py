"""Configuration defaults; overridden by a TOML file and command line options."""
import copy
import tomllib

DEFAULTS = {
    "namespace": "project",
    # [[module-name-regex, "child::namespace"], ...] -> namespace <namespace>::<child>
    "namespace_map": [],
    "type_suffix": "_type",        # stripped from derived type names
    "type_case": "keep",           # "keep" | "camel"
    # type-bound procedures with these binding names become constructors (if the passed
    # object is intent(out)) / destructors (if there are no further arguments)
    "constructor_bindings": ["configure"],
    "destructor_bindings": ["finalize"],
    "include_guard": "ifndef",    # "ifndef": #ifndef FILE_NAME_HPP ... #endif; "pragma": #pragma once
    "doxygen": "//!",             # prefix of documentation comments ("//!" or "///")
    "brace_style": "allman",      # "allman": { on its own line; "attached": if (...) {
    # True: the run sees all derived classes (files to translate and -I): classes without derived
    # class are final, methods are virtual only if they are deferred or overridden.
    # False: every method of a class hierarchy is virtual, no final
    "closed_hierarchy": True,
    # "auto": data members are private; protected if used by methods of derived classes, public if
    #         used anywhere else in the translated files (other classes, free functions, tests)
    # "fortran": as declared in Fortran (public unless the type has a private statement)
    "member_access": "auto",
    # local variables are declared at their first use (merged with the defining assignment,
    # allocate or constructor call where possible); loop variables in the for statement
    "declare_at_first_use": True,
    # loops whose variable is mostly used as array index run from 0: do i = 1, n -> for (int i = 0; i < n; ++i)
    "zero_based_loops": True,
    # optional intent(in) arrays: "empty": `const vec& w = vec()`, present(w) -> w.size() > 0;
    # "optional": `const std::optional<vec>& w = std::nullopt`
    "optional_arrays": "empty",
    "explicit_this": False,       # True: always write this->member
    # values of the error flag that mean success (lower case), e.g. ["0", "myproj_success"]
    "error_success": ["0"],
    "error_arg": "ierr",           # error flag argument, removed (C++ uses exceptions)
    "scalar": "scalar",
    "vec": "vec",
    "mat": "mat",
    "ivec": "Eigen::VectorXi",
    # "auto": rank-1 integer arrays are std::vector<int> unless they are used in array expressions,
    #         sections, as vector subscripts or passed to procedures that need an Eigen vector
    # "eigen": always ivec
    "int_arrays": "auto",
    # namespace of the slicing placeholders all / last: "Eigen::placeholders" (Eigen >= 3.5 / master,
    # where Eigen::all is deprecated) or "Eigen" (Eigen 3.4, where the placeholders:: ones are deprecated)
    "eigen_placeholders": "Eigen::placeholders",
    "imat": "Eigen::MatrixXi",
    "int": "int",
    # real/integer kind -> C++ type; unknown kinds fall back to scalar / int
    "kind_map": {"8": "double", "4": "float", "real64": "double", "real32": "float",
                 "int64": "std::int64_t", "int32": "std::int32_t"},
    # "auto": const vec& / vec&; Eigen::Ref only for out/inout dummies that receive a slice
    #         (block, row, column) in one of the translated calls
    # "plain": always const vec& / vec&
    # "ref": always Eigen::Ref (accepts slices everywhere, like Fortran)
    "array_args": "auto",
    "header_ext": ".hpp",
    "source_ext": ".cpp",
    # included by every generated header (must provide scalar, vec, mat)
    "extra_includes": [],
    # Fortran #include "x" -> C++ #include "y"; "" drops the include
    "include_map": {},
    # Fortran derived type (lower case) -> existing C++ class, e.g. "foo_type" = "wrappers::Foo"
    "type_map": {},
    # module -> header: `use module` becomes #include "header" ("" = no include). For modules that
    # exist in C++ already, and to rename the output files of translated modules
    "module_headers": {},
    # components that are accessor functions in the existing C++ class:
    # Fortran type -> component names (["*"]: all components): obj%models -> obj.models();
    # renamed accessor: "coupling_manager=couplingManager"
    "accessors": {},
    "ignore_modules": ["funit", "pfunit", "pfunit_mod", "iso_fortran_env", "iso_c_binding",
                       "ieee_arithmetic"],
    # macro name -> Fortran replacement ({0}, {1}: macro arguments), applied before parsing.
    # Replace the names by the real ones in the config file; a [macros] table there extends
    # these defaults. "{0}" / "allocate({0})": the macro disappears in C++.
    # "call NAME({0})": translated with the call_templates entry of NAME (lower case).
    "macros": {
        "PROJECT_CHK_IERR": "{0}",
        "PROJECT_ALLOCATE": "allocate({0})",
        "PROJECT_ASSERT": "call PROJECT_ASSERT({0})",
        "PROJECT_CHK_ARGUMENT": "call PROJECT_CHK_ARGUMENT({0})",
        "PROJECT_ASSERT_MSG": "call PROJECT_ASSERT_MSG({0}, {1})",
        "PROJECT_CHK_ARGUMENT_MSG": "call PROJECT_CHK_ARGUMENT_MSG({0}, {1})",
    },
    # [[regex, replacement], ...] applied to the Fortran source before anything else
    "pre_patches": [],
    # called procedure (lower case) -> C++ statement template
    # {0}, {1}: translated arguments; {0:str}: argument as text for use inside a string literal
    "call_templates": {
        "project_assert": 'if (!({0})) throw std::logic_error("assertion failed: {0:str}");',
        "project_chk_argument": 'if (!({0})) throw std::invalid_argument("invalid argument: {0:str}");',
        "project_assert_msg": "if (!({0})) throw std::logic_error({1});",
        "project_chk_argument_msg": "if (!({0})) throw std::invalid_argument({1});",
    },
    # called function / subroutine (lower case) -> C++ function, same arguments; or a template
    # with argument conversions: "c_fn({0}, {1:cstr}, {2:ptr}, {3:data})"
    #   {i:cstr} std::string -> const char*, {i:ptr} address (output argument), {i:data} Eigen data pointer
    "function_map": {},
    "test_extensions": [".pf", ".pfunit"],     # pFUnit test files; also: modules using test_modules
    "test_modules": ["funit", "pfunit", "pfunit_mod"],
    # "type%binding" (lower case) -> C++ template for calls of that type-bound procedure:
    # {self}: the object, {0}, {1}: arguments (conversions as in function_map), e.g.
    # "lu_type%solve" = "{1} = {self}.solve({0});"
    "method_templates": {},
    # wrapper types around a single component (lower case) -> that component: variables of the type
    # become the component itself, members are `mutable` (e.g. mutablerealarray1d_type = "v":
    # type(MutableRealArray1d_type) :: tmp; this%tmp%v(i) -> mutable vec tmp; tmp(i - 1))
    "unwrap_types": {},
    # wrapped modules (--wrap): C binding conventions
    "c_prefix": "project",                    # project_<Class>_<method>
    "c_class_names": {},                      # Fortran type -> class name in C names (shorter names)
    "c_real": "PROJECT_REAL",                 # C type macro of real
    "c_single_precision": "PROJECT_SINGLE_PRECISION",
    "wrap_string_in": ["project_str", "str_array_ptr_to_str"],    # module, C string -> Fortran string
    # sizes of array arguments that are not given by a size check: "type%binding" -> {"x": "this%n"}
    "wrap_sizes": {},
    # check array sizes in the C++ glue before the call (the C interface has no sizes)
    "wrap_size_checks": False,
    # abstract interface (lower case) -> C++ type of procedure arguments / components declared
    # `procedure(name)`; default: an alias `using name = std::function<...>` generated from the interface
    "procedure_map": {},
    # dummy arguments declared class(*) with one of these names are removed from all signatures and
    # calls (user data of callbacks: captured by the lambda in C++)
    "drop_args": [],
    # named constants / enumerators (lower case) -> C++ expression, e.g.
    # snapshotmode_read = "SnapshotMode::READ"
    "constant_map": {},
    "gtest_assert": "ASSERT",      # pFUnit asserts abort the test -> ASSERT_*; "EXPECT" changes the logic
    # the project provides EXPECT_NEAR / ASSERT_NEAR for Eigen types: used for array comparisons;
    # False: EXPECT_LE((expected - actual).cwiseAbs().maxCoeff(), tolerance)
    "gtest_eigen_near": True,
    "test_includes": [],           # additional includes of translated test files
    "keep_original": False,        # emit the Fortran statement as comment above each statement
}


def load(path=None, **overrides):
    cfg = copy.deepcopy(DEFAULTS)
    for p in ([path] if isinstance(path, str) else path or []):     # later files override earlier ones
        with open(p, "rb") as f:
            user = tomllib.load(f)
        for key, val in user.items():
            if key not in cfg:
                raise SystemExit(f"f2cpp: unknown config key '{key}' in {p}")
            if isinstance(cfg[key], dict):
                cfg[key].update(val)
            elif isinstance(cfg[key], list) and key in ("extra_includes", "test_includes", "ignore_modules",
                                                         "pre_patches", "namespace_map"):
                cfg[key] = cfg[key] + [v for v in val if v not in cfg[key]]
            else:
                cfg[key] = val
    # accessors: type -> {component: C++ accessor name or None (same name)}
    cfg["accessors"] = {k.lower(): {n.split("=")[0].strip().lower(): (n.split("=")[1].strip() if "=" in n else None)
                                    for n in v} for k, v in cfg["accessors"].items()}
    cfg["drop_args"] = [a.lower() for a in cfg["drop_args"]]
    cfg["unwrap_types"] = {k.lower(): v.lower() for k, v in cfg["unwrap_types"].items()}
    cfg["wrap_sizes"] = {k.lower(): {a.lower(): e for a, e in v.items()} for k, v in cfg["wrap_sizes"].items()}
    cfg["c_class_names"] = {k.lower(): v for k, v in cfg["c_class_names"].items()}
    for key in ("call_templates", "method_templates", "procedure_map", "constant_map", "function_map", "type_map", "module_headers", "kind_map"):    # Fortran names
        cfg[key] = {k.lower(): v for k, v in cfg[key].items()}
    cfg["ignore_modules"] = [m.lower() for m in cfg["ignore_modules"]]
    for key, val in overrides.items():
        if val is not None:
            cfg[key] = val
    return cfg
