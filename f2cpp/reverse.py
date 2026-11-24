"""Abstract Fortran types implemented in C++ (mixin of WrapEmitter).

Common mechanism: every deferred binding of the abstract type becomes a function pointer of a C type;
a Fortran type in the binding module extends the abstract type and calls the pointers; the C++ class
has the pure virtual methods and private static <method>_wrapper functions whose addresses are put into
the pointers (the Fortran library references no symbol of the C++ library).

- listeners (wrap_listeners): owned by C++; the Fortran object (<Listener>_cpp) is created by the C++
  constructor, set on a wrapped object with set_listener; object arguments are the owner
- reverse types (wrap_reverse): C struct project_<Type> per C++ object (function pointers, destroy,
  userData); Fortran CWrapper<Thing>_type owns the C++ object (final -> destroy); the other direction: a
  Fortran implementation behind such a struct (create_<Thing>_C_from_Fortran, wrap_<method>) is used in C++
  as Wrapper<Thing>
- reverse factories (wrap_reverse_factories): as reverse types, registered by the C++ constructor under
  a name in the existing Fortran factory registration

Possible simplification: listeners as reverse types without destroy (C struct in the C++ object,
set_listener passes the struct, Fortran wraps it non-owning); one mechanism with an ownership flag and the
owner rule for object arguments.
"""
import re

from .model import lname, items_of
from .translate import TODO


def fill(stmt, obj):
    """a statement of to_f for the object obj: "@" is the object; "#ALLOC#" statements are complete"""
    return stmt[7:].replace("@", obj) if stmt.startswith("#ALLOC#") else f"{obj}%{stmt.replace('@', obj)}"


KEYWORDS = re.compile(r"\b(REAL|INTEGER|LOGICAL|CHARACTER|COMPLEX|DOUBLE\s+PRECISION|TYPE|CLASS|PROCEDURE|"
                      r"INTENT|IN|OUT|INOUT|DIMENSION|ALLOCATABLE|POINTER|TARGET|CONTIGUOUS|OPTIONAL|VALUE|"
                      r"PARAMETER|SAVE|KIND|LEN|NULL)\b(?=\s*[(,:=)]|\s*$|\s+::)")


def lower_keywords(text):
    """declarations printed by fparser (upper case keywords) in the project's style: lower case keywords, names
    as declared (not inside strings)"""
    parts = re.split(r"""('[^']*'|"[^"]*")""", text)
    return "".join(pt if pt[:1] in "'\"" else KEYWORDS.sub(lambda m: m.group(1).lower(), pt) for pt in parts)


FORTRAN_INTRINSICS = set("""abs all allocated any associated ceiling count cpu_time dble dot_product epsilon exp
floor huge index int kind lbound len len_trim log log10 matmul max maxloc maxval merge min minloc minval mod modulo
move_alloc nint norm2 null pack present product random_number real reshape scan shape sign sin size sqrt sum
system_clock tiny transfer transpose trim ubound""".split())


def cpp_const(v, k):
    """C++: constant of the struct c (C strings: std::string, NULL: empty)"""
    return f'std::string(c.{v.cname} != nullptr ? c.{v.cname} : "")' if k == "string" else f"c.{v.cname}"


