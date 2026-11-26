"""Configuration: config file (what is translated / wrapped and how it maps to C++) and style file (how the
generated code looks, defaults in style.toml). Both are read into one flat dict with internal keys; README.md
documents the file formats. Keyword overrides of load() use the internal keys."""
import copy
import os
import re
import tomllib

STYLE_FILE = os.path.join(os.path.dirname(__file__), "style.toml")

# internal keys; the style keys are filled from style.toml
DEFAULTS = {
    "namespace": "project",
    "namespace_map": [],            # [[module-name-regex, "child::namespace"], ...]
    "constructor_bindings": ["configure"],
    "destructor_bindings": ["finalize"],
    "error_success": ["0"],
    "error_arg": "ierr",
    "drop_args": [],
    "extra_includes": [],
    "strict": False,
    "keep_original": False,         # command line only
    "pre_patches": [],
    "macros": {},                   # user macros; the <NS>_* defaults are added by normalise()
    "macro_prefix": None,
    "macro_include": None,
    "call_templates": {},
    "function_map": {},
    "procedure_map": {},
    "constant_map": {},
    "include_map": {},
    "name_headers": {},             # C++ name -> include, where the generated code uses the name
    "kind_map": {"8": "double", "4": "float", "real64": "double", "real32": "float",
                 "int64": "std::int64_t", "int32": "std::int32_t"},
    "test_extensions": [".pf", ".pfunit"],
    "test_modules": ["funit", "pfunit", "pfunit_mod"],
    "test_includes": [],
    "ignore_modules": ["funit", "pfunit", "pfunit_mod", "iso_fortran_env", "iso_c_binding", "ieee_arithmetic"],
    "module_headers": {},
    "exclude": [],                  # "module:name" (glob patterns)
    "type_map": {},
    "accessors": {},                # type -> {component | "*": (C++ name or None, get | set | ref)}
    "method_templates": {},
    "unwrap_types": {},
    "expand_args": {},
    "named_elements": {},
    "named_element_lists": {},
    "c_class_names": {},
    "c_procedure_names": {},
    "wrap_chk_argument": None,
    "wrap_chk_ierr": None,
    "wrap_fail": None,
    "wrap_string_in": None,
    "wrap_string_out": None,
    "wrap_sizes": {},
    "wrap_independent_bases": [],
    "wrap_constants": {},
    "wrap_owned": [],
    "wrap_translate": [],
    "wrap_handle_member": {},
    "wrap_listeners": [],
    "wrap_reverse": [],
    "wrap_reverse_cpp": [],         # interop = "cpp": only the C++ implementation used from Fortran
    "wrap_reverse_factories": {},
    "reverse_factory_instances": {},
}

# style file: section -> {key: internal key}
STYLE_KEYS = {
    "names": {k: k for k in ("type_suffix", "type_case", "header_ext", "source_ext", "handle_member", "c_prefix",
                             "c_real", "c_single_precision")},
    "layout": {k: k for k in ("include_guard", "doxygen", "brace_style", "c_decl_width")},
    "types": {k: k for k in ("scalar", "vec", "mat", "ivec", "imat", "int")},
    "conventions": {**{k: k for k in ("member_access", "optional_arrays", "array_args", "int_arrays",
                                      "eigen_placeholders", "gtest_assert", "gtest_eigen_near")},
                    "size_checks": "wrap_size_checks"},
    "members": {"member": "member_name", **{k: "member_" + k for k in ("getter_name", "setter_name", "getter",
                                                                       "getter_ref", "setter", "setter_ref",
                                                                       "reference")}},
    "errors": {"log": "log_stmt", "log_includes": "log_includes", "size_message": "size_message",
               "call_message": "call_message"},
}
# placeholders of the style templates
PLACEHOLDERS = {"member_name": {"name"}, "member_getter_name": {"name"}, "member_setter_name": {"name"},
                "log_stmt": {"class", "message", "what"},
                "size_message": {"where", "param"}, "call_message": {"cfunc", "where"},
                **{"member_" + k: {"type", "name", "member"}
                   for k in ("getter", "getter_ref", "setter", "setter_ref", "reference")}}
CHOICES = {"type_case": ("keep", "camel"), "include_guard": ("ifndef", "pragma"), "brace_style": ("allman", "attached"),
           "member_access": ("auto", "fortran"), "optional_arrays": ("empty", "optional"),
           "array_args": ("auto", "plain", "ref"), "int_arrays": ("auto", "eigen"), "gtest_assert": ("ASSERT", "EXPECT")}

