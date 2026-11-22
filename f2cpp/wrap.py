"""Wrapped modules: the C++ interface of the translation, implemented by glue code that calls the
Fortran implementation through a generated bind(C) layer.

For a module M (file M.F90) three files are generated:
  M_c_binding.F90  bind(C) routines project_<Class>_<method> with !@c_declaration blocks (-> M.h)
  M.hpp            the classes with the same public interface as the translation
  M.cpp            glue: argument conversion, call, ierr -> exception

Step 1: non-polymorphic classes; methods with scalar, fixed-size array (rank 1 / 2) and input
string arguments, optional scalars, scalar function results; getters for public components.
"""
import os
import re

from fparser.two.utils import walk
from fparser.two import Fortran2003 as F

from .emit import ModuleEmitter, BANNER, squeeze, strip_blank
from .model import cls, lname, items_of
from .translate import TODO, Untranslatable
from .reverse import ReverseMixin


def lower_camel(name):
    return name[:1].lower() + name[1:]


class WrapEmitter(ReverseMixin, ModuleEmitter):
    """emits header, glue code and C binding of a wrapped module"""

    # ------------------------------------------------------------------ names
    def c_class_name(self, tname):
        return self.cfg["c_class_names"].get(tname) or self.prog.type_name(tname).split("::")[-1]

    def c_class(self, t):
        return self.c_class_name(t.name)

    def root(self, t):
        """(root type of the hierarchy or None if not parsed, its name)"""
        name, cur = t.name, t
        while cur is not None and cur.parent and cur.parent not in self.cfg["wrap_independent_bases"]:
            name = cur.parent
            cur = self.prog.find_type(cur.parent, cur.module)
        return cur, name

    def is_root(self, t):
        return self.root(t)[1] == t.name

    def overrides_wrapped(self, t):
        """binding names that t inherits from wrapped ancestors (their C++ methods dispatch in Fortran)"""
        names, cur = set(), t
        while cur is not None and not self.is_root(cur):
            cur = self.prog.find_type(cur.parent, cur.module)
            if cur is not None:
                names |= set(cur.bindings)
        return names

    def poly(self, t):
        """class hierarchy: the handle points to <Root>Wrapper_type with class(Root_type), pointer :: ptr"""
        root, _ = self.root(t)
        return not self.is_root(t) or (root is not None and (bool(root.children) or root.abstract))

    def wrapper_type(self, t):
        return f"{self.c_class_name(self.root(t)[1])}Wrapper_type"

    def c_type_name(self, t):
        """C typedef of the handle (one per class hierarchy): project_MySolver"""
        return f"{self.cfg['c_prefix']}_{self.c_class_name(self.root(t)[1])}"

    def struct_handle(self, t):
        return self.cfg["wrap_handle"] == "struct" and self.root(t)[1] not in self.cfg["wrap_direct_handles"]

    def struct_type(self, t):
        """bind(C) type of the C struct of the handle: <Root>_c"""
        return f"{self.c_class_name(self.root(t)[1])}_c"

    def handle_member(self, t):
        """C++ member of the C handle (wrap_handle_member of the root type, default handle_)"""
        return self.cfg["wrap_handle_member"].get(self.root(t)[1], "handle_")

    def use_only(self, lines):
        """use statements (with only-lists) of the wrapped module and of the other parsed modules whose public
        entities the binding code refers to (types of arguments of listeners, owned types, kinds ...)"""
        mod = self.mod
        words, local = set(), set()
        for line in lines:
            code = line.split("!")[0] if not line.lstrip().startswith(("!", "#")) else ""
            code = re.sub(r'"[^"]*"', "", code)
            code = re.sub(r"%\s*\w+", "", code)                   # components / bindings
            words |= {w.lower() for w in re.findall(r"[A-Za-z_]\w*", code)}
            m = re.match(r"\s*(?:\w+\s+)*(?:subroutine|function)\s+\w+\s*\(([^)]*)\)(?:\s*result\((\w+)\))?", code, re.I)
            if m:                               # dummies / results of the binding procedures
                local |= {a.strip().lower() for a in m.group(1).split(",") if a.strip()}
                local |= {m.group(2).lower()} if m.group(2) else set()
            if "::" in code and not re.match(r"\s*(use|procedure)\b", code, re.I):
                rhs = re.sub(r"\([^()]*\)", "", code.split("::", 1)[1])      # declared locals
                local |= {e.split("=")[0].strip().lower() for e in rhs.split(",") if e.strip()}
        order = self.prog.visible(mod) + [m for m in self.prog.modules.values() if m not in self.prog.visible(mod)]
        found = {}                              # module -> {name: spelling}
        for w in sorted(words - local):
            for m in order:
                if m is not mod and m.is_test:
                    continue
                known = {**{t.name: t.spelling or t.name for t in m.types.values()},
                         **{p.name: p.cname for p in m.procs.values()}, **m.generic_spelling,
                         **{v.name: v.cname for v in m.vars.values()}}
                if w in known and m.is_public(w, m.vars[w].access if w in m.vars else None):
                    found.setdefault(m.name, {})[w] = known[w]
                    break
        res = []
        for m in order:
            names = sorted(found.get(m.name, {}).values(), key=str.lower)
            if not names and m is not mod:
                continue
            line = f"  use {m.spelling or m.name}, only:" + (" " if names else "")
            out = []
            for k, n in enumerate(names):
                piece = n + (", " if k < len(names) - 1 else "")
                if len(line) + len(piece) > 100:
                    out.append(line.rstrip() + " &")
                    line = "    "
                line += piece
            res.append("\n".join(out + [line]))
        return "\n".join(res)

    def wrapped_types(self):
        """public, not excluded types of the module"""
        return [t for t in self.mod.types.values() if not self.excluded(t.name) and self.mod.is_public(t.name)]

    def owned(self, t):
        """C++ object owns the Fortran object (wrap_owned): destructor frees it, move-only"""
        return t.name in self.cfg["wrap_owned"] and self.is_root(t) and not self.poly(t) and self.struct_handle(t)

    def owned_type(self, v):
        """(type, None) of an array argument of a wrap_owned type, or (None, reason)"""
        ot = self.prog.find_type(v.tname, self.mod)
        if ot is None or ot.module.name not in getattr(self.prog, "wrapped", ()):
            return None, f"argument {v.cname} (wrap_owned type {v.tname} not in a wrapped module)"
        if not self.owned(ot):
            return None, f"argument {v.cname} (wrap_owned type {v.tname}: only root types without extensions)"
        return ot, None

    def wrap_from_c(self, t, name, ht, hpp, cpp):
        """<Class>_private::wrapFromC(c): C++ object of the C object c (protected constructor; derived
        classes inherit it). Pure C++ later: delete it with the constructor and handle_."""
        hm = self.handle_member(t) if t is not None else "handle_"
        cls = name.split("::")[-1]
        hpp.emit(0, f"namespace {cls}_private")
        hpp.emit(0, "{")
        hpp.emit(0, f"std::unique_ptr<{cls}> wrapFromC({ht}& c);  //!< C++ object of the C object c")
        if self.owned(t):
            hpp.emit(0, f"{ht}* toC(const {cls}& x);  //!< the C object of x (passed to Fortran)")
        hpp.emit(0, "}")
        hpp.emit(0, "")
        cpp.emit(0, f"std::unique_ptr<{cls}> {cls}_private::wrapFromC({ht}& c)")
        cpp.emit(0, "{")
        cpp.emit(1, f"return std::unique_ptr<{cls}>(new {cls}(c));  // new: protected constructor, make_unique has no access")
        cpp.emit(0, "}")
        cpp.emit(0, "")
        if self.owned(t):
            free = self.c_name(t, "free")
            cpp.emit(0, f"{ht}* {cls}_private::toC(const {cls}& x)")
            cpp.emit(0, "{")
            cpp.emit(1, f"return x.{hm};")
            cpp.emit(0, "}")
            cpp.emit(0, "")
            cpp.emit(0, f"{name}::~{cls}()")
            cpp.emit(0, "{")
            cpp.emit(1, f"{free}({hm});")
            cpp.emit(0, "}")
            cpp.emit(0, "")
            cpp.emit(0, f"{name}::{cls}({cls}&& o) noexcept : {hm}(o.{hm})")
            cpp.emit(0, "{")
            cpp.emit(1, f"o.{hm} = nullptr;")
            cpp.emit(0, "}")
            cpp.emit(0, "")
            cpp.emit(0, f"{name}& {name}::operator=({cls}&& o) noexcept")
            cpp.emit(0, "{")
            cpp.emit(1, "if (this != &o)")
            cpp.emit(1, "{")
            cpp.emit(2, f"{free}({hm});")
            cpp.emit(2, f"{hm} = o.{hm};")
            cpp.emit(2, f"o.{hm} = nullptr;")
            cpp.emit(1, "}")
            cpp.emit(1, "return *this;")
            cpp.emit(0, "}")
            cpp.emit(0, "")

    def object_access(self, t, cname, handle, has_err, body):
        """binding statements: the Fortran object `obj` from the handle, around the statements body"""
        decl, stmts, ptr = [], [""], handle
        if self.struct_handle(t):               # handle -> bind(C) struct -> p_impl
            decl.append(f"    type({self.struct_type(t)}), pointer :: this_c")
            stmts.append(f"    call c_f_pointer({handle}, this_c)")
            ptr = "this_c%p_impl"
        if not self.poly(t):
            return decl + [f"    type({t.spelling or t.name}), pointer :: obj"], \
                stmts + [f"    call c_f_pointer({ptr}, obj)"] + [f"    {x}" for x in body]
        root, rname = self.root(t)
        decl.append(f"    type({self.wrapper_type(t)}), pointer :: wrapper")
        stmts.append(f"    call c_f_pointer({ptr}, wrapper)")
        if t.name == rname:                     # methods of the root: dynamic dispatch in Fortran
            decl.append(f"    class({t.spelling or t.name}), pointer :: obj")
            return decl, stmts + ["    obj => wrapper%ptr"] + [f"    {x}" for x in body]
        fail = "ierr = -1" if has_err else f'error stop "{cname}: object of another type"'
        return decl, stmts + ["    select type (obj => wrapper%ptr)", f"    class is ({t.spelling or t.name})"] + \
            [f"      {x}" for x in body] + ["    class default", f"      {fail}", "    end select"]

    def c_name(self, t, what):
        return f"{self.cfg['c_prefix']}_{self.c_class(t)}_{what}"

    def dllexport(self, cname):
        m = self.cfg["wrap_dllexport"]
        return [f"#ifdef {m}", f"  {m} :: {cname}", "#endif"] if m else []

    # ------------------------------------------------------------------ analysis
    def array_sizes(self, p, t):
        """{(dummy, dim or None): Fortran expression node} from size checks and explicit shapes"""
        tr = self.tr(p)
        sizes = {}
        execs = [c for c in p.node.content if cls(c) == "Execution_Part"]
        for call in walk(execs, F.Call_Stmt) if execs else []:
            if lname(call.items[0]) not in tr.macro_calls or call.items[1] is None:
                continue
            top = items_of(call.items[1])[0]
            for cond in [top] + walk(top, F.Level_4_Expr):        # also conditions combined with .and.
              if cls(cond) != "Level_4_Expr" or str(cond.items[1]).lower() not in ("==", ".eq."):
                continue
              for a, b in ((cond.items[0], cond.items[2]), (cond.items[2], cond.items[0])):
                if cls(a) == "Intrinsic_Function_Reference" and lname(a.items[0]) == "size":
                    args = items_of(a.items[1])
                    if cls(args[0]) == "Name" and lname(args[0]) in p.args:
                        dim = int(str(args[1])) if len(args) > 1 and str(args[1]).isdigit() else None
                        sizes.setdefault((lname(args[0]), dim), b)
        for st in walk(execs, F.Allocate_Stmt) if execs else []:   # allocate(x(n)) of an allocatable dummy
            for al in items_of(st.items[1]):
                if cls(al) != "Allocation" or cls(al.items[0]) != "Name" or lname(al.items[0]) not in p.args:
                    continue
                for k, spec in enumerate(items_of(al.items[1])):
                    lo, hi = spec.items if cls(spec) == "Allocate_Shape_Spec" else (None, spec)
                    if hi is not None and (lo is None or str(lo).strip() == "1"):
                        sizes.setdefault((lname(al.items[0]), k + 1), hi)
        for a in p.args:
            v = p.vars.get(a)
            if v is not None and v.rank and all(hi is not None for _, hi in v.dims):
                for k, (lo, hi) in enumerate(v.dims):
                    if lo is None:
                        sizes.setdefault((a, k + 1), hi)
        return sizes

    def fortran_size(self, p, sizes, a, dim, depth=0):
        """size of dummy a (dimension dim, None: total of a rank-1 array) as Fortran code in the
        binding (this% -> obj%), None if unknown"""
        key = f"{p.method_of}%{(p.bind_name or p.name).lower()}" if p.method_of else p.name
        conf = self.cfg["wrap_sizes"].get(key, {})
        key = a if dim in (None, 1) and p.vars[a].rank == 1 else f"{a}({dim})"
        if key in conf:
            text = conf[key]
        else:
            keys = [(a, None), (a, 1)] if p.vars[a].rank == 1 else [(a, dim)]
            node = next((sizes[k] for k in keys if k in sizes), None)
            if node is None:
                return None
            text = str(node)
        text = re.sub(r"\s*%\s*", "%", text)
        if p.pass_arg:
            text = re.sub(rf"\b{re.escape(p.pass_arg)}%", "obj%", text, flags=re.I)

        def size_of(m):
            other = m.group(1).lower()
            if depth > 3 or other not in p.args:
                return m.group(0)
            r = self.fortran_size(p, sizes, other, int(m.group(2)) if m.group(2) else
                                  (None if p.vars[other].rank == 1 else 1), depth + 1)
            return f"({r})" if r else m.group(0)
        text = re.sub(r"size\(\s*(\w+)\s*(?:,\s*(\d))?\s*\)", size_of, text, flags=re.I)
        return None if re.search(r"size\(\s*(\w+)", text, flags=re.I) and \
            re.search(r"size\(\s*(\w+)", text, flags=re.I).group(1).lower() in p.args else text

    def wrappable(self, p, t):
        """-> (list of (dummy, kind, sizes), None) or (None, reason)"""
        err = self.tr(p).err
        sizes = self.array_sizes(p, t)
        res = []
        for a in p.args:
            if a in (p.pass_arg, err):
                continue
            v = p.vars[a]
            opt = v.has("optional")
            if v.base == "type" and v.tname in self.cfg["wrap_listeners"] and not v.rank:
                lt = self.prog.find_type(v.tname, self.mod)
                if lt is None or lt.module.name not in getattr(self.prog, "wrapped", ()):
                    return None, f"argument {v.cname} (listener type {v.tname} not in a wrapped module)"
                res.append((a, "listener", None))
                continue
            if v.base == "type" and v.tname in self.cfg["wrap_owned"] and v.rank == 1 and not opt:
                ot, why = self.owned_type(v)
                if why:
                    return None, why
                n = self.fortran_size(p, sizes, a, None)
                if v.intent == "out" and n is None:
                    return None, f"size of argument {v.cname} unknown (size check or wrap_sizes)"
                if v.intent not in ("in", "out"):
                    return None, f"argument {v.cname} (wrap_owned array: intent(in) / intent(out))"
                res.append((a, "owned-" + v.intent, [n or f"{a}_n1"]))
                continue
            if v.base == "type" and v.tname == "*" and not v.rank and v.has("allocatable") and \
                    v.intent in ("in", "out"):          # class(*): std::any in C++
                res.append((a, "any-" + v.intent, None))
                continue
            if v.base in ("procedure", "type", "complex", "unknown"):
                return None, f"argument {v.cname} ({v.base})"
            if v.base == "character":
                if v.rank or v.intent != "in":
                    return None, f"argument {v.cname} (character, only intent(in) scalars)"
                res.append((a, "optional-string" if opt else "string", None))
                continue
            if v.rank == 0:
                if opt and (v.intent != "in" or v.base == "logical"):
                    return None, f"argument {v.cname} (optional, only intent(in) real / integer)"
                res.append((a, "optional" if opt else "scalar", None))
                continue
            if v.rank > 2 or v.has("allocatable") or v.has("pointer"):
                return None, f"argument {v.cname} (array: rank 1 / 2, not allocatable / pointer)"
            dims = [self.fortran_size(p, sizes, a, d + 1 if v.rank == 2 else None) for d in range(v.rank)]
            if None in dims and (v.intent or "inout") != "out":
                dims = [f"{a}_n{d + 1}" for d in range(v.rank)]      # size passed from C++
            elif None in dims:
                return None, f"size of argument {v.cname} unknown (size check or wrap_sizes)"
            res.append((a, "optional-array" if opt else "array", dims))
        if p.kind == "function":
            r = p.vars.get(p.result)
            if r is None or r.rank or r.base not in ("real", "integer", "logical"):
                return None, "function result (only real / integer / logical scalars)"
        return res, None

    # ------------------------------------------------------------------ type mappings
    def c_type(self, v):
        return {"real": self.cfg["c_real"], "integer": "int", "logical": "bool"}[v.base]

    def f_type(self, v):
        return {"real": "real(C_REAL)", "integer": "integer(C_INT)", "logical": "logical(C_BOOL)"}[v.base]

    def getters(self, t):
        """public components with a getter: [(var, kind)] kind scalar | array | logical"""
        res = []
        consts = self.cfg["wrap_constants"].get(self.root(t)[1], [])
        for v in t.comps.values():
            if not comp_public(t, v) or v.name in consts or self.excluded(f"{t.name}%{v.name}"):
                continue
            if v.companion_of:                  # unwrap_types: obj%arr(:)%comp is not contiguous
                self.report.add("todo", f"wrap-component:{v.name}")
                res.append((v, f"{v.companion_of[0]}(:)%{v.companion_of[1]}; as constant: wrap_constants"))
                continue
            if v.base in ("real", "integer") and v.rank == 0 and not v.has("pointer"):
                res.append((v, "scalar"))
            elif v.base == "logical" and v.rank == 0:
                res.append((v, "logical"))
            elif v.base in ("real", "integer") and v.rank in (1, 2) and (v.has("allocatable") or v.has("pointer")):
                res.append((v, "array"))
            else:
                self.report.add("todo", f"wrap-component:{v.name}")
                res.append((v, self.component_kind(v)))
        return res

    def constants(self, t):
        """configured constant components of t: ([(var, kind, size)], [TODO texts]);
        kind scalar | logical | fixed | array"""
        cache = self.__dict__.setdefault("constants_cache", {})
        if t.name not in cache:
            cache[t.name] = self._constants(t)
        return cache[t.name]

    def plain_kind(self, v):
        """(kind, size, None) of a plain component (scalar | logical | fixed | array) or (None, None, reason)"""
        if v.base not in ("real", "integer", "logical") or v.rank > 1 or v.has("pointer") and not v.rank:
            return None, None, self.component_kind(v)
        if v.rank == 0:
            return ("logical" if v.base == "logical" else "scalar"), None, None
        if v.has("allocatable") or v.has("pointer"):
            return "array", None, None
        lo, up = v.dims[0]
        size = str(up).strip() if up is not None else ""
        if size.isdigit() and (lo is None or str(lo).strip() == "1"):
            return "fixed", size, None
        return None, None, self.component_kind(v) + " (literal size, lower bound 1)"

    def value_type(self, v):
        """(type, None) of a constant component whose type is a struct of plain components, or (None, reason)"""
        if v.base != "type" or v.rank or v.polymorphic or v.has("pointer") or v.has("allocatable"):
            return None, self.component_kind(v)
        td = self.prog.find_type(v.tname, self.mod)
        if td is None:
            return None, self.component_kind(v) + " (type not parsed: give its module with -I)"
        if td.module is not self.mod and td.module.name in getattr(self.prog, "wrapped", ()):
            return None, self.component_kind(v) + " (type of a wrapped module)"
        if td.parent or td.bindings:
            return None, self.component_kind(v) + " (type with parent / methods)"
        for c in td.comps.values():
            if self.excluded(f"{td.name}%{c.name}"):
                continue
            kind, _, why = self.plain_kind(c)
            if kind is None:
                return None, f"{self.component_kind(v)}: component {c.cname}: {why}"
        return td, None

    def value_types(self):
        """types of struct constants: a C struct and a C++ copy of the class instead of a wrapper class
        (types of other modules: their translation is the C++ class)"""
        res = {}
        for t in self.mod.types.values():
            for v, kind, td in self.constants(t)[0]:
                if kind == "struct":
                    res[td.name] = td
        return res

    def _constants(self, t):
        names = self.cfg["wrap_constants"].get(t.name, [])
        if not names:
            return [], []
        if not self.is_root(t) or not self.struct_handle(t):
            self.report.add("todo", f"wrap-constants:{t.name}")
            return [], [f"constants {', '.join(names)}: only for root types with a struct handle"]
        res, todo = [], []
        for n in names:
            v = t.comps.get(n)
            if v is None:
                kind, extra, why = None, None, "no such component"
            elif v.unwrapped:
                kind, extra, why = None, None, f"objects of the unwrapped type {v.unwrapped_type} " \
                    "(only its companions can be constants)"
            elif v.base == "type":
                extra, why = self.value_type(v)
                kind = "struct" if extra is not None else None
            else:
                kind, extra, why = self.plain_kind(v)
            if why:
                self.report.add("todo", f"wrap-constant:{n}")
                todo.append(f"constant {n} not wrapped: {why}")
                continue
            res.append((v, kind, extra))       # extra: size (fixed) / type (struct)
        return res, todo

    def constants_struct(self, consts):
        """(C struct member lines, Fortran bind(C) component lines)"""
        c, f = [], []
        for v, kind, extra in consts:
            ct, n = self.c_type(v) if kind != "struct" else "", v.cname
            if kind == "struct":                # its members are initialised
                c.append(f"{self.cfg['c_prefix']}_{self.c_class(extra)} {n};")
                f.append(f"type({self.c_class(extra)}_c) :: {n}")
            elif kind in ("scalar", "logical"):
                c.append(f"{ct} {n} = " + ("false;" if kind == "logical" else "0;"))
                f.append(f"{self.f_type(v)} :: {n} = " + (".false." if kind == "logical" else "0"))
            elif kind == "fixed":
                c.append(f"{ct} {n}[{extra}] = {{}};")
                f.append(f"{self.f_type(v)} :: {n}({extra}) = " + (".false." if v.base == "logical" else "0"))
            else:
                c += [f"{ct}* {n} = nullptr;", f"int {n}_size = 0;"]
                f += [f"type(C_PTR) :: {n} = C_NULL_PTR", f"integer(C_INT) :: {n}_size = 0"]
        return c, f

    def plain_consts(self, td):
        return [(c, self.plain_kind(c)[0], self.plain_kind(c)[1]) for c in td.comps.values()
                if not self.excluded(f"{td.name}%{c.name}")]

    def copy_constants(self, consts, to, frm, decls, frees):
        """Fortran statements: constants of the object (frm) into the C struct (to); logical arrays are
        copied into logical(C_BOOL) buffers (decls: their pointers, frees: statements of destroy)"""
        body = []
        for v, kind, extra in consts:
            n, m = f"{to}%{v.cname}", f"{frm}%{v.name}"
            whole = m
            if v.companion_of:                  # unwrap_types companion: component of the array elements
                whole = f"{frm}%{v.companion_of[0]}"
                m = f"{whole}(:)%{v.companion_of[1]}"
            if kind == "struct":
                body += self.copy_constants(self.plain_consts(extra), n, m, decls, frees)
            elif kind == "logical" or kind == "fixed" and v.base == "logical":
                body.append(f"{n} = logical({m}, C_BOOL)")
            elif kind == "array":
                test = "associated" if v.has("pointer") else "allocated"
                body += [f"{n} = C_NULL_PTR", f"{n}_size = 0", f"if ({test}({whole})) then"]
                if v.base == "logical" or v.companion_of:   # not C bool / not contiguous: a copy
                    buf = f"buf{len(decls) + 1}"
                    decls.append(f"{self.f_type(v)}, pointer :: {buf}(:)")
                    conv = f"logical({m}, C_BOOL)" if v.base == "logical" else m
                    body += [f"  allocate({buf}(size({whole})))", f"  {buf} = {conv}",
                             f"  if (size({buf}) > 0) {n} = c_loc({buf}(1))"]
                    frees += [f"if (c_associated({n})) then", f"  call c_f_pointer({n}, {buf}, [{n}_size])",
                              f"  deallocate({buf})", "end if"]
                else:
                    body.append(f"  {n} = c_loc({m})")
                body += [f"  {n}_size = size({whole})", "end if"]
            else:
                body.append(f"{n} = {m}")
        return body

    def setup_destroy(self, t, consts, fb):
        """Fortran (not bind(C)) project_<Class>_setup(obj) -> handle / project_<Class>_destroy(handle):
        C struct (and wrapper of a hierarchy) with the constants; the object must outlive the handle"""
        setup, destroy = self.c_name(t, "setup"), self.c_name(t, "destroy")
        ft, poly, struct = t.spelling or t.name, self.poly(t), self.struct_handle(t)
        sty, wty = self.struct_type(t), self.wrapper_type(t) if self.poly(t) else None
        own = ([f"type({sty}), pointer :: this_c"] if struct else []) + \
            ([f"type({wty}), pointer :: wrapper"] if poly else [])
        bufs, frees = [], []
        copy = self.copy_constants(consts, "this_c", "obj", bufs, frees) if struct else []
        fb += ["", f"  !> handle of obj for C / C++ (obj must outlive it; release with {destroy})",
               f"  function {setup}(obj) result(handle)",
               f"    {'class' if poly else 'type'}({ft}), target :: obj", "    type(C_PTR) :: handle"] + \
            [f"    {d}" for d in own + bufs] + [""]
        p = "c_loc(wrapper)" if poly else "c_loc(obj)"
        if poly:
            fb += ["    allocate(wrapper)", "    wrapper%ptr => obj"]
        if struct:
            fb += ["    allocate(this_c)", f"    this_c%p_impl = {p}"] + [f"    {x}" for x in copy] + \
                ["    handle = c_loc(this_c)"]
        else:
            fb.append(f"    handle = {p}")
        fb += [f"  end function {setup}", "",
               f"  !> releases the handle of {setup} (not the object)", f"  subroutine {destroy}(handle)",
               "    type(C_PTR), intent(inout) :: handle"] + [f"    {d}" for d in own + bufs] + \
            ["", "    if (.not. c_associated(handle)) return"]
        if struct:
            fb += ["    call c_f_pointer(handle, this_c)"] + [f"    {x}" for x in frees]
        if poly:
            fb += [f"    call c_f_pointer({'this_c%p_impl' if struct else 'handle'}, wrapper)", "    deallocate(wrapper)"]
        if struct:
            fb.append("    deallocate(this_c)")
        fb += ["    handle = C_NULL_PTR", f"  end subroutine {destroy}"]
        if self.owned(t):                       # C++ owns the object: frees object and struct
            free, h = self.c_name(t, "free"), lower_camel(self.c_class(t))
            fb += ["", "  !@c_declaration", f"  !  void {free}({self.c_type_name(t)}* {h});", "  !@end_c_declaration",
                   f"  !> frees the Fortran object of the handle (created by {setup} of an allocated object) and the handle",
                   f"  subroutine {free}({h}) bind(C, name=\"{free}\")"] + self.dllexport(free) + \
                [f"    type(C_PTR), value :: {h}", f"    type({sty}), pointer :: this_c", f"    type({ft}), pointer :: obj", "",
                 f"    if (.not. c_associated({h})) return", f"    call c_f_pointer({h}, this_c)",
                 "    call c_f_pointer(this_c%p_impl, obj)", "    deallocate(obj)", "    deallocate(this_c)",
                 f"  end subroutine {free}"]

    def constant_getter(self, t, v, kind, hdecl, cpp):
        hm = self.handle_member(t) if t is not None else "handle_"
        tr, n = self.tr(), v.cname
        if kind == "struct":
            for line in self.component_docs(t).get(v.name, []):
                hdecl.emit(1, f"{self.cfg['doxygen']} {line}".rstrip())
            hdecl.emit(1, f"const {self.prog.type_name(v.tname)}& {n}() const {{ return {n}_; }}")
            return
        if kind == "scalar":
            decl, body = f"{tr.cpp_type(v)} {n}() const", f"return {hm}->{n};"
        elif kind == "logical":
            decl, body = f"bool {n}() const", f"return {hm}->{n};"
        else:
            et = tr.eigen_type(v, 1)
            size = f"{hm}->{n}_size" if kind == "array" else next(e for w, k, e in self.consts if w is v)
            decl, body = f"Eigen::Map<const {et}> {n}() const", f"return Eigen::Map<const {et}>({hm}->{n}, {size});"
        for line in self.member_docs(t, v):
            hdecl.emit(1, f"{self.cfg['doxygen']} {line}".rstrip())
        hdecl.emit(1, decl + ";")
        cpp.emit(0, decl.replace(f" {n}()", f" {self.prog.type_name(t.name)}::{n}()", 1))
        cpp.emit(0, "{")
        cpp.emit(1, body)
        cpp.emit(0, "}")
        cpp.emit(0, "")

    def component_kind(self, v):
        """description of a component without getter (the reason)"""
        td = self.prog.find_type(v.tname, self.mod) if v.base == "type" else None
        tname = (td.spelling if td is not None and td.spelling else v.tname)
        kind = f"{'class' if v.polymorphic else 'type'}({tname})" if v.base == "type" else v.base
        alloc = [a for a in ("allocatable", "pointer") if v.has(a)]
        if v.rank:
            kind += f", rank {v.rank}" + (f", {alloc[0]}" if alloc else ", fixed size")
        elif "pointer" in alloc:
            kind += ", pointer"
        return kind

    # ------------------------------------------------------------------ method binding
    def proc_c_name(self, p):
        """C name of a module procedure: project_<procedure> (c_procedure_names: other names)"""
        return self.cfg["c_procedure_names"].get(p.name) or f"{self.cfg['c_prefix']}_{p.cname}"

    def method(self, t, bd, p, args, fb, hdecl, cpp):
        """emit the binding, the declaration and the glue of one method (t, bd None: module procedure)"""
        hm = self.handle_member(t) if t is not None else "handle_"
        tr = self.tr(p)
        err = tr.err
        cname = self.c_name(t, bd.name) if t is not None else self.proc_c_name(p)
        handle = lower_camel(self.c_class(t)) if t is not None else None
        has_err = err in p.args
        f_args, c_args, decls, pre, post, call, glue = [], [], [], [], [], [], []
        if t is not None:
            f_args.append(handle)
            const = "const " if p.pass_arg and p.vars[p.pass_arg].intent == "in" else ""
            c_args.append(f"{const}{self.c_type_name(t)}* {handle}")
            decls.append(f"type(C_PTR), value :: {handle}")
        for a, kind, dims in args:
            v = p.vars[a]
            f_args.append(a)
            if kind == "string":
                decls.append(f"type(C_PTR), value :: {a}")
                c_args.append(f"const char* {a}")
                call.append(f"{a}={self.cfg['wrap_string_in'][1]}({a})")
                glue.append(f"{v.cname}.c_str()")
            elif kind == "optional-string":     # NULL: absent (an unallocated actual is not present)
                decls.append(f"type(C_PTR), value :: {a}")
                decls.append(f"character(len=:), allocatable :: {a}_f")
                c_args.append(f"const char* {a}")
                # sourced allocation: no reallocation on assignment (-fno-realloc-lhs)
                pre.append(f"if (c_associated({a})) allocate({a}_f, source={self.cfg['wrap_string_in'][1]}({a}))")
                call.append(f"{a}={a}_f")
                glue.append(f"{v.cname} ? {v.cname}->c_str() : nullptr")
            elif kind in ("any-in", "any-out"):     # class(*): Fortran data held by C++ (see f2cpp_any)
                self.any_used = True
                cn, ah = v.cname, self.any_handle()
                decls += [f"type(C_PTR), {'value' if kind == 'any-in' else 'intent(out)'} :: {a}",
                          f"class(*), allocatable :: {a}_f"]
                call.append(f"{a}={a}_f")
                if kind == "any-in":            # moved out of the holder for the call and back
                    c_args.append(f"{ah}* {a}")
                    pre.append(f"call f2cpp_any_from_holder({a}, {a}_f)")
                    post.append(f"call f2cpp_any_back({a}, {a}_f)")
                    self.any_helpers |= {"f2cpp_any_from_holder", "f2cpp_any_back"}
                    glue.append(f"f2cpp_any::to_fortran({cn})")
                else:                           # a new holder (NULL: unallocated)
                    c_args.append(f"{ah}** {a}")
                    post.append(f"call f2cpp_any_to_holder({a}_f, {a})")
                    self.any_helpers.add("f2cpp_any_to_holder")
                    glue += [("pre", f"{ah}* {cn}_h = nullptr;"), f"&{cn}_h",
                             ("post", f"{cn} = f2cpp_any::from_fortran({cn}_h, &{self.any_release_name()});")]
            elif kind == "listener":           # the C++ listener's Fortran bridge object; user: this C++ object
                lt = self.prog.find_type(v.tname, self.mod)
                bridge = f"{self.c_class(lt)}_cpp"
                if lt.module is not self.mod:
                    self.extra_uses.add(f"  use {lt.module.spelling}_c_binding, only: {bridge}")
                f_args.append(f"{a}_user")
                c_args += [f"{self.c_type_name(lt)}* {a}", f"void* {a}_user"]
                decls += [f"type(C_PTR), value :: {a}", f"type(C_PTR), value :: {a}_user",
                          f"type({bridge}), pointer :: {a}_f"]
                pre += [f"nullify({a}_f)", f"if (c_associated({a})) then", f"  call c_f_pointer({a}, {a}_f)",
                        f"  {a}_f%user = {a}_user"] + ([f"  {a}_f%owner => obj"] if t is not None else []) + ["end if"]
                call.append(f"{a}={a}_f")
                lcls = self.prog.type_name(lt.name, self.mod)
                to_c = f"{lcls.rsplit('::', 1)[0] + '::' if '::' in lcls else ''}{lcls.split('::')[-1]}_private::toC"
                ref = {"optref": (f"{v.cname} ? ", f"{v.cname}->get()", " : nullptr")}.get(v.opt_kind, ("", v.cname, ""))
                glue.append(f"{ref[0]}{to_c}({ref[1]}){ref[2]}")
                if t is not None:
                    root = self.prog.type_name(self.root(t)[1], self.mod)
                    glue.append(f"const_cast<void*>(static_cast<const void*>(static_cast<const {root}*>(this)))")
                else:
                    glue.append("nullptr")
            elif kind in ("owned-out", "owned-in"):
                self.owned_array(p, t, v, a, kind, dims, f_args, c_args, decls, pre, post, call, glue)
            elif kind in ("scalar", "optional"):
                intent = v.intent or "inout"
                if kind == "optional":
                    decls.append(f"{self.f_type(v)}, intent(in), optional :: {a}")
                    c_args.append(f"const {self.c_type(v)}* {a}")
                    glue.append(f"{v.cname} ? &*{v.cname} : nullptr")
                elif intent == "in":
                    decls.append(f"{self.f_type(v)}, value :: {a}")
                    c_args.append(f"{self.c_type(v)} {a}")
                    glue.append(v.cname)
                else:
                    decls.append(f"{self.f_type(v)}, intent({intent}) :: {a}")
                    c_args.append(f"{self.c_type(v)}* {a}")
                    glue.append(f"&{v.cname}")
                if v.base == "logical" and kind == "scalar":         # C_BOOL <-> default logical
                    decls.append(f"logical :: {a}_f")
                    if intent != "out":
                        pre.append(f"{a}_f = {a}")
                    if intent != "in":
                        post.append(f"{a} = {a}_f")
                    call.append(f"{a}={a}_f")
                else:
                    call.append(f"{a}={a}")
            else:
                intent = v.intent or "inout"
                const = "const " if intent == "in" else ""
                c_args.append(f"{const}{self.c_type(v)}* {a}")
                # C++ object of the array and its presence (optional: NULL pointer = absent)
                ok = v.opt_kind if kind == "optional-array" else None
                obj = {"optref": f"{v.cname}->get()", "optional": f"(*{v.cname})"}.get(ok, v.cname)
                present = {"optref": v.cname, "optional": v.cname, "empty": f"{v.cname}.size() > 0"}.get(ok)
                glue.append(f"{present} ? {obj}.data() : nullptr" if present else f"{v.cname}.data()")
                if dims[0] == f"{a}_n1":         # size unknown in Fortran: passed from C++
                    have = [f"{obj}.size()"] if v.rank == 1 else [f"{obj}.rows()", f"{obj}.cols()"]
                    for d, h in zip(dims, have):
                        f_args.append(d)
                        c_args.append(f"int {d}")
                        decls.append(f"integer(C_INT), value :: {d}")
                        glue.append(f"{present} ? static_cast<int>({h}) : 0" if present else f"static_cast<int>({h})")
                if v.base == "logical":         # C bool -> Fortran logical: a copy (unallocated = absent)
                    shape, colons = ", ".join(dims), ", ".join([":"] * v.rank)
                    decls += [f"type(C_PTR), value :: {a}", f"logical(C_BOOL), pointer :: {a}_c({colons})",
                              f"logical, allocatable :: {a}_f({colons})"]
                    pre += [f"if (c_associated({a})) then", f"  call c_f_pointer({a}, {a}_c, [{shape}])",
                            f"  allocate({a}_f({shape}))"] + \
                        ([] if intent == "out" else [f"  {a}_f({colons}) = {a}_c"]) + ["end if"]   # no realloc-lhs
                    if intent != "in":
                        post.append(f"if (allocated({a}_f)) {a}_c = logical({a}_f, C_BOOL)")
                    call.append(f"{a}={a}_f")
                elif present:                   # pointer to the data or disassociated (= absent)
                    shape = ", ".join(dims)
                    decls.append(f"type(C_PTR), value :: {a}")
                    # no `=> null()` in the declaration: that would imply SAVE (stale association next call)
                    decls.append(f"{self.f_type(v)}, pointer :: {a}_f({', '.join([':'] * v.rank)})")
                    pre += [f"nullify({a}_f)", f"if (c_associated({a})) call c_f_pointer({a}, {a}_f, [{shape}])"]
                    call.append(f"{a}={a}_f")
                elif v.rank == 1:
                    decls.append(f"{self.f_type(v)}, intent({intent}) :: {a}(*)")
                    call.append(f"{a}={a}(1:{dims[0]})")
                else:
                    decls.append(f"{self.f_type(v)}, intent({intent}), target :: {a}(*)")
                    decls.append(f"{self.f_type(v)}, pointer, contiguous :: {a}_2d(:, :)")
                    pre.append(f"{a}_2d(1:{dims[0]}, 1:{dims[1]}) => {a}(1:({dims[0]}) * ({dims[1]}))")
                    call.append(f"{a}={a}_2d")
                # C++: the C interface has no sizes -> out arrays get the size of the Fortran check,
                # the others are checked before the call (the binding would read / write past the end)
                sizes = self.array_sizes(p, t)
                nodes = [sizes.get((a, None)) or sizes.get((a, 1))] if v.rank == 1 else \
                    [sizes.get((a, 1)), sizes.get((a, 2))]
                try:
                    codes = [tr.expr(n).code for n in nodes] if all(n is not None for n in nodes) else None
                except Untranslatable:
                    codes = None
                if intent == "out" and codes is not None and "Ref<" not in tr.param_type(v):
                    glue.append(("pre", (f"if ({present}) " if present else "") + f"{obj}.resize({', '.join(codes)});"))
                elif intent == "out" and "Ref<" not in tr.param_type(v):    # known in Fortran only
                    self.report.add("todo", "wrap-out-size")
                    glue.append(("pre", f"// {TODO}: size of {v.cname} ({', '.join(dims)} in Fortran) not "
                                        f"translatable: resize {v.cname} before the call"))
                elif not self.cfg["wrap_size_checks"]:
                    pass
                elif codes is None:
                    self.report.add("todo", "wrap-size-check")
                    glue.append(("pre", f"// {TODO}: size of {v.cname} is not checked"))
                else:
                    have = [f"{obj}.size()"] if v.rank == 1 else [f"{obj}.rows()", f"{obj}.cols()"]
                    cond = " || ".join(f"{h} != {c}" for h, c in zip(have, codes))
                    if present:
                        cond = f"{present} && ({cond})"
                    glue.append(("pre", f'if ({cond}) throw std::invalid_argument("{cname}: size of {v.cname}");'))
        if has_err:
            f_args.append("ierr")
            decls.append("integer(C_INT), intent(out) :: ierr")
            c_args.append("int* ierr")
            call.append("ierr=ierr")
        ret, _, params, const = self.signature(tr, p, False)
        decl_params = self.signature(tr, p, True)[2]
        r = p.vars.get(p.result) if p.kind == "function" else None
        c_ret = self.c_type(r) if r is not None else "void"
        # --- binding
        fb.append("")
        fb.append("  !@c_declaration")
        fb.append(f"  !  {c_ret} {cname}({', '.join(c_args)});")
        fb.append("  !@end_c_declaration")
        kw = "function" if r is not None else "subroutine"
        res = " result(res)" if r is not None else ""
        fb.append(f"  {kw} {cname}({', '.join(f_args)}){res} bind(C, name=\"{cname}\")")
        fb.extend(self.dllexport(cname))
        for d in decls:
            fb.append(f"    {d}")
        if r is not None:
            fb.append(f"    {self.f_type(r)} :: res")
        if t is not None:
            target = f"obj%{bd.generic or bd.name}" if bd.access == "private" else f"obj%{bd.name}"
        else:                       # private specific of a public generic interface: through the generic
            target = p.cname if self.mod.is_public(p.name) or not p.generic else p.generic
        body = list(pre) + [f"res = {target}({', '.join(call)})" if r is not None else
                            f"call {target}({', '.join(call)})"] + list(post)
        if t is not None:
            odecl, ostmts = self.object_access(t, cname, handle, has_err, body)
            fb.extend(odecl)
            fb.extend(ostmts)
        else:
            fb.append("")
            fb.extend(f"    {x}" for x in body)
        fb.append(f"  end {kw} {cname}")
        # --- glue
        ind, qual, name = (1, self.prog.type_name(t.name) + "::", bd.spelling if bd.deferred else p.bind_name) \
            if t is not None else (0, "", self.signature(tr, p, False)[1])
        self.doc(hdecl, p, ind)
        hdecl.emit(ind, f"{ret} {name}({decl_params}){const};")
        cpp.emit(0, f"{ret} {qual}{name}({params}){const}")
        cpp.emit(0, "{")
        for g in glue:
            if isinstance(g, tuple) and g[0] == "pre":
                cpp.emit(1, g[1])
        cargs = ", ".join(([hm] if t is not None else []) + [g for g in glue if not isinstance(g, tuple)] + (["&ierr"] if has_err else []))
        if has_err:
            cpp.emit(1, "int ierr = 0;")
        posts = [g[1] for g in glue if isinstance(g, tuple) and g[0] == "post"]
        if r is not None and (has_err or posts):
            cpp.emit(1, f"const {ret} res = {cname}({cargs});")
        elif r is not None:
            cpp.emit(1, f"return {cname}({cargs});")
        else:
            cpp.emit(1, f"{cname}({cargs});")
        for g in posts:                         # after the call (also on error: the objects are owned)
            cpp.emit(1, g)
        if r is not None and posts and not has_err:
            cpp.emit(1, "return res;")
        if has_err:
            cpp.emit(1, f'if (ierr != 0) throw std::runtime_error("ierr!=0 returned from {cname}");')
            if r is not None:
                cpp.emit(1, "return res;")
        cpp.emit(0, "}")
        cpp.emit(0, "")

    def owned_array(self, p, t, v, a, kind, dims, f_args, c_args, decls, pre, post, call, glue):
        """array of a wrap_owned type: std::vector<X> in C++, an array of handles in C"""
        ot = self.owned_type(v)[0]
        ht, ft, st = self.c_type_name(ot), ot.spelling or ot.name, self.struct_type(ot)
        cls = self.prog.type_name(ot.name, self.mod)
        priv = cls.rsplit("::", 1)
        priv = f"{priv[0]}::{priv[1]}_private" if len(priv) == 2 else f"{cls}_private"
        n, cn = dims[0], v.cname
        if ot.module is not self.mod:
            self.extra_uses.add(f"  use {ot.module.spelling}_c_binding, only: {st}, {self.c_name(ot, 'setup')}")
        decls += [f"type({ft}), allocatable :: {a}_f(:)", f"type({ft}), pointer :: {a}_e", f"integer :: {a}_i"]
        call.append(f"{a}={a}_f")
        if kind == "owned-out":                 # the elements become owned C++ objects
            c_args.append(f"{ht}** {a}")
            decls.insert(len(decls) - 3, f"type(C_PTR), intent(out) :: {a}(*)")
            post += [f"{a}(1:{n}) = C_NULL_PTR", f"if (allocated({a}_f)) then",
                     f"  do {a}_i = 1, min({n}, size({a}_f))", f"    allocate({a}_e, source={a}_f({a}_i))",
                     f"    {a}({a}_i) = {self.c_name(ot, 'setup')}({a}_e)", "  end do", "end if"]
            node = self.array_sizes(p, t).get((a, None)) or self.array_sizes(p, t).get((a, 1))
            try:
                size = self.tr(p).expr(node).code if node is not None else None
            except Untranslatable:
                size = None
            if size is None:
                self.report.add("todo", "wrap-out-size")
                glue.append(("pre", f"// {TODO}: size of {cn} ({n} in Fortran) not translatable"))
                size = "0"
            glue.append(("pre", f"std::vector<{ht}*> {cn}_h({size}, nullptr);"))
            glue.append(f"{cn}_h.data()")
            glue.append(("post", f"{cn}.clear();"))
            glue.append(("post", f"for ({ht}* h : {cn}_h) {cn}.push_back(h ? std::move(*{priv}::wrapFromC(*h)) : {cls}());"))
        else:                                   # copies of the objects (deep copy of their components)
            c_args.append(f"{ht}* const* {a}")
            decls.insert(len(decls) - 3, f"type(C_PTR), intent(in) :: {a}(*)")
            decls.append(f"type({st}), pointer :: {a}_c")
            pre += [f"allocate({a}_f({n}))", f"do {a}_i = 1, {n}", f"  if (.not. c_associated({a}({a}_i))) cycle",
                    f"  call c_f_pointer({a}({a}_i), {a}_c)", f"  call c_f_pointer({a}_c%p_impl, {a}_e)",
                    f"  {a}_f({a}_i) = {a}_e", "end do"]
            glue.append(("pre", f"std::vector<{ht}*> {cn}_h;"))
            glue.append(("pre", f"for (const auto& e : {cn}) {cn}_h.push_back({priv}::toC(e));"))
            glue.append(f"{cn}_h.data()")
            if n == f"{a}_n1":                  # size passed from C++
                f_args.append(n)
                c_args.append(f"int {n}")
                decls.append(f"integer(C_INT), value :: {n}")
                glue.append(f"static_cast<int>({cn}.size())")

    def tr(self, proc=None):
        tr = super().tr(proc)
        tr.via_getters = getattr(self, "via_getters", False)     # translated method of a wrapped class
        return tr

    def translated_here(self, t, bd, p):
        """listed in wrap_translate ("type%binding" / procedure, glob patterns): implemented in C++"""
        import fnmatch
        key = f"{t.name}%{bd.name}" if t is not None else p.name
        return any(fnmatch.fnmatch(key, pat) for pat in self.cfg["wrap_translate"])

    def doc(self, tr, p, ind, name=None):
        """the documentation of p; without a doxygen comment: one naming the Fortran procedure"""
        n = len(tr.out)
        super().doc(tr, p, ind)
        if not any(line.lstrip().startswith(self.cfg["doxygen"]) for line in tr.out[n:]):
            tr.emit(ind, f"{self.cfg['doxygen']} Fortran {name or p.cname} (undocumented)")
            tr.out.insert(n, tr.out.pop())

    def translated_method(self, t, bd, p, hdecl, cpp):
        """the same declaration as the wrapped method, the translation as implementation (no binding)"""
        tr = self.tr(p)
        ret, fname, decl_params, const = self.signature(tr, p, True)
        ind, name = (1, bd.spelling if bd.deferred else p.bind_name) if t is not None else (0, fname)
        self.doc(hdecl, p, ind)
        hdecl.emit(ind, f"{ret} {name}({decl_params}){const};")
        self.via_getters = True                 # components: getters into the Fortran object
        try:
            self.emit_proc(cpp, p, 0)
        finally:
            self.via_getters = False
        cpp.emit(0, "")
        key = f"{t.name}%{bd.name}" if t is not None else p.name
        self.report.add("translated", "wrapped-translated")
        self.report.add("info", f"wrapped-translated:{key}")      # Fortran callers still use the Fortran version

    def not_wrapped(self, t, p, reason, hdecl, cpp, bname=None):
        tr = self.tr(p)
        ret, fname, params, const = self.signature(tr, p, False)
        self.report.add("untranslated", "wrap:" + reason.split(" (")[0].split(" ")[0])
        ind, qual, name = (1, self.prog.type_name(t.name) + "::", bname or p.bind_name) if t is not None \
            else (0, "", fname)
        hdecl.emit(ind, f"// {TODO}: not wrapped (throws std::logic_error): {reason}")
        self.doc(hdecl, p, ind)
        hdecl.emit(ind, f"{ret} {name}({self.signature(tr, p, True)[2]}){const};")
        cpp.emit(0, f"// {TODO}: not wrapped: {reason}")
        cpp.emit(0, f"{ret} {qual}{name}({params}){const}")
        cpp.emit(0, "{")
        cpp.emit(1, f'throw std::logic_error("not wrapped: {qual}{name}");')
        cpp.emit(0, "}")
        cpp.emit(0, "")

    def getter(self, t, v, kind, fb, hdecl, cpp):
        hm = self.handle_member(t) if t is not None else "handle_"
        tr = self.tr()
        cname = self.c_name(t, v.name)
        handle = lower_camel(self.c_class(t))
        cls_name = self.prog.type_name(t.name)
        fb.append("")
        fb.append("  !@c_declaration")
        if kind == "scalar":
            ct = self.c_type(v)
            fb.append(f"  !  {ct}* {cname}(const {self.c_type_name(t)}* {handle});")
            fb.append("  !@end_c_declaration")
            odecl, ostmts = self.object_access(t, cname, handle, False, [f"res = c_loc(obj%{v.name})"])
            fb += [f"  function {cname}({handle}) result(res) bind(C, name=\"{cname}\")"] + self.dllexport(cname) + [
                   f"    type(C_PTR), value :: {handle}", "    type(C_PTR) :: res"] + odecl + ostmts + \
                [f"  end function {cname}"]
            ty = tr.cpp_type(v)
            decls = [(f"{ty}& {v.cname}()", f"return *{cname}({hm});"),
                     (f"const {ty}& {v.cname}() const", f"return *{cname}({hm});")]
        elif kind == "logical":
            fb.append(f"  !  bool {cname}(const {self.c_type_name(t)}* {handle});")
            fb.append("  !@end_c_declaration")
            odecl, ostmts = self.object_access(t, cname, handle, False, [f"res = obj%{v.name}"])
            fb += [f"  function {cname}({handle}) result(res) bind(C, name=\"{cname}\")"] + self.dllexport(cname) + [
                   f"    type(C_PTR), value :: {handle}", "    logical(C_BOOL) :: res"] + odecl + ostmts + \
                [f"  end function {cname}"]
            decls = [(f"bool {v.cname}() const", f"return {cname}({hm});")]  # Fortran logical: no reference
            setter = self.c_name(t, f"{v.name}_set")       # ... so a setter (overload of the getter name)
            fb += ["", "  !@c_declaration", f"  !  void {setter}({self.c_type_name(t)}* {handle}, bool value);",
                   "  !@end_c_declaration"]
            odecl, ostmts = self.object_access(t, setter, handle, False, [f"obj%{v.name} = value"])
            fb += [f"  subroutine {setter}({handle}, value) bind(C, name=\"{setter}\")"] + self.dllexport(setter) + [
                   f"    type(C_PTR), value :: {handle}", "    logical(C_BOOL), value :: value"] + odecl + ostmts + \
                [f"  end subroutine {setter}"]
            decls.append((f"void {v.cname}(bool value)", f"{setter}({hm}, value);"))
        else:
            ct = self.c_type(v)
            dims = ["n1"] if v.rank == 1 else ["n1", "n2"]
            fb.append(f"  !  void {cname}(const {self.c_type_name(t)}* {handle}, {ct}** data, "
                      + ", ".join(f"int* {d}" for d in dims) + ");")
            fb.append("  !@end_c_declaration")
            test = "associated" if v.has("pointer") else "allocated"
            inner = ["data = C_NULL_PTR"] + [f"{d} = 0" for d in dims] + \
                [f"if ({test}(obj%{v.name})) then", f"  data = c_loc(obj%{v.name})"] + \
                [f"  {d} = size(obj%{v.name}, {k + 1})" for k, d in enumerate(dims)] + ["end if"]
            odecl, ostmts = self.object_access(t, cname, handle, False, inner)
            fb += [f"  subroutine {cname}({handle}, data, {', '.join(dims)}) bind(C, name=\"{cname}\")"] + self.dllexport(cname) + [
                   f"    type(C_PTR), value :: {handle}", "    type(C_PTR), intent(out) :: data",
                   f"    integer(C_INT), intent(out) :: {', '.join(dims)}"] + odecl + ostmts + \
                [f"  end subroutine {cname}"]
            et = tr.eigen_type(v, v.rank)
            body = (f"{ct}* data = nullptr; " + " ".join(f"int {d} = 0;" for d in dims) +
                    f" {cname}({hm}, &data, " + ", ".join(f"&{d}" for d in dims) +
                    "); return Eigen::Map<{}>(data, " + ", ".join(dims) + ");")
            decls = [(f"Eigen::Map<{et}> {v.cname}()", body.replace("{}", et)),
                     (f"Eigen::Map<const {et}> {v.cname}() const", body.replace("{}", f"const {et}"))]
        doc, dox = self.component_docs(t).get(v.name, []) or [f"component {v.cname}"], self.cfg["doxygen"]
        for k, (decl, body) in enumerate(decls):
            lines = list(doc)
            if lines and k == 1:
                lines[-1] += " (setter)" if kind == "logical" else " (const version)"
            for line in lines:
                hdecl.emit(1, f"{dox} {line}".rstrip())
            hdecl.emit(1, decl + ";")
            cpp.emit(0, decl.replace(f" {v.cname}(", f" {cls_name}::{v.cname}(", 1))
            cpp.emit(0, "{")
            for stmt in body.split("; "):
                cpp.emit(1, stmt.rstrip(";") + ";")
            cpp.emit(0, "}")
            cpp.emit(0, "")

    # ------------------------------------------------------------------ file headers
    def header_comments(self):
        """comment nodes of the file header (first comment block before the module statement)"""
        from .prepass import CPP_MARK, BLANK_MARK
        parent = getattr(self.mod.node, "parent", None)
        blocks = [[]]
        for c in (parent.content if parent is not None else []):
            if c is self.mod.node:
                break
            if cls(c) != "Comment":
                continue
            if str(c).startswith((CPP_MARK, BLANK_MARK)):
                blocks.append([])
            else:
                blocks[-1].append(c)
        return next((b for b in blocks if b), [])

    def brief(self, lines, prefix):
        return [re.sub(r"(@brief:?\s*)", lambda m: m.group(1) + prefix, line, count=1) for line in lines]

    def cpp_file_header(self, ext, prefix=None, suffix=""):
        from .emit import plain_comment
        top = self.tr()
        top.block(self.header_comments(), 0)
        if not any("@file" in line for line in top.out):
            top.out = [plain_comment(line) for line in top.out]
        lines = self.file_header(top, ext, suffix)
        return self.brief(lines, prefix) if prefix else lines

    def fortran_file_header(self, name, prefix):
        lines = []
        for c in self.header_comments():
            line = re.sub(re.escape(os.path.basename(self.src)), name, str(c), flags=re.I)
            line = re.sub(r"(@file:?\s*)\S+\.(f90|f|pfunit|pf)\b", lambda m: m.group(1) + name, line, flags=re.I)
            lines.append(line)
        return self.brief(lines, prefix)

    # ------------------------------------------------------------------ module
    def generate(self):
        """-> (header, glue source, binding source)"""
        mod, cfg, prog = self.mod, self.cfg, self.prog
        self.report.start(self.src)
        stem = prog.file_stem(mod.name)
        hpp, cpp = self.tr(), self.tr()
        fb = []                                     # binding routines
        typedefs, needs_str, wrappers, uses, structs = [], False, [], set(), []
        self.extra_uses = set()                     # binding modules of other wrapped modules (owned types)
        self.any_used, self.any_helpers = False, set()  # class(*) arguments: holders, f2cpp_any, used helpers
        self.bridges, self.c_includes = [], set()   # Fortran types of listeners / reverse types; C headers
        self.cw = {"spec": [], "procs": [], "uses": set(), "binding_names": []}    # c_wrapper module
        self.wrap_routines, self.public_extra = set(), []       # wrap_<method>; Fortran entries made public
        self.needs_cstdio = False
        self.cwc = {"hpp": self.tr(), "cpp": self.tr()}     # C++ CWrapper<Thing> (Fortran implementations)
        stem_h = os.path.basename(stem) + ".h"
        values = self.value_types()
        vstructs = []
        for td in values.values():              # struct constants: C struct, bind(C) type, C++ copy of the class
            cn = f"{cfg['c_prefix']}_{self.c_class(td)}"
            typedefs += [f"!typedef struct {cn}", "!{"] + \
                [f"!  {line}" for line in self.constants_struct(self.plain_consts(td))[0]] + [f"!}} {cn};", "!"]
            vstructs += [f"  !> C struct {cn}: constant copy of {td.spelling or td.name}",
                         f"  type, bind(C), public :: {self.c_class(td)}_c"] + \
                [f"    {line}" for line in self.constants_struct(self.plain_consts(td))[1]] + \
                [f"  end type {self.c_class(td)}_c", ""]
            if td.module is self.mod:
                self.emit_type(hpp, td, 0)
                hpp.emit(0, "")
            self.report.add("translated", "wrapped-constant-type")
        for t in self.wrapped_types():
            if t.name in cfg["unwrap_types"] or t.name in values:
                continue
            if t.name in cfg["wrap_listeners"]:
                self.listener(t, hpp, cpp, fb, typedefs)
                continue
            if t.name in self.reverse_types():
                self.reverse_type(t, hpp, cpp, fb, typedefs)
                continue
            name = self.prog.type_name(t.name)
            ht = self.c_type_name(t)
            root, rname = self.root(t)
            hm = self.handle_member(t)
            if self.is_root(t):
                if self.struct_handle(t):       # struct of the handle: bind(C) type <Class>_c
                    typedefs += [f"!typedef struct {ht}", "!{", "!  void* p_impl = nullptr;"] + \
                        [f"!  {line}" for line in self.constants_struct(self.constants(t)[0])[0]] + [f"!}} {ht};", "!"]
                    structs.append(t)
                else:
                    typedefs.append(f"!typedef struct {ht} {ht};")
                if self.poly(t):
                    wrappers.append(t)
            elif root is not None and root.module is not mod:       # hierarchy from another module
                # C header of the base's binding: named after its module (also if its C++ header is mapped)
                rstem = root.module.spelling if root.module.name in cfg["module_headers"] else \
                    os.path.basename(prog.file_stem(root.module.name))
                if f'!#include "{rstem}.h"' not in typedefs:
                    typedefs.insert(0, f'!#include "{rstem}.h"')
                imported = [self.wrapper_type(t)] + ([self.struct_type(t)] if self.struct_handle(t) else [])
                uses.add(f"  use {root.module.spelling}_c_binding, only: {', '.join(imported)}")
            elif root is None:
                hpp.emit(0, f"// {TODO}: base type {rname} is not parsed (give its module with -I)")
                self.report.add("todo", "wrap:unknown-base")
            final = " final" if not t.children and not t.abstract else ""
            base = f" : public {prog.type_name(t.parent, mod)}" if t.parent else ""
            self.consts, self.const_todo = self.constants(t)
            if self.is_root(t):
                self.setup_destroy(t, self.consts, fb)
            self.wrap_from_c(t, name, ht, hpp, cpp)
            hpp.emit(0, f"class {name}{final}{base}")
            hpp.emit(0, "{")
            hpp.emit(0, "public:")
            hpp.emit(1, f"{name}() = default;  //!< empty object")
            if not self.is_root(t):             # no added members: the parent's constructors
                parent = prog.type_name(t.parent, mod)
                hpp.emit(1, f"using {parent}::{parent.split('::')[-1]};")
            hpp.emit(1, f"{name}(const {name}&) = delete;  //!< no copies (one C++ object per Fortran object)")
            hpp.emit(1, f"{name}& operator=(const {name}&) = delete;  //!< no copies")
            if self.owned(t):                  # owns the Fortran object: the moved-from object releases nothing
                hpp.emit(1, f"~{name}();  //!< frees the Fortran object")
                hpp.emit(1, f"{name}({name}&& o) noexcept;  //!< takes over the Fortran object of o")
                hpp.emit(1, f"{name}& operator=({name}&& o) noexcept;  //!< frees its own, takes over the one of o")
            else:
                hpp.emit(1, f"{name}({name}&&) = default;  //!< takes over the handle")
                hpp.emit(1, f"{name}& operator=({name}&&) = default;  //!< takes over the handle")
            hpp.emit(0, "")
            inherited = self.overrides_wrapped(t)
            for bd in [b for b in t.bindings.values() if not self.excluded(f"{t.name}%{b.name}")]:
                p = prog.binding_proc(t, bd)
                if bd.name in inherited:
                    continue            # the method of the base class dispatches in Fortran
                if p is None or bd.nopass:
                    why = "nopass" if p is not None else "procedure not found (module not parsed?)"
                    hpp.emit(1, f"// {TODO}: binding {bd.spelling} not wrapped: {why}")
                    self.report.add("untranslated", "wrap:" + ("nopass" if p is not None else "binding-procedure"))
                    continue
                if p.is_ctor or p.is_dtor:
                    hpp.emit(1, f"// {bd.name}: object created / destroyed in Fortran")
                    continue
                if bd.access == "private" and not bd.generic:
                    continue
                if self.translated_here(t, bd, p):
                    self.translated_method(t, bd, p, hpp, cpp)
                    continue
                args, reason = self.wrappable(p, t)
                if reason:
                    self.not_wrapped(t, p, reason, hpp, cpp, bd.spelling if bd.deferred else None)
                    continue
                needs_str |= any(kind in ("string", "optional-string") for _, kind, _ in args)
                self.method(t, bd, p, args, fb, hpp, cpp)
                self.report.add("translated", "wrapped-method")
            getters = self.getters(t)
            if getters or self.consts or self.const_todo:
                hpp.emit(0, "")
            for v, kind, _ in self.consts:
                self.constant_getter(t, v, kind, hpp, cpp)
                self.report.add("translated", "wrapped-constant")
            for why in self.const_todo:
                hpp.emit(1, f"// {TODO}: {why}")
            for v, kind in getters:
                if kind in ("scalar", "logical", "array"):
                    self.getter(t, v, kind, fb, hpp, cpp)
                    self.report.add("translated", "wrapped-getter")
                else:
                    hpp.emit(1, f"// {TODO}: no getter for component {v.cname} ({kind}; getters: real / integer / "
                                "logical scalars, real / integer allocatable / pointer arrays of rank 1 / 2)")
            hpp.emit(0, "")
            hpp.emit(0, "protected:")
            copies = [(v, td) for v, kind, td in self.consts if kind == "struct"]
            if self.is_root(t) and copies:      # C++ copies of struct constants: filled in the constructor
                hpp.emit(1, f"explicit {name}({ht}& c);  //!< the object created in Fortran")
                cpp.emit(0, f"{name}::{name.split('::')[-1]}({ht}& c) : {hm}(&c)")
                cpp.emit(0, "{")
                tr = self.tr()
                for v, td in copies:
                    for w, kind, size in self.plain_consts(td):
                        dst, src = f"{v.cname}_.{w.cname}", f"c.{v.cname}.{w.cname}"
                        if kind == "array":             # pointer + size into the Fortran object
                            size = f"{src}_size"
                        if kind in ("fixed", "array") and tr.storage(w) == "eigen":
                            cpp.emit(1, f"{dst} = Eigen::Map<const {tr.eigen_type(w, 1)}>({src}, {size});")
                        elif kind in ("fixed", "array"):
                            cpp.emit(1, f"{dst}.assign({src}, {src} + {size});")
                        else:
                            cpp.emit(1, f"{dst} = {src};")
                cpp.emit(0, "}")
                cpp.emit(0, "")
            elif self.is_root(t):
                hpp.emit(1, f"explicit {name}({ht}& c) : {hm}(&c) {{}}  //!< the object created in Fortran")
            cls = name.split("::")[-1]
            hpp.emit(1, f"friend std::unique_ptr<{cls}> {cls}_private::wrapFromC({ht}& c);")
            if self.owned(t):
                hpp.emit(1, f"friend {ht}* {cls}_private::toC(const {cls}& x);")
            if self.is_root(t):
                hpp.emit(1, f"{ht}* {hm} = nullptr;  //!< the Fortran object (C binding: {stem_h})")
                for v, td in copies:
                    hpp.emit(1, f"{prog.type_name(td.name)} {v.cname}_;  //!< C++ copy of the constant {v.cname}")
            hpp.emit(0, "};")
            hpp.emit(0, "")
        for m in sorted(self.c_includes):              # C headers of other modules (reverse types)
            rstem = prog.modules[m].spelling if m in cfg["module_headers"] else os.path.basename(prog.file_stem(m))
            if f'!#include "{rstem}.h"' not in typedefs:
                typedefs.insert(0, f'!#include "{rstem}.h"')
        from .prepass import CPP_MARK
        for p in mod.procs.values():
            for c in p.doc:                             # #ifdef ... around procedures: in all three files
                if str(c).startswith(CPP_MARK):
                    hpp.comment(c, 0)                   # include_map applies in C++
                    cpp.comment(c, 0)
                    fb.append(str(c)[len(CPP_MARK):])   # the Fortran binding keeps it
            if self.excluded_proc(p):
                continue
            if p.method_of or not (mod.is_public(p.name) or (p.generic and mod.is_public(p.generic.lower()))):
                continue
            if self.translated_here(None, None, p):
                self.translated_method(None, None, p, hpp, cpp)
                continue
            args, reason = self.wrappable(p, None)
            if reason:
                self.not_wrapped(None, p, reason, hpp, cpp)
                continue
            needs_str |= any(kind in ("string", "optional-string") for _, kind, _ in args)
            self.method(None, None, p, args, fb, hpp, cpp)
            self.report.add("translated", "wrapped-procedure")

        for v in mod.vars.values():                     # public module variables / constants: not wrapped
            if mod.is_public(v.name, v.access) and not self.excluded(v.name):
                what = "constant" if v.has("parameter") else "variable"
                hpp.emit(0, f"// {TODO}: module {what} {v.cname} not wrapped")
                self.report.add("todo", f"wrap-module-{what}:{v.name}")

        # header
        h = self.cpp_file_header(cfg["header_ext"]) + \
            [BANNER.format(src=os.path.basename(self.src)) + " (wrapped Fortran)"] + self.guard()[0] + [""]
        h += self.std_includes("\n".join(hpp.out) + ("\nstd::function" if mod.ifaces else ""),
                               self.HEADER_INCLUDES)
        inc_h, inc_c = self.includes("\n".join(hpp.out), "\n".join(cpp.out))
        h += [f"#include {i}" for i in cfg["extra_includes"]] + inc_h + inc_c
        # the C interface stays out of the C++ header: only the handle types (same typedef as in the .h)
        handles = sorted({self.c_type_name(t) for t in self.wrapped_types()
                          if t.name not in cfg["unwrap_types"] and t.name not in values})
        if handles:
            h += [""] + [f"struct {ht};  //!< C handle, see {os.path.basename(stem)}.h" for ht in handles]
        if self.any_used:                           # opaque handle of Fortran data (class(*))
            h += [f"struct {self.any_handle()};  //!< Fortran data (class(*)), see {os.path.basename(stem)}.h"]
        h += ["", self.open_ns(), ""] + ([self.any_cpp(), ""] if self.any_used else [])
        # forward declarations of the classes (they may refer to each other) and std::function aliases of
        # abstract interfaces (procedure arguments), as in the translation
        classes = [t for t in self.wrapped_types() if t.name not in cfg["unwrap_types"]]
        deferred = {b.iface for m in prog.modules.values() for t in m.types.values()
                    for b in t.bindings.values() if b.deferred}
        aliases = self.tr()
        for p in mod.ifaces.values():
            if p.external or p.name in deferred or p.name in cfg["procedure_map"]:
                continue
            ret, _, params, _ = self.signature(self.tr(p), p, False)
            self.doc(aliases, p, 0)
            aliases.emit(0, f"using {p.cname} = std::function<{ret}({params})>;")
            aliases.emit(0, "")
        if classes:
            h += [f"class {prog.type_name(t.name)};" for t in classes] + [""]
        h += aliases.out
        h += strip_blank(hpp.out) + ["", f"}}  // namespace {self.ns}", ""] + self.guard()[1]
        c = self.cpp_file_header(cfg["source_ext"], "C++ wrapper: ") + \
            [BANNER.format(src=os.path.basename(self.src)) + " (wrapped Fortran)",
             f'#include "{stem}{cfg["header_ext"]}"', "", "#include <stdexcept>", "",
             f'#include "{os.path.basename(stem)}.h"', ""] + (["#include <cstdio>", ""] if self.needs_cstdio else []) + \
            [self.open_ns(), ""]
        c += strip_blank(cpp.out) + ["", f"}}  // namespace {self.ns}", ""]

        # binding module
        single, real = cfg["c_single_precision"], cfg["c_real"]
        b = self.fortran_file_header(f"{os.path.basename(stem)}_c_binding.F90", "C binding: ") or \
            [f"!> C binding of module {mod.spelling}"]
        b += [f"! Generated by f2cpp from {os.path.basename(self.src)}",
              f"module {mod.spelling}_c_binding",
             "  use, intrinsic :: iso_c_binding",
             f"  use {mod.spelling}"]
        if needs_str:
            b.append(f"  use {cfg['wrap_string_in'][0]}, only: {cfg['wrap_string_in'][1]}")
        b += sorted(uses | self.extra_uses)
        b += ["  implicit none", f"#ifdef {single}", "#define C_REAL C_FLOAT", "#else", "#define C_REAL C_DOUBLE",
              "#endif", "", "!@c_declaration", "!", "!#include <stdbool.h>", "!", f"!#ifdef {single}",
              f"!#define {real} float", "!#else", f"!#define {real} double", "!#endif", "!"]
        b += typedefs + ["!", "!@end_c_declaration", ""] + vstructs + self.bridges
        for t in structs:                           # the handle points to this struct
            b += [f"  !> C struct {self.c_type_name(t)}: handle of {t.spelling or t.name}",
                  f"  type, bind(C), public :: {self.struct_type(t)}",
                  "    type(C_PTR) :: p_impl = C_NULL_PTR" +
                  ("   !< the Fortran object" if not self.poly(t) else f"   !< {self.wrapper_type(t)}")] + \
                 [f"    {line}" for line in self.constants_struct(self.constants(t)[0])[1]] + [
                  f"  end type {self.struct_type(t)}", ""]
        for t in wrappers:                          # the handle of a class hierarchy points to this wrapper
            b += [f"  !> handle of {t.spelling or t.name} and its extensions",
                  f"  type, public :: {self.wrapper_type(t)}",
                  f"    class({t.spelling or t.name}), pointer :: ptr => null()",
                  f"  end type {self.wrapper_type(t)}", ""]
        if self.any_used:
            spec, procs = self.any_fortran()
            b += spec
            fb += procs
        b += ["contains"] + short_names(fb) + ["", f"end module {mod.spelling}_c_binding", ""]
        self.finish_module(b)
        cw = None                                   # c_wrapper module: Fortran types implemented in C++
        if self.cw["spec"] or self.cw["procs"]:
            name = self.cwrapper_module(mod)
            what = ", ".join(t.spelling or t.name for t in self.wrapped_types() if t.name in self.reverse_types())
            cw = (self.fortran_file_header(f"{name}.F90", "Fortran types implemented in C++: ") or
                  [f"!> {what} implemented by C++ objects (through the C binding)"]) + [
                  f"! Generated by f2cpp from {os.path.basename(self.src)}", f"module {name}",
                  "  use, intrinsic :: iso_c_binding", f"  use {mod.spelling}",
                  use_line(f"  use {mod.spelling}_c_binding, only:", list(dict.fromkeys(self.cw["binding_names"])))] + \
                sorted(self.cw["uses"]) + \
                ["  implicit none", f"#ifdef {single}", "#define C_REAL C_FLOAT", "#else", "#define C_REAL C_DOUBLE",
                 "#endif", ""] + self.cw["spec"] + ["contains"] + short_names(self.cw["procs"]) + \
                ["", f"end module {name}", ""]
            self.finish_module(cw)
            cw = "\n".join(cw)
        files = {}                                  # C++ CWrapper<Thing>: <stem>_c_wrapper.hpp / .cpp
        if self.cwc["hpp"].out:
            base, hx, cx = os.path.basename(stem), cfg["header_ext"], cfg["source_ext"]
            macro = re.sub(r"\W", "_", f"{base}_c_wrapper{hx}").upper()
            guard = ([f"#ifndef {macro}", f"#define {macro}"], [f"#endif // {macro}", ""]) \
                if cfg["include_guard"] == "ifndef" else (["#pragma once"], [])
            banner = BANNER.format(src=os.path.basename(self.src)) + " (C++ classes calling C structs)"
            brief = "C++ wrapper of C structs: "
            ch = self.cpp_file_header(hx, brief, "_c_wrapper") + [banner] + guard[0] + ["", f'#include "{base}{hx}"', "", self.open_ns(), ""] + \
                strip_blank(self.cwc["hpp"].out) + ["", f"}}  // namespace {self.ns}", ""] + guard[1]
            cc = self.cpp_file_header(cx, brief, "_c_wrapper") + [banner, f'#include "{base}_c_wrapper{hx}"', "", "#include <stdexcept>", "",
                  f'#include "{base}.h"', "", self.open_ns(), ""] + strip_blank(self.cwc["cpp"].out) + \
                ["", f"}}  // namespace {self.ns}", ""]
            files = {f"_c_wrapper{hx}": "\n".join(self.braces(squeeze(ch))),
                     f"_c_wrapper{cx}": "\n".join(self.braces(squeeze(cc)))}
        return ("\n".join(self.braces(squeeze(h))), "\n".join(self.braces(squeeze(c))), "\n".join(b), cw, files)

    def finish_module(self, b):
        """C declarations in the first column, only-lists, private with one public statement per name"""
        mod = self.mod
        inside, out = False, []
        for line in b:
            if line.strip() == "!@c_declaration":
                inside = True
            out += split_params(line.lstrip(), self.cfg["c_decl_width"]) if inside else [line]
            if line.strip() == "!@end_c_declaration":
                inside = False
        b[:] = [x for line in out for x in fortran_continue(line)]
        b[b.index(f"  use {mod.spelling}")] = self.use_only(b)
        b[b.index("  use, intrinsic :: iso_c_binding")] = iso_c_only(b)
        types = []                                  # public: one statement per name (types included)
        for j, line in enumerate(b):
            m = re.match(r"(\s*type,\s*(?:[\w()]+,\s*)*)public\s*::\s*(\w+)", line)
            if m and not line.lstrip().startswith("!"):
                b[j] = line.replace(", public ::", " ::")
                types.append(m.group(2))
        k = b.index("  implicit none")
        own = [x for x in public_list(b) if x.split("::")[1].strip() not in self.wrap_routines]   # wrap_*: pointers only
        public = [f"  public :: {t}" for t in types] + own + \
            [f"  public :: {n}" for n in self.public_extra if n in "\n".join(b) and f"  public :: {n}" not in b]
        b[k + 1:k + 1] = ["  private"] + ([""] + section("public declarations") + public if public else [])
        p = b.index(f"#ifdef {self.cfg['c_single_precision']}")    # the precision macros: before the module
        macros = b[p:p + 5]
        del b[p:p + 5 + (b[p + 5:p + 6] == [""])]
        m = next(j for j, line in enumerate(b) if re.match(r"module \w+$", line))
        b[m:m] = macros + [""]
        start = max([j for j, line in enumerate(b) if line.startswith("  public ::")] or [b.index("  private")]) + 1
        b[:] = spec_sections(b, start)


