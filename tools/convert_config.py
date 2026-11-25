#!/usr/bin/env python3
"""One-time conversion of an f2cpp config of the old (flat) format into the new config file and a style file.

    python3 tools/convert_config.py old.toml new.toml [--style new_style.toml]

Entries that became defaults (derived from the namespace, the macro include, ...) are dropped; removed options and
ambiguous entries are listed on stderr. Comments of the old file are not kept."""
import argparse
import json
import os
import re
import sys
import tomllib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from f2cpp import config  # noqa: E402

REMOVED = {"closed_hierarchy": True, "declare_at_first_use": True, "zero_based_loops": True, "explicit_this": False,
           "wrap_handle": "struct", "wrap_direct_handles": [], "wrap_dllexport": "DLLEXPORT", "keep_original": False}
# old key -> (style section, style key)
STYLE = {internal: (sec, k) for sec, keys in config.STYLE_KEYS.items() for k, internal in keys.items()}
PROJECT = {"namespace": "namespace", "namespace_map": "namespace_map", "extra_includes": "includes",
           "constructor_bindings": "constructor_bindings", "destructor_bindings": "destructor_bindings",
           "error_arg": "error_arg", "error_success": "error_success", "drop_args": "drop_args", "strict": "strict"}
MAP = {"kind_map": "kinds", "constant_map": "constants", "include_map": "includes", "function_map": "functions",
       "call_templates": "calls", "procedure_map": "interfaces"}
TESTS = {"test_extensions": "extensions", "test_modules": "modules", "test_includes": "includes"}
TYPE_DICT = {"type_map": "cpp", "wrap_constants": "constants", "unwrap_types": "unwrap", "expand_args": "expand",
             "named_element_lists": "element_list", "wrap_handle_member": "handle_member", "c_class_names": "c_name",
             "wrap_reverse_factories": "factory", "reverse_factory_instances": "instance"}
TYPE_LIST = {"wrap_independent_bases": ("independent", True), "wrap_owned": ("owned", True),
             "wrap_reverse": ("interop", "bidirectional"), "wrap_listeners": ("interop", "listener")}


def warn(msg):
    print(f"convert_config: {msg}", file=sys.stderr)


class Names:
    """keeps the first spelling of each (case-insensitive) Fortran name"""
    def __init__(self):
        self.seen = {}

    def __call__(self, name):
        return self.seen.setdefault(name.lower(), name)