# config file: [project] / [prepass] / [tests] / [map] / [wrap] key -> (internal key, merge: "append" | "update" | None)
SECTION_KEYS = {
    "project": {"namespace": ("namespace", None), "namespace_map": ("namespace_map", "append"),
                "includes": ("extra_includes", "append"), "constructor_bindings": ("constructor_bindings", None),
                "destructor_bindings": ("destructor_bindings", None), "error_arg": ("error_arg", None),
                "error_success": ("error_success", None), "drop_args": ("drop_args", None), "strict": ("strict", None)},
    "prepass": {"patches": ("pre_patches", "append")},
    "tests": {"extensions": ("test_extensions", None), "modules": ("test_modules", None),
              "includes": ("test_includes", "append")},
    "map": {"kinds": ("kind_map", "update"), "constants": ("constant_map", "update"),
            "includes": ("include_map", "update"), "functions": ("function_map", "update"),
            "calls": ("call_templates", "update"), "interfaces": ("procedure_map", "update"),
            "headers": ("name_headers", "update")},
    "wrap": {"string_in": ("wrap_string_in", None), "string_out": ("wrap_string_out", None)},
}
TYPE_KEYS = {"cpp", "independent", "interop", "factory", "instance", "owned", "constants", "accessors", "unwrap",
             "expand", "named_elements", "element_list", "handle_member", "c_name",
             "translate", "bindings"}
BINDING_KEYS = {"template", "translate", "sizes"}
PROCEDURE_KEYS = {"c_name", "translate", "sizes"}
MODULE_KEYS = {"header", "exclude"}
SECTIONS = set(SECTION_KEYS) | {"style", "macros", "modules", "types", "procedures"}
# keys of the old (flat) format
OLD_KEYS = (set(DEFAULTS) | {k for sec in STYLE_KEYS.values() for k in sec.values()} | {
    "closed_hierarchy", "declare_at_first_use", "zero_based_loops", "explicit_this", "wrap_handle",
    "wrap_direct_handles", "wrap_dllexport", "member_suffix", "log_error", "log_error_include", "member_accessors"}) - SECTIONS


def default_macros(prefix):
    """macros of the project (prefix: upper case) and the C++ statements of the calls they become"""
    p, low = prefix, prefix.lower()
    macros = {f"{p}_CHK_IERR": "{0}", f"{p}_ALLOCATE": "allocate({0})"}
    calls = {}
    for name, exc, text in (("ASSERT", "logic_error", "assertion failed"),
                            ("CHK_ARGUMENT", "invalid_argument", "invalid argument")):
        macros[f"{p}_{name}"] = f"call {p}_{name}({{0}})"
        macros[f"{p}_{name}_MSG"] = f"call {p}_{name}_MSG({{0}}, {{1}})"
        calls[f"{low}_{name.lower()}"] = f'if (!({{0}})) throw std::{exc}("{text}: {{0:str}}");'
        calls[f"{low}_{name.lower()}_msg"] = f"if (!({{0}})) throw std::{exc}({{1}});"
    return macros, calls


def parse_accessor(entry):
    """"component[=name][:mode]" -> (component (lower case) or "*", (C++ name or None, get | set | ref))"""
    m = re.fullmatch(r"\s*(\*|\w+)\s*(?:=\s*(\w+)\s*)?(?::\s*(\w+)\s*)?", entry)
    if not m or (m.group(3) or "get") not in ("get", "set", "ref"):
        raise SystemExit(f"f2cpp: accessor {entry!r}: expected component[=name][:get|set|ref]")
    return m.group(1).lower(), (m.group(2), m.group(3) or "get")


def subst(template, **values):
    """template with its {placeholders} replaced (no format escaping)"""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def _fail(path, msg):
    raise SystemExit(f"f2cpp: {path}: {msg}")