SEPARATOR = "  !" + "=" * 80


def fortran_continue(line, width=132):
    """Fortran code lines longer than width (free form limit): continued (&) at a blank outside strings"""
    if len(line) <= width or line.lstrip().startswith(("!", "#")):
        return [line]
    ind = " " * (len(line) - len(line.lstrip()) + 4)
    res, rest = [], line
    while len(rest) > width:
        quote, cut, good = None, None, None     # good: after a comma or before result( / bind(
        for k, ch in enumerate(rest[:width - 2]):
            if ch in "\"'" and quote in (None, ch):
                quote = None if quote else ch
            elif ch == " " and quote is None and k > len(ind):
                cut = k
                if rest[k - 1] == "," and not rest[:k].rstrip(",").endswith("bind(C") or \
                        rest.startswith(("result(", "bind("), k + 1):
                    good = k
        cut = good or cut
        if cut is None:
            break
        res.append(rest[:cut] + " &")
        rest = ind + rest[cut + 1:].lstrip()
    return res + [rest]


def split_params(line, width):
    """a C declaration (comment line) longer than width: one parameter per line, aligned after the parenthesis"""
    if len(line) <= width or not line.rstrip().endswith(");"):
        return [line]
    text = line.rstrip()
    depth, open_ = 0, None
    for k in range(len(text) - 2, -1, -1):          # the parenthesis of the (last) parameter list
        depth += {")": 1, "(": -1}.get(text[k], 0)
        if depth == 0 and text[k] == "(":
            open_ = k
            break
    if open_ is None:
        return [line]
    params, depth, cur = [], 0, ""
    for ch in text[open_ + 1:-2]:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            params.append(cur.strip())
            cur = ""
        else:
            cur += ch
    params.append(cur.strip())
    if len(params) < 2:                         # one parameter: on its own line only beyond the Fortran limit
        ind = len(text[1:]) - len(text[1:].lstrip())
        return [line] if len(text) <= 132 else [text[:open_ + 1], "!" + " " * (ind + 4) + params[0] + ");"]
    pad = "!" + " " * open_
    return [text[:open_ + 1] + params[0] + ","] + [pad + p + "," for p in params[1:-1]] + [pad + params[-1] + ");"]


