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
    "error_arg": "ierr",           # error flag argument, removed (C++ uses exceptions)
    "scalar": "scalar",
    "vec": "vec",
    "mat": "mat",
    "ivec": "Eigen::VectorXi",
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
    # modules that are not translated because C++ code exists: module -> header ("" = no include)
    "module_headers": {},
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
    },
    # [[regex, replacement], ...] applied to the Fortran source before anything else
    "pre_patches": [],
    # called procedure (lower case) -> C++ statement template
    # {0}, {1}: translated arguments; {0:str}: argument as text for use inside a string literal
    "call_templates": {
        "project_assert": 'if (!({0})) throw std::logic_error("assertion failed: {0:str}");',
        "project_chk_argument": 'if (!({0})) throw std::invalid_argument("invalid argument: {0:str}");',
    },
    "gtest_assert": "ASSERT",      # pFUnit asserts abort the test -> ASSERT_*; or "EXPECT"
    "keep_original": False,        # emit the Fortran statement as comment above each statement
}


def load(path=None, **overrides):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for key, val in user.items():
            if key not in cfg:
                raise SystemExit(f"f2cpp: unknown config key '{key}' in {path}")
            if isinstance(cfg[key], dict):
                cfg[key].update(val)
            else:
                cfg[key] = val
    for key, val in overrides.items():
        if val is not None:
            cfg[key] = val
    return cfg
