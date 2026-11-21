"""Abstract Fortran types implemented in C++ (mixin of WrapEmitter).

Common mechanism: every deferred binding of the abstract type becomes a function pointer of a C type;
a Fortran type in the binding module extends the abstract type and calls the pointers; the C++ class
has the pure virtual methods and private static <method>_wrapper functions whose addresses are put into
the pointers (the Fortran library references no symbol of the C++ library).

- listeners (wrap_listeners): owned by C++; the Fortran object (<Listener>_cpp) is created by the C++
  constructor, set on a wrapped object with set_listener; object arguments are the owner
- reverse types (wrap_reverse): C struct project_<Type> per C++ object (function pointers, destroy,
  p_impl, userData); Fortran CWrapper<Thing>_type owns the C++ object (final -> destroy)
- reverse factories (wrap_reverse_factories): as reverse types, registered by the C++ constructor under
  a name in the existing Fortran factory registration

Possible simplification: listeners as reverse types without destroy (C struct in the C++ object,
set_listener passes the struct, Fortran wraps it non-owning); one mechanism with an ownership flag and the
owner rule for object arguments.
"""
import re

from .model import lname, items_of
from .translate import TODO


class ReverseMixin:

    # ------------------------------------------------------------------ one deferred binding
    def forward(self, lt, bd, iface, *, proc, aiface, funptr, self_c, self_dummy, self_actual, cpp_obj, cls,
                objects):
        """parts of a deferred binding implemented in C++: the Fortran procedure (arguments to C, call
        through the function pointer funptr), the Fortran interface of the function and the C++ definition
        of cls::<binding>_wrapper (calls the virtual method; exceptions -> ierr).
        self_*: first C parameter (C++ object: cpp_obj); objects: "owner" (only the owner of a listener)
        or "handle" (temporary handle of a wrapped object)"""
        prog, cfg = self.prog, self.cfg
        tr = self.tr(iface)
        tramp = f"{bd.name}_wrapper"
        err = tr.err
        has_err = err in iface.args and iface.vars[err].base == "integer" and not iface.vars[err].rank
        this = iface.pass_arg
        decl_nodes = []
        c_params, f_dummies, f_decls = [self_c], [self_dummy], [f"type(C_PTR), value :: {self_dummy}"]
        locals_, checks, pre, post, actuals = [], [], [], [], [self_actual]
        cpp_init, cpp_pre, cpp_args, cpp_post = [], [], [], []
        todo = None
        fail = f"{err} = -1" if has_err else f'error stop "{proc}: called for another object than its owner"'
        for a in iface.args:
            v = iface.vars[a]
            if v.node is not None and v.node not in decl_nodes:
                decl_nodes.append(v.node)
            if a == this or (has_err and a == err) or a == iface.result:
                continue
            intent = v.intent or "inout"
            rev = v.tname if v.base == "type" else None
            if v.base == "type" and not v.rank and rev in self.reverse_types() and v.has("allocatable") \
                    and intent == "out":        # created object (factory): owned by Fortran
                rt = prog.find_type(rev, self.mod)
                rh, rcls = self.c_type_name(rt), prog.type_name(rt.name, self.mod)
                c_params.append(f"{rh}** {a}")
                f_dummies.append(a)
                f_decls.append(f"type(C_PTR), intent(out) :: {a}")
                locals_.append(f"type(C_PTR) :: {a}_p")
                pre.append(f"{a}_p = C_NULL_PTR")
                actuals.append(f"{a}_p")
                post.append(f"if (c_associated({a}_p)) call {self.c_name(rt, 'wrap')}({a}_p, {a})")
                self.use_binding(rt, [self.c_name(rt, "wrap")])
                cpp_init.append(f"*{a} = nullptr;")
                cpp_pre.append(f"std::unique_ptr<{rcls}> {a}_u;")
                cpp_args.append(f"{a}_u")
                cpp_post += [f"*{a} = {a}_u ? {self.private_ns(rcls)}::toC(*{a}_u) : nullptr;",
                             f"{a}_u.release(); // NOLINT(bugprone-unused-return-value)"]
            elif v.base == "type" and not v.rank and objects == "owner":     # listener: only the owner
                c_params.append(f"void* {a}")
                f_dummies.append(a)
                f_decls.append(f"type(C_PTR), value :: {a}")
                checks += [f"if (.not. listener_same({this}%owner, {a})) then", f"  {fail}", "  return", "end if"]
                actuals.append(f"{this}%user")
                cpp_args.append(f"*static_cast<const {prog.type_name(v.tname, self.mod)}*>({a})")
            elif v.base == "type" and not v.rank:                            # temporary handle
                xt = prog.find_type(v.tname, self.mod)
                if xt is None or xt.module.name not in getattr(prog, "wrapped", ()) or not self.is_root(xt) or \
                        v.has("allocatable") or v.has("pointer"):
                    todo = f"argument {v.cname}: type {v.tname} is not a wrapped root type"
                    continue
                xh, xcls = self.c_type_name(xt), prog.type_name(xt.name, self.mod)
                setup, destroy = self.c_name(xt, "setup"), self.c_name(xt, "destroy")
                self.use_binding(xt, [setup, destroy])
                c_params.append(f"{xh}* {a}")
                f_dummies.append(a)
                f_decls.append(f"type(C_PTR), value :: {a}")
                locals_.append(f"type(C_PTR) :: {a}_h")
                pre.append(f"{a}_h = {setup}({a})      ! temporary handle")
                actuals.append(f"{a}_h")
                post.append(f"call {destroy}({a}_h)")
                cpp_pre.append(f"std::unique_ptr<{xcls}> {a}_w = {self.private_ns(xcls)}::wrapFromC(*{a});")
                cpp_args.append(f"*{a}_w")
            elif v.base in ("real", "integer", "logical") and not v.rank:
                conv = {"real": "real", "integer": "int", "logical": "logical"}[v.base]
                kind = {"real": "C_REAL", "integer": "C_INT", "logical": "C_BOOL"}[v.base]
                f_dummies.append(a)
                locals_.append(f"{self.f_type(v)} :: {a}_c")
                actuals.append(f"{a}_c")
                if intent == "in":
                    c_params.append(f"{self.c_type(v)} {a}")
                    f_decls.append(f"{self.f_type(v)}, value :: {a}")
                    pre.append(f"{a}_c = {conv}({a}, {kind})")
                    cpp_args.append(a)
                else:                           # by reference, copied back
                    c_params.append(f"{self.c_type(v)}* {a}")
                    f_decls.append(f"{self.f_type(v)}, intent(inout) :: {a}")
                    if intent == "inout":
                        pre.append(f"{a}_c = {conv}({a}, {kind})")
                    post.append(f"{a} = logical({a}_c)" if v.base == "logical" else f"{a} = {a}_c")
                    cpp_args.append(f"*{a}")
            elif v.base in ("real", "integer") and v.rank == 1 and not v.has("allocatable") and not v.has("pointer"):
                const = "const " if intent == "in" else ""
                c_params += [f"{const}{self.c_type(v)}* {a}", f"int {a}_n"]
                f_dummies += [a, f"{a}_n"]
                f_decls += [f"type(C_PTR), value :: {a}", f"integer(C_INT), value :: {a}_n"]
                locals_ += [f"{self.f_type(v)}, allocatable, target :: {a}_c(:)", f"type(C_PTR) :: {a}_p"]
                pre += [f"allocate({a}_c(size({a})))"] + ([] if intent == "out" else [f"{a}_c(:) = {a}"]) + \
                    [f"{a}_p = C_NULL_PTR", f"if (size({a}_c) > 0) {a}_p = c_loc({a}_c(1))"]
                actuals += [f"{a}_p", f"int(size({a}_c), C_INT)"]
                et = tr.eigen_type(v, 1)
                if intent == "in":
                    cpp_args.append(f"{et}(Eigen::Map<const {et}>({a}, {a}_n))")
                else:                           # a C++ vector, copied back (the size must not change)
                    post.append(f"{a}(:) = {a}_c")
                    cpp_pre.append(f"{et} {a}_v({a}_n);" if intent == "out" else
                                   f"{et} {a}_v = Eigen::Map<const {et}>({a}, {a}_n);")
                    cpp_args.append(f"{a}_v")
                    cpp_post += [f'if ({a}_v.size() != {a}_n) throw std::length_error("{v.cname}: size changed");',
                                 f"Eigen::Map<{et}>({a}, {a}_n) = {a}_v;"]
            else:
                todo = f"argument {v.cname}: {self.component_kind(v)}"
        res_v = iface.vars.get(iface.result) if iface.kind == "function" else None
        if res_v is not None and (res_v.rank or res_v.base not in ("real", "integer", "logical")):
            todo = f"result {res_v.cname}: {self.component_kind(res_v)}"
        if res_v is not None:                   # the result: by reference
            r = iface.result
            c_params.append(f"{self.c_type(res_v)}* {r}")
            f_dummies.append(r)
            f_decls.append(f"{self.f_type(res_v)}, intent(out) :: {r}")
            locals_.append(f"{self.f_type(res_v)} :: {r}_c")
            actuals.append(f"{r}_c")
        if has_err:
            c_params.append(f"int* {err}")
            f_dummies.append(err)
            f_decls.append(f"integer(C_INT), intent(out) :: {err}")
            locals_.append(f"integer(C_INT) :: {err}_c")
            actuals.append(f"{err}_c")
        out = ["", f"  !> {bd.spelling}: {cls}::{tramp}"]
        if todo:
            self.report.add("todo", f"wrap-reverse:{bd.name}")
            out.append(f"  ! {TODO}: {todo}")
        kw = "function" if res_v is not None else "subroutine"
        args = [a for a in iface.args if a != iface.result]
        out.append(f"  {kw} {proc}({', '.join(args)})" + (f" result({iface.result})" if res_v is not None else ""))
        for node in decl_nodes:                 # the interface of the deferred binding, the bridge as object
            text = str(node)
            if this in [lname(e.items[0]) for e in items_of(node.items[2])]:
                text = re.sub(rf"(?i)class\s*\(\s*{re.escape(lt.name)}\s*\)", f"class({self.bridge_name(lt)})", text)
            out.append(f"    {text}")
        if res_v is not None and res_v.node is None:        # result declared by the function prefix
            out.append(f"    {self.fortran_decl(res_v)} :: {iface.result}")
        elif res_v is not None and res_v.node not in decl_nodes:
            out.append(f"    {res_v.node}")
        res = {"todo": todo, "tramp": tramp}
        if todo:
            res["fortran"] = out + [f'    error stop "{proc}: {todo}"', f"  end {kw} {proc}"]
            return res
        out += [f"    procedure({aiface}), pointer :: f"] + [f"    {d}" for d in locals_] + [""]
        out += [f"    {x}" for x in ([f"{err} = 0"] if has_err else []) + checks + pre]
        out += [f"    call c_f_procpointer({funptr}, f)", f"    call f({', '.join(actuals)})"]
        if has_err:
            out.append(f"    {err} = {err}_c")
        out += [f"    {x}" for x in post]
        if res_v is not None:
            r = iface.result
            out.append(f"    {r} = logical({r}_c)" if res_v.base == "logical" else f"    {r} = {r}_c")
        res["fortran"] = out + [f"  end {kw} {proc}"]
        res["abstract"] = ["  abstract interface", f"    !> {cls}::{tramp}",
                           f"    subroutine {aiface}({', '.join(f_dummies)}) bind(C)", "      import"] + \
            [f"      {d}" for d in f_decls] + [f"    end subroutine {aiface}", "  end interface", ""]
        res["c_params"] = ", ".join(c_params)
        # C++ declaration / definition: the C++ real type (same type as the C macro, which is not in the .hpp)
        res["cpp_params"] = re.sub(rf"\b{re.escape(cfg['c_real'])}\b", cfg["scalar"], res["c_params"])
        res["c_types"] = ", ".join(re.sub(r"\s*\w+$", "", c) for c in c_params)
        call = f"{cpp_obj}->{bd.spelling}({', '.join(cpp_args)});"
        if res_v is not None:
            call = f"*{iface.result} = {call}"
        on_error = [f"    *{err} = -1;"] if has_err else ["    std::terminate();"]
        res["cpp"] = [f"void {cls}::{tramp}({res['cpp_params']})", "{"] + \
            ([f"  *{err} = 0;"] if has_err else []) + [f"  {x}" for x in cpp_init] + \
            ["  try", "  {"] + [f"    {x}" for x in cpp_pre + [call] + cpp_post] + ["  }",
             "  catch (const std::exception& e)       // must not unwind through Fortran", "  {",
             f'    std::fprintf(stderr, "{cls}::{bd.spelling}: %s\\n", e.what());'] + on_error + \
            ["  }", "  catch (...)", "  {"] + on_error + ["  }", "}", ""]
        return res

    def fortran_decl(self, v):
        """type of a scalar result declared by the function prefix"""
        return f"{v.base}({v.kind})" if v.kind else v.base

    def private_ns(self, cls):
        """<Class>_private (in the namespace of the class)"""
        head, _, last = cls.rpartition("::")
        return f"{head}::{last}_private" if head else f"{cls}_private"

    def use_binding(self, t, names):
        """names of the binding module of t's module (if it is another module); its C header"""
        if t.module is not self.mod:
            self.extra_uses.add(f"  use {t.module.spelling}_c_binding, only: {', '.join(names)}")
            self.c_includes.add(t.module.name)

    def methods_of(self, lt):
        """all bindings (deferred and with a default implementation, all overridable in Fortran)"""
        res = []
        for bd in lt.bindings.values():
            p = self.prog.binding_proc(lt, bd)
            if p is not None and not bd.nopass and p.pass_arg:
                res.append((bd, p))
        return res

    def emit_lines(self, cpp, lines):
        for line in lines:
            cpp.emit((len(line) - len(line.lstrip())) // 2, line.strip()) if line.strip() else cpp.emit(0, "")

    # ------------------------------------------------------------------ listeners
    def bridge_name(self, lt):
        if lt.name in self.cfg["wrap_listeners"]:
            return f"{self.c_class(lt)}_cpp"
        return f"CWrapper{re.sub(r'^Abstract', '', self.c_class(lt))}_type"

    def listener(self, lt, hpp, cpp, fb, typedefs):
        """abstract listener type: C++ class with pure virtual methods and private static <method>_wrapper;
        Fortran bridge type extending it (in the binding) that calls them through function pointers passed
        by the C++ constructor"""
        prog = self.prog
        name = prog.type_name(lt.name)
        cls, ht = name.split("::")[-1], self.c_type_name(lt)
        bridge, ft = self.bridge_name(lt), lt.spelling or lt.name
        create, free = self.c_name(lt, "create"), self.c_name(lt, "free")
        typedefs.append(f"!typedef struct {ht} {ht};")
        methods = []
        for bd, iface in self.methods_of(lt):
            this = iface.pass_arg
            m = self.forward(lt, bd, iface, proc=f"{self.c_class(lt).lower()}_cpp_{bd.name}",
                             aiface=f"{self.c_class(lt)}_{bd.name}_c", funptr=f"{this}%{bd.name}_fn",
                             self_c="void* userData", self_dummy="userData", self_actual=f"{this}%userData",
                             cpp_obj=f"static_cast<{cls}*>(userData)", cls=cls, objects="owner")
            m["fn"], m["proc"] = f"{bd.name}_fn", f"{self.c_class(lt).lower()}_cpp_{bd.name}"
            methods.append((bd, iface, m))
        live = [m for m in methods if not m[2]["todo"]]
        # --- C++ class
        hpp.emit(0, f"namespace {cls}_private")
        hpp.emit(0, "{")
        hpp.emit(0, f"{ht}* toC(const {cls}& l);  //!< the Fortran object of the listener")
        hpp.emit(0, "}")
        hpp.emit(0, "")
        hpp.emit(0, f"class {name}")
        hpp.emit(0, "{")
        hpp.emit(0, "public:")
        hpp.emit(1, f"{cls}();  //!< creates its Fortran object")
        hpp.emit(1, f"virtual ~{cls}();  //!< frees it: unset the listener before")
        hpp.emit(1, f"{cls}(const {cls}&) = delete;  //!< the address is known to Fortran")
        hpp.emit(1, f"{cls}& operator=(const {cls}&) = delete;")
        self.pure_virtuals(hpp, methods)
        hpp.emit(0, "")
        hpp.emit(0, "private:")
        for bd, iface, m in live:
            hpp.emit(1, f"//! internal wrapper for {bd.spelling}")
            hpp.emit(1, f"static void {m['tramp']}({m['cpp_params']});")
        hpp.emit(1, f"{ht}* fortran_ = nullptr;  //!< the Fortran object ({bridge}) calling this object")
        hpp.emit(1, f"friend {ht}* {cls}_private::toC(const {cls}& l);")
        hpp.emit(0, "};")
        hpp.emit(0, "")
        fns = "".join(f", &{cls}::{m['tramp']}" for _, _, m in live)
        self.emit_lines(cpp, [f"{name}::{cls}() : fortran_({create}(this{fns}))", "{", "}", "",
                              f"{name}::~{cls}()", "{", f"  {free}(fortran_);", "}", "",
                              f"{ht}* {cls}_private::toC(const {cls}& l)", "{", "  return l.fortran_;", "}", ""] +
                        [x for _, _, m in live for x in m["cpp"]])
        self.default_methods(cpp, methods)
        self.needs_cstdio = True
        # --- Fortran: interfaces of the trampolines, bridge type, its procedures, create / free
        for _, _, m in live:
            self.bridges += m["abstract"]
        self.bridges += [f"  !> {ft} implemented in C++: calls the C++ object through function pointers",
                         f"  type, extends({ft}), public :: {bridge}"] + \
            [f"    type(C_FUNPTR) :: {m['fn']} = C_NULL_FUNPTR   !< {cls}::{m['tramp']}" for _, _, m in live] + \
            ["    type(C_PTR) :: user = C_NULL_PTR     !< C++ object of the owner (set_listener)",
             "    class(*), pointer :: owner => null() !< Fortran object of the owner",
             "    type(C_PTR) :: userData = C_NULL_PTR !< the C++ listener",
             "  contains"] + [f"    procedure :: {bd.spelling} => {m['proc']}" for bd, _, m in methods] + \
            [f"  end type {bridge}", ""]
        for _, _, m in methods:
            fb += m["fortran"]
        if not any(line.startswith("  logical function listener_same") for line in fb):
            fb += ["", "  !> is x the Fortran object of p", "  logical function listener_same(p, x)",
                   "    class(*), pointer, intent(in) :: p", "    class(*), target, intent(in) :: x",
                   "    listener_same = associated(p, x)", "  end function listener_same"]
        c_fns = "".join(f", void (*{m['fn']})({m['c_types']})" for _, _, m in live)
        f_fns = "".join(f", {m['fn']}" for _, _, m in live)
        fb += ["", "!@c_declaration", f"!  {ht}* {create}(void* userData{c_fns});", f"!  void {free}({ht}* listener);",
               "!@end_c_declaration", "  !> Fortran object of the C++ listener (with its trampolines)",
               f"  function {create}(userData{f_fns}) result(listener) bind(C, name=\"{create}\")"] + \
            self.dllexport(create) + \
            ["    type(C_PTR), value :: userData"] + [f"    type(C_FUNPTR), value :: {m['fn']}" for _, _, m in live] + \
            ["    type(C_PTR) :: listener", f"    type({bridge}), pointer :: b", "", "    allocate(b)",
             "    b%userData = userData"] + [f"    b%{m['fn']} = {m['fn']}" for _, _, m in live] + \
            ["    listener = c_loc(b)", f"  end function {create}", "",
             f"  subroutine {free}(listener) bind(C, name=\"{free}\")"] + self.dllexport(free) + \
            ["    type(C_PTR), value :: listener", f"    type({bridge}), pointer :: b", "",
             "    if (.not. c_associated(listener)) return", "    call c_f_pointer(listener, b)", "    deallocate(b)",
             f"  end subroutine {free}"]
        self.report.add("translated", "wrapped-listener")

    def pure_virtuals(self, hpp, methods):
        """deferred: pure virtual; with a default implementation: virtual (definition: default_methods)"""
        for bd, iface, m in methods:
            ret, _, params, const = self.signature(self.tr(iface), iface, True)
            hpp.emit(0, "")
            self.doc(hpp, iface, 1)
            hpp.emit(1, f"virtual {ret} {bd.spelling}({params}){const}" + (" = 0;" if bd.deferred else ";"))

    def default_methods(self, cpp, methods):
        """translated Fortran default implementations of the non-deferred bindings"""
        for bd, p, m in methods:
            if not bd.deferred:
                self.emit_proc(cpp, p, 0)
                cpp.emit(0, "")
                self.report.add("translated", "reverse-default-method")

    # ------------------------------------------------------------------ reverse types / factories
    def reverse_types(self):
        return set(self.cfg["wrap_reverse"]) | set(self.cfg["wrap_reverse_factories"])

    def reverse_type(self, lt, hpp, cpp, fb, typedefs):
        """abstract type implemented in C++: C struct project_<Type> in each C++ object (function pointers,
        destroy, p_impl, userData); Fortran CWrapper<Thing>_type (owns the C++ object; factories: registered
        in the Fortran factory registration by the C++ constructor)"""
        prog, cfg = self.prog, self.cfg
        name = prog.type_name(lt.name)
        cls, ht, cc = name.split("::")[-1], self.c_type_name(lt), self.c_class(lt)
        st, bridge, ft = f"{cc}_c", self.bridge_name(lt), lt.spelling or lt.name
        holder = f"{cc}Holder_type"
        factory = cfg["wrap_reverse_factories"].get(lt.name)
        methods = []
        for bd, iface in self.methods_of(lt):
            this = iface.pass_arg
            proc = f"{bridge[:-5].lower()}_{bd.name}"
            m = self.forward(lt, bd, iface, proc=proc, aiface=f"{cc}_{bd.name}_c", funptr=f"{this}%c%{bd.name}",
                             self_c=f"struct {ht}* self", self_dummy="self", self_actual=f"c_loc({this}%c)",
                             cpp_obj=f"static_cast<{cls}*>(self->userData)", cls=cls, objects="handle")
            m["proc"] = proc
            methods.append((bd, iface, m))
        live = [m for m in methods if not m[2]["todo"]]
        # --- C struct (in the c_declaration block; struct ... for its own type)
        members = [f"  void (*{bd.name})({m['c_types']}) = nullptr;" for bd, _, m in live]
        if factory is None:
            members.append(f"  void (*destroy)(struct {ht}*) = nullptr;")
        members += ["  void* p_impl = nullptr;", "  void* userData = nullptr;"]
        typedefs += [f"!typedef struct {ht}", "!{"] + [f"!{x}" for x in members] + [f"!}} {ht};", "!"]
        # --- C++ class
        hpp.emit(0, f"namespace {cls}_private")
        hpp.emit(0, "{")
        hpp.emit(0, f"{ht}* toC(const {cls}& x);  //!< the C struct of x")
        hpp.emit(0, "}")
        hpp.emit(0, "")
        hpp.emit(0, f"class {name}")
        hpp.emit(0, "{")
        hpp.emit(0, "public:")
        if factory is None:
            hpp.emit(1, f"{cls}();")
        else:
            hpp.emit(1, f"explicit {cls}(const std::string& name);  //!< registers the factory (Fortran)")
        hpp.emit(1, f"virtual ~{cls}();" + ("" if factory is None else "  //!< unregisters it"))
        hpp.emit(1, f"{cls}(const {cls}&) = delete;  //!< the address is known to C / Fortran")
        hpp.emit(1, f"{cls}& operator=(const {cls}&) = delete;")
        self.pure_virtuals(hpp, methods)
        hpp.emit(0, "")
        hpp.emit(0, "private:")
        for bd, iface, m in live:
            hpp.emit(1, f"//! internal wrapper for {bd.spelling}")
            hpp.emit(1, f"static void {m['tramp']}({m['cpp_params']});")
        if factory is None:
            hpp.emit(1, "//! internal wrapper for the destruction (the object is owned by the caller)")
            hpp.emit(1, f"static void destroy_wrapper({ht}* self);")
        hpp.emit(1, f"std::unique_ptr<{ht}> c_;  //!< the C struct of this object")
        if factory is not None:
            hpp.emit(1, "std::string name_;  //!< registered name")
        hpp.emit(1, f"friend {ht}* {cls}_private::toC(const {cls}& x);")
        hpp.emit(0, "};")
        hpp.emit(0, "")
        init = [f"  c_->{bd.name} = &{m['tramp']};" for bd, _, m in live] + \
            (["  c_->destroy = &destroy_wrapper;"] if factory is None else []) + ["  c_->userData = this;"]
        reg, unreg = self.c_name(lt, "register"), self.c_name(lt, "unregister")
        if factory is None:
            lines = [f"{name}::{cls}() : c_(std::make_unique<{ht}>())", "{"] + init + ["}", "",
                     f"{name}::~{cls}() = default;", "",
                     f"void {cls}::destroy_wrapper({ht}* self)", "{",
                     f"  std::unique_ptr<{cls}> owner(static_cast<{cls}*>(self->userData));  // deletes it", "}", ""]
        else:
            lines = [f"{name}::{cls}(const std::string& name) : c_(std::make_unique<{ht}>()), name_(name)", "{"] + \
                init + [f"  {reg}(c_.get(), name_.c_str());", "}", "",
                        f"{name}::~{cls}()", "{", f"  {unreg}(name_.c_str());", "}", ""]
        lines += [f"{ht}* {cls}_private::toC(const {cls}& x)", "{", "  return x.c_.get();", "}", ""]
        self.emit_lines(cpp, lines + [x for _, _, m in live for x in m["cpp"]])
        self.default_methods(cpp, methods)
        self.needs_cstdio = True
        # --- Fortran: interfaces, bind(C) struct, holder (target of p_impl), wrapper type
        for _, _, m in live:
            self.bridges += m["abstract"]
        if factory is None:
            self.bridges += ["  abstract interface", f"    !> {cls}::destroy_wrapper",
                             f"    subroutine {cc}_destroy_c(self) bind(C)", "      import",
                             "      type(C_PTR), value :: self", f"    end subroutine {cc}_destroy_c", "  end interface", ""]
        self.bridges += [f"  !> C struct {ht}: functions of the C++ object", f"  type, bind(C), public :: {st}"] + \
            [f"    type(C_FUNPTR) :: {bd.name} = C_NULL_FUNPTR" for bd, _, m in live] + \
            (["    type(C_FUNPTR) :: destroy = C_NULL_FUNPTR"] if factory is None else []) + \
            ["    type(C_PTR) :: p_impl = C_NULL_PTR    !< the Fortran object (holder)",
             "    type(C_PTR) :: userData = C_NULL_PTR  !< the C++ object", f"  end type {st}", "",
             f"  !> target of p_impl: the Fortran object of a C++ object", f"  type, public :: {holder}",
             f"    class({ft}), pointer :: ptr => null()", f"  end type {holder}", "",
             f"  !> {ft} implemented in C++" + (" (owns the C++ object)" if factory is None else ""),
             f"  type, extends({ft}), public :: {bridge}", f"    type({st}), pointer :: c => null()",
             f"    type({holder}), pointer :: holder => null()", "  contains"] + \
            [f"    procedure :: {bd.spelling} => {m['proc']}" for bd, _, m in methods] + \
            ([f"    final :: {bridge[:-5].lower()}_final"] if factory is None else []) + [f"  end type {bridge}", ""]
        for _, _, m in methods:
            fb += m["fortran"]
        fin, wrap = f"{bridge[:-5].lower()}_final", self.c_name(lt, "wrap")
        if factory is None:
            fb += ["", "  !> deletes the C++ object", f"  subroutine {fin}(this)", f"    type({bridge}), intent(inout) :: this",
                   f"    procedure({cc}_destroy_c), pointer :: f", "",
                   "    if (associated(this%c)) then", "      this%c%p_impl = C_NULL_PTR",
                   "      if (c_associated(this%c%destroy)) then", "        call c_f_procpointer(this%c%destroy, f)",
                   "        call f(c_loc(this%c))", "      end if", "    end if",
                   "    if (associated(this%holder)) deallocate(this%holder)", f"  end subroutine {fin}", "",
                   f"  !> x: the Fortran object of the C++ object c (owns it)", f"  subroutine {wrap}(c, x)",
                   "    type(C_PTR), intent(in) :: c", f"    class({ft}), allocatable, target, intent(out) :: x", "",
                   f"    allocate({bridge} :: x)", "    select type (x)", f"    type is ({bridge})",
                   "      call c_f_pointer(c, x%c)", "      allocate(x%holder)", "      x%holder%ptr => x",
                   "      x%c%p_impl = c_loc(x%holder)", "    end select", f"  end subroutine {wrap}"]
        else:                                   # registration of the C++ factory under a name
            name_in = cfg["wrap_string_in"]
            self.extra_uses.add(f"  use {name_in[0]}, only: {name_in[1]}")
            fb += ["", "!@c_declaration", f"!  void {reg}({ht}* factory, const char* name);",
                   f"!  void {unreg}(const char* name);", "!@end_c_declaration",
                   "  !> registers the C++ factory under name (Fortran factory registration)",
                   f"  subroutine {reg}(factory, name) bind(C, name=\"{reg}\")"] + self.dllexport(reg) + \
                ["    type(C_PTR), value :: factory, name", f"    type({bridge}) :: f", "",
                 "    call c_f_pointer(factory, f%c)",
                 f"    call {factory['register']}({name_in[1]}(name), f)      ! stores a copy",
                 f"  end subroutine {reg}", "",
                 f"  subroutine {unreg}(name) bind(C, name=\"{unreg}\")"] + self.dllexport(unreg) + \
                ["    type(C_PTR), value :: name", "", f"    call {factory['unregister']}({name_in[1]}(name))",
                 f"  end subroutine {unreg}"]
        self.report.add("translated", "wrapped-reverse")