def section(title):
    return [SEPARATOR, f"  ! {title}"]


def spec_sections(b, start):
    """the specification part after the public statements: the abstract interfaces and the types, each under a
    section comment (C declarations and comments stay with their item); the leading C declarations (includes,
    macros, forward declarations) go between implicit none and private"""
    end = b.index("contains")
    seg, head = b[start:end], []
    while seg and not seg[0].strip():
        seg.pop(0)
    if seg and seg[0] == "!@c_declaration":     # the C prologue (includes, macros, forward declarations)
        j = seg.index("!@end_c_declaration")
        head, seg = seg[:j + 1], seg[j + 1:]
    chunks, pending, depth, kind = {"interface": [], "type": []}, [], 0, None
    for line in seg:
        t = line.split("!")[0].strip().lower()
        if depth == 0 and not t and not line.strip():
            if pending and pending[-1].strip():
                pending.append(line)
            continue
        pending.append(line)
        if re.match(r"(abstract\s+)?interface\b", t):
            depth, kind = depth + 1, kind or "interface"
        elif re.match(r"type\b(?!\s*\()", t):
            depth, kind = depth + 1, kind or "type"
        elif re.match(r"end\s+(interface|type)\b", t):
            depth -= 1
            if depth == 0:
                while pending and not pending[0].strip():
                    pending.pop(0)
                chunks[kind].append(pending)
                pending, kind = [], None
    out = b[:start]
    if head:                                    # like a use statement: between implicit none and private
        k = out.index("  implicit none")
        out[k + 1:k + 1] = [""] + head + [""]
    abstract = [c for c in chunks["interface"] if "  abstract interface" in c and c[-1] == "  end interface"]
    if abstract:                                # one abstract interface block with all interface bodies
        merged = ["  abstract interface"]
        for i, c in enumerate(abstract):
            merged += ([""] if i else []) + [x for j, x in enumerate(c)
                                             if x != "  abstract interface" and j != len(c) - 1]
        chunks["interface"] = [merged + ["  end interface"]] + [c for c in chunks["interface"] if c not in abstract]
    for kind, title in (("interface", "interface declarations"), ("type", "type declarations")):
        if chunks[kind]:
            out += [""] + section(title)
            for i, c in enumerate(chunks[kind]):
                out += ([""] if i else []) + c
    while pending and not pending[-1].strip():
        pending.pop()
    return out + ([""] + pending if any(x.strip() for x in pending) else []) + [""] + b[end:]



