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


def lower_camel(name):
    return name[:1].lower() + name[1:]


class WrapEmitter(ModuleEmitter):
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

    def wrap_from_c(self, t, name, ht, hpp, cpp):
        """<Class>_private::wrapFromC(c): C++ object of the C object c (protected constructor; derived
        classes inherit it). Pure C++ later: delete it with the constructor and handle_."""
        cls = name.split("::")[-1]
        hpp.emit(0, f"namespace {cls}_private")
        hpp.emit(0, "{")
        hpp.emit(0, f"std::unique_ptr<{cls}> wrapFromC({ht}& c);  //!< C++ object of the C object c")
        hpp.emit(0, "}")
        hpp.emit(0, "")
        cpp.emit(0, f"std::unique_ptr<{cls}> {cls}_private::wrapFromC({ht}& c)")
        cpp.emit(0, "{")
        cpp.emit(1, f"return std::unique_ptr<{cls}>(new {cls}(c));  // new: protected constructor, make_unique has no access")
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
            if v.rank > 2 or opt or v.has("allocatable") or v.has("pointer"):
                return None, f"argument {v.cname} (array: rank 1 / 2, not optional / allocatable)"
            dims = [self.fortran_size(p, sizes, a, d + 1 if v.rank == 2 else None) for d in range(v.rank)]
            if None in dims:
                return None, f"size of argument {v.cname} unknown (size check or wrap_sizes)"
            res.append((a, "array", dims))
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
            if not comp_public(t, v) or v.name in consts:
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
        if v.base == "logical":
            return None, None, self.component_kind(v)
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
            kind, _, why = self.plain_kind(c)
            if kind not in ("scalar", "logical", "fixed"):
                return None, f"{self.component_kind(v)}: component {c.cname}: {why or 'allocatable / pointer'}"
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
            if kind == "struct":
                c.append(f"{self.cfg['c_prefix']}_{self.c_class(extra)} {n};")
                f.append(f"type({self.c_class(extra)}_c) :: {n}")
            elif kind in ("scalar", "logical"):
                c.append(f"{ct} {n};")
                f.append(f"{self.f_type(v)} :: {n} = " + (".false." if kind == "logical" else "0"))
            elif kind == "fixed":
                c.append(f"{ct} {n}[{extra}];")
                f.append(f"{self.f_type(v)} :: {n}({extra}) = 0")
            else:
                c += [f"{ct}* {n};", f"int {n}_size;"]
                f += [f"type(C_PTR) :: {n} = C_NULL_PTR", f"integer(C_INT) :: {n}_size = 0"]
        return c, f

    def plain_consts(self, td):
        return [(c, self.plain_kind(c)[0], self.plain_kind(c)[1]) for c in td.comps.values()]

    def copy_constants(self, consts, to, frm):
        """Fortran statements: constants of the object (frm) into the C struct (to)"""
        body = []
        for v, kind, extra in consts:
            n, m = f"{to}%{v.cname}", f"{frm}%{v.name}"
            if kind == "struct":
                body += self.copy_constants(self.plain_consts(extra), n, m)
            elif kind == "logical":
                body.append(f"{n} = logical({m}, C_BOOL)")
            elif kind == "array":
                test = "associated" if v.has("pointer") else "allocated"
                body += [f"{n} = C_NULL_PTR", f"{n}_size = 0", f"if ({test}({m})) then",
                         f"  {n} = c_loc({m})", f"  {n}_size = size({m})", "end if"]
            else:
                body.append(f"{n} = {m}")
        return body

    def setup_destroy(self, t, consts, fb):
        """Fortran (not bind(C)) project_<Class>_setup(obj) -> handle / project_<Class>_destroy(handle):
        C struct (and wrapper of a hierarchy) with the constants; the object must outlive the handle"""
        setup, destroy = self.c_name(t, "setup"), self.c_name(t, "destroy")
        ft, poly, struct = t.spelling or t.name, self.poly(t), self.struct_handle(t)
        sty, wty = self.struct_type(t), self.wrapper_type(t) if self.poly(t) else None
        fb += ["", f"  !> handle of obj for C / C++ (obj must outlive it; release with {destroy})",
               f"  function {setup}(obj) result(handle)",
               f"    {'class' if poly else 'type'}({ft}), target :: obj", "    type(C_PTR) :: handle"] +             ([f"    type({sty}), pointer :: this_c"] if struct else []) +             ([f"    type({wty}), pointer :: wrapper"] if poly else []) + [""]
        p = "c_loc(wrapper)" if poly else "c_loc(obj)"
        if poly:
            fb += ["    allocate(wrapper)", "    wrapper%ptr => obj"]
        if struct:
            fb += ["    allocate(this_c)", f"    this_c%p_impl = {p}"] +                 [f"    {x}" for x in self.copy_constants(consts, "this_c", "obj")] + ["    handle = c_loc(this_c)"]
        else:
            fb.append(f"    handle = {p}")
        fb += [f"  end function {setup}", "",
               f"  !> releases the handle of {setup} (not the object)", f"  subroutine {destroy}(handle)",
               "    type(C_PTR), intent(inout) :: handle"] +             ([f"    type({sty}), pointer :: this_c"] if struct else []) +             ([f"    type({wty}), pointer :: wrapper"] if poly else []) + ["", "    if (.not. c_associated(handle)) return"]
        if struct:
            fb.append("    call c_f_pointer(handle, this_c)")
        if poly:
            fb += [f"    call c_f_pointer({'this_c%p_impl' if struct else 'handle'}, wrapper)", "    deallocate(wrapper)"]
        if struct:
            fb.append("    deallocate(this_c)")
        fb += ["    handle = C_NULL_PTR", f"  end subroutine {destroy}"]

    def constant_getter(self, t, v, kind, hdecl, cpp):
        tr, n = self.tr(), v.cname
        if kind == "struct":
            for line in self.component_docs(t).get(v.name, []):
                hdecl.emit(1, f"{self.cfg['doxygen']} {line}".rstrip())
            hdecl.emit(1, f"const {self.prog.type_name(v.tname)}& {n}() const {{ return {n}_; }}  "
                          "//!< constant (C++ copy)")
            return
        if kind == "scalar":
            decl, body = f"{tr.cpp_type(v)} {n}() const", f"return handle_->{n};"
        elif kind == "logical":
            decl, body = f"bool {n}() const", f"return handle_->{n};"
        else:
            et = tr.eigen_type(v, 1)
            size = f"handle_->{n}_size" if kind == "array" else next(e for w, k, e in self.consts if w is v)
            decl, body = f"Eigen::Map<const {et}> {n}() const", f"return Eigen::Map<const {et}>(handle_->{n}, {size});"
        for line in self.component_docs(t).get(v.name, []):
            hdecl.emit(1, f"{self.cfg['doxygen']} {line}".rstrip())
        hdecl.emit(1, decl + ";  //!< constant (C struct)")
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

    def component_docs(self, t):
        """{component: [comment text]}: comments before the statement and behind it on the same line"""
        from .prepass import CPP_MARK, BLANK_MARK, PF_MARK
        flat = []
        for part in t.node.content[1:-1]:       # fparser spreads components / comments over several nodes
            k = cls(part)
            if k == "Component_Part":
                flat += part.content
            elif k == "Comment":
                flat.append(part)
            elif k == "Type_Bound_Procedure_Part":     # comments before `contains` sit in it
                flat += part.content[:[cls(c) for c in part.content].index("Contains_Stmt")]
                break
        docs, lead, last = {}, [], []
        for d in flat:
            if cls(d) == "Comment":
                text = str(d)
                if not text.strip() or text.startswith((CPP_MARK, BLANK_MARK, PF_MARK)):
                    lead = []
                    continue
                text = re.sub(r"^![<>!]?\s?", "", text.strip()).rstrip()
                prev = last[-1][1] if last else None
                if prev is not None and d.item is not None and getattr(prev, "item", None) is not None \
                        and prev.item.span[1] == d.item.span[0]:
                    for n, _ in last:
                        docs[n].append(text)        # trailing comment of the previous statement
                else:
                    lead.append(text)
                continue
            names = [lname(e.items[0]) for e in items_of(d.items[2])] if cls(d) == "Data_Component_Def_Stmt" else []
            last = [(n, d) for n in names]
            for n in names:
                docs[n] = list(lead)
            lead = []
        return docs

    # ------------------------------------------------------------------ method binding
    def proc_c_name(self, p):
        """C name of a module procedure: project_<procedure> (c_procedure_names: other names)"""
        return self.cfg["c_procedure_names"].get(p.name) or f"{self.cfg['c_prefix']}_{p.cname}"

    def method(self, t, bd, p, args, fb, hdecl, cpp):
        """emit the binding, the declaration and the glue of one method (t, bd None: module procedure)"""
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
                pre.append(f"if (c_associated({a})) {a}_f = {self.cfg['wrap_string_in'][1]}({a})")
                call.append(f"{a}={a}_f")
                glue.append(f"{v.cname} ? {v.cname}->c_str() : nullptr")
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
                glue.append(f"{v.cname}.data()")
                if v.rank == 1:
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
                    glue.append(("pre", f"{v.cname}.resize({', '.join(codes)});"))
                elif not self.cfg["wrap_size_checks"]:
                    pass
                elif codes is None:
                    self.report.add("todo", "wrap-size-check")
                    glue.append(("pre", f"// {TODO}: size of {v.cname} is not checked"))
                else:
                    have = [f"{v.cname}.size()"] if v.rank == 1 else [f"{v.cname}.rows()", f"{v.cname}.cols()"]
                    cond = " || ".join(f"{h} != {c}" for h, c in zip(have, codes))
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
            if isinstance(g, tuple):
                cpp.emit(1, g[1])
        cargs = ", ".join((["handle_"] if t is not None else []) + [g for g in glue if not isinstance(g, tuple)] + (["&ierr"] if has_err else []))
        if has_err:
            cpp.emit(1, "int ierr = 0;")
        if r is not None and has_err:
            cpp.emit(1, f"const {ret} res = {cname}({cargs});")
        elif r is not None:
            cpp.emit(1, f"return {cname}({cargs});")
        else:
            cpp.emit(1, f"{cname}({cargs});")
        if has_err:
            cpp.emit(1, f'if (ierr != 0) throw std::runtime_error("ierr!=0 returned from {cname}");')
            if r is not None:
                cpp.emit(1, "return res;")
        cpp.emit(0, "}")
        cpp.emit(0, "")

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
            decls = [(f"{ty}& {v.cname}()", f"return *{cname}(handle_);"),
                     (f"const {ty}& {v.cname}() const", f"return *{cname}(handle_);")]
        elif kind == "logical":
            fb.append(f"  !  bool {cname}(const {self.c_type_name(t)}* {handle});")
            fb.append("  !@end_c_declaration")
            odecl, ostmts = self.object_access(t, cname, handle, False, [f"res = obj%{v.name}"])
            fb += [f"  function {cname}({handle}) result(res) bind(C, name=\"{cname}\")"] + self.dllexport(cname) + [
                   f"    type(C_PTR), value :: {handle}", "    logical(C_BOOL) :: res"] + odecl + ostmts + \
                [f"  end function {cname}"]
            decls = [(f"bool {v.cname}() const", f"return {cname}(handle_);")]  # Fortran logical: no reference
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
                    f" {cname}(handle_, &data, " + ", ".join(f"&{d}" for d in dims) +
                    "); return Eigen::Map<{}>(data, " + ", ".join(dims) + ");")
            decls = [(f"Eigen::Map<{et}> {v.cname}()", body.replace("{}", et)),
                     (f"Eigen::Map<const {et}> {v.cname}() const", body.replace("{}", f"const {et}"))]
        doc, dox = self.component_docs(t).get(v.name, []), self.cfg["doxygen"]
        for k, (decl, body) in enumerate(decls):
            lines = list(doc)
            if lines and k == 1:
                lines[-1] += " (const version)"
            for line in lines:
                hdecl.emit(1, f"{dox} {line}".rstrip())
            hdecl.emit(1, decl + ";")
            cpp.emit(0, decl.replace(f" {v.cname}()", f" {cls_name}::{v.cname}()", 1))
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

    def cpp_file_header(self, ext, prefix=None):
        from .emit import plain_comment
        top = self.tr()
        top.block(self.header_comments(), 0)
        if not any("@file" in line for line in top.out):
            top.out = [plain_comment(line) for line in top.out]
        lines = self.file_header(top, ext)
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
        stem_h = os.path.basename(stem) + ".h"
        values = self.value_types()
        vstructs = []
        for td in values.values():              # struct constants: C struct, bind(C) type, C++ copy of the class
            cn = f"{cfg['c_prefix']}_{self.c_class(td)}"
            typedefs += [f"!typedef struct {cn} {{"] + \
                [f"!  {line}" for line in self.constants_struct(self.plain_consts(td))[0]] + [f"!}} {cn};"]
            vstructs += [f"  !> C struct {cn}: constant copy of {td.spelling or td.name}",
                         f"  type, bind(C), public :: {self.c_class(td)}_c"] + \
                [f"    {line}" for line in self.constants_struct(self.plain_consts(td))[1]] + \
                [f"  end type {self.c_class(td)}_c", ""]
            if td.module is self.mod:
                self.emit_type(hpp, td, 0)
                hpp.emit(0, "")
            self.report.add("translated", "wrapped-constant-type")
        for t in [t for t in mod.types.values() if not self.excluded(t.name)]:
            if t.name in cfg["unwrap_types"] or t.name in values:
                continue
            name = self.prog.type_name(t.name)
            ht = self.c_type_name(t)
            root, rname = self.root(t)
            if self.is_root(t):
                if self.struct_handle(t):       # struct of the handle: bind(C) type <Class>_c
                    typedefs += [f"!typedef struct {ht} {{", "!  void* p_impl;"] + \
                        [f"!  {line}" for line in self.constants_struct(self.constants(t)[0])[0]] + [f"!}} {ht};"]
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
            hpp.emit(1, f"{name}(const {name}&) = delete;")
            hpp.emit(1, f"{name}& operator=(const {name}&) = delete;")
            hpp.emit(1, f"{name}({name}&&) = default;")
            hpp.emit(1, f"{name}& operator=({name}&&) = default;")
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
                cpp.emit(0, f"{name}::{name.split('::')[-1]}({ht}& c) : handle_(&c)")
                cpp.emit(0, "{")
                tr = self.tr()
                for v, td in copies:
                    for w, kind, size in self.plain_consts(td):
                        dst, src = f"{v.cname}_.{w.cname}", f"c.{v.cname}.{w.cname}"
                        if kind == "fixed" and tr.storage(w) == "eigen":
                            cpp.emit(1, f"{dst} = Eigen::Map<const {tr.eigen_type(w, 1)}>({src}, {size});")
                        elif kind == "fixed":
                            cpp.emit(1, f"{dst}.assign({src}, {src} + {size});")
                        else:
                            cpp.emit(1, f"{dst} = {src};")
                cpp.emit(0, "}")
                cpp.emit(0, "")
            elif self.is_root(t):
                hpp.emit(1, f"explicit {name}({ht}& c) : handle_(&c) {{}}  //!< the object created in Fortran")
            cls = name.split("::")[-1]
            hpp.emit(1, f"friend std::unique_ptr<{cls}> {cls}_private::wrapFromC({ht}& c);")
            if self.is_root(t):
                hpp.emit(1, f"{ht}* handle_ = nullptr;  //!< the Fortran object (C binding: {stem_h})")
                for v, td in copies:
                    hpp.emit(1, f"{prog.type_name(td.name)} {v.cname}_;  //!< C++ copy of the constant {v.cname}")
            hpp.emit(0, "};")
            hpp.emit(0, "")
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
        handles = sorted({self.c_type_name(t) for t in mod.types.values()
                          if t.name not in cfg["unwrap_types"] and not self.excluded(t.name) and t.name not in values})
        if handles:
            h += [""] + [f"struct {ht};  //!< C handle, see {os.path.basename(stem)}.h" for ht in handles]
        h += ["", self.open_ns(), ""]
        # forward declarations of the classes (they may refer to each other) and std::function aliases of
        # abstract interfaces (procedure arguments), as in the translation
        classes = [t for t in mod.types.values() if t.name not in cfg["unwrap_types"] and not self.excluded(t.name)]
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
             f'#include "{os.path.basename(stem)}.h"', "", self.open_ns(), ""]
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
        b += sorted(uses)
        b += ["  implicit none", f"#ifdef {single}", "#define C_REAL C_FLOAT", "#else", "#define C_REAL C_DOUBLE",
              "#endif", "", "!@c_declaration", "!", "!#include <stdbool.h>", "!", f"!#ifdef {single}",
              f"!#define {real} float", "!#else", f"!#define {real} double", "!#endif", "!"]
        b += typedefs + ["!", "!@end_c_declaration", ""] + vstructs
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
        b += ["contains"] + fb + ["", f"end module {mod.spelling}_c_binding", ""]
        return ("\n".join(self.braces(squeeze(h))), "\n".join(self.braces(squeeze(c))), "\n".join(b))


def c_header(binding, guard):
    """C header from the !@c_declaration blocks of a binding module"""
    out, inside = [f"#ifndef {guard}", f"#define {guard}", "", "#ifdef __cplusplus", 'extern "C" {', "#endif", ""], False
    for line in binding.split("\n"):
        s = line.strip()
        if s == "!@c_declaration":
            inside = True
        elif s == "!@end_c_declaration":
            inside = False
            out.append("")
        elif inside and s.startswith("!"):
            out.append(s[1:].strip())
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