def convert(old):
    new, style = {}, {}
    name = Names()
    types, procs, mods = {}, {}, {}
    known = set(config.DEFAULTS) | set(STYLE) | set(REMOVED) | {"member_suffix", "log_error", "log_error_include",
                                                                "member_accessors"}
    for k in old:
        if k not in known:
            warn(f"unknown key {k!r} dropped")
    for k, default in REMOVED.items():
        if k in old and old[k] != default:
            warn(f"{k} = {old[k]!r} dropped: the option was removed (now always {default!r})")
    sect = lambda s: new.setdefault(s, {})                      # noqa: E731
    typ = lambda t: types.setdefault(name(t), {})               # noqa: E731
    ns = old.get("namespace", config.DEFAULTS["namespace"])
    prefix = ns.split("::")[0].upper()

    # --- style
    defaults = {}
    config.load_style(defaults, config.STYLE_FILE)
    for k, (sec, sk) in STYLE.items():
        if k in old and old[k] != defaults.get(k):
            style.setdefault(sec, {})[sk] = old[k]
    if old.get("member_suffix", "_") != "_":
        style.setdefault("members", {})["member"] = "{name}" + old["member_suffix"]
    if old.get("log_error"):
        style.setdefault("errors", {})["log"] = \
            old["log_error"] + '("{class}", ("{message}: " + std::string({what})).c_str());'
        style["errors"]["log_includes"] = ["<string>"] + ([old["log_error_include"]] if old.get("log_error_include") else [])

    # --- project, tests, prepass, wrap
    for k, nk in PROJECT.items():
        if k in old and old[k] != config.DEFAULTS[k]:
            sect("project")[nk] = old[k]
    for k, nk in TESTS.items():
        if k in old and old[k] != config.DEFAULTS[k]:
            sect("tests")[nk] = old[k]
    if old.get("pre_patches"):
        sect("prepass")["patches"] = old["pre_patches"]
    for k in ("wrap_string_in", "wrap_string_out"):
        if old.get(k):
            sect("wrap")[k[5:]] = old[k]

    # --- macros: defaults <PREFIX>_*, roles in the generated Fortran from the include
    dmacros, dcalls = config.default_macros(prefix)
    calls = {k.lower(): v for k, v in old.get("call_templates", {}).items()}
    macros = {}
    for m, repl in old.get("macros", {}).items():
        called = re.match(r"\s*call\s+(\w+)", repl, re.I)
        target = (called.group(1) if called else m).lower()
        ent = {}
        if dmacros.get(m) != repl:
            ent["fortran"] = repl
        if target in calls:
            if calls[target] != dcalls.get(target) or "fortran" in ent:
                ent["cpp"] = calls[target]
            del calls[target]
        if ent:
            macros[m] = ent
    for c in list(calls):
        if dcalls.get(c) == calls[c]:
            del calls[c]
    inc = None
    roles = {"wrap_chk_ierr": f"{prefix}_CHK_IERR", "wrap_chk_argument": f"{prefix}_CHK_ARGUMENT",
             "wrap_fail": f"{prefix}_ASSERT(.false., {{ierr}})"}
    for k, macro in roles.items():
        v = old.get(k)
        if v is None:
            continue
        stmt, vinc = (v, None) if isinstance(v, str) else (v[0], v[1] if len(v) > 1 else None)
        if stmt != macro:
            warn(f"{k} = {v!r}: not the default {macro!r} of the macros (dropped; rename the macro or set [macros] prefix)")
        if vinc:
            if inc not in (None, vinc):
                warn(f"{k}: include {vinc!r} differs from {inc!r} (dropped)")
            inc = inc or vinc
    missing = [k for k in roles if old.get(k) is None]
    if inc and missing:
        warn(f"[macros] include = {inc!r}: the generated Fortran now also uses the macros for {', '.join(missing)}")
    if inc or macros:
        sect("macros")
        if inc:
            new["macros"]["include"] = inc
        new["macros"].update(macros)

    # --- map
    for k, nk in MAP.items():
        vals = dict(old.get(k, {}))
        if k == "call_templates":
            vals = {c: t for c, t in old.get(k, {}).items() if c.lower() in calls}
        if k == "kind_map":
            vals = {a: b for a, b in vals.items() if config.DEFAULTS["kind_map"].get(a) != b}
        if k == "include_map" and inc and vals.get(inc) == "":
            del vals[inc]
        if vals:
            sect("map")[nk] = vals

    # --- modules
    for m in old.get("ignore_modules", []):
        if m.lower() not in config.DEFAULTS["ignore_modules"]:
            mods.setdefault(name(m), {})["header"] = ""
    for m, h in old.get("module_headers", {}).items():
        mods.setdefault(name(m), {})["header"] = h
    ex = old.get("exclude", {})
    items = [(m, n) for m, ns_ in ex.items() for n in ns_] if isinstance(ex, dict) else [e.split(":", 1) for e in ex]
    for m, n in items:
        mods.setdefault(name(m), {}).setdefault("exclude", []).append(n)

    # --- types / procedures
    for k, nk in TYPE_DICT.items():
        for t, v in old.get(k, {}).items():
            typ(t)[nk] = v
    # accessors: existing classes (old accessors: writes through the returned reference -> :ref) and translated
    # classes (old member_accessors: already component[:mode]) in one list
    for t, v in old.get("accessors", {}).items():
        typ(t).setdefault("accessors", []).extend(e if ":" in e else e + ":ref" for e in v)
    for t, v in old.get("member_accessors", {}).items():
        typ(t).setdefault("accessors", []).extend(v)
    for t, v in old.get("named_elements", {}).items():
        typ(t)["named_elements"] = True if v == "" else v
    for k, (nk, v) in TYPE_LIST.items():
        for t in old.get(k, []):
            typ(t)[nk] = v
    for key, v in old.get("method_templates", {}).items():
        t, b = key.split("%", 1)
        typ(t).setdefault("bindings", {}).setdefault(b, {})["template"] = v
    for key, v in old.get("wrap_sizes", {}).items():
        if "%" in key:
            t, b = key.split("%", 1)
            typ(t).setdefault("bindings", {}).setdefault(b, {})["sizes"] = v
        else:
            procs.setdefault(name(key), {})["sizes"] = v
    for p, c in old.get("c_procedure_names", {}).items():
        procs.setdefault(name(p), {})["c_name"] = c
    for key in old.get("wrap_translate", []):
        if "%" in key:
            t, b = key.split("%", 1)
            if b == "*":
                typ(t)["translate"] = True
            else:
                typ(t).setdefault("bindings", {}).setdefault(b, {})["translate"] = True
        else:
            if key.lower() in {t.lower() for t in types} or key.lower().endswith("_type"):
                warn(f"wrap_translate {key!r} is a procedure name (as before: no effect for a type); "
                     f"all methods of the type: [types.{key}] translate = true")
            procs.setdefault(name(key), {})["translate"] = True
    if mods:
        new["modules"] = mods
    if types:
        new["types"] = types
    if procs:
        new["procedures"] = procs
    return new, style