def public_list(lines):
    """public statement of the binding module: bind(C) procedures, setup / destroy / wrap (used from Fortran)
    and the abstract interfaces (types are declared public themselves)"""
    names, in_abstract, after_contains = [], False, False
    for line in lines:
        t = line.split("!")[0].strip().lower()
        if t == "contains" and not line.startswith("    "):
            after_contains = True
        if t.startswith("abstract interface"):
            in_abstract = True
        elif t.startswith("end interface"):
            in_abstract = False
        m = re.match(r"(?:\w+\s+)*(subroutine|function)\s+(\w+)\s*\(", line.split("!")[0].strip(), re.I)
        if not m:
            continue
        name = m.group(2)
        if in_abstract or (after_contains and line.startswith("  ") and not line.startswith("   ") and
                           ("bind(c" in t or re.search(r"_(setup|destroy|wrap)$", name.lower()))):
            if name not in names:
                names.append(name)
    return [f"  public :: {n}" for n in names]


def short_names(fb):
    """Fortran names are at most 63 characters: longer procedure names are shortened (prefix + hash); the C
    name of bind(C) routines stays the binding label"""
    import hashlib
    long = {m.group(1) for line in fb for m in [re.match(r"\s*(?:subroutine|function)\s+(\w+)\s*\(", line)]
            if m and len(m.group(1)) > 63}                  # also setup / destroy (called from Fortran)
    if not long:
        return fb
    short = {n: f"{n[:54]}_{hashlib.sha1(n.encode()).hexdigest()[:8]}" for n in long}
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(long, key=len, reverse=True))) + r")\b")
    out = []
    for line in fb:
        if line.lstrip().startswith("!"):           # C declarations: the C name
            out.append(line)
            continue
        parts = re.split(r'("[^"]*")', line)        # not in strings (binding label, messages)
        out.append("".join(pt if pt.startswith('"') else pat.sub(lambda m: short[m.group(1)], pt) for pt in parts))
    return out


