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
    # parts of modules that are not emitted (stay in Fortran only), glob patterns; in the config file
    # [exclude] module = ["name", ...] (name: module procedure, type, module variable, "type%binding",
    # "type%component" of wrapped types / struct constants)
    "exclude": [],
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
    # names derived from the (outer) namespace NS if not set: ns_<Class>_<method>, NS_REAL,
    # NS_SINGLE_PRECISION, module ns_str
    "c_prefix": None,                         # <ns>_<Class>_<method>
    "c_class_names": {},
    "c_procedure_names": {},                  # module procedure -> C name (default project_<procedure>)                      # Fortran type -> class name in C names (shorter names)
    "c_real": None,                           # C type macro of real: <NS>_REAL
    "c_single_precision": None,               # <NS>_SINGLE_PRECISION
    "wrap_string_in": None,                   # [module, function] C string -> Fortran string: [<ns>_str, str_array_ptr_to_str]
    # sizes of array arguments that are not given by a size check: "type%binding" (module procedures:
    # "procedure") -> {"x": "this%n", "a(2)": "3"}
    "wrap_sizes": {},
    # check array sizes in the C++ glue before the call (the C interface has no sizes)
    "wrap_size_checks": False,
    # handle of wrapped objects: "struct": pointer to a bind(C) struct <Class>_c whose member p_impl points
    # to the Fortran object (class hierarchies: to <Root>Wrapper_type); "direct": pointer to the object /
    # wrapper itself. wrap_direct_handles: root types that use "direct" anyway
    "wrap_handle": "struct",
    # base types that exist independently in Fortran and C++ (e.g. a generic serialization base): not
    # part of the wrapped hierarchy, the next derived type is its root (handle, wrapper type)
    "wrap_independent_bases": [],
    "wrap_direct_handles": [],
    # "constant" components of wrapped root types: members of the C struct of the handle (copied by
    # project_<Class>_set_constants, called in wrapFromC), only a const getter in C++.
    # {"mysolver_type": ["n", "diag"]}: real / integer / logical scalars, rank 1 arrays (fixed size:
    # copied; allocatable / pointer: pointer + size into the Fortran object)
    "wrap_constants": {},
    # wrapped types whose C++ objects own their Fortran object (destructor frees it; created in the binding,
    # e.g. elements of array arguments: std::vector<X>, intent(out) / intent(in))
    "wrap_owned": [],
    # types whose C++ objects are neither copyable nor movable: local arrays of them become one named
    # variable per element (inputs(1) = T(...) -> Cls inputs_1(...); inputs -> {inputs_1, inputs_2});
    # type -> C++ class ("" = the type's class name)
    "named_elements": {},
    # type -> C++ type of the list of elements (whole array as argument): VariableVector{inputs_1, inputs_2}
    # (needed where the list is forwarded, e.g. std::make_unique<T>(...)); default: a plain braced list
    "named_element_lists": {},
    # C++ member holding the C handle per root type (derived classes use their root's), default handle_:
    # {"model_type": "model_c_"} (names of hand-written base classes)
    "wrap_handle_member": {},
    # abstract types used as listeners (only deferred bindings with intent(in) arguments): a C++ class with
    # pure virtual methods; its objects can be set as listener of wrapped objects
    "wrap_listeners": [],
    # macro for the DLL export attribute of the bind(C) routines ("" for none):
    # #ifdef DLLEXPORT / DLLEXPORT :: name / #endif
    "wrap_dllexport": "DLLEXPORT",
    # dummy arguments of a (not translated) type that are several parameters in C++:
    # type -> ["C++ type name = component", ...], e.g.
    # confignode_type = ["int tixiHandle = tixiHandle", "const std::string& xpath = xml_path"]
    "expand_args": {},
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
            if key == "exclude":            # [exclude] module = ["name", ...] or ["module:name", ...]
                items = [f"{m}:{n}" for m, names in val.items() for n in names] if isinstance(val, dict) else val
                cfg[key] = cfg[key] + [v for v in items if v not in cfg[key]]
            elif isinstance(cfg[key], dict):
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
    # "type" = "component" or ["component", "metadata", ...]: the first replaces the variable, the others
    # are companion variables <variable>_<component>
    lists = {k.lower(): [v] if isinstance(v, str) else list(v) for k, v in cfg["unwrap_types"].items()}
    cfg["unwrap_types"] = {k: v[0].lower() for k, v in lists.items()}
    cfg["unwrap_companions"] = {k: [c.lower() for c in v[1:]] for k, v in lists.items() if len(v) > 1}
    import re as _re
    # "C++ type name = component"; without "= component" the component has the parameter's name
    cfg["expand_args"] = {k.lower(): [(m.group(1), m.group(2), (m.group(3) or m.group(2)).lower()) for m in
                                      (_re.match(r"^\s*([^=]*[^=\s])\s+(\w+)\s*(?:=\s*(\w+))?\s*$", e) for e in v) if m]
                          for k, v in cfg["expand_args"].items()}
    cfg["wrap_sizes"] = {k.lower(): {a.lower(): e for a, e in v.items()} for k, v in cfg["wrap_sizes"].items()}
    cfg["c_class_names"] = {k.lower(): v for k, v in cfg["c_class_names"].items()}
    cfg["c_procedure_names"] = {k.lower(): v for k, v in cfg["c_procedure_names"].items()}
    for key in ("call_templates", "method_templates", "procedure_map", "constant_map", "function_map", "type_map", "module_headers", "kind_map"):    # Fortran names
        cfg[key] = {k.lower(): v for k, v in cfg[key].items()}
    cfg["ignore_modules"] = [m.lower() for m in cfg["ignore_modules"]]
    cfg["exclude"] = [e.lower().replace(" ", "") for e in cfg["exclude"]]
    cfg["wrap_direct_handles"] = [t.lower() for t in cfg["wrap_direct_handles"]]
    cfg["wrap_constants"] = {k.lower(): [c.lower() for c in v] for k, v in cfg["wrap_constants"].items()}
    cfg["wrap_owned"] = [t.lower() for t in cfg["wrap_owned"]]
    cfg["named_elements"] = {k.lower(): v for k, v in cfg["named_elements"].items()}
    cfg["named_element_lists"] = {k.lower(): v for k, v in cfg["named_element_lists"].items()}
    cfg["wrap_handle_member"] = {k.lower(): v for k, v in cfg["wrap_handle_member"].items()}
    cfg["wrap_listeners"] = [t.lower() for t in cfg["wrap_listeners"]]
    cfg["wrap_independent_bases"] = [t.lower() for t in cfg["wrap_independent_bases"]]
    for key, val in overrides.items():
        if val is not None:
            cfg[key] = val
    ns = cfg["namespace"].split("::")[0]
    cfg["c_prefix"] = cfg["c_prefix"] or ns.lower()
    cfg["c_real"] = cfg["c_real"] or f"{ns.upper()}_REAL"
    cfg["c_single_precision"] = cfg["c_single_precision"] or f"{ns.upper()}_SINGLE_PRECISION"
    cfg["wrap_string_in"] = cfg["wrap_string_in"] or [f"{ns.lower()}_str", "str_array_ptr_to_str"]
    return cfg