def load_style(cfg, path):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for sec, vals in data.items():
        if sec not in STYLE_KEYS or not isinstance(vals, dict):
            _fail(path, f"unknown style section [{sec}]")
        for k, v in vals.items():
            if k not in STYLE_KEYS[sec]:
                _fail(path, f"unknown style entry {sec}.{k}")
            key = STYLE_KEYS[sec][k]
            if key in CHOICES and v not in CHOICES[key]:
                _fail(path, f"{sec}.{k} = {v!r}: one of {', '.join(CHOICES[key])}")
            bad = set(re.findall(r"\{(\w+)\}", v)) - PLACEHOLDERS[key] if key in PLACEHOLDERS else set()
            if bad:
                _fail(path, f"{sec}.{k}: unknown placeholder(s) {', '.join(sorted(bad))} "
                            f"(known: {', '.join(sorted(PLACEHOLDERS[key]))})")
            cfg[key] = v


def _merge(cfg, key, val, how):
    if how == "append":
        cfg[key] = cfg[key] + [v for v in val if v not in cfg[key]]
    elif how == "update":
        cfg[key].update(val)
    else:
        cfg[key] = val


def _flag(cfg, key, name, on):
    if on and name not in cfg[key]:
        cfg[key].append(name)


def _check(path, where, ent, known):
    if not isinstance(ent, dict):
        _fail(path, f"{where}: a table expected")
    bad = set(ent) - known
    if bad:
        _fail(path, f"{where}: unknown key(s) {', '.join(sorted(bad))} (known: {', '.join(sorted(known))})")


def load_config(cfg, path, data):
    """one config file into cfg"""
    old = set(data) & OLD_KEYS
    if old:
        _fail(path, f"old config format (keys {', '.join(sorted(old))}): convert it with tools/convert_config.py")
    for sec, vals in data.items():
        if sec not in SECTIONS:
            _fail(path, f"unknown config section [{sec}]")
        if sec == "style":
            continue
        if not isinstance(vals, dict):
            _fail(path, f"[{sec}]: a table expected")
        if sec in SECTION_KEYS:
            _check(path, f"[{sec}]", vals, set(SECTION_KEYS[sec]))
            for k, v in vals.items():
                key, how = SECTION_KEYS[sec][k]
                _merge(cfg, key, v, how)
        elif sec == "macros":
            for k, v in vals.items():
                if k in ("include", "prefix"):
                    cfg["macro_" + k] = v
                    continue
                _check(path, f"macros.{k}", v, {"fortran", "cpp"})
                if "fortran" in v:
                    cfg["macros"][k] = v["fortran"]
                if "cpp" in v:          # C++ statement of the call the macro becomes (or of the macro itself)
                    m = re.match(r"\s*call\s+(\w+)", v.get("fortran", cfg["macros"].get(k, "")), re.I)
                    cfg["call_templates"][(m.group(1) if m else k).lower()] = v["cpp"]
        elif sec == "modules":
            for mod, ent in vals.items():
                _check(path, f"modules.{mod}", ent, MODULE_KEYS)
                if "header" in ent:
                    cfg["module_headers"][mod] = ent["header"]
                    _flag(cfg, "ignore_modules", mod, not ent["header"])
                cfg["exclude"] += [f"{mod}:{n}" for n in ent.get("exclude", []) if f"{mod}:{n}" not in cfg["exclude"]]
        elif sec == "procedures":
            for name, ent in vals.items():
                _check(path, f"procedures.{name}", ent, PROCEDURE_KEYS)
                if "c_name" in ent:
                    cfg["c_procedure_names"][name] = ent["c_name"]
                if "sizes" in ent:
                    cfg["wrap_sizes"][name] = ent["sizes"]
                _flag(cfg, "wrap_translate", name, ent.get("translate"))
        else:
            for name, ent in vals.items():
                load_type(cfg, path, name, ent)


TYPE_SIMPLE = {"cpp": "type_map", "constants": "wrap_constants", "accessors": "accessors", "unwrap": "unwrap_types",
               "expand": "expand_args",
               "element_list": "named_element_lists", "handle_member": "wrap_handle_member", "c_name": "c_class_names",
               "factory": "wrap_reverse_factories", "instance": "reverse_factory_instances"}