ISO_C = ["C_PTR", "C_NULL_PTR", "C_FUNPTR", "C_NULL_FUNPTR", "C_INT", "C_BOOL", "C_CHAR", "C_NULL_CHAR", "C_FLOAT",
         "C_DOUBLE", "c_f_procpointer", "c_funloc", "c_loc",
         "c_f_pointer", "c_associated"]


def iso_c_only(lines):
    """use of iso_c_binding with the names the binding code refers to (C_REAL: C_FLOAT and C_DOUBLE)"""
    words = set()
    for line in lines:
        if not line.lstrip().startswith("!"):
            words |= {w.lower() for w in re.findall(r"[A-Za-z_]\w*", line.split("!")[0])}
    if "c_real" in words:
        words |= {"c_float", "c_double"}
    names = [n for n in ISO_C if n.lower() in words]
    return use_line("  use, intrinsic :: iso_c_binding, only:", names)


def use_line(head, names):
    """use statement with an only-list, continued after about 100 characters"""
    if not names:
        return head
    out, cur = [], head + " "
    for k, n in enumerate(names):
        piece = n + (", " if k < len(names) - 1 else "")
        if len(cur) + len(piece) > 100:
            out.append(cur.rstrip() + " &")
            cur = "    "
        cur += piece
    return "\n".join(out + [cur])