# ---------------------------------------------------------------------- TOML output
def key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f"'{v}'" if "'" not in v and "\n" not in v and ('"' in v or "\\" in v) else json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(value(x) for x in v) + "]"
    return "{" + ", ".join(f"{key(k)} = {value(x)}" for k, x in v.items()) + "}"


def write_table(out, path, tab, entities=False):
    """[path] with its scalar / list entries; dict entries inline if all are short, else as own tables
    (entities: each entry is a table of one module / type / procedure, nested tables (bindings) always own tables)"""
    dicts = {k: v for k, v in tab.items() if isinstance(v, dict)}
    nested = {k: v for k, v in dicts.items() if any(isinstance(x, dict) for x in v.values())}
    inline = {k: v for k, v in dicts.items() if k not in nested}
    long = {k: v for k, v in inline.items() if len(f"{key(k)} = {value(v)}") > 100}
    if path[-1] == "bindings" and all(len(v) == 1 for v in inline.values()):
        long = {}               # one key each (bindings.solve.template): dotted keys
        tab = {**{k: v for k, v in tab.items() if k not in inline},
               **{f"{key(k)}.{key(next(iter(v)))}": next(iter(v.values())) for k, v in inline.items()}}
        inline = {}
    nested.update(long if entities else inline if long else {})
    simple = {k: v for k, v in tab.items() if k not in nested}
    tab_keys = {key(k) for k in dicts}
    if simple:
        out.append(f"[{'.'.join(key(p) for p in path)}]")
        for k, v in simple.items():
            line = f"{k if '.' in k and k.split('.')[0] in tab_keys else key(k)} = {value(v)}"
            if isinstance(v, list) and len(line) > 120:         # long lists: several lines
                rows, cur = [], ""
                for x in v:
                    if cur and len(cur) + len(value(x)) > 110:
                        rows.append(cur.rstrip())
                        cur = ""
                    cur += value(x) + ", "
                rows.append(cur.rstrip(", "))
                line = line.split(" = ")[0] + " = [\n" + "".join(f"    {r}\n" for r in rows[:-1]) + f"    {rows[-1]},\n]"
            out.append(line)
        out.append("")
    for k, v in nested.items():
        write_table(out, path + [k], v)


def dumps(data, top=None, entities=("modules", "types", "procedures")):
    out = [f"{key(k)} = {value(v)}" for k, v in (top or {}).items()]
    out += [""] if out else []
    order = ["project", "macros", "prepass", "map", "tests", "wrap", "modules", "types", "procedures"]
    for sec in sorted(data, key=lambda s: order.index(s) if s in order else len(order)):
        tab = data[sec]
        write_table(out, [sec], tab, entities=sec in entities)
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--style", help="style file to write (default: <new>_style.toml next to new)")
    args = ap.parse_args(argv)
    with open(args.old, "rb") as f:
        old = tomllib.load(f)
    new, style = convert(old)
    top = {}
    if style:
        spath = args.style or os.path.splitext(args.new)[0] + "_style.toml"
        top["style"] = os.path.relpath(spath, os.path.dirname(os.path.abspath(args.new)))
        with open(spath, "w") as f:
            f.write("# f2cpp style (only the entries that differ from f2cpp/style.toml)\n\n" + dumps(style, entities=()))
    with open(args.new, "w") as f:
        f.write(f"# f2cpp config, converted from {os.path.basename(args.old)}\n\n" + dumps(new, top))
    config.load(args.new)       # check
    return 0


if __name__ == "__main__":
    sys.exit(main())