def load_type(cfg, path, t, ent):
    _check(path, f"types.{t}", ent, TYPE_KEYS)
    for k, key in TYPE_SIMPLE.items():
        if k in ent:
            cfg[key][t] = ent[k]
    ne = ent.get("named_elements")             # true: the type's own class; a string: that C++ class
    if ne is not None and ne is not False:
        cfg["named_elements"][t] = "" if ne is True else ne
    interop = ent.get("interop")
    if interop not in (None, "bidirectional", "cpp", "listener"):
        _fail(path, f"types.{t}.interop = {interop!r}: \"bidirectional\", \"cpp\" or \"listener\"")
    _flag(cfg, "wrap_reverse", t, interop in ("bidirectional", "cpp"))
    _flag(cfg, "wrap_reverse_cpp", t, interop == "cpp")
    _flag(cfg, "wrap_listeners", t, interop == "listener")
    _flag(cfg, "wrap_independent_bases", t, ent.get("independent"))
    _flag(cfg, "wrap_owned", t, ent.get("owned"))
    _flag(cfg, "wrap_translate", f"{t}%*", ent.get("translate"))
    for b, bent in ent.get("bindings", {}).items():
        _check(path, f"types.{t}.bindings.{b}", bent, BINDING_KEYS)
        if "template" in bent:
            cfg["method_templates"][f"{t}%{b}"] = bent["template"]
        if "sizes" in bent:
            cfg["wrap_sizes"][f"{t}%{b}"] = bent["sizes"]
        _flag(cfg, "wrap_translate", f"{t}%{b}", bent.get("translate"))


def load(path=None, **overrides):
    cfg = copy.deepcopy(DEFAULTS)
    load_style(cfg, STYLE_FILE)
    for p in ([path] if isinstance(path, str) else path or []):     # later files override earlier ones
        with open(p, "rb") as f:
            data = tomllib.load(f)
        if "style" in data:
            load_style(cfg, os.path.join(os.path.dirname(os.path.abspath(p)), data["style"]))
        load_config(cfg, p, data)
    # macros of the Fortran source: prefix from the namespace of the config files (not from -n)
    cfg["macro_prefix"] = cfg["macro_prefix"] or cfg["namespace"].split("::")[0].upper()
    for key, val in overrides.items():          # keyword overrides: normalised like the file values
        if val is not None:
            if key not in cfg:
                raise TypeError(f"f2cpp: unknown config key '{key}'")
            cfg[key] = val
    return normalise(cfg)


def normalise(cfg):
    ns = cfg["namespace"].split("::")[0]
    # project macros: defaults <PREFIX>_*, extended / replaced by the config
    prefix = cfg["macro_prefix"]
    macros, calls = default_macros(prefix)
    cfg["macros"] = {**macros, **cfg["macros"]}
    cfg["call_templates"] = {**calls, **cfg["call_templates"]}
    inc = cfg["macro_include"]
    if inc:                     # generated Fortran uses the macros; the include has no C++ counterpart
        cfg["wrap_chk_ierr"] = cfg["wrap_chk_ierr"] or [f"{prefix}_CHK_IERR", inc]
        cfg["wrap_chk_argument"] = cfg["wrap_chk_argument"] or [f"{prefix}_CHK_ARGUMENT", inc]
        cfg["wrap_fail"] = cfg["wrap_fail"] or [f"{prefix}_ASSERT(.false., {{ierr}})", inc]
        cfg["include_map"].setdefault(inc, "")
    # accessors: type -> {component | "*": (C++ name or None: style getter_name / setter_name, mode)}
    cfg["accessors"] = {k.lower(): dict(parse_accessor(e) for e in v) for k, v in cfg["accessors"].items()}
    cfg["drop_args"] = [a.lower() for a in cfg["drop_args"]]
    # "component" or ["component", "metadata", ...]: the first replaces the variable, the others are companion
    # variables <variable>_<component>
    lists = {k.lower(): [v] if isinstance(v, str) else list(v) for k, v in cfg["unwrap_types"].items()}
    cfg["unwrap_types"] = {k: v[0].lower() for k, v in lists.items()}
    cfg["unwrap_companions"] = {k: [c.lower() for c in v[1:]] for k, v in lists.items() if len(v) > 1}
    # "C++ type name = component"; without "= component" the component has the parameter's name
    cfg["expand_args"] = {k.lower(): [(m.group(1), m.group(2), (m.group(3) or m.group(2)).lower()) for m in
                                      (re.match(r"^\s*([^=]*[^=\s])\s+(\w+)\s*(?:=\s*(\w+))?\s*$", e) for e in v) if m]
                          for k, v in cfg["expand_args"].items()}
    cfg["wrap_sizes"] = {k.lower(): {a.lower(): e for a, e in v.items()} for k, v in cfg["wrap_sizes"].items()}
    for key in ("call_templates", "method_templates", "procedure_map", "constant_map", "function_map", "type_map",
                "module_headers", "kind_map", "c_class_names", "c_procedure_names", "named_elements",
                "named_element_lists", "wrap_handle_member", "reverse_factory_instances"):    # Fortran names
        cfg[key] = {k.lower(): v for k, v in cfg[key].items()}
    for key in ("ignore_modules", "wrap_owned", "wrap_listeners", "wrap_reverse", "wrap_reverse_cpp",
                "wrap_independent_bases"):
        cfg[key] = [t.lower() for t in cfg[key]]
    for key in ("exclude", "wrap_translate"):
        cfg[key] = [e.lower().replace(" ", "") for e in cfg[key]]
    cfg["wrap_constants"] = {k.lower(): [c.lower() for c in v] for k, v in cfg["wrap_constants"].items()}
    cfg["wrap_reverse_factories"] = {k.lower(): {a.lower(): p for a, p in v.items()}
                                     for k, v in cfg["wrap_reverse_factories"].items()}
    cfg["c_prefix"] = cfg["c_prefix"] or ns.lower()
    cfg["c_real"] = cfg["c_real"] or f"{ns.upper()}_REAL"
    cfg["c_single_precision"] = cfg["c_single_precision"] or f"{ns.upper()}_SINGLE_PRECISION"
    cfg["wrap_string_in"] = cfg["wrap_string_in"] or [f"{ns.lower()}_str", "str_array_ptr_to_str"]
    return cfg