def fspell(v):
    """Fortran spelling of a variable (cname without the escape of C++ keywords)"""
    return v.cname if v.cname.lower() == v.name else v.name


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
        if objects == "handle" and iface.vars.get(this) is not None and iface.vars[this].intent == "in":
            self_c = "const " + self_c          # intent(in) object: const struct pointer (Fortran: type(C_PTR))
            cpp_obj = cpp_obj.replace(f"static_cast<{cls}*>", f"static_cast<const {cls}*>")   # const method
        c_params, f_dummies, f_decls = [self_c], [self_dummy], [f"type(C_PTR), value :: {self_dummy}"]
        locals_, checks, pre, post, actuals = [], [], [], [], [self_actual]
        cpp_init, cpp_pre, cpp_args, cpp_post = [], [], [], []
        # the C++ object as a named variable before the try block (debugging): AbstractThing* thing = ...
        obj_var = re.sub(r"^Abstract", "", cls)
        obj_var = obj_var[:1].lower() + obj_var[1:]
        if obj_var in {v.cname for v in iface.vars.values()}:
            obj_var += "_obj"
        const_obj = "const " if f"static_cast<const {cls}*>" in cpp_obj else ""
        cpp_init.append(f"{const_obj}{cls}* {obj_var} = {cpp_obj};")
        cpp_obj = obj_var
        todo = None
        fail = self.fail_lines(err) if has_err else [f'error stop "{proc}: called for another object than its owner"']
        for a in iface.args:
            v = iface.vars[a]
            if v.node is not None and v.node not in decl_nodes:
                decl_nodes.append(v.node)
            if a == this or (has_err and a == err) or a == iface.result:
                continue
            a = fspell(v)                       # the declared spelling (also the C / C++ parameter names)
            intent = v.intent or "inout"
            rev = v.tname if v.base == "type" else None
            if v.base == "type" and v.tname == "*":   # C++ data (std::any) in a Fortran class(*)
                ca = self.cpp_any() if objects == "handle" and not v.rank else None
                if ca and intent == "out" and v.has("allocatable"):     # new C++ data, owned by Fortran
                    c_params.append(f"{ca['ht']}** {a}")
                    f_dummies.append(a)
                    f_decls.append(f"type(C_PTR), intent(out) :: {a}")
                    locals_.append(f"type(C_PTR) :: {a}_p")
                    pre.append(f"{a}_p = C_NULL_PTR")
                    actuals.append(f"{a}_p")
                    post += [f"if (c_associated({a}_p)) then      ! NULL: unallocated", f"  allocate({ca['fw']} :: {a})",
                             f"  select type ({a})", f"  type is ({ca['fw']})", f"    call c_f_pointer({a}_p, {a}%c)",
                             "  end select", "end if"]
                    cpp_init.append(f"*{a} = nullptr;")
                    cpp_pre.append(f"std::any {a}_v;")
                    cpp_args.append(f"{a}_v")
                    cpp_post.append(f"*{a} = any_to_c(std::move({a}_v));")
                elif ca and intent == "in":     # only C++ data (created by this module's C++ objects)
                    c_params.append(f"const {ca['ht']}* {a}")
                    f_dummies.append(a)
                    f_decls.append(f"type(C_PTR), value :: {a}")
                    locals_.append(f"type(C_PTR) :: {a}_p")
                    stop = [f"  {x}" for x in self.fail_lines(err)] if has_err else \
                        [f'  error stop "{proc}: {v.cname} is not C++ data of this module"']
                    sel = [f"select type ({a})", f"type is ({ca['fw']})", f"  if (associated({a}%c)) {a}_p = c_loc({a}%c)",
                           "class default"] + stop + ["end select"]
                    checks += [f"{a}_p = C_NULL_PTR"] + ([f"if (allocated({a})) then"] + [f"  {x}" for x in sel] +
                                                         ["end if"] if v.has("allocatable") else sel)
                    actuals.append(f"{a}_p")
                    cpp_args.append(f"any_from_c({a})")
                else:
                    todo = f"argument {v.cname}: class(*) (C++ data: allocatable intent(out) / intent(in) scalars)"
                continue
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
                if rt.module is not self.mod:   # its c_wrapper module (and its C header for the struct)
                    self.extra_uses.add(f"  use {self.cwrapper_module(rt.module)}, only: {self.c_name(rt, 'wrap')}")
                    self.c_includes.add(rt.module.name)
                cpp_init.append(f"*{a} = nullptr;")
                cpp_pre.append(f"std::unique_ptr<{rcls}> {a}_u;")
                cpp_args.append(f"{a}_u")
                cpp_post += [f"*{a} = {a}_u ? {self.private_ns(rcls)}::toC(*{a}_u) : nullptr;",
                             f"{a}_u.release(); // NOLINT(bugprone-unused-return-value)"]
            elif v.base == "type" and not v.rank and objects == "owner":     # listener: only the owner
                c_params.append(f"void* {a}")
                f_dummies.append(a)
                f_decls.append(f"type(C_PTR), value :: {a}")
                checks += [f"if (.not. listener_same({this}%owner, {a})) then"] + [f"  {x}" for x in fail] + ["end if"]
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
            elif v.base in ("real", "integer") and not v.rank:      # same kind as the C type (else: compile error)
                f_dummies.append(a)
                actuals.append(a)
                if intent == "in":
                    c_params.append(f"{self.c_type(v)} {a}")
                    f_decls.append(f"{self.f_type(v)}, value :: {a}")
                    cpp_args.append(a)
                else:                           # by reference
                    c_params.append(f"{self.c_type(v)}* {a}")
                    f_decls.append(f"{self.f_type(v)}, intent({intent}) :: {a}")
                    cpp_args.append(f"*{a}")
            elif v.base == "logical" and not v.rank:      # C bool: a copy
                f_dummies.append(a)
                locals_.append(f"{self.f_type(v)} :: {a}_c")
                actuals.append(f"{a}_c")
                if intent == "in":
                    c_params.append(f"{self.c_type(v)} {a}")
                    f_decls.append(f"{self.f_type(v)}, value :: {a}")
                    pre.append(f"{a}_c = logical({a}, C_BOOL)")
                    cpp_args.append(a)
                else:                           # by reference, copied back
                    c_params.append(f"{self.c_type(v)}* {a}")
                    f_decls.append(f"{self.f_type(v)}, intent(inout) :: {a}")
                    if intent == "inout":
                        pre.append(f"{a}_c = logical({a}, C_BOOL)")
                    post.append(f"{a} = logical({a}_c)")
                    cpp_args.append(f"*{a}")
            elif v.base in ("real", "integer") and v.rank == 1 and not v.has("allocatable") and not v.has("pointer"):
                const = "const " if intent == "in" else ""
                known = self.known_size(lt, iface, v.name) if objects == "handle" else None
                if known:                       # size known on both sides: checked here, no size argument
                    n = known[0].replace("obj%", f"{this}%")
                    c_params.append(f"{const}{self.c_type(v)}* {a}")
                    f_dummies.append(a)
                    f_decls.append(f"{self.f_type(v)}, intent({intent}) :: {a}(*)")
                    chk = cfg["wrap_chk_argument"]
                    if has_err and chk:
                        checks.append(f"{chk[0]}(size({a}) == {n}, {err})")
                    else:
                        checks += [f"if (size({a}) /= {n}) then"] + \
                            ([f"  {x}" for x in self.fail_lines(err)] if has_err else
                             [f'  error stop "{proc}: size of {v.cname} /= {n}"']) + ["end if"]
                    cpp_init.append(f"const int {a}_n = {known[1].replace('{OBJ}', cpp_obj + '->')};")
                else:
                    c_params += [f"{const}{self.c_type(v)}* {a}", f"int {a}_n"]
                    f_dummies += [a, f"{a}_n"]
                    f_decls += [f"{self.f_type(v)}, intent({intent}) :: {a}(*)", f"integer(C_INT), value :: {a}_n"]
                # passed directly (same kind as the C type, else: compile error; contiguous copy by the compiler)
                actuals += [a] + ([] if known else [f"int(size({a}), C_INT)"])
                et = tr.eigen_type(v, 1)
                if intent == "in":
                    cpp_args.append(f"{et}(Eigen::Map<const {et}>({a}, {a}_n))")
                else:                           # a C++ vector, copied back (the size must not change)
                    cpp_pre.append(f"{et} {a}_v({a}_n);" if intent == "out" else
                                   f"{et} {a}_v = Eigen::Map<const {et}>({a}, {a}_n);")
                    cpp_args.append(f"{a}_v")
                    cpp_post += [f"if( {a}_v.size() != {a}_n )",
                                 f'  throw std::length_error("{cls}::{bd.spelling}: dimension of parameter {v.cname} changed!");',
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
            if res_v.base == "logical":
                locals_.append(f"{self.f_type(res_v)} :: {r}_c")
                actuals.append(f"{r}_c")
            else:
                actuals.append(r)
        if has_err:
            c_params.append(f"int* {err}")
            f_dummies.append(err)
            f_decls.append(f"integer(C_INT), intent(out) :: {err}")
            actuals.append(err)
        out = ["", f"  !> {bd.spelling}: {cls}::{tramp}"]
        if todo:
            self.report.add("todo", f"wrap-reverse:{bd.name}")
            out.append(f"  ! {TODO}: {todo}")
        kw = "function" if res_v is not None else "subroutine"
        args = [fspell(iface.vars[a]) if a in iface.vars else a for a in iface.args if a != iface.result]
        out.append(f"  {kw} {proc}({', '.join(args)})" + (f" result({iface.result})" if res_v is not None else ""))
        for node in decl_nodes:                 # the interface of the deferred binding, the bridge as object
            text = lower_keywords(str(node))
            names = [lname(e.items[0]) for e in items_of(node.items[2])]
            if this in names:
                text = re.sub(rf"(?i)class\s*\(\s*{re.escape(lt.name)}\s*\)", f"class({self.bridge_name(lt)})", text)
            docs = [line for ns, lines in iface.param_docs if set(ns) & set(names) for line in lines]
            out.append(f"    {text}" + (f"  !< {docs[0]}" if docs else ""))     # the original comments
            out += [f"    !< {line}" for line in docs[1:]]
        if res_v is not None and res_v.node is None:        # result declared by the function prefix
            out.append(f"    {self.fortran_decl(res_v)} :: {iface.result}")
        elif res_v is not None and res_v.node not in decl_nodes:
            out.append(f"    {lower_keywords(str(res_v.node))}")
        res = {"todo": todo, "tramp": tramp}
        if todo:
            res["fortran"] = out + [f'    error stop "{proc}: {todo}"', f"  end {kw} {proc}"]
            return res
        out += [f"    procedure({aiface}), pointer :: f"] + [f"    {d}" for d in locals_] + [""]
        out += [f"    {x}" for x in ([f"{err} = 0"] if has_err else []) + checks + pre]
        call = f"call f({', '.join(actuals)})"
        if has_err and cfg["wrap_chk_ierr"]:    # PROJECT_CHK_IERR(call f(..., ierr), ierr)
            call = f"{cfg['wrap_chk_ierr'][0]}({call}, {err})"
        out += [f"    call c_f_procpointer({funptr}, f)", f"    {call}"]
        out += [f"    {x}" for x in post]
        if res_v is not None and res_v.base == "logical":
            out.append(f"    {iface.result} = logical({iface.result}_c)")
        res["fortran"] = out + [f"  end {kw} {proc}"]
        res["c_params"] = ", ".join(c_params)
        # C++ declaration / definition: the C++ real type (same type as the C macro, which is not in the .hpp)
        res["cpp_params"] = re.sub(rf"\b{re.escape(cfg['c_real'])}\b", cfg["scalar"], res["c_params"])
        res["c_types"] = ", ".join(re.sub(r"(\(\*+)\w+\)", r"\1)", c) if "(*" in c else re.sub(r"\s*\w+$", "", c)
                                   for c in c_params)       # parameter types (function pointers: without name)
        res["abstract"] = self.abstract_interface(aiface, res["c_types"], f"{cls}::{tramp}", f_dummies, f_decls)
        call = f"{cpp_obj}->{bd.spelling}({', '.join(cpp_args)});"
        if res_v is not None:
            call = f"*{iface.result} = {call}"
        on_error = [f"    *{err} = -1;"] if has_err else ["    std::terminate();"]
        log, msg = cfg["log_error"], f"encountered exception in {bd.spelling}"
        res["cpp"] = [f"void {cls}::{tramp}({res['cpp_params']}) noexcept", "{"] + \
            ([f"  *{err} = 0;"] if has_err else []) + [f"  {x}" for x in cpp_init] + \
            ["  try", "  {"] + [f"    {x}" for x in cpp_pre + [call] + cpp_post] + ["  }",
             "  catch (const std::exception& e)       // must not unwind through Fortran", "  {",
             f'    {log}("{cls}", ("{msg}: " + std::string(e.what())).c_str());' if log else
             f'    std::fprintf(stderr, "{cls}: {msg}: %s\\n", e.what());'] + on_error + \
            ["  }", "  catch (...)", "  {",
             f'    {log}("{cls}", "{msg}");' if log else f'    std::fprintf(stderr, "{cls}: {msg}\\n");'] + \
            on_error + ["  }", "}", ""]
        return res

    def abstract_interface(self, name, c_types, doc, dummies, decls):
        """abstract interface (bind(C)) with the C typedef of its function pointer (same name)"""
        c_types = re.sub(r"\bstruct (\w+)\*", r"\1*", c_types)        # the forward declaration comes first
        return ["!@c_declaration", f"!typedef void (*{name})({c_types});", "!@end_c_declaration",
                "  abstract interface", f"    !> {doc}", f"    subroutine {name}({', '.join(dummies)}) bind(C)",
                f"      import {', '.join(self.imported(decls))}",
                "      implicit none"] + [f"      {d}" for d in decls] + \
            ["    end subroutine", "  end interface", ""]

    @staticmethod
    def imported(decls):
        """kinds / types the declarations of an interface body refer to (explicit import list)"""
        names = []
        for d in decls:
            for n in re.findall(r"(?i)\b(?:type|class|integer|real|logical|complex|character)\s*\(\s*(?:kind\s*=\s*)?"
                                r"([A-Za-z_]\w*)", d):
                if n not in names:
                    names.append(n)
        return names

    def known_size(self, lt, iface, a):
        """size of the rank-1 array dummy a of a reverse-type method that both sides know: an expression of
        integer literals and constants (wrap_constants) of the object, from wrap_sizes, a size check or the
        documentation ("dimension (this\\%n)") -> (Fortran code with "obj%", C++ code with "{OBJ}") or None"""
        consts = {v.name: v for v, _ in self.reverse_constants(lt)[0]}
        if not consts:
            return None
        try:
            text = self.fortran_size(iface, self.array_sizes(iface, lt, doc=True), a, None)
        except Exception:                       # no parse tree to analyse (e.g. external interface)
            return None
        if text is None:
            return None
        names = [n.lower() for n in re.findall(r"obj%(\w+)", text)]
        rest = re.sub(r"obj%\w+", "1", text)
        if not names or any(n not in consts for n in names) or "**" in rest or \
                not re.fullmatch(r"[\d\s+\-*/()]*", rest):
            return None
        cpp = re.sub(r"obj%(\w+)", lambda m: "{OBJ}" + consts[m.group(1).lower()].cname + "()", text)
        return text, cpp

    def fail_lines(self, err):
        """Fortran statements for an error with error flag (and return): wrap_fail or ierr = -1"""
        f = self.cfg["wrap_fail"]
        f = f[0] if isinstance(f, list) else f
        return [f.format(ierr=err)] if f else [f"{err} = -1", "return"]

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

    # ------------------------------------------------------------------ class(*) <-> std::any
    def cpp_any(self):
        """names of the class(*) / std::any data passed between C++ and Fortran in this module (declared on first
        use): C struct <prefix>_<module>_Any (destroy, userData: std::any created by C++ or AnyHolder_type created
        by Fortran; the creator unpacks it), bind(C) Any_c; C++ data in Fortran: CWrapperAny_type (c_wrapper
        module, private, owns it: move_alloc only), C++ any_to_c / any_from_c (.cpp)"""
        ca = self.any_struct()
        if not self.cpp_data_used:              # C++ data in Fortran: CWrapperAny_type (c_wrapper module)
            self.cpp_data_used = True
            ht, cw = ca["ht"], self.cw
            cw["binding_names"] += ["Any_c", f"{ht}_destroy"]
            cw["spec"] += ["  !> C++ data (std::any) in a class(*): owns it (move_alloc only, never copy)",
                           "  type :: CWrapperAny_type", "    type(Any_c), pointer :: c => null()", "  contains",
                           "    final :: cwrapperany_final", "  end type CWrapperAny_type", ""]
            cw["procs"] += ["", "  !> deletes the C++ data", "  subroutine cwrapperany_final(this)",
                            "    type(CWrapperAny_type), intent(inout) :: this",
                            f"    procedure({ht}_destroy), pointer :: f", "",
                            "    if (.not. associated(this%c)) return",
                            "    if (c_associated(this%c%destroy)) then", "      call c_f_procpointer(this%c%destroy, f)",
                            "      call f(c_loc(this%c))", "    end if", "  end subroutine cwrapperany_final"]
        return ca

    def any_struct(self):
        """the C struct <prefix>_<module>_Any (binding, once): destroy, userData"""
        if self.cppany is None:
            ht = f"{self.module_c_name()}_Any"
            self.cppany = {"ht": ht, "st": "Any_c", "fw": "CWrapperAny_type", "destroy": f"{ht}_destroy"}
            self.any_typedefs.append(f"!typedef struct {ht} {ht};")
            self.bridges += self.abstract_interface(f"{ht}_destroy", f"{ht}*", "destroy of C++ data (std::any)",
                                                    ["this"], ["type(C_PTR), value :: this"])
            self.bridges += ["!@c_declaration", f"!struct {ht}", "!{", f"!  {ht}_destroy destroy = nullptr;",
                             "!  void* userData = nullptr;", "!};", "!@end_c_declaration",
                             f"  !> C struct {ht}: class(*) / std::any data owned by the other side "
                             "(created by C++ or Fortran)",
                             "  type, bind(C), public :: Any_c",
                             "    type(C_FUNPTR) :: destroy = C_NULL_FUNPTR",
                             "    type(C_PTR) :: userData = C_NULL_PTR  !< std::any (C++) / AnyHolder_type (Fortran)",
                             "  end type Any_c", ""]
        return self.cppany

    def string_to_c(self, v):
        """create_<Thing>_C_from_Fortran: a C string copy of the character constant (freed by destroy_...);
        copy(fStr, ierr) result(cStr): character(kind=C_CHAR), pointer, contiguous :: cStr(:)"""
        n, copy = v.cname, self.c_string_names()[0]
        src = f"fthing%ptr%{n}" if v.has("allocatable") else f"trim(fthing%ptr%{n})"
        chk = self.cfg["wrap_chk_ierr"]
        body = [f"{chk[0]}({n}_c => {copy}({src}, ierr), ierr)"] if chk else \
            [f"{n}_c => {copy}({src}, ierr)", "if (ierr /= 0) return"]
        body.append(f"cthing%{n} = c_loc({n}_c)")
        if v.has("allocatable"):
            body = [f"if (allocated(fthing%ptr%{n})) then"] + [f"  {x}" for x in body] + ["end if"]
        return [f"    {x}" for x in body]

    def c_string_names(self):
        """(copy, delete) of C strings: the project's helpers (wrap_string_out) or generated ones"""
        out = self.cfg["wrap_string_out"]
        return (out[1], out[2]) if out else ("f2cpp_copy_string", "f2cpp_delete_string")

    def c_string_helpers(self, fb):
        """Fortran (binding, once): C strings allocated / freed in Fortran (generated unless wrap_string_out)"""
        if getattr(self, "c_string_done", False):
            return
        self.c_string_done = True
        out = self.cfg["wrap_string_out"]
        if out:
            self.extra_uses.add(f"  use {out[0]}, only: {out[1]}, {out[2]}")
            return
        fb += ["", "  !> a new C string (NUL-terminated copy of fStr), deleted by f2cpp_delete_string",
               "  function f2cpp_copy_string(fStr, ierr) result(cStr)", "    character(len=*), intent(in) :: fStr",
               "    integer, intent(out) :: ierr", "    character(kind=C_CHAR), pointer, contiguous :: cStr(:)",
               "    integer :: i", "", "    allocate(cStr(len(fStr) + 1), stat=ierr)", "    if (ierr /= 0) return",
               "    do i = 1, len(fStr)", "      cStr(i) = fStr(i:i)", "    end do",
               "    cStr(len(fStr) + 1) = C_NULL_CHAR", "  end function f2cpp_copy_string", "",
               "  !> deletes a C string of f2cpp_copy_string (NULL: nothing)",
               "  subroutine f2cpp_delete_string(cStr_ptr)", "    type(C_PTR), intent(inout) :: cStr_ptr",
               "    character(kind=C_CHAR), pointer, contiguous :: buf(:)", "    integer :: n", "",
               "    if (.not. c_associated(cStr_ptr)) return", "    call c_f_pointer(cStr_ptr, buf, [huge(n)])",
               "    n = 1", "    do while (buf(n) /= C_NULL_CHAR)", "      n = n + 1", "    end do",
               "    call c_f_pointer(cStr_ptr, buf, [n])      ! the allocated array", "    deallocate(buf)",
               "    cStr_ptr = C_NULL_PTR", "  end subroutine f2cpp_delete_string"]

    def fortran_any_data(self, fb):
        """Fortran (binding, once): holder of Fortran data (class(*)) in the struct for C++, its destroy"""
        if self.fortran_data_used:
            return
        self.fortran_data_used = True
        st = self.any_struct()["st"]
        self.bridges += ["  !> Fortran data (class(*)) for C++ (userData of the struct, created by Fortran)",
                         "  type :: AnyHolder_type", "    class(*), allocatable :: p", "  end type AnyHolder_type", ""]
        fb += ["", "  !> x (moved) into a new struct for C++ (owned by C++; NULL: unallocated)",
               "  function f2cpp_any_new(x) result(c)", "    class(*), allocatable, intent(inout) :: x",
               "    type(C_PTR) :: c", f"    type({st}), pointer :: cs", "    type(AnyHolder_type), pointer :: hp", "",
               "    c = C_NULL_PTR", "    if (.not. allocated(x)) return", "    allocate(hp)", "    call move_alloc(x, hp%p)",
               "    allocate(cs)", "    cs%userData = c_loc(hp)", "    cs%destroy = c_funloc(f2cpp_any_destroy)",
               "    c = c_loc(cs)", "  end function f2cpp_any_new", "",
               "  !> deletes the struct and the Fortran data (called from C++)",
               "  subroutine f2cpp_any_destroy(this) bind(C, name=\"\")", "    type(C_PTR), value :: this",
               f"    type({st}), pointer :: cs", "    type(AnyHolder_type), pointer :: hp", "",
               "    call c_f_pointer(this, cs)", "    call c_f_pointer(cs%userData, hp)", "    deallocate(hp)",
               "    deallocate(cs)", "  end subroutine f2cpp_any_destroy", "",
               "  !> the holder of the struct c if it holds Fortran data",
               "  logical function f2cpp_any_holder(c, hp)", "    type(C_PTR), intent(in) :: c",
               "    type(AnyHolder_type), pointer, intent(out) :: hp", f"    type({st}), pointer :: cs", "",
               "    f2cpp_any_holder = .false.", "    hp => null()", "    if (.not. c_associated(c)) return",
               "    call c_f_pointer(c, cs)", "    if (.not. c_associated(cs%destroy, c_funloc(f2cpp_any_destroy))) return",
               "    call c_f_pointer(cs%userData, hp)", "    f2cpp_any_holder = allocated(hp%p)",
               "  end function f2cpp_any_holder"]
        self.wrap_routines.add("f2cpp_any_destroy")

    def any_ns(self):
        """namespace of the module's class(*) helpers: <module>_private"""
        return f"{self.mod.spelling}_private"

    def fortran_any_decl(self):
        """C++ (.hpp): Fortran data (class(*)) of calls in a std::any (definitions: fortran_any_cpp)"""
        ht = self.cppany["ht"]
        return [f"namespace {self.any_ns()}", "{",
                f"std::any any_from_fortran({ht}* c);  //!< Fortran data (class(*)): in a std::any (copies share it)",
                f"const {ht}* any_to_fortran(const std::any& a);  //!< back to Fortran (empty: NULL; C++ data throws)",
                "}", ""]

    def fortran_any_cpp(self):
        """C++ (module .cpp): Fortran data of calls in a std::any, deleted in Fortran (destroy of the struct)"""
        ht = self.cppany["ht"]
        return [f"namespace {self.any_ns()}", "{", "", "namespace", "{", "",
                "//! Fortran data (class(*)) held by C++: deleted in Fortran (destroy of the struct)",
                "class FortranData", "{", "public:",
                f"  explicit FortranData({ht}* c) : c_(c) {{}}  //!< owns c", "  //! deletes the Fortran data",
                "  ~FortranData()", "  {", "    if( c_ != nullptr && c_->destroy != nullptr )", "      c_->destroy(c_);",
                "  }", "  FortranData(const FortranData&) = delete;  //!< no copies (shared through std::shared_ptr)",
                "  FortranData& operator=(const FortranData&) = delete;  //!< no copies",
                f"  {ht}* c_ = nullptr;  //!< the struct (userData: the Fortran holder)", "};", "",
                "}  // namespace", "",
                f"std::any any_from_fortran({ht}* c)", "{", "  if( c == nullptr )", "    return {};",
                "  return std::make_shared<FortranData>(c);", "}", "",
                f"const {ht}* any_to_fortran(const std::any& a)", "{", "  if( !a.has_value() )", "    return nullptr;",
                "  if( a.type() != typeid(std::shared_ptr<FortranData>) )",
                '    throw std::invalid_argument("std::any passed to Fortran: not Fortran data");',
                "  return std::any_cast<const std::shared_ptr<FortranData>&>(a)->c_;", "}", "",
                f"}}  // namespace {self.any_ns()}", ""]

    def cpp_any_helpers(self):
        """C++ (.cpp): std::any <-> C struct (owned by the Fortran object; deleted by its destroy)"""
        ht = self.cppany["ht"]
        return ["namespace", "{", "",
                "//! deletes C++ data held by Fortran (the struct and its std::any)",
                f"void destroy_any({ht}* c) noexcept", "{",
                "  std::unique_ptr<std::any> data(static_cast<std::any*>(c->userData));",
                f"  std::unique_ptr<{ht}> owner(c);", "}", "",
                "//! C++ data for Fortran (class(*)): owned by the Fortran object (empty: NULL)",
                f"{ht}* any_to_c(std::any a)", "{", "  if( !a.has_value() )", "    return nullptr;  // unallocated in Fortran",
                f"  auto c = std::make_unique<{ht}>();",
                "  c->destroy = &destroy_any;",
                "  c->userData = new std::any(std::move(a));  // owned by c (destroy_any)",
                "  return c.release();", "}", "",
                "//! the C++ data of c (empty: NULL)",
                f"const std::any& any_from_c(const {ht}* c)", "{", "  static const std::any empty;",
                "  return c == nullptr ? empty : *static_cast<const std::any*>(c->userData);", "}", "",
                "}  // namespace", ""]

    def module_c_name(self):
        """C name of the module: <prefix>_<module>, the module name alone if it starts with <prefix>_"""
        sp, pre = self.mod.spelling, self.cfg["c_prefix"]
        return sp if sp.lower().startswith(pre.lower() + "_") else f"{pre}_{sp}"

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
                             aiface=f"{ht}_{bd.name}", funptr=f"{this}%{bd.name}_fn",
                             self_c="void* userData", self_dummy="userData", self_actual=f"{this}%userData",
                             cpp_obj=f"static_cast<{cls}*>(userData)", cls=cls, objects="owner")
            m["fn"], m["proc"], m["typedef"] = f"{bd.name}_fn", f"{self.c_class(lt).lower()}_cpp_{bd.name}", f"{ht}_{bd.name}"
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
        hpp.emit(1, f"{cls}& operator=(const {cls}&) = delete;  //!< no copies")
        self.pure_virtuals(hpp, methods)
        hpp.emit(0, "")
        hpp.emit(0, "private:")
        for bd, _, m in live:
            hpp.emit(1, f"//! internal wrapper for {bd.spelling}")
            hpp.emit(1, f"static void {m['tramp']}({m['cpp_params']}) noexcept;")
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
        c_fns = "".join(f", {m['typedef']} {m['fn']}" for _, _, m in live)
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
        for bd, iface, _ in methods:
            ret, _, params, const = self.signature(self.tr(iface), iface, True)
            hpp.emit(0, "")
            self.doc(hpp, iface, 1, bd.spelling)
            hpp.emit(1, f"virtual {ret} {bd.spelling}({params}){const}" + (" = 0;" if bd.deferred else ";"))

    def default_methods(self, cpp, methods):
        """translated Fortran default implementations of the non-deferred bindings"""
        for bd, p, _ in methods:
            if not bd.deferred:
                self.emit_proc(cpp, p, 0)
                cpp.emit(0, "")
                self.report.add("translated", "reverse-default-method")

    # ------------------------------------------------------------------ reverse types / factories
    def reverse_constants(self, lt):
        """wrap_constants of a reverse type: ([(var, kind)] of real / integer / logical / character scalars
        (kind scalar | logical | string), [TODO texts]);
        set by the C++ constructor, in the C struct, copied into the Fortran object"""
        cache = self.__dict__.setdefault("reverse_constants_cache", {})
        if lt.name in cache:
            return cache[lt.name]
        res, todo = [], []
        for n in self.cfg["wrap_constants"].get(lt.name, []):
            v = self.prog.find_component(lt, n)     # also inherited components
            if v is not None and v.base == "character" and not v.rank:      # C string (const char*)
                res.append((v, "string"))
                continue
            kind, _, why = self.plain_kind(v) if v is not None else (None, None, "no such component")
            if why is None and kind not in ("scalar", "logical"):
                why = f"{self.component_kind(v)} (reverse types: scalars only)"
            if why:
                self.report.add("todo", f"wrap-constant:{n}")
                todo.append(f"constant {n} not wrapped: {why}")
                continue
            res.append((v, kind))
        cache[lt.name] = res, todo
        return res, todo

    def cw_proc_name(self, bridge, bd):
        """procedure of the c_wrapper module for binding bd: the binding name (procedure :: name); prefixed with
        the type if another type of the module has it already or it is the name of an intrinsic"""
        used = self.cw.setdefault("proc_names", set())
        name = bd.spelling
        if name.lower() in used or name.lower() in FORTRAN_INTRINSICS:
            name = f"{bridge[:-5].lower()}_{bd.name}"
        used.add(name.lower())
        return name

    def reverse_types(self):
        return set(self.cfg["wrap_reverse"]) | set(self.cfg["wrap_reverse_factories"])

    def reverse_type(self, lt, hpp, cpp, fb, typedefs):
        """abstract type implemented in C++: C struct project_<Type> in each C++ object (function pointers,
        destroy, userData); Fortran CWrapper<Thing>_type (owns the C++ object; factories: registered
        in the Fortran factory registration by the C++ constructor)"""
        prog, cfg = self.prog, self.cfg
        name = prog.type_name(lt.name)
        cls, ht, cc = name.split("::")[-1], self.c_type_name(lt), self.c_class(lt)
        st, bridge, ft = f"{cc}_c", self.bridge_name(lt), lt.spelling or lt.name
        factory = cfg["wrap_reverse_factories"].get(lt.name)
        cw = self.cw                            # the Fortran wrapper types go into the c_wrapper module
        saved_uses, self.extra_uses = self.extra_uses, cw["uses"]
        methods, cpp_only = [], []
        for bd, iface in self.methods_of(lt):
            if not bd.deferred and self.translated_here(lt, bd, iface):      # wrap_translate: C++ only
                cpp_only.append((bd, iface, None))
                continue
            this = iface.pass_arg
            proc = self.cw_proc_name(bridge, bd)
            m = self.forward(lt, bd, iface, proc=proc, aiface=f"{ht}_{bd.name}", funptr=f"{this}%c%{bd.name}",
                             self_c=f"{ht}* self", self_dummy="this", self_actual=f"c_loc({this}%c)",
                             cpp_obj=f"static_cast<{cls}*>(self->userData)", cls=cls, objects="handle")
            m["proc"] = proc
            methods.append((bd, iface, m))
        self.extra_uses = saved_uses
        live = [m for m in methods if not m[2]["todo"]]
        # --- C struct (in the c_declaration block; struct ... for its own type)
        typedefs.append(f"!typedef struct {ht} {ht};")       # forward declaration (the struct: after the typedefs)
        consts, const_todo = self.reverse_constants(lt)          # C struct: constants, functions, userData
        members = [f"  const char* {v.cname} = nullptr;" if k == "string" else
                   f"  {self.c_type(v)} {v.cname} = " + ("false;" if k == "logical" else "0;") for v, k in consts]
        members += [f"  {ht}_{bd.name} {bd.name} = nullptr;" for bd, _, m in live]
        if factory is None:
            members.append(f"  {ht}_destroy destroy = nullptr;")
        members += ["  void* userData = nullptr;"]
        tr = self.tr()
        ctype = {"logical": "bool", "string": "const std::string&"}
        cparams = ", ".join(f"{ctype.get(k) or tr.cpp_type(v)} {v.cname}" for v, k in consts)
        strings = [v for v, k in consts if k == "string"]     # C++: held by the object, c_str() in the struct
        cnames = ", ".join(v.cname for v, _ in consts)
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
            hpp.emit(1, (f"explicit {cls}({cparams});  //!< creates its C struct (constants: {cnames})" if consts
                         else f"{cls}();  //!< creates its C struct"))
        else:
            hpp.emit(1, f"explicit {cls}(const std::string& name{', ' + cparams if consts else ''});"
                        "  //!< registers the factory (Fortran)")
        hpp.emit(1, f"virtual ~{cls}();" + ("  //!< frees its C struct" if factory is None else "  //!< unregisters it"))
        hpp.emit(1, f"{cls}(const {cls}&) = delete;  //!< the address is known to C / Fortran")
        hpp.emit(1, f"{cls}& operator=(const {cls}&) = delete;  //!< no copies")
        all_methods = methods + cpp_only
        for line in const_todo:
            hpp.emit(1, f"// {TODO}: {line}")
        for v, k in consts:                     # constants: const getters (C struct)
            hpp.emit(0, "")
            owner = next(tt for tt in [lt, *self.prog.ancestors(lt)] if v.name in tt.comps)   # inherited: its doc
            for line in self.component_docs(owner).get(v.name, []) or [f"constant {v.cname}"]:
                hpp.emit(1, f"{self.cfg['doxygen']} {line}".rstrip())
            hpp.emit(1, f"{ctype.get(k) or tr.cpp_type(v)} {v.cname}() const;")
        self.pure_virtuals(hpp, all_methods)
        hpp.emit(0, "")
        hpp.emit(0, "private:")
        for bd, _, m in live:
            hpp.emit(1, f"//! internal wrapper for {bd.spelling}")
            hpp.emit(1, f"static void {m['tramp']}({m['cpp_params']}) noexcept;")
        if factory is None:
            hpp.emit(1, "//! internal wrapper for the destruction (the object is owned by the caller)")
            hpp.emit(1, f"static void destroy_wrapper({ht}* self) noexcept;")
        hpp.emit(1, f"std::unique_ptr<{ht}> c_;  //!< the C struct of this object")
        for v in strings:
            hpp.emit(1, f"std::string {v.cname}_;  //!< constant {v.cname} (the C struct points to it)")
        if factory is not None:
            hpp.emit(1, "std::string name_;  //!< registered name")
        hpp.emit(1, f"friend {ht}* {cls}_private::toC(const {cls}& x);")
        hpp.emit(0, "};")
        hpp.emit(0, "")
        init = [f"  c_->{v.cname} = {v.cname}_.c_str();" if k == "string" else f"  c_->{v.cname} = {v.cname};"
                for v, k in consts] + \
            [f"  c_->{bd.name} = &{m['tramp']};" for bd, _, m in live] + \
            (["  c_->destroy = &destroy_wrapper;"] if factory is None else []) + ["  c_->userData = this;"]
        reg, unreg = self.c_name(lt, "register"), self.c_name(lt, "unregister")
        if factory is None:
            sinit = "".join(f", {v.cname}_({v.cname})" for v in strings)
            lines = [f"{name}::{cls}({cparams}) : c_(std::make_unique<{ht}>()){sinit}", "{"] + init + ["}", "",
                     f"{name}::~{cls}() = default;", "",
                     f"void {cls}::destroy_wrapper({ht}* self) noexcept", "{",
                     f"  std::unique_ptr<{cls}> owner(static_cast<{cls}*>(self->userData));  // deletes it", "}", ""]
        else:
            lines = [f"{name}::{cls}(const std::string& name{', ' + cparams if consts else ''}) : "
                     f"c_(std::make_unique<{ht}>()), name_(name)" + "".join(f", {v.cname}_({v.cname})" for v in strings),
                     "{"] + \
                init + [f"  {reg}(c_.get(), name_.c_str());", "}", "",
                        f"{name}::~{cls}()", "{", f"  {unreg}(name_.c_str());", "}", ""]
        lines += [f"{ht}* {cls}_private::toC(const {cls}& x)", "{", "  return x.c_.get();", "}", ""]
        for v, k in consts:
            lines += [f"{ctype.get(k) or tr.cpp_type(v)} {cls}::{v.cname}() const", "{",
                      f"  return {v.cname}_;" if k == "string" else f"  return c_->{v.cname};", "}", ""]
        self.emit_lines(cpp, lines + [x for _, _, m in live for x in m["cpp"]])
        self.default_methods(cpp, all_methods)
        self.needs_cstdio = True
        # --- Fortran binding: interfaces of the functions, bind(C) struct (what C sees)
        for _, _, m in live:
            self.bridges += m["abstract"]
        cw["binding_names"] += [f"{ht}_{bd.name}" for bd, _, _ in live] + [st]
        if factory is None:
            self.bridges += self.abstract_interface(f"{ht}_destroy", f"{ht}*", f"{cls}::destroy_wrapper", ["this"],
                                                    ["type(C_PTR), value :: this"])
            cw["binding_names"].append(f"{ht}_destroy")
        self.bridges += ["!@c_declaration", f"!struct {ht}", "!{"] + [f"!{x}" for x in members] + ["!};",
                         "!@end_c_declaration", f"  !> C struct {ht}: functions of the C++ object",
                         f"  type, bind(C), public :: {st}"] + \
            [f"    type(C_PTR) :: {v.cname} = C_NULL_PTR  !< C string" if k == "string" else
             f"    {self.f_type(v)} :: {v.cname} = " + (".false." if k == "logical" else "0") for v, k in consts] + \
            [f"    type(C_FUNPTR) :: {bd.name} = C_NULL_FUNPTR" for bd, _, m in live] + \
            (["    type(C_FUNPTR) :: destroy = C_NULL_FUNPTR"] if factory is None else []) + \
            ["    type(C_PTR) :: userData = C_NULL_PTR  !< the implementation (C++ or Fortran object)", f"  end type {st}", ""]
        str_in = cfg["wrap_string_in"]
        if strings:
            cw["uses"].add(f"  use {str_in[0]}, only: {str_in[1]}")
        to_f = [f"{v.cname} = logical(@%c%{v.cname})" if k == "logical" else
                f"{v.cname} = {str_in[1]}(@%c%{v.cname})" if k == "string" and not v.has("allocatable") else
                f"{v.cname} = @%c%{v.cname}"
                for v, k in consts if not (k == "string" and v.has("allocatable"))]
        # constants: C struct -> Fortran object (@: the object); deferred length: allocated (no realloc-lhs)
        to_f += [f"#ALLOC#allocate(@%{v.cname}, source=trim({str_in[1]}(@%c%{v.cname})))"
                 for v, k in consts if k == "string" and v.has("allocatable")]
        # --- c_wrapper module: the Fortran type implemented by the C++ object
        cw["spec"] += [f"  !> {ft} implemented in C++" + (" (owns the C++ object)" if factory is None else ""),
                       f"  type, extends({ft}), public :: {bridge}", f"    type({st}), pointer :: c => null()",
                       "  contains"] + \
            [f"    procedure :: {bd.spelling}" if m["proc"] == bd.spelling else
             f"    procedure :: {bd.spelling} => {m['proc']}" for bd, _, m in methods] + \
            ([f"    final :: {bridge[:-5].lower()}_final"] if factory is None else []) + [f"  end type {bridge}", ""]
        for _, _, m in methods:
            cw["procs"] += m["fortran"]
        fin, wrap = f"{bridge[:-5].lower()}_final", self.c_name(lt, "wrap")
        if factory is None:
            cw["procs"] += ["", "  !> deletes the C++ object", f"  subroutine {fin}(this)",
                            f"    type({bridge}), intent(inout) :: this", f"    procedure({ht}_destroy), pointer :: f", "",
                            "    if (.not. associated(this%c)) return",
                            "    if (c_associated(this%c%destroy)) then", "      call c_f_procpointer(this%c%destroy, f)",
                            "      call f(c_loc(this%c))", "    end if", f"  end subroutine {fin}", "",
                            "  !> x: the Fortran object of the C++ object c (owns it)",
                            f"  subroutine {wrap}(c, x)", "    type(C_PTR), intent(in) :: c",
                            f"    class({ft}), allocatable, target, intent(out) :: x", "",
                            f"    allocate({bridge} :: x)", "    select type (x)", f"    type is ({bridge})",
                            "      call c_f_pointer(c, x%c)"] + [f"      {fill(a, 'x')}" for a in to_f] + ["    end select",
                            f"  end subroutine {wrap}"]
        else:                                   # registration of the C++ factory under a name
            name_in = cfg["wrap_string_in"]
            cw["uses"].add(f"  use {name_in[0]}, only: {name_in[1]}")
            cw["procs"] += ["", "!@c_declaration", f"!  void {reg}({ht}* factory, const char* name);",
                            f"!  void {unreg}(const char* name);", "!@end_c_declaration",
                            "  !> registers the C++ factory under name (Fortran factory registration)",
                            f"  subroutine {reg}(factory, name) bind(C, name=\"{reg}\")"] + self.dllexport(reg) + \
                ["    type(C_PTR), value :: factory, name", f"    type({bridge}) :: f", "",
                 "    call c_f_pointer(factory, f%c)"] + [f"    {fill(a, 'f')}" for a in to_f] + [
                 f"    call {factory['register']}({name_in[1]}(name), f)      ! stores a copy",
                 f"  end subroutine {reg}", "",
                 f"  subroutine {unreg}(name) bind(C, name=\"{unreg}\")"] + self.dllexport(unreg) + \
                ["    type(C_PTR), value :: name", "", f"    call {factory['unregister']}({name_in[1]}(name))",
                 f"  end subroutine {unreg}"]
        if factory is None:                     # the other direction: Fortran implementations used in C++
            self.fortran_behind(lt, [(bd, iface, m["todo"]) for bd, iface, m in methods], fb)
        self.report.add("translated", "wrapped-reverse")

    def fortran_behind(self, lt, methods, fb):
        """a Fortran implementation behind the C struct: wrap_<method> (the function pointers),
        create_<Thing>_C_from_Fortran (fills a struct; stored by the caller) and the C++ CWrapper<Thing>
        calling the function pointers of a struct (which outlives it)"""
        prog = self.prog
        name = prog.type_name(lt.name)
        cls, ht, cc, ft = name.split("::")[-1], self.c_type_name(lt), self.c_class(lt), lt.spelling or lt.name
        thing = re.sub(r"^Abstract", "", cc)
        st, fw, wcls = f"{cc}_c", f"{cc}Wrapper_type", f"CWrapper{re.sub(r'^Abstract', '', cls)}"
        create = f"create_{thing}_C_from_Fortran"
        entries, todos = [], []
        consts = self.reverse_constants(lt)[0]
        base = f" : {cls}({', '.join('{}' for _ in consts)})" if consts else ""
        for bd, iface, todo in methods:         # todo: no function pointer in the struct
            routine = f"wrap_{bd.name}"
            if routine in self.wrap_routines:   # several reverse types in the module
                routine = f"wrap_{cc.lower()}_{bd.name}"
            part = self.fortran_method_c(lt, bd, iface, routine, st, fw)
            if todo or "todo" in part:          # C++: throws (a function pointer in the struct stays null)
                todos.append((bd, iface, part.get("todo", todo)))
                continue
            self.wrap_routines.add(routine)
            if part["any_data"]:                # Fortran data for C++: holder, its destroy (once)
                self.fortran_any_data(fb)
            entries.append((bd, iface, routine, part))
        # create_<Thing>_C_from_Fortran
        self.bridges += [f"  !> a Fortran implementation of {ft} for C (target of userData)", f"  type, public :: {fw}",
                         f"    class({ft}), pointer :: ptr => null()", f"  end type {fw}", ""]
        fb += ["", "  !> the C struct of a Fortran implementation (the caller stores both; fthing must outlive cthing)",
               f"  subroutine {create}(fthing, cthing, ierr)", f"    type({fw}), target, intent(in) :: fthing",
               f"    type({st}), intent(out) :: cthing", "    integer, intent(out) :: ierr"] + \
            [f"    character(kind=C_CHAR), pointer, contiguous :: {v.cname}_c(:)" for v, k in consts if k == "string"] + \
            ["", "    ierr = 0"] + \
            [x for v, k in consts for x in (self.string_to_c(v) if k == "string" else
             [f"    cthing%{v.cname} = " + (f"logical(fthing%ptr%{v.cname}, C_BOOL)" if k == "logical" else
                                          f"fthing%ptr%{v.cname}")])] + \
            [f"    cthing%{bd.name} = c_funloc({routine})" for bd, _, routine, _ in entries] + \
            [f"    ! {TODO}: {bd.spelling} not wrapped ({todo}): C++ {wcls}::{bd.spelling} throws"
             for bd, _, todo in todos] + \
            ["    cthing%userData = c_loc(fthing)", f"  end subroutine {create}"]
        destroy = f"destroy_{thing}_C_from_Fortran"
        strings = [v for v, k in consts if k == "string"]
        if strings:                             # C strings of the struct: allocated in Fortran (once per module)
            self.c_string_helpers(fb)
        fb += ["", f"  !> frees what {create} allocated (C strings) and resets the struct",
               f"  subroutine {destroy}(cthing, ierr)", f"    type({st}), intent(inout) :: cthing",
               "    integer, intent(out) :: ierr", "", "    ierr = 0"] + \
            [f"    call {self.c_string_names()[1]}(cthing%{v.cname})" for v in strings] + \
            [f"    cthing = {st}()", f"  end subroutine {destroy}"]
        self.public_extra += [create, destroy]
        for _, _, _, part in entries:          # after create_<Thing>_C_from_Fortran
            fb += part["fortran"]
        # C++ <Abstract>_private::CWrapper<Thing> (own files <stem>_c_wrapper.hpp / .cpp)
        hpp, cpp = self.cwc["hpp"], self.cwc["cpp"]
        ns = self.private_ns(name)
        over = "//! override base class implementation -> call corresponding C function pointer"
        self.emit_lines(hpp, [f"namespace {ns}", "{", "",
                              f"//! {cls} implemented behind a C struct (e.g. Fortran: {create}): a copy of the struct; "
                              "its implementation (userData) outlives this object",
                              f"class {wcls} final : public {cls}", "{", "public:",
                              f"  {wcls}(){base} {{}}  //!< empty object (tests): the methods throw" if consts else
                              f"  {wcls}() = default;  //!< empty object (tests): the methods throw",
                              f"  explicit {wcls}(const {ht}& c);  //!< calls the function pointers of c"])
        for bd, iface in [(e[0], e[1]) for e in entries]:
            ret, _, params, const = self.signature(self.tr(iface), iface, True)
            self.emit_lines(hpp, ["", f"  {over}", f"  virtual {ret} {bd.spelling}({params}){const} override;"])
        for bd, iface, _ in todos:
            ret, _, params, const = self.signature(self.tr(iface), iface, True)
            self.emit_lines(hpp, ["", f"  //! override base class implementation -> throws ({TODO}: not wrapped)",
                                  f"  virtual {ret} {bd.spelling}({params}){const} override;"])
        self.emit_lines(hpp, ["", "private:", f"  {ht} other_{{}};  //!< the struct of the implementation (copy)", "};",
                              "", f"}}  // namespace {ns}", ""])
        cpp.emit(0, f"namespace {ns}")
        cpp.emit(0, "{")
        cpp.emit(0, "")
        self.emit_lines(cpp, [f"{wcls}::{wcls}(const {ht}& c) : " +
                              (f"{cls}({', '.join(cpp_const(v, k) for v, k in consts)}), " if consts else "") +
                              "other_(c)", "{", "}", ""])
        for bd, iface, _, part in entries:
            ret, _, params, const = self.signature(self.tr(iface), iface, False)
            lines = [f"{ret} {wcls}::{bd.spelling}({params}){const}", "{",
                     f"  if( other_.{bd.name} == nullptr )",
                     f'    throw std::logic_error("{wcls}::{bd.spelling}: no implementation");']
            lines += [f"  {x}" for x in part["cpp_pre"]]
            call = f"other_.{bd.name}(&other_{''.join(', ' + a for a in part['cpp_args'])});"
            lines.append(f"  {call}")
            lines += [f"  {x}" for x in part["cpp_mid"]]      # owned results first (also on errors)
            if part["has_err"]:
                lines += ["  if( ierr != 0 )",
                          f'    throw std::runtime_error("Error: calling C {ht}::{bd.name} function pointer in '
                          f'{wcls}::{bd.spelling}!");']
            lines += [f"  {x}" for x in part["cpp_post"]] + ["}", ""]
            self.emit_lines(cpp, lines)
        for bd, iface, todo in todos:
            ret, _, params, const = self.signature(self.tr(iface), iface, False)
            self.report.add("todo", f"wrap-reverse-fortran:{bd.name}")
            self.emit_lines(cpp, [f"{ret} {wcls}::{bd.spelling}({params}){const}", "{", f"  // {TODO}: {todo}",
                                  f'  throw std::logic_error("{wcls}::{bd.spelling}: not wrapped ({todo})");', "}", ""])
        self.emit_lines(cpp, [f"}}  // namespace {ns}", ""])
        self.report.add("translated", "wrapped-reverse-fortran")

    def fortran_method_c(self, lt, bd, iface, routine, st, fw):
        """wrap_<method>: C arguments of the function pointer -> the Fortran implementation ({"todo": reason} if an
        argument kind is not supported here: objects, class(*), created objects)"""
        tr = self.tr(iface)
        err = tr.err
        has_err = err in iface.args and iface.vars[err].base == "integer" and not iface.vars[err].rank
        this = iface.pass_arg
        wcls = "CWrapper" + re.sub(r"^Abstract", "", self.prog.type_name(lt.name).split("::")[-1])
        dummies, sizes, decls, locals_, pre, post, call, cpp_pre, cpp_args, cpp_post = \
            ["this"], [], ["type(C_PTR), value :: this"], [], [], [], {}, [], [], []    # call: actual per dummy
        cpp_mid, keep_ierr, any_data = [], False, False     # cpp_mid: after the call (before the ierr check)
        for a in iface.args:
            v = iface.vars[a]
            if a == this or (has_err and a == err) or a == iface.result:
                continue
            key, a = a, fspell(v)               # key: order of the actual arguments; a: declared spelling
            intent, cn = v.intent or "inout", v.cname
            if v.base in ("real", "integer") and not v.rank:
                dummies.append(a)
                decls.append(f"{self.f_type(v)}, value :: {a}" if intent == "in" else
                             f"{self.f_type(v)}, intent({intent}) :: {a}")
                call[key] = a
                cpp_args.append(cn if intent == "in" else f"&{cn}")
            elif v.base == "logical" and not v.rank:
                dummies.append(a)
                decls.append(f"logical(C_BOOL), value :: {a}" if intent == "in" else
                             f"logical(C_BOOL), intent({intent}) :: {a}")
                locals_.append(f"logical :: {a}_f")
                if intent != "out":
                    pre.append(f"{a}_f = {a}")
                if intent != "in":
                    post.append(f"{a} = logical({a}_f, C_BOOL)")
                call[key] = f"{a}_f"
                cpp_args.append(cn if intent == "in" else f"&{cn}")
            elif v.base in ("real", "integer") and v.rank == 1 and not v.has("allocatable") and not v.has("pointer"):
                known = self.known_size(lt, iface, v.name)
                if known:                       # size known on both sides: checked / resized in C++
                    n = known[1].replace("{OBJ}", "")
                    dummies.append(a)
                    decls.append(f"{self.f_type(v)}, intent({intent}), target :: {a}(*)")
                    locals_.append(f"{self.fortran_decl(v)}, pointer, contiguous :: {a}_f(:)")   # Fortran kind (= C kind)
                    pre.append(f"{a}_f => {a}(1:{known[0].replace('obj%', 'this_c%')})")   # constants in the struct
                    call[key] = f"{a}_f"
                    cpp_pre += [f"{cn}.resize({n});"] if intent == "out" else \
                        [f"if( {cn}.size() != {n} )",
                         f'  throw std::invalid_argument("{wcls}::{bd.spelling}: wrong dimension of parameter {cn}!");']
                    cpp_args.append(f"{cn}.data()")
                else:
                    dummies += [a, f"{a}_n"]
                    sizes.append(f"integer(C_INT), value :: {a}_n")
                    decls.append(f"{self.f_type(v)}, intent({intent}) :: {a}({a}_n)")
                    call[key] = a
                    cpp_args += [f"{cn}.data()", f"static_cast<int>({cn}.size())"]
            elif v.base == "type" and v.tname == "*" and not v.rank and \
                    (intent == "out" and v.has("allocatable") or intent == "in"):     # Fortran data for C++
                ca = self.cpp_any()
                dummies.append(a)
                locals_.append(f"class(*), allocatable :: {a}_f")
                call[key] = f"{a}_f"
                if intent == "out":             # moved into a new holder (owned by C++); NULL on errors
                    decls.append(f"type(C_PTR), intent(out) :: {a}")
                    pre.append(f"{a} = C_NULL_PTR")
                    post.append(f"{a} = f2cpp_any_new({a}_f)")
                    cpp_pre.append(f"{ca['ht']}* {cn}_c = nullptr;")
                    cpp_args.append(f"&{cn}_c")
                    cpp_mid.append(f"{cn} = {self.any_ns()}::any_from_fortran({cn}_c);")
                else:                           # moved out of its holder for the call and back
                    decls.append(f"type(C_PTR), value :: {a}")
                    locals_.append(f"type(AnyHolder_type), pointer :: {a}_h")
                    stop = [f"  {x}" for x in self.fail_lines(err)] if has_err else \
                        [f'  error stop "{routine}: {cn} is not Fortran data"']
                    if v.has("allocatable"):    # NULL: unallocated; other (C++) data: error
                        pre += [f"if (f2cpp_any_holder({a}, {a}_h)) then", f"  call move_alloc({a}_h%p, {a}_f)",
                                f"else if (c_associated({a})) then"] + stop + ["end if"]
                        post.insert(0, f"if (associated({a}_h)) call move_alloc({a}_f, {a}_h%p)")
                    else:                       # must be present: NULL (empty std::any) is an error as well
                        pre += [f"if (.not. f2cpp_any_holder({a}, {a}_h)) then"] + stop + ["end if",
                                f"call move_alloc({a}_h%p, {a}_f)"]
                        post.insert(0, f"call move_alloc({a}_f, {a}_h%p)")
                    keep_ierr = True            # the data goes back to its holder also on errors
                    cpp_args.append(f"{self.any_ns()}::any_to_fortran({cn})")
                any_data = True
            else:                               # objects / class(*) / created objects: C++ implementations only
                return {"todo": f"argument {cn}: {self.component_kind(v)}"}
        res_v = iface.vars.get(iface.result) if iface.kind == "function" else None
        if res_v is not None and (res_v.rank or res_v.base not in ("real", "integer", "logical")):
            return {"todo": f"result {res_v.cname}: {self.component_kind(res_v)}"}
        if res_v is not None:
            r = iface.result
            dummies.append(r)
            decls.append(f"{self.f_type(res_v)}, intent(out) :: {r}")
            cpp_pre.append(f"{self.tr(iface).cpp_type(res_v)} {r}{{}};")
            cpp_args.append(f"&{r}")
            cpp_post.append(f"return {r};")
        chk = None if keep_ierr else self.cfg["wrap_chk_ierr"]
        if has_err:
            dummies.append(err)
            decls.append(f"integer(C_INT), intent(out) :: {err}")
            if chk:                             # PROJECT_CHK_IERR(call ..., ierr): the error flag itself
                call[err] = err
            else:
                locals_.append(f"integer :: {err}_f")
                call[err] = f"{err}_f"
                post.insert(0, f"{err} = {err}_f")
            cpp_pre.append("int ierr = 0;")
            cpp_args.append("&ierr")
        # positional actual arguments (all present, in the order of the dummies)
        target = f"wrapper%ptr%{bd.name}({', '.join(call[a] for a in iface.args if a in call)})"
        body = [f"{iface.result} = {target}" if res_v is not None and res_v.base != "logical" else
                f"{iface.result} = logical({target}, C_BOOL)" if res_v is not None else f"call {target}"]
        if has_err and chk:
            body = [f"{chk[0]}({body[0]}, {err})"]
        fortran = ["", f"  !> {bd.spelling} of a Fortran implementation (function pointer {st}%{bd.name})",
                   f"  subroutine {routine}({', '.join(dummies)}) bind(C, name=\"\")"] + \
            [f"    {d}" for d in sizes + decls] + \
            [f"    type({st}), pointer :: this_c", f"    type({fw}), pointer :: wrapper"] + [f"    {d}" for d in locals_] + \
            ["", "    call c_f_pointer(this, this_c)", "    call c_f_pointer(this_c%userData, wrapper)"] + \
            [f"    {x}" for x in pre + body + post] + [f"  end subroutine {routine}"]
        return {"fortran": fortran, "cpp_pre": cpp_pre, "cpp_args": cpp_args, "cpp_post": cpp_post,
                "cpp_mid": cpp_mid, "any_data": any_data,
                "has_err": has_err}

    def cwrapper_module(self, m):
        """name of the c_wrapper module of module m: project_thing -> project_c_wrapper_thing"""
        sp, pre = m.spelling or m.name, self.cfg["c_prefix"]
        if sp.lower().startswith(pre.lower() + "_"):
            return f"{sp[:len(pre) + 1]}c_wrapper_{sp[len(pre) + 1:]}"
        return f"{pre}_c_wrapper_{sp}"
