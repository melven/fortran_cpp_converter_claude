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
    def c_class(self, t):
        return self.cfg["c_class_names"].get(t.name) or self.prog.type_name(t.name).split("::")[-1]

    def c_type_name(self, t):
        """C typedef of the handle: project_MySolver"""
        return f"{self.cfg['c_prefix']}_{self.c_class(t)}"

    def c_name(self, t, what):
        return f"{self.cfg['c_prefix']}_{self.c_class(t)}_{what}"

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
        for v in t.comps.values():
            if t.private_comps or v.access == "private":
                continue
            if v.base in ("real", "integer") and v.rank == 0 and not v.has("pointer"):
                res.append((v, "scalar"))
            elif v.base == "logical" and v.rank == 0:
                res.append((v, "logical"))
            elif v.base in ("real", "integer") and v.rank in (1, 2) and (v.has("allocatable") or v.has("pointer")):
                res.append((v, "array"))
            else:
                self.report.add("todo", f"wrap-component:{v.name}")
        return res

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
            c_args.append(f"{self.c_type_name(t)} {handle}")
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
        for d in decls:
            fb.append(f"    {d}")
        if r is not None:
            fb.append(f"    {self.f_type(r)} :: res")
        if t is not None:
            fb.append(f"    type({t.spelling or t.name}), pointer :: obj")
            fb.append("")
            fb.append(f"    call c_f_pointer({handle}, obj)")
        else:
            fb.append("")
        fb.extend(f"    {s}" for s in pre)
        if t is not None:
            target = f"obj%{bd.generic or bd.name}" if bd.access == "private" else f"obj%{bd.name}"
        else:                       # private specific of a public generic interface: through the generic
            target = p.cname if self.mod.is_public(p.name) or not p.generic else p.generic
        if r is not None:
            fb.append(f"    res = {target}({', '.join(call)})")
        else:
            fb.append(f"    call {target}({', '.join(call)})")
        fb.extend(f"    {s}" for s in post)
        fb.append(f"  end {kw} {cname}")
        # --- glue
        ind, qual, name = (1, self.prog.type_name(t.name) + "::", p.bind_name) if t is not None else \
            (0, "", self.signature(tr, p, False)[1])
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

    def not_wrapped(self, t, p, reason, hdecl, cpp):
        tr = self.tr(p)
        ret, fname, params, const = self.signature(tr, p, False)
        self.report.add("untranslated", "wrap:" + reason.split(" (")[0].split(" ")[0])
        ind, qual, name = (1, self.prog.type_name(t.name) + "::", p.bind_name) if t is not None else (0, "", fname)
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
        ftype = t.spelling or t.name
        fb.append("")
        fb.append("  !@c_declaration")
        if kind == "scalar":
            ct = self.c_type(v)
            fb.append(f"  !  {ct}* {cname}({self.c_type_name(t)} {handle});")
            fb.append("  !@end_c_declaration")
            fb += [f"  function {cname}({handle}) result(res) bind(C, name=\"{cname}\")",
                   f"    type(C_PTR), value :: {handle}", "    type(C_PTR) :: res",
                   f"    type({ftype}), pointer :: obj", "", f"    call c_f_pointer({handle}, obj)",
                   f"    res = c_loc(obj%{v.name})", f"  end function {cname}"]
            decl, body = f"{tr.cpp_type(v)}& {v.cname}() const", f"return *{cname}(handle_);"
        elif kind == "logical":
            fb.append(f"  !  bool {cname}({self.c_type_name(t)} {handle});")
            fb.append("  !@end_c_declaration")
            fb += [f"  function {cname}({handle}) result(res) bind(C, name=\"{cname}\")",
                   f"    type(C_PTR), value :: {handle}", "    logical(C_BOOL) :: res",
                   f"    type({ftype}), pointer :: obj", "", f"    call c_f_pointer({handle}, obj)",
                   f"    res = obj%{v.name}", f"  end function {cname}"]
            decl, body = f"bool {v.cname}() const", f"return {cname}(handle_);"
        else:
            ct = self.c_type(v)
            dims = ["n1"] if v.rank == 1 else ["n1", "n2"]
            fb.append(f"  !  void {cname}({self.c_type_name(t)} {handle}, {ct}** data, "
                      + ", ".join(f"int* {d}" for d in dims) + ");")
            fb.append("  !@end_c_declaration")
            test = "associated" if v.has("pointer") else "allocated"
            fb += [f"  subroutine {cname}({handle}, data, {', '.join(dims)}) bind(C, name=\"{cname}\")",
                   f"    type(C_PTR), value :: {handle}", "    type(C_PTR), intent(out) :: data",
                   f"    integer(C_INT), intent(out) :: {', '.join(dims)}",
                   f"    type({ftype}), pointer :: obj", "", f"    call c_f_pointer({handle}, obj)",
                   "    data = C_NULL_PTR"] + [f"    {d} = 0" for d in dims] + \
                  [f"    if ({test}(obj%{v.name})) then", f"      data = c_loc(obj%{v.name})"] + \
                  [f"      {d} = size(obj%{v.name}, {k + 1})" for k, d in enumerate(dims)] + \
                  ["    end if", f"  end subroutine {cname}"]
            et = tr.eigen_type(v, v.rank)
            decl = f"Eigen::Map<{et}> {v.cname}() const"
            body = (f"{ct}* data = nullptr; " + " ".join(f"int {d} = 0;" for d in dims) +
                    f" {cname}(handle_, &data, " + ", ".join(f"&{d}" for d in dims) +
                    f"); return Eigen::Map<{et}>(data, " + ", ".join(dims) + ");")
        hdecl.emit(1, decl + ";")
        cpp.emit(0, decl.replace(f" {v.cname}()", f" {cls_name}::{v.cname}()", 1))
        cpp.emit(0, "{")
        for stmt in body.split("; "):
            cpp.emit(1, stmt.rstrip(";") + ";")
        cpp.emit(0, "}")
        cpp.emit(0, "")

    # ------------------------------------------------------------------ module
    def generate(self):
        """-> (header, glue source, binding source)"""
        mod, cfg, prog = self.mod, self.cfg, self.prog
        self.report.start(self.src)
        stem = prog.file_stem(mod.name)
        hpp, cpp = self.tr(), self.tr()
        fb = []                                     # binding routines
        typedefs, needs_str = [], False
        for t in mod.types.values():
            if t.name in cfg["unwrap_types"]:
                continue
            name = self.prog.type_name(t.name)
            cc = self.c_class(t)
            ht = self.c_type_name(t)
            typedefs.append(f"!typedef void* {ht};")
            if prog.polymorphic(t):
                self.report.add("todo", "wrap:polymorphic")
                hpp.emit(0, f"// {TODO}: polymorphic class: wrapping of class hierarchies is not supported yet")
            hpp.emit(0, f"class {name} final")
            hpp.emit(0, "{")
            hpp.emit(0, "public:")
            hpp.emit(1, f"{name}() = default;  //!< empty object (tests)")
            hpp.emit(1, f"explicit {name}({ht} handle) : handle_(handle) {{}}  //!< object created in Fortran")
            hpp.emit(1, f"{name}(const {name}&) = delete;")
            hpp.emit(1, f"{name}& operator=(const {name}&) = delete;")
            hpp.emit(1, f"{name}({name}&&) = default;")
            hpp.emit(1, f"{name}& operator=({name}&&) = default;")
            hpp.emit(1, f"{ht} handle() const {{ return handle_; }}")
            hpp.emit(0, "")
            for bd in t.bindings.values():
                p = prog.binding_proc(t, bd)
                if p is None or bd.deferred or bd.nopass or p.is_ctor or p.is_dtor:
                    if p is not None and (p.is_ctor or p.is_dtor):
                        hpp.emit(1, f"// {bd.name}: object created / destroyed in Fortran")
                    continue
                if bd.access == "private" and not bd.generic:
                    continue
                args, reason = self.wrappable(p, t)
                if reason:
                    self.not_wrapped(t, p, reason, hpp, cpp)
                    continue
                needs_str |= any(kind in ("string", "optional-string") for _, kind, _ in args)
                self.method(t, bd, p, args, fb, hpp, cpp)
                self.report.add("translated", "wrapped-method")
            getters = self.getters(t)
            if getters:
                hpp.emit(0, "")
            for v, kind in getters:
                self.getter(t, v, kind, fb, hpp, cpp)
                self.report.add("translated", "wrapped-getter")
            hpp.emit(0, "")
            hpp.emit(0, "private:")
            hpp.emit(1, f"{ht} handle_ = nullptr;")
            hpp.emit(0, "};")
            hpp.emit(0, "")
        for p in mod.procs.values():
            if p.method_of or not (mod.is_public(p.name) or (p.generic and mod.is_public(p.generic.lower()))):
                continue
            args, reason = self.wrappable(p, None)
            if reason:
                self.not_wrapped(None, p, reason, hpp, cpp)
                continue
            needs_str |= any(kind in ("string", "optional-string") for _, kind, _ in args)
            self.method(None, None, p, args, fb, hpp, cpp)
            self.report.add("translated", "wrapped-procedure")

        # header
        h = [BANNER.format(src=os.path.basename(self.src)) + " (wrapped Fortran)"] + self.guard()[0] + [""]
        h += self.std_includes("\n".join(hpp.out), self.HEADER_INCLUDES)
        h += [f"#include {i}" for i in cfg["extra_includes"]] + self.includes()
        h += [f'#include "{os.path.basename(stem)}.h"', "", self.open_ns(), ""]
        h += strip_blank(hpp.out) + ["", f"}}  // namespace {self.ns}", ""] + self.guard()[1]
        c = [BANNER.format(src=os.path.basename(self.src)) + " (wrapped Fortran)",
             f'#include "{stem}{cfg["header_ext"]}"', "", "#include <stdexcept>", "",
             self.open_ns(), ""]
        c += strip_blank(cpp.out) + ["", f"}}  // namespace {self.ns}", ""]

        # binding module
        single, real = cfg["c_single_precision"], cfg["c_real"]
        b = [f"!> C binding of module {mod.spelling} (generated by f2cpp)",
             f"module {mod.spelling}_c_binding",
             "  use, intrinsic :: iso_c_binding",
             f"  use {mod.spelling}"]
        if needs_str:
            b.append(f"  use {cfg['wrap_string_in'][0]}, only: {cfg['wrap_string_in'][1]}")
        b += ["  implicit none", f"#ifdef {single}", "#define C_REAL C_FLOAT", "#else", "#define C_REAL C_DOUBLE",
              "#endif", "", "!@c_declaration", "!", "!#include <stdbool.h>", "!", f"!#ifdef {single}",
              f"!#define {real} float", "!#else", f"!#define {real} double", "!#endif", "!"]
        b += typedefs + ["!", "!@end_c_declaration", "", "contains"] + fb + ["", f"end module {mod.spelling}_c_binding", ""]
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


def register_accessors(prog, wrapped):
    """components of wrapped classes are getters in C++: obj%n -> obj.n()"""
    for m in wrapped:
        for t in m.types.values():
            if t.private_comps:
                continue
            acc = prog.cfg["accessors"].setdefault(t.name, {})
            for v in t.comps.values():
                if v.access != "private":
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