def unused_entries(cfg, words, prog):
    """report categories of config entries that the files of a run do not use: config-unused:<entry> (no such name
    in the sources; glob patterns: no matching name), config-wrong-kind:<entry> (e.g. a type under [procedures])"""
    import fnmatch
    defaults = copy.deepcopy(DEFAULTS)
    macros, calls = default_macros(cfg["macro_prefix"])
    types = {n for m in prog.modules.values() for n in m.types}
    procs = {n for m in prog.modules.values() for n in list(m.procs) + list(m.ifaces) + list(m.generics)}

    def seen(name):
        name = name.lower()
        return any(fnmatch.fnmatchcase(w, name) for w in words) if any(c in name for c in "*?[") else name in words
    entries = []                                # (label, names that must occur)
    for key in ("type_map", "accessors", "wrap_constants", "unwrap_types", "expand_args", "named_elements",
                "named_element_lists", "wrap_handle_member", "c_class_names", "wrap_reverse_factories",
                "reverse_factory_instances"):
        entries += [(f"types.{t}", [t]) for t in cfg[key]]
    for key in ("wrap_reverse", "wrap_listeners", "wrap_independent_bases", "wrap_owned"):
        entries += [(f"types.{t}", [t]) for t in cfg[key]]
    entries += [(f"types.{t}.accessors.{c}", [t, c]) for t, comps in cfg["accessors"].items() for c in comps if c != "*"]
    for key in ("method_templates", "wrap_sizes", "wrap_translate"):
        for e in cfg[key]:
            t, _, b = e.partition("%")
            entries.append((f"types.{t}.bindings.{b}", [t] + ([b] if b != "*" else [])) if b else (f"procedures.{t}", [t]))
    entries += [(f"procedures.{p}", [p]) for p in cfg["c_procedure_names"]]
    entries += [(f"modules.{m}", [m]) for m in cfg["module_headers"]]
    entries += [(f"modules.{e.split(':')[0]}.exclude.{e.split(':', 1)[1]}", e.split(":", 1)) for e in cfg["exclude"]]
    entries += [(f"macros.{n}", [n]) for n, v in cfg["macros"].items() if macros.get(n) != v]
    for key, sec in (("function_map", "functions"), ("constant_map", "constants"), ("procedure_map", "interfaces")):
        entries += [(f"map.{sec}.{n}", [n]) for n in cfg[key]]
    entries += [(f"map.calls.{n}", [n]) for n, v in cfg["call_templates"].items() if calls.get(n) != v]
    entries += [(f"map.kinds.{n}", [n]) for n, v in cfg["kind_map"].items() if defaults["kind_map"].get(n) != v]
    res = []
    for label, names in dict(entries).items():
        if not all(seen(n) for n in names):
            res.append(f"config-unused:{label}")
        elif label.startswith("procedures.") and names[0] in types and names[0] not in procs:
            res.append(f"config-wrong-kind:{label} (a type)")
    return sorted(set(res))