def c_header(binding, guard):
    """C header from the !@c_declaration blocks of a binding module"""
    out, inside = [f"#ifndef {guard}", f"#define {guard}", "", "#ifdef __cplusplus", 'extern "C" {', "#endif", ""], False
    block = []
    for line in binding.split("\n"):
        s = line.strip()
        if s == "!@c_declaration":
            inside, block = True, []
        elif s == "!@end_c_declaration":
            inside = False
            text = [b[1:].rstrip() for b in block]      # indentation relative to the block
            ind = min((len(t) - len(t.lstrip()) for t in text if t.strip()), default=0)
            out += [t[ind:] for t in text] + [""]
        elif inside and s.startswith("!"):
            block.append(s)
    out += ["#ifdef __cplusplus", "}", "#endif", "", f"#endif // {guard}", ""]
    res = []
    for line in out:
        if not (line == "" and res and res[-1] == ""):
            res.append(line)
    return "\n".join(res)


def comp_public(t, v):
    """explicit public / private attribute, else the type's default (private statement)"""
    return v.access == "public" if v.access else not t.private_comps


def register_accessors(prog, wrapped):
    """components of wrapped classes are getters in C++: obj%n -> obj.n()"""
    for m in wrapped:
        for t in m.types.values():
            acc = prog.cfg["accessors"].setdefault(t.name, {})
            for v in t.comps.values():
                if comp_public(t, v):
                    acc.setdefault(v.name, None)


def check_dependencies(prog, wrapped, translated, report):
    """wrapped (Fortran) modules that use translated modules: fine as long as the Fortran original of
    the translated module is still built; only noted in the report"""
    names = {m.name for m in translated}
    for m in wrapped:
        for u in m.uses:
            if u in names:
                report.start(m.name)
                report.add("info", f"wrap-uses-translated:{u}")
    return True
