"""Expression and statement translation (Fortran parse tree -> C++ text)."""
import re
import sys
from dataclasses import dataclass

from .model import cls, lname, items_of, cname, Var
from .prepass import CPP_MARK, PF_MARK, BLANK_MARK, ASSERT_PREFIX
from .config import subst

TODO = "TODO(f2cpp)"
# separator lines of the generated Fortran (the project's style): not carried over into C++
FORTRAN_SEPARATORS = ("!" + "=" * 80, "!" + "-" * 70)


NON_WORD = r"\W"      # (no backslash inside f-string expressions: Python 3.11)


class Untranslatable(Exception):
    """statement cannot be translated; it is emitted as commented Fortran"""

    def __init__(self, category, msg=""):
        super().__init__(msg or category)
        self.category = category


@dataclass
class X:
    """translated expression"""
    code: str
    rank: int = 0
    base: str = "unknown"
    prec: int = 100
    var: Var = None          # set for (parts of) variables
    tname: str = None
    raw: str = None          # pointer-like object: code without dereference
    arr: bool = False        # Eigen Array (not Matrix) expression
    whole: bool = False      # complete variable (no subscripts)
    qual: str = ""           # "Parent::" for explicit parent calls
    res_var: Var = None      # result variable of the called function
    fwd: str = None          # optional dummy: the std::optional object itself
    neg: object = None       # the negated expression, if it has a simpler form than !(...)
    on_this: bool = False    # member of the object the method is called on
    obj: bool = False        # element of std::vector<mat>: a real matrix object, not a block
    uptr_elem: bool = False  # element of a std::vector<std::unique_ptr<T>> (unwrapped type)
    via_accessor: bool = False   # component through a getter (accessor, wrapped class)
    access: str = None       # accessor mode of a component accessed from outside: get | set | ref
    setter: str = None       # set mode: code of the setter function (obj.tol)


P_UNARY, P_MUL, P_ADD, P_REL, P_EQ, P_AND, P_OR, P_TERN = 90, 80, 70, 60, 55, 40, 30, 20


class Seq(str):
    """code of an Eigen::seq slice; seg: (start, length) if contiguous with a simple length"""
    seg = None
REL_OPS = {".lt.": "<", ".le.": "<=", ".gt.": ">", ".ge.": ">=", ".eq.": "==", ".ne.": "!=",
           "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "/=": "!="}
MATH1 = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh"}
EIGEN_ARRAY_FN = {"asin": "asin", "acos": "acos", "atan": "atan"}


def cancel_shift(a, b):
    """integer comparison of two shifted values (i + 1 == j + 1, from 0-based indices): the same top-level
    constant on both sides cancels"""
    shift = re.compile(r"(.+) ([+-]) (\d+)")
    ma, mb = shift.fullmatch(a.code), shift.fullmatch(b.code)
    if a.prec == P_ADD and b.prec == P_ADD and a.base == b.base == "integer" and ma and mb and \
            ma.group(2, 3) == mb.group(2, 3):
        ca, cb = ma.group(1), mb.group(1)
        return X(ca, 0, "integer", P_ADD if re.search(r"[+-]", ca) else P_MUL), \
            X(cb, 0, "integer", P_ADD if re.search(r"[+-]", cb) else P_MUL)
    return a, b


def par(x, need=100, strict=False):
    if x.prec < need or (strict and x.prec == need):
        return f"({x.code})"
    return x.code


def cstring(lit):
    body = lit[1:-1].replace(lit[0] * 2, lit[0])
    return '"' + body.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Translator:
    """translates the statements of one procedure (or module-level initialisers)"""

    def __init__(self, program, module, proc, report):
        self.prog, self.mod, self.proc, self.report = program, module, proc, report
        self.named = {}         # named_elements: array -> ({element index: C++ variable}, variables declared)
        self.brace_arrays = False   # array constructors as braced lists
        self.via_getters = False    # translated method of a wrapped class: components are getters
        self.cfg = program.cfg
        self.err = self.cfg["error_arg"].lower()
        # macros that stay macros in C++ (replacement "call NAME(...)")
        self.macro_calls = {m.group(1).lower() for t in self.cfg["macros"].values()
                            for m in [re.match(r"\s*call\s+(\w+)", t, re.I)] if m}
        self.overrides = {}
        self.out = []
        self.loop_local = set()    # loop variables that are only used inside their loops
        self.loop_decl = set()     # ... declared in the for statement
        self.body_decl = {}        # id(assignment) -> variable declared there (loop body, emit.loop_body_decl)
        self.shift = {}            # loop variable -> offset: Fortran value = C++ value + offset
        self.notes = []            # soft TODOs of the current statement
        self.last_err_call = None  # (line index, code) of the last call with removed error arg

    # ------------------------------------------------------------------ lookup
    def lookup(self, n):
        if n in self.overrides:
            return self.overrides[n]
        p = self.proc
        while p is not None:                # own variables, then those of the host (host association)
            if n in p.vars:
                return p.vars[n]
            p = p.host
        for m in self.prog.visible(self.mod):
            if n in m.vars and (m is self.mod or m.is_public(n, m.vars[n].access)):
                return m.vars[n]
        return None

    def lookup_proc(self, n, args=None):
        """procedure of that name; for a generic name the specific procedure that fits the arguments"""
        p = self.proc
        while p is not None:                # internal procedures of the procedure and of its host
            if n in p.internal:
                return p.internal[n]
            p = p.host
        for m in self.prog.visible(self.mod):
            if m is not self.mod and not m.is_public(n):
                continue                    # private names of other modules are not visible
            if n in m.procs:
                return m.procs[n]
            if n in m.generics:
                cands = [m.procs[s] for s in m.generics[n] if s in m.procs]
                if cands:
                    return self.pick_specific(cands, args) if args is not None else cands[0]
        return None

    def pick_specific(self, cands, args):
        """generic resolution: the first specific procedure whose dummies fit the actual arguments
        (number, keywords, rank and intrinsic type as far as known)"""
        actual = [self.split_arg(a) for a in args]
        actual = [(kw, node) for kw, node in actual if not self.is_err(node) and kw != self.err]
        for p in cands:
            dummies = [d for d in p.args if d != self.err and d != p.pass_arg]
            given, pos, ok = {}, 0, True
            for kw, node in actual:
                if kw is None:
                    if pos >= len(dummies):
                        ok = False
                        break
                    kw, pos = dummies[pos], pos + 1
                if kw not in dummies or kw in given:
                    ok = False
                    break
                given[kw] = node
            ok = ok and all(d in given or p.vars[d].has("optional") for d in dummies)
            notes = list(self.notes)
            for d, node in given.items() if ok else ():
                dv = p.vars[d]
                try:
                    x = self.ref(node)
                except Exception:
                    continue                # unknown: assume it fits
                if d in self.dropped(p) or dv.base == "procedure":
                    continue
                if x.rank != dv.rank or ("unknown" not in (x.base, dv.base) and x.base != dv.base and
                                         {x.base, dv.base} != {"integer", "real"} or
                                         (x.base, dv.base) == ("real", "integer")):
                    ok = False
                    break
            self.notes = notes
            if ok:
                return p
        return cands[0]

    def find_type(self, tname):
        return self.prog.find_type(tname, self.mod) if tname else None

    def note(self, category, msg):
        self.report.add_once("todo", category, (self.mod.name, self.proc.name if self.proc else None, msg))
        if msg not in self.notes:
            self.notes.append(msg)

    # ------------------------------------------------------------------ types
    def elem_type(self, v):
        cfg = self.cfg
        if v.base == "real":
            return cfg["kind_map"].get(v.kind, cfg["scalar"]) if v.kind else cfg["scalar"]
        if v.base == "integer":
            return cfg["kind_map"].get(v.kind, cfg["int"]) if v.kind else cfg["int"]
        if v.base == "logical":
            return "bool"
        if v.base == "character":
            return "std::string"
        if v.base == "complex":
            return f"std::complex<{cfg['scalar']}>"
        if v.base == "procedure":
            return self.proc_type(v.tname)
        if v.base == "type" and v.tname == "*":     # unlimited polymorphic: no C++ counterpart
            self.note("unlimited-polymorphic", f"class(*) {v.cname}: std::any (check the use)")
            return "std::any"
        if v.base == "type":
            if v.tname not in self.cfg["type_map"] and self.find_type(v.tname) is None:
                self.report.add("info", "unknown-type:" + v.tname)
            name = self.prog.type_name(v.tname, self.mod)
            return f"std::unique_ptr<{name}>" if v.elem_uptr and v.rank else name
        return "auto"

    def find_iface(self, name):
        """abstract interface (signature of procedure arguments)"""
        for m in [*self.prog.visible(self.mod), *self.prog.modules.values()]:
            if name in m.ifaces:
                return m.ifaces[name]
        return None

    def proc_type(self, name):
        if name in self.cfg["procedure_map"]:
            return self.cfg["procedure_map"][name]
        p = self.find_iface(name)
        if p is None:
            self.report.add("info", "unknown-interface:" + str(name))
        return p.cname if p is not None else cname(str(name))

    def expansion(self, v):
        """[(C++ type, parameter name, component)] of a dummy argument expanded by expand_args, or None"""
        if v is None or v.base != "type" or v.rank:
            return None
        exp = self.cfg["expand_args"].get(v.tname)
        if not exp or v.is_dummy:
            return exp
        # variables: the parameter names, prefixed by the variable name if another one of the type is visible
        same = lambda w: w is not v and w.base == "type" and not w.rank and w.tname == v.tname
        home = next((m for m in self.prog.modules.values() if m.vars.get(v.name) is v), None)
        if home is not None:                    # module variable: unique within its module
            clash = any(same(w) for w in home.vars.values())
        else:
            clash = any(same(w) for q in self.scope_chain() for w in q.vars.values()) or \
                any(same(w) for m in self.prog.visible(self.mod) for w in m.vars.values())
        return [(t, f"{v.cname}_{n}", c) for t, n, c in exp] if clash else exp

    def expanded_variables(self, v):
        """local / module variable of an expand_args type: its C++ variables ["T name", ...] (no const / &)"""
        if v.is_dummy or not self.expansion(v):
            return None
        return [re.sub(r"^const\s+|\s*&$", "", t).strip() + " " + n for t, n, _ in self.expansion(v)]

    def expanded_name(self, node):
        """Name node of an expanded dummy argument -> its expansion"""
        return self.expansion(self.lookup(lname(node))) if cls(node) == "Name" else None

    def dropped(self, p):
        """dummies that are removed: class(*) user data arguments named in drop_args"""
        if p is None or not self.cfg["drop_args"]:
            return set()
        return {a for a in p.args if a in self.cfg["drop_args"] and a in p.vars
                and p.vars[a].polymorphic and p.vars[a].tname == "*"}

    def scope_chain(self):
        """the procedure and its hosts (internal procedures / lambdas see the host's variables)"""
        p = self.proc
        while p is not None:
            yield p
            p = p.host

    def method_proc(self):
        """outermost host: the procedure that is a method (if any)"""
        chain = list(self.scope_chain())
        return chain[-1] if chain else None

    def is_dropped(self, node):
        if cls(node) != "Name" or lname(node) in self.overrides:
            return False
        n = lname(node)
        owner = next((p for p in self.scope_chain() if n in p.vars), None)
        return owner is not None and n in self.dropped(owner)

    def storage(self, v):
        """kind of container: scalar eigen vec3 stdvec uptr ptr"""
        if v.rank == 0:
            if v.base == "procedure":
                return "scalar"
            if v.base == "type" and v.has("allocatable") and v.tname != "*":     # class(*): std::any
                return "uptr"
            return "ptr" if v.has("pointer") else "scalar"
        if v.int_vec:
            return "stdvec"
        if v.base in ("real", "integer", "logical", "complex"):
            return "eigen" if v.rank <= 2 else "vec3" if v.rank == 3 else "flat"
        return "stdvec" if v.rank == 1 else "objmat" if v.rank == 2 else "flat"

    def extents(self, v):
        """extents of a flat (column-major) array as C++ expressions"""
        dims = v.dims if all(hi is not None for _, hi in v.dims) else v.alloc_dims
        if not dims:
            raise Untranslatable("flat-array-extent")
        res = []
        for lo, hi in dims:
            if lo is None:
                res.append(par(self.expr(hi)))
            else:
                res.append(f"({par(self.expr(hi), P_ADD)} - {par(self.expr(lo), P_ADD, True)} + 1)")
        return res

    def nest(self, v):
        """(depth of the std::vector nesting, rank of the Eigen object stored in it)

        numeric rank 3: std::vector of matrices, a(:, :, i) -> a[i]; rank-1 object arrays: std::vector"""
        st = self.storage(v)
        return (1, 2) if st == "vec3" else (1, 0) if st == "stdvec" else (0, v.rank)

    def leaf_type(self, v):
        return self.eigen_type(v, 2) if self.storage(v) == "vec3" else self.elem_type(v)

    def nested_ctor(self, v, dims):
        """constructor / assign() arguments of a nested std::vector with the sizes dims"""
        depth, lr = self.nest(v)
        t = self.leaf_type(v)
        cur = f"{t}({', '.join(dims[:lr])})" if lr else ""
        for n in reversed(dims[lr + 1:]):
            cur = f"std::vector<{t}>({n}{', ' + cur if cur else ''})"
            t = f"std::vector<{t}>"
        return f"{dims[lr]}{', ' + cur if cur else ''}"

    def eigen_type(self, v, rank):
        cfg, e = self.cfg, self.elem_type(v)
        if v.base == "real" and e == cfg["scalar"]:
            return cfg["vec"] if rank == 1 else cfg["mat"]
        if v.base == "integer" and e == cfg["int"]:
            return cfg["ivec"] if rank == 1 else cfg["imat"]
        if v.base == "logical":
            return f"Eigen::Array<bool, Eigen::Dynamic, {'1' if rank == 1 else 'Eigen::Dynamic'}>"
        return f"Eigen::Matrix<{e}, Eigen::Dynamic, {'1' if rank == 1 else 'Eigen::Dynamic'}>"

    def cpp_type(self, v):
        """type of a variable / component / function result"""
        st, e = self.storage(v), self.elem_type(v)
        if st == "scalar":
            return e
        if st == "uptr":
            return f"std::unique_ptr<{e}>"
        if st == "ptr":
            return f"{e}*"
        if st == "eigen":
            return self.eigen_type(v, v.rank)
        if st == "objmat":
            return f"Eigen::Array<{e}, Eigen::Dynamic, Eigen::Dynamic>"
        if st == "flat":
            self.report.add("todo", "flat-array")
            return self.eigen_type(v, 1) if v.base not in ("type", "character") else f"std::vector<{e}>"
        t = self.leaf_type(v)
        for _ in range(self.nest(v)[0]):
            t = f"std::vector<{t}>"
        return t

    def param_type(self, v):
        """type of a dummy argument"""
        t, st = self.cpp_type(v), self.storage(v)
        const = v.intent == "in"
        if v.base == "procedure":
            return f"const std::optional<{t}>&" if v.opt_kind else f"const {t}&"
        if v.opt_kind == "empty":
            return f"const {t}&"
        if v.opt_kind == "optional":
            return f"std::optional<{t}>" if st == "scalar" and v.base != "character" else \
                f"const std::optional<{t}>&"
        if v.opt_kind == "optref":
            return f"std::optional<std::reference_wrapper<{'const ' if const else ''}{t}>>"
        if v.has("allocatable") or v.has("pointer") or st in ("vec3", "stdvec", "objmat", "flat"):
            return f"const {t}&" if const else f"{t}&"
        if st == "eigen":
            if self.cfg["array_args"] == "ref" or (v.needs_ref and self.cfg["array_args"] == "auto"):
                return f"const Eigen::Ref<const {t}>&" if const else f"Eigen::Ref<{t}>"
            return f"const {t}&" if const else f"{t}&"
        if v.base in ("type", "character"):
            return f"const {t}&" if const else f"{t}&"
        return t if const or v.has("value") else f"{t}&"

    # ------------------------------------------------------------------ expressions
    def expr(self, node):
        """translate an expression; an integer array used here needs to be an Eigen vector"""
        x = self.ref(node)
        self.touch(x)
        return x

    @staticmethod
    def int_array(v):
        return v is not None and v.base == "integer" and v.rank == 1

    def touch(self, x):
        if x.rank > 0:
            for v in (x.var, x.res_var):
                if self.int_array(v):
                    v.needs_eigen = True

    def link(self, a, b):
        self.prog.links.append((a, b))

    def ref(self, node):
        """translate an expression that is used in a way a std::vector<int> supports as well"""
        k = cls(node)
        m = getattr(self, "x_" + k, None)
        if m is not None:
            return m(node)
        its = getattr(node, "items", None)
        if its is not None and len(its) == 3 and isinstance(its[1], str) and not isinstance(its[0], str):
            return self.binary(its[0], its[1], its[2])
        if its is not None and len(its) == 2 and isinstance(its[0], str) and not isinstance(its[1], str) \
                and its[1] is not None:
            return self.unary(its[0], its[1])
        raise Untranslatable("expr:" + k)

    def x_Parenthesis(self, node):
        x = self.expr(node.items[1])
        return X(f"({x.code})", x.rank, x.base, 100, tname=x.tname, arr=x.arr)

    def x_Int_Literal_Constant(self, node):
        return X(str(node.items[0]), 0, "integer")

    def x_Real_Literal_Constant(self, node):
        s = re.sub(r"e\+?0+$", "", str(node.items[0]).lower().replace("d", "e"))
        return X(s if "." in s or "e" in s else s + ".0", 0, "real")

    def x_Logical_Literal_Constant(self, node):
        return X("true" if "T" in str(node.items[0]).upper() else "false", 0, "logical")

    def x_Char_Literal_Constant(self, node):
        return X(cstring(str(node.items[0])), 0, "character")

    def x_Name(self, node):
        n = lname(node)
        if self.is_dropped(node):
            raise Untranslatable("dropped-argument")
        if self.expanded_name(node):
            raise Untranslatable("expanded-argument")       # only components / passing on are translated
        local = self.overrides.get(n) or next((p.vars[n] for p in self.scope_chain() if n in p.vars), None)
        if local is None and n in self.cfg["constant_map"]:        # local names hide the mapping
            return X(self.cfg["constant_map"][n])
        v = self.lookup(n)
        if v is not None and v.named_elements:  # the whole array: braced list of the element variables
            k = self.named_count(v)
            lst = self.cfg["named_element_lists"].get(v.tname, "")
            cur = self.named.get(v.name, ({}, 0))[0]
            return X(lst + "{" + ", ".join(cur.get(i, f"{v.cname}_{i}") for i in range(1, k + 1)) + "}", 1, "type",
                     100, v, v.tname, whole=True)
        if v is not None and n in self.shift and n not in self.overrides:
            return X(f"{v.cname} + {self.shift[n]}", 0, "integer", P_ADD, v)
        if v is None:
            if n == self.err:
                raise Untranslatable("error-arg")
            p = self.lookup_proc(n)
            if p is None:
                self.report.add("info", "unknown-name:" + n)
            return X(cname(str(node)))
        return self.var_ref(v, v.cname)

    def var_ref(self, v, code):
        x = X(code, v.rank, v.base, 100, v, v.tname, whole=True)
        if v.is_this:
            x.raw, x.code, x.prec = "this", "*this", P_UNARY
        elif v.opt_kind == "optref":
            x.code, x.fwd = f"{code}->get()", code
        elif v.ptr_like:
            x.raw, x.code, x.prec = code, "*" + code, P_UNARY
            x.fwd = code if v.opt_kind else None
        return x

    def x_Part_Ref(self, node):
        return self.ref_or_call(node.items[0], node.items[1], node)

    def x_Function_Reference(self, node):
        if cls(node.items[0]) == "Procedure_Designator":
            code, p = self.method_call(node.items[0], node.items[1])
            return self.result_x(code, p)
        return self.ref_or_call(node.items[0], node.items[1], node)

    x_Intrinsic_Function_Reference = x_Function_Reference
    x_Structure_Constructor = x_Part_Ref

    def result_x(self, code, p):
        if p is not None and p.result and p.result in p.vars:
            r = p.vars[p.result]
            return X(code, r.rank, r.base, 100, tname=r.tname, res_var=r)
        return X(code)

    def ref_or_call(self, name_node, args, node):
        n = lname(name_node)
        v = self.lookup(n)
        if v is not None and v.base == "procedure":             # function passed as argument
            iface = self.find_iface(v.tname)
            argc = self.bound_args(iface, items_of(args), False) if iface is not None else \
                self.plain_args(items_of(args))
            callee = f"(*{v.cname})" if v.opt_kind else v.cname
            return self.result_x(f"{callee}({', '.join(argc)})", iface)
        if v is not None and v.named_elements:
            return self.named_element(v, items_of(args))
        if v is not None and cls(node) != "Intrinsic_Function_Reference" or (v is not None and v.rank > 0):
            return self.subscript(self.x_Name(name_node), v, items_of(args))
        if n in self.cfg["function_map"]:
            fn = self.cfg["function_map"][n]
            return X(self.mapped_call(n, items_of(args)), 0,
                     "character" if fn == "std::to_string" else "unknown")
        p = self.lookup_proc(n, items_of(args))
        if p is not None:
            self.spelled(p, name_node)
            return self.result_x(self.call_code(p, items_of(args)), p)
        if n in INTRINSICS or cls(node) == "Intrinsic_Function_Reference":
            return self.intrinsic(n, items_of(args))
        t = self.find_type(n)
        if t is not None:
            self.note("structure-constructor", "structure constructor: check member order")
            for comp in t.comps.values():           # aggregate initialisation needs public members
                comp.access_need = 2
            args_c = ", ".join(self.expr(a.items[1] if cls(a) == "Component_Spec" else a).code
                               for a in items_of(args))
            return X(f"{self.prog.type_name(n, self.mod)}{{{args_c}}}", 0, "type", tname=n)
        self.report.add("info", "external-function:" + n)
        return self.unknown_ref(cname(str(name_node)), items_of(args))

    def mapped_call(self, n, args):
        """call of a function from the function_map (plain rename or template with conversions)"""
        fn = self.cfg["function_map"][n]
        if "{" not in fn:
            xs = [self.expr(node) for _, node in map(self.split_arg, args)
                  if not self.is_err(node) and not self.is_dropped(node)]
            if fn.startswith("std::") and any(x.rank > 0 for x in xs):
                fn = fn[5:]         # Eigen argument: the project's overload, not the std function
            return f"{fn}({', '.join(self.matrix(x) for x in xs)})"
        return self.fill_template(fn, args, n)

    def fill_template(self, template, args, what, obj=None):
        """{0}, {1:cstr}, {2:ptr}, {3:data}: translated arguments; {self}: the object of a method call;
        {module}: module name, {module_base}: without the prefix <namespace>_"""
        xs = [X(", ".join(n for _, n, _ in self.expanded_name(node))) if self.expanded_name(node) else
              self.expr(node) for _, node in map(self.split_arg, args)
              if not self.is_err(node) and not self.is_dropped(node)]
        modname = self.mod.spelling or self.mod.name
        prefix = self.cfg["c_prefix"] + "_"
        base = modname[len(prefix):] if modname.lower().startswith(prefix.lower()) else modname

        def sub(m):
            if m.group(0) == "{module}":
                return modname
            if m.group(0) == "{module_base}":
                return base
            if m.group(0) == "{self}":
                if obj is None:
                    raise Untranslatable("template-self:" + what)
                return obj.raw or par(obj) if obj.raw != "this" else "*this"
            i, conv = int(m.group(1)), m.group(2)
            if i >= len(xs):
                raise Untranslatable("template-arguments:" + what)
            x = xs[i]
            code = self.matrix(x)
            if conv == "cstr":
                return code if re.fullmatch(r'"([^"\\]|\\.)*"', code) else f"std::string({code}).c_str()"
            if conv == "ptr":
                return "&" + par(x, P_UNARY)
            if conv == "data":
                return par(x) + ".data()"
            return code
        return re.sub(r"\{self\}|\{module\}|\{module_base\}|\{(\d+)(?::(\w+))?\}", sub, template)

    def method_template(self, tname, binding):
        """template configured for a type-bound procedure of the type or one of its base types"""
        import fnmatch
        t = self.find_type(tname)
        for name in [tname] + ([a.name for a in self.prog.ancestors(t)] if t is not None else []):
            if f"{name}%{binding}" in self.cfg["method_templates"]:
                return self.cfg["method_templates"][f"{name}%{binding}"]
            for key, template in self.cfg["method_templates"].items():     # glob patterns: "*_config_type%read"
                if fnmatch.fnmatch(f"{name}%{binding}", key):
                    return template
        return None

    def call_template(self, n):
        """call template of procedure n (exact name or glob pattern, e.g. "log_*")"""
        return match_name(n, self.cfg["call_templates"])

    @staticmethod
    def declares_self(template):
        """the template declares the object itself: "Type {self}(...);" -> no separate declaration"""
        return re.match(r"^\s*[\w:<>,{}]+\s+\{self\}\s*[({;]", template) is not None

    def unknown_ref(self, base, subs, array=False):
        """name(...) of unknown kind: an array section if it has a triplet (or array: known to be an
        array, e.g. an accessor), else passed through as call"""
        if not array and not any(cls(s) == "Subscript_Triplet" for s in subs):
            return X(f"{base}({', '.join(self.plain_args(subs))})")
        sc = [self.sub_code(s, None) for s in subs]
        self.note("unknown-array", "array of unknown type (assumed Eigen, lower bound 1)")
        if all(k == "all" for k, _ in sc):
            return X(base, len(sc))
        return X(f"{base}({', '.join(c for _, c in sc)})", sum(k != "idx" for k, _ in sc))

    def member(self, obj, name, qual=""):
        """code of member 'name' of obj; 'this->' is omitted unless a local name hides the member"""
        if obj.raw == "this":
            hidden = {v.cname for p in self.scope_chain() for v in p.vars.values() if not v.is_this}
            hidden |= {v.cname for v in self.overrides.values()}
            if qual or name not in hidden:
                return qual + name
        sep = "->" if obj.raw is not None else "."
        return (obj.raw if obj.raw is not None else par(obj)) + sep + qual + name

    def accessor(self, tname, comp, spelled):
        """(getter name, setter name, mode, declaring type or None) if the component of type tname (or a base
        type) has accessors, else None"""
        acc = self.cfg["accessors"]
        if not acc or tname is None:
            return None
        chain, t = [tname], self.find_type(tname)       # the type and its base types
        while t is not None and t.parent:
            chain.append(t.parent)
            t = self.find_type(t.parent)
        for name in chain:
            t = self.find_type(name)
            names = acc.get(name, {})
            if (t is not None and comp in t.comps) or comp in names or (t is None and "*" in names):
                # the type that defines the component decides; type not parsed: trust the configuration
                ent = names.get(comp) or names.get("*")
                if ent is None:
                    return None
                get, put = (ent[0] or subst(self.cfg[k], name=spelled) for k in ("member_getter_name",
                                                                                  "member_setter_name"))
                return get, put, ent[1], t
        return None

    def own_member(self, owner, td, n, on_this):
        """component n with accessors, used in a method of the class declaring it (or a derived one): the data
        member itself, if that class is translated in this run (not wrapped, not an existing C++ class)"""
        return owner is not None and on_this and self.in_class_of(td, n) and \
            owner.module.name in getattr(self.prog, "translated", ()) and owner.name not in self.cfg["type_map"]

    def in_class_of(self, td, n):
        """the current method belongs to the type that declares component n of td or to a type derived from it"""
        mp = self.method_proc()
        t = self.find_type(mp.method_of) if mp is not None and mp.method_of else None
        owner = next((a for a in [td, *self.prog.ancestors(td)] if n in a.comps), None)
        return t is not None and owner is not None and owner in [t, *self.prog.ancestors(t)]

    def used_member(self, td, name, comp):
        """record from where a component is used: own methods, methods of derived classes, elsewhere"""
        mp = self.method_proc()
        ctx = self.find_type(mp.method_of) if mp is not None and mp.method_of else None
        if ctx is not None and ctx.comps.get(name) is comp:
            return
        if ctx is not None and any(a.comps.get(name) is comp for a in self.prog.ancestors(ctx)):
            comp.access_need = max(comp.access_need, 1)
        else:
            comp.access_need = 2

    def used_binding(self, owner, b):
        """record from where a binding is called (C++ visibility of private bindings)"""
        mp = self.method_proc()
        ctx = self.find_type(mp.method_of) if mp is not None and mp.method_of else None
        if ctx is not None and ctx is owner:
            return
        b.access_need = max(b.access_need, 1 if ctx is not None and owner in self.prog.ancestors(ctx) else 2)

    def x_Proc_Component_Ref(self, node):
        """a % b inside the argument list of an unknown function (parsed as structure constructor)"""
        base, _, last = node.items
        return self.x_Data_Ref(None, (list(base.items) if cls(base) == "Data_Ref" else [base]) + [last])

    x_Data_Pointer_Object = x_Proc_Component_Ref        # a % p => target

    def x_Data_Ref(self, node, parts=None):
        parts = parts or list(node.items)
        exp = self.expanded_name(parts[0])
        if exp:                                 # component of an expanded argument: its parameter
            comp = parts[1]
            n = lname(comp.items[0] if cls(comp) == "Part_Ref" else comp)
            hit = next(((t, c) for t, c, k in exp if k == n), None)
            if hit is None or len(parts) > 2 or cls(comp) == "Part_Ref":
                raise Untranslatable("expanded-argument")
            base = "character" if "string" in hit[0] else "integer" if "int" in hit[0] else \
                "logical" if "bool" in hit[0] else "real" if any(w in hit[0] for w in ("double", "float", "scalar")) \
                else "unknown"
            return X(hit[1], 0, base)
        cur = self.expr(parts[0])
        on_this = cur.raw == "this"
        # holder: the object of the last component, hsubs: its subscripts (companions of unwrapped types)
        holder, hsubs = None, (items_of(parts[0].items[1]) if cls(parts[0]) == "Part_Ref" else None)
        for p in parts[1:]:
            pname, subs = (p.items[0], items_of(p.items[1])) if cls(p) == "Part_Ref" else (p, None)
            n = lname(pname)
            if cur.var is not None and cur.var.companions and n in cur.var.companions and subs is None:
                cv = cur.var.companions[n]      # arr(i)%offset -> arr_offset[i - 1]
                acc = self.accessor(holder.tname, cv.name, cv.cname) if holder is not None else None
                acc = acc[0] if acc else None
                if acc:                         # getter (wrapped class or existing C++ class)
                    x = X(self.member(holder, acc, holder.qual) + "()", cv.rank, cv.base, 100, cv, whole=True)
                else:
                    x = self.var_ref(cv, self.member(holder, cv.cname, holder.qual)) if holder is not None else \
                        self.var_ref(cv, cv.cname)
                cur = self.subscript(x, cv, hsubs) if hsubs is not None else x
                if acc and cur.rank == 0 and cur.code.startswith(x.code + "(") and cur.code.endswith(")"):
                    cur.code = f"{x.code}[{cur.code[len(x.code) + 1:-1]}]"    # Eigen vector or std::vector
                continue
            if cur.var is not None and cur.var.unwrapped == n and (cur.whole or cur.uptr_elem):
                if subs is not None:            # x%v(i) of an unwrapped wrapper type: x(i)
                    cur = self.subscript(cur, cur.var, subs)
                continue
            holder, hsubs = cur, subs
            if cur.rank > 0:
                raise Untranslatable("component-of-array")
            td = self.find_type(cur.tname)
            comp = self.prog.find_component(td, n) if td else None
            if comp is None and td and subs is not None and p is parts[-1] and self.prog.find_binding(td, n)[1]:
                code, proc = self.call_on(cur, pname, p.items[1])     # a%b(i)%f(x): function binding
                cur = self.result_x(code, proc)
                continue
            if comp is None:
                if td and any(a.name == n for a in self.prog.ancestors(td)):
                    cur = X(cur.code, 0, "type", cur.prec, tname=n, raw=cur.raw,
                            qual=self.prog.type_name(n, self.mod) + "::")
                    continue
                self.report.add("info", "unknown-component:" + n)
                ent = self.accessor(cur.tname, n, cname(str(pname)))
                x = X(self.member(cur, ent[0], cur.qual) + "()" if ent else
                      self.member(cur, cname(str(pname)), cur.qual))
                if subs is not None:            # an accessor is data: subscripts are array indices
                    x = self.unknown_ref(x.code, subs, array=bool(ent))
                if ent:
                    x.access, x.setter = ent[2], self.member(cur, ent[1], cur.qual)
            elif (ent := self.accessor(td.name, n, comp.cname)) and self.own_member(ent[3], td, n, on_this):
                self.used_member(td, n, comp)       # accessors of a translated class: <member> in its methods
                x = self.var_ref(comp, self.member(cur, subst(self.cfg["member_name"], name=comp.cname), cur.qual))
                if subs is not None:
                    x = self.subscript(x, comp, subs)
            else:
                self.used_member(td, n, comp)
                acc = ent[0] if ent else None
                if comp.elem_uptr and td.module.name in getattr(self.prog, "wrapped", ()):
                    # objects of an unwrapped type in a wrapped (Fortran) class: only the companions exist
                    nxt = parts[parts.index(p) + 1] if p is not parts[-1] else None
                    nn = lname(nxt.items[0] if cls(nxt) == "Part_Ref" else nxt) if nxt is not None else None
                    if subs is None and nxt is None and comp.companions:     # size(b%arr): of a companion
                        cv = next(iter(comp.companions.values()))
                        acc = (self.accessor(cur.tname, cv.name, cv.cname) or (cv.cname,))[0]
                        self.report.add("info", f"unwrapped-shape:{n}->{cv.name}")
                        cur = X(self.member(cur, acc, cur.qual) + "()", cv.rank, cv.base, 100, cv, whole=True)
                        continue
                    if not (subs is not None and nn in (comp.companions or {})):
                        raise Untranslatable("unwrapped-object-of-wrapped-class")
                if acc:     # the accessor returns a reference, also for pointer components
                    x = X(self.member(cur, acc, cur.qual) + "()", comp.rank, comp.base, 100, comp,
                          comp.tname, whole=True, via_accessor=True, access=ent[2],
                          setter=self.member(cur, ent[1], cur.qual))
                    if self.via_getters and n in self.cfg["wrap_constants"].get(td.name, []):
                        self.note("wrapped-constant", f"{comp.cname}: copy made at setup (changes are not seen)")
                else:
                    x = self.var_ref(comp, self.member(cur, comp.cname, cur.qual))
                if subs is not None:
                    x = self.subscript(x, comp, subs)
                    x.access = ent[2] if acc else None
            # through a const getter (not a pointer): the whole chain is read-only
            if cur.access == "get" and x.access is None and not (cur.var is not None and cur.var.has("pointer")):
                x.access = "get"
            cur = x
            cur.on_this = on_this
        return cur

    # index handling
    def linear(self, node):
        """(code of the variable part or None, constant) for `literal`, `name`, `expr +- literal`
        (shifted loop variables included); None for other expressions"""
        k = cls(node)
        if k == "Int_Literal_Constant":
            return None, int(str(node.items[0]))
        if k == "Name" and lname(node) in self.shift and lname(node) not in self.overrides:
            return self.lookup(lname(node)).cname, self.shift[lname(node)]
        if k == "Level_2_Expr" and node.items[1] in "+-" and cls(node.items[2]) == "Int_Literal_Constant":
            sign = 1 if node.items[1] == "+" else -1
            sub = self.linear(node.items[0])
            if sub is None:
                x = self.expr(node.items[0])
                if x.rank > 0:
                    return None
                sub = par(x, P_ADD), 0
            return sub[0], sub[1] + sign * int(str(node.items[2].items[0]))
        return None

    def minus(self, node, c):
        """translate integer expression 'node - c' with constant folding"""
        lin = self.linear(node)
        if lin is not None:
            base, val = lin[0], lin[1] - c
            if base is None:
                return str(val)
            return base if val == 0 else f"{base} {'+' if val > 0 else '-'} {abs(val)}"
        if c == 0:
            return self.expr(node).code
        x = self.expr(node)
        if x.rank > 0:
            raise Untranslatable("vector-subscript")
        return f"{par(x, P_ADD)} - {c}" if c > 0 else f"{par(x, P_ADD)} + {-c}"

    def index0(self, node, lb):
        """zero-based index for subscript 'node' of a dimension with lower bound lb"""
        if lb is not None and str(lb) == str(node):
            return "0"
        if lb is None:
            return self.minus(node, 1)
        if cls(lb) == "Int_Literal_Constant":
            return self.minus(node, int(str(lb.items[0])))
        if cls(lb) == "Level_2_Unary_Expr" and cls(lb.items[1]) == "Int_Literal_Constant":
            return self.minus(node, -int(str(lb.items[1].items[0])))
        return f"{par(self.expr(node), P_ADD)} - {par(self.expr(lb), P_ADD, True)}"

    def lower_bounds(self, v):
        """lower bound nodes (None = 1) from the declaration, or - if literal - from an allocate"""
        lbs = [d[0] for d in v.dims]
        if v.alloc_dims and all(lb is None for lb in lbs):
            lbs = [lo if lo is not None and re.fullmatch(r"-?\s*\d+", str(lo)) else None
                   for lo, _ in v.alloc_dims]
        return lbs

    def sub_code(self, s, lb):
        """-> ('idx'|'all'|'seq', code)"""
        if cls(s) != "Subscript_Triplet":
            x = self.expr(s)
            if x.rank == 1:
                # vector subscript (e.g. permutation); Eigen accepts index arrays. The Fortran index
                # values are shifted by the lower bound of the indexed dimension
                off = 1 if lb is None else int(str(lb).replace(" ", "")) \
                    if re.fullmatch(r"-?\s*\d+", str(lb)) else None
                if off is None:
                    return "vidx", f"({self.as_array(x)} - {par(self.expr(lb), P_ADD, True)})"
                if off == 0:
                    return "vidx", par(x)
                return "vidx", f"({self.as_array(x)} {'-' if off > 0 else '+'} {abs(off)})"
            return "idx", self.index0(s, lb)
        lo, hi, step = s.items
        if lo is None and hi is None and step is None:
            return "all", self.cfg["eigen_placeholders"] + "::all"
        a = self.index0(lo, lb) if lo is not None else "0"
        b = self.index0(hi, lb) if hi is not None else self.cfg["eigen_placeholders"] + "::last"
        c = f", {self.expr(step).code}" if step is not None else ""
        code = Seq(f"Eigen::seq({a}, {b}{c})")
        if step is None and hi is not None and (lb is None or lo is not None):
            n = self.slice_length(lo, hi)
            if n is not None:                   # contiguous with a simple length: segment / block
                code.seg = (a, n)
        return "seq", code

    def slice_length(self, lo, hi):
        """length hi - lo + 1 of a slice by cancelling identical +/- terms of lo and hi (as written); None
        unless what remains of lo is a literal (lo None: 1)"""
        def terms(node, sign, out):
            k = cls(node)
            if k == "Level_2_Expr" and node.items[1] in ("+", "-"):
                terms(node.items[0], sign, out)
                terms(node.items[2], sign if node.items[1] == "+" else -sign, out)
            elif k == "Int_Literal_Constant":
                out.append((sign, None, int(str(node.items[0]))))
            elif k == "Level_2_Unary_Expr" and str(node.items[0]).strip() in ("+", "-"):
                terms(node.items[1], sign if str(node.items[0]).strip() == "+" else -sign, out)
            else:
                out.append((sign, node, re.sub(r"\s+", "", str(node)).lower()))
        th, tl = [], []
        terms(hi, 1, th)
        if lo is not None:
            terms(lo, 1, tl)
        else:
            tl = [(1, None, 1)]
        const = sum(sg * v for sg, n, v in th if n is None) - sum(sg * v for sg, n, v in tl if n is None) + 1
        rest = [t for t in th if t[1] is not None]
        for sg, _, key in [t for t in tl if t[1] is not None]:
            hit = next((t for t in rest if t[0] == sg and t[2] == key), None)
            if hit is None:
                return None                     # lo has a term that hi does not have
            rest.remove(hit)
        if not rest:
            return str(const) if const >= 0 else None
        if len(rest) > 2:
            return None                         # not simpler than the end index
        code = ""
        for sg, n, _ in rest:
            x = self.expr(n)
            if x.rank:
                return None
            code += (f"{par(x, P_ADD)}" if not code and sg > 0 else
                     f"{' + ' if sg > 0 else ' - '}{par(x, P_ADD, True)}" if code else f"-{par(x, P_UNARY)}")
        return code if const == 0 else f"{code} {'+' if const > 0 else '-'} {abs(const)}"

    def named_count(self, v):
        """number of elements of a named_elements array (literal size, lower bound 1)"""
        lo, hi = v.dims[0] if v.dims else (None, None)
        if hi is None and v.named_size is not None:         # allocate(x(2)) before
            return v.named_size
        if hi is None or not str(hi).strip().isdigit() or (lo is not None and str(lo).strip() != "1"):
            raise Untranslatable("named-elements-size")
        return int(str(hi).strip())

    def named_element(self, v, subs):
        """inputs(2) -> X of the variable inputs_2"""
        if len(subs) != 1 or cls(subs[0]) != "Int_Literal_Constant":
            raise Untranslatable("named-elements-index")
        k = int(str(subs[0].items[0]))
        return X(self.named.get(v.name, ({}, 0))[0].get(k, f"{v.cname}_{k}"), 0, "type", 100, None, v.tname)

    def new_named_element(self, v, subs):
        """inputs(k) = ...: a new C++ variable (counted up per array: an element assigned again gets a new one)"""
        if len(subs) != 1 or cls(subs[0]) != "Int_Literal_Constant":
            raise Untranslatable("named-elements-index")
        cur, n = self.named.get(v.name, ({}, 0))
        name = f"{v.cname}_{n + 1}"
        cur[int(str(subs[0].items[0]))] = name
        self.named[v.name] = (cur, n + 1)
        return name

    def subscript(self, x, v, subs):
        if v is not None and v.named_elements:
            return self.named_element(v, subs)
        res = self.subscript_(x, v, subs)
        if v.elem_uptr and res.rank == 0:      # unique_ptr element: pointer-like
            res.raw, res.code, res.prec, res.uptr_elem = res.code, "*" + res.code, P_UNARY, True
        return res

    def subscript_(self, x, v, subs):
        st = self.storage(v)
        if st in ("scalar", "uptr", "ptr") or len(subs) != v.rank:
            raise Untranslatable("substring" if v.base == "character" else "subscript")
        lbs = self.lower_bounds(v)
        base = par(x)
        res = X("", 0, v.base, 100, v, v.tname)
        depth, lr = self.nest(v)
        if depth:
            trail = [self.sub_code(s, lb) for s, lb in zip(subs[lr:], lbs[lr:], strict=True)]
            full = all(cls(s) == "Subscript_Triplet" and s.items == (None, None, None) for s in subs[:lr])
            if full and all(k == "all" for k, _ in trail):
                return x
            if any(k != "idx" for k, _ in trail):
                raise Untranslatable("slice-of-nested-vector")
            base += "".join(f"[{c}]" for _, c in trail)
            if lr == 0:
                res.code = base
                return res
            subs, lbs, res.obj = subs[:lr], lbs[:lr], True
        sc = [self.sub_code(s, lb) for s, lb in zip(subs, lbs, strict=True)]
        kinds = [k for k, _ in sc]
        if st == "flat":
            if any(k != "idx" for k in kinds):
                raise Untranslatable("slice-of-flat-array")
            ext, idx = self.extents(v), sc[-1][1]
            for (_, c), e in zip(reversed(sc[:-1]), reversed(ext[:-1]), strict=True):
                idx = f"{c} + {e} * ({idx})"
            self.note("flat-array", f"rank-{v.rank} array stored flat (column-major)")
            res.code = f"{base}({idx})" if v.base not in ("type", "character") else f"{base}[{idx}]"
            return res
        res.rank = sum(k != "idx" for k in kinds)
        seg = [getattr(c, "seg", None) for _, c in sc]
        if len(sc) == 1 and seg[0]:             # contiguous slices with a simple length
            res.code = f"{base}.segment({seg[0][0]}, {seg[0][1]})"
        elif len(sc) == 2 and seg[0] and seg[1]:
            res.code = f"{base}.block({seg[0][0]}, {seg[1][0]}, {seg[0][1]}, {seg[1][1]})"
        elif len(sc) == 2 and seg[0] and kinds[1] == "idx":
            res.code = f"{base}.col({sc[1][1]}).segment({seg[0][0]}, {seg[0][1]})"
        elif len(sc) == 2 and seg[1] and kinds[0] == "idx":
            res.code = f"{base}.row({sc[0][1]}).segment({seg[1][0]}, {seg[1][1]}).transpose()"
        elif len(sc) == 2 and seg[0] and kinds[1] == "all":
            res.code = f"{base}.middleRows({seg[0][0]}, {seg[0][1]})"
        elif len(sc) == 2 and seg[1] and kinds[0] == "all":
            res.code = f"{base}.middleCols({seg[1][0]}, {seg[1][1]})"
        elif len(sc) == 1:
            res.code = base if kinds[0] == "all" else f"{base}({sc[0][1]})"
        elif kinds == ["all", "all"]:
            res.code = base
        elif kinds == ["all", "idx"]:
            res.code = f"{base}.col({sc[1][1]})"
        elif kinds == ["idx", "all"]:
            res.code = f"{base}.row({sc[0][1]}).transpose()"
        else:
            res.code = f"{base}({sc[0][1]}, {sc[1][1]})"
            if kinds[0] == "idx" and kinds[1] != "idx":
                res.code += ".transpose()"
        return res

    # operators
    def unary(self, op, operand):
        x = self.expr(operand)
        o = op.lower()
        if o == ".not.":
            if x.neg is not None:
                return x.neg
            return X("!" + par(x, P_UNARY), x.rank, "logical", P_UNARY, arr=x.arr)
        if o == "-":
            return X("-" + par(x, P_UNARY), x.rank, x.base, P_UNARY, arr=x.arr)
        return x

    def as_array(self, x):
        """operand for an Eigen coefficient-wise (Array) expression"""
        return x.code if x.rank == 0 or x.arr else par(x) + ".array()"

    def binary(self, lhs, op, rhs):
        a, b = self.expr(lhs), self.expr(rhs)
        o = op.lower()
        rank = max(a.rank, b.rank)
        base = "real" if "real" in (a.base, b.base) else (a.base if a.base == b.base else "unknown")
        if a.rank > 2 or b.rank > 2:
            raise Untranslatable("rank3-expression")
        if o == "**":
            return self.power(a, b)
        if o == "//":
            left, right = par(a, P_ADD), par(b, P_ADD, True)
            lit = r'"([^"\\]|\\.)*"'
            if re.fullmatch(lit, left) and re.fullmatch(lit, right):    # "a" + "b" would add pointers
                left = f"std::string({left})"
            return X(f"{left} + {right}", 0, "character", P_ADD)
        if o in REL_OPS:
            c, p = REL_OPS[o], P_EQ if REL_OPS[o] in ("==", "!=") else P_REL
            if rank > 0:
                return X(f"{self.as_array_p(a, p)} {c} {self.as_array_p(b, p)}", rank, "logical", p, arr=True)
            a, b = cancel_shift(a, b)
            return X(f"{par(a, p)} {c} {par(b, p, True)}", 0, "logical", p)
        if o in (".and.", ".or.", ".eqv.", ".neqv."):
            c, p = {".and.": ("&&", P_AND), ".or.": ("||", P_OR), ".eqv.": ("==", P_EQ),
                    ".neqv.": ("!=", P_EQ)}[o]
            if o == ".or.":     # a && b || c is correct, but compilers warn: parenthesise &&
                return X(f"{par(a, P_AND, True)} {c} {par(b, P_AND, True)}", rank, "logical", p, arr=rank > 0)
            return X(f"{par(a, p)} {c} {par(b, p, True)}", rank, "logical", p, arr=rank > 0)
        if o in "+-":
            if rank > 0 and (a.rank == 0 or b.rank == 0 or a.arr or b.arr):
                return X(f"{self.as_array_p(a, P_ADD)} {o} {self.as_array_p(b, P_ADD, o == '-')}",
                         rank, base, P_ADD, arr=True)
            return X(f"{par(a, P_ADD)} {o} {par(b, P_ADD, o == '-')}", rank, base, P_ADD)
        if o in "*/" and a.rank > 0 and b.rank > 0:
            if not (a.arr or b.arr):
                fn = "cwiseProduct" if o == "*" else "cwiseQuotient"
                return X(f"{par(a)}.{fn}({b.code})", rank, base)
            return X(f"{self.as_array_p(a, P_MUL)} {o} {self.as_array_p(b, P_MUL, True)}", rank, base, P_MUL,
                     arr=True)
        if o == "*":
            return X(f"{par(a, P_MUL)} * {par(b, P_MUL, True)}", rank, base, P_MUL, arr=a.arr or b.arr)
        if o == "/":
            if b.rank > 0 and not b.arr:
                return X(f"{par(a, P_MUL)} / {self.as_array(b)}", rank, base, P_MUL, arr=True)
            return X(f"{par(a, P_MUL)} / {par(b, P_MUL, True)}", rank, base, P_MUL, arr=a.arr or b.arr)
        raise Untranslatable("operator:" + o)

    def as_array_p(self, x, need, strict=False):
        if x.rank == 0 or x.arr:
            return par(x, need, strict)
        return par(x) + ".array()"

    def power(self, a, b):
        if a.rank > 0:
            return X(f"{self.as_array(a) if a.arr else par(a) + '.array()'}.pow({b.code})", a.rank, a.base,
                     arr=True)
        if a.code.isdigit() and b.code.isdigit() and int(a.code) ** int(b.code) < 2 ** 31:
            return X(str(int(a.code) ** int(b.code)), 0, "integer")
        if b.code in ("2", "3", "4") and re.fullmatch(r"[\w.\[\]]+|[\w.\[\]]*(->[\w.\[\]]+)+", a.code):
            return X(" * ".join([a.code] * int(b.code)), 0, a.base, P_MUL)
        if a.base == "integer" and b.base == "integer":     # integer result as in Fortran
            return X(f"static_cast<{self.cfg['int']}>(std::pow({a.code}, {b.code}))", 0, "integer")
        return X(f"std::pow({a.code}, {b.code})", 0, "real")

    def matrix(self, x):
        """Matrix-world value of an expression (for assignment / function arguments)"""
        return f"({x.code}).matrix()" if x.arr and x.base != "logical" else x.code

    def ac_items(self, node):
        vals = items_of(node.items[1])
        if cls(node.items[1]) == "Ac_Spec":
            vals = items_of(node.items[1].items[1])
        xs = [self.expr(v) for v in vals]
        if not xs or any(x.rank > 0 for x in xs):
            raise Untranslatable("array-constructor")
        return xs

    def x_Array_Constructor(self, node):
        xs = self.ac_items(node)
        if self.brace_arrays:                   # constructor arguments of named_elements: {1, 2} / {"a", "b"}
            return X("{" + ", ".join(x.code for x in xs) + "}", 1, xs[0].base if xs else "unknown")
        if all(x.base == "character" for x in xs):
            return X("std::vector<std::string>{" + ", ".join(x.code for x in xs) + "}", 1, "character")
        # operands of the comma initialiser: << binds stronger than comparisons and logical operators
        items = ", ".join(par(x, P_ADD) for x in xs)
        if all(x.base == "logical" for x in xs):
            t = "Eigen::Array<bool, Eigen::Dynamic, 1>"
            return X(f"({t}({len(xs)}) << {items}).finished()", 1, "logical", arr=True)
        base = "integer" if all(x.base == "integer" for x in xs) else "real"
        t = self.cfg["ivec"] if base == "integer" else self.cfg["vec"]
        return X(f"({t}({len(xs)}) << {items}).finished()", 1, base)

    # ------------------------------------------------------------------ calls
    def split_arg(self, a):
        if cls(a) in ("Actual_Arg_Spec", "Component_Spec"):
            return lname(a.items[0]), a.items[1]
        return None, a

    def is_err(self, node):
        return cls(node) == "Name" and lname(node) == self.err

    def plain_args(self, args):
        res = []
        for a in args:
            kw, node = self.split_arg(a)
            if self.is_err(node) or self.is_dropped(node):
                continue
            if self.expanded_name(node):        # expanded argument passed to an unknown procedure
                res += [n for _, n, _ in self.expanded_name(node)]
                continue
            x = self.expr(node)
            res.append((f"/*{kw}=*/ " if kw else "") + self.matrix(x))
        return res

    def bound_args(self, p, args, skip_pass=True):
        """actual arguments in dummy order -> list of C++ codes"""
        dummies = [d for d in p.args if d != self.err and not (skip_pass and d == p.pass_arg)]
        given, pos = {}, 0
        for a in args:
            kw, node = self.split_arg(a)
            if self.is_err(node):
                continue
            if kw is None:
                if pos >= len(dummies):
                    raise Untranslatable("too-many-arguments")
                kw = dummies[pos]
                pos += 1
            given[kw] = node
        gone = self.dropped(p)              # user data arguments: neither dummy nor actual in C++
        dummies = [d for d in dummies if d not in gone]
        res = []
        last = max([i for i, d in enumerate(dummies) if d in given], default=-1)
        for d in dummies[:last + 1]:
            dv = p.vars[d]
            if d not in given:
                res.append(f"{self.cpp_type(dv)}()" if dv.opt_kind == "empty" else
                           "std::nullopt" if dv.opt_kind else "nullptr")
                continue
            if self.expansion(dv):              # expanded argument passed on: its parameters
                exp = self.expanded_name(given[d])
                if not exp or [k for _, _, k in exp] != [k for _, _, k in self.expansion(dv)]:
                    raise Untranslatable("expanded-argument")
                res += [n for _, n, _ in exp]
                continue
            if self.int_array(dv):
                x = self.ref(given[d])
                other = x.var if x.whole and self.int_array(x.var) else x.res_var
                if self.int_array(other):
                    self.link(dv, other)                # actual and dummy must have the same type
                else:
                    dv.needs_eigen = True
                    self.touch(x)
            elif dv.base == "procedure" and gone & set(given) and cls(given[d]) == "Name" and \
                    self.lookup(lname(given[d])) is None and \
                    getattr(self.lookup_proc(lname(given[d])), "cb_arg", None):
                # callback implementation with user data: bind the user data in a lambda
                res.append(self.callback_lambda(self.lookup_proc(lname(given[d])), dv,
                                                given[sorted(gone & set(given))[0]]))
                continue
            else:
                x = self.expr(given[d])
            code = self.matrix(x)
            passes_handle = dv.ptr_like and not dv.opt_kind       # unique_ptr& / pointer& dummy
            if x.rank > 0 and dv.rank > 0 and dv.intent != "in" and self.storage(dv) == "eigen" and \
                    not (x.whole or (x.obj and "(" not in code.split("]")[-1] and "." not in code.split("]")[-1])):
                dv.needs_ref = True     # slice passed to an out/inout dummy
                if self.cfg["array_args"] == "plain":
                    self.note("slice-argument", f"slice passed to non-const reference '{dv.cname}'")
            if dv.opt_kind and x.var is not None and x.var.opt_kind == dv.opt_kind and x.whole \
                    and x.fwd is not None:
                code = x.fwd                                        # forward optional argument
            elif passes_handle and x.raw is not None:
                code = x.raw
            res.append(code)
        return res

    @staticmethod
    def spelled(p, name_node):
        """record how a resolved reference to procedure p is written"""
        s = str(name_node).strip()
        if s.lower() == p.name:
            p.spellings.add(s)

    def callback_lambda(self, q, dummy, user_data):
        """callback q(userData, x, f) passed together with its user data -> [&](x, f) { obj.q(x, f); }"""
        iface = self.find_iface(dummy.tname)
        if iface is None:
            raise Untranslatable("callback-interface")
        gone = self.dropped(iface)
        i_dummies = [a for a in iface.args if a != self.err]
        q_dummies = [a for a in q.args if a != self.err]
        if len(i_dummies) != len(q_dummies):
            raise Untranslatable("callback-signature")
        params = [iface.vars[a] for a in i_dummies if a not in gone]
        obj = self.expr(user_data)
        args = []
        for ia in i_dummies:                    # same length as q_dummies (checked above)
            if ia in gone:
                if not q.cb_method:
                    args.append(obj.code)
            else:
                args.append(iface.vars[ia].cname)
        callee = self.member(obj, q.cname) if q.cb_method else q.cname
        plist = ", ".join(f"{self.param_type(v)} {v.cname}" for v in params)
        ret = "return " if iface.kind == "function" else ""
        return f"[&]({plist}) {{ {ret}{callee}({', '.join(args)}); }}"

    def call_code(self, p, args):
        """call of a known module procedure (free function or method called by implementation name)"""
        name = cname(p.generic or p.cname)
        mp = self.method_proc()
        if not p.method_of and mp is not None and mp.method_of:
            # a member of the same name would hide the free function
            t = self.find_type(mp.method_of)
            if t is not None and any(name in tt.bindings or name in tt.comps or name in tt.generics
                                     for tt in [t, *self.prog.ancestors(t)]):
                name = f"{self.prog.namespace(p.module.name)}::{name}"
        if not p.method_of:
            return f"{name}({', '.join(self.bound_args(p, args, False))})"
        t = self.find_type(p.method_of)
        tn = self.prog.type_name(p.method_of, self.mod)
        if not p.pass_arg:
            return f"{tn}::{p.bind_name}({', '.join(self.bound_args(p, args))})"
        # object = actual argument associated with the passed-object dummy
        idx = p.args.index(p.pass_arg)
        obj, rest = None, []
        for i, a in enumerate(args):
            kw, node = self.split_arg(a)
            if kw == p.pass_arg or (kw is None and i == idx and obj is None):
                obj = self.expr(node)
            else:
                rest.append(a)
        if obj is None:
            raise Untranslatable("passed-object")
        qual = f"{tn}::" if t is not None and self.prog.polymorphic(t) else ""   # non-virtual call
        if p.is_ctor or p.is_dtor:
            plain = self.member(obj, p.cname) + "()"
            return self.lifetime_call(p, obj, self.bound_args(p, rest),
                                      obj.tname is not None and obj.tname != p.method_of, plain)
        return f"{self.member(obj, p.bind_name, qual)}" \
               f"({', '.join(self.bound_args(p, rest))})"

    def method_call(self, desig, args):
        """obj%binding(args) -> (code, procedure or None)"""
        base_node, _, bname = desig.items
        return self.call_on(self.expr(base_node), bname, args)

    def call_on(self, obj, bname, args):
        """binding bname of the translated object obj called with args -> (code, procedure or None)"""
        n = lname(bname)
        template = self.method_template(obj.tname, n) if obj.tname else None
        if template is not None:        # method of an existing C++ class with another interface
            return self.fill_template(template, items_of(args), f"{obj.tname}%{n}", obj), None
        td = self.find_type(obj.tname)
        owner, b = self.prog.find_binding(td, n) if td else (None, None)
        p = self.prog.binding_proc(owner, b) if owner else None
        if b is not None:
            b.spellings.add(str(bname).strip())
            self.used_binding(owner, b)
        if p is None and owner is not None and n in owner.generics:
            # generic binding: find the specific procedure that fits the arguments
            cands = []
            for tt in [td, *self.prog.ancestors(td)]:
                for s_name in tt.generics.get(n, ()):
                    owner_s, b_s = self.prog.find_binding(td, s_name)
                    ps = self.prog.binding_proc(owner_s, b_s) if owner_s else None
                    if ps is not None and ps not in cands:
                        cands.append(ps)
            p = self.pick_specific(cands, items_of(args)) if cands else None
            for tt in [td, *self.prog.ancestors(td)]:     # visibility of the specific bindings (overloads)
                for s_name in tt.generics.get(n, ()):
                    owner_s, b_s = self.prog.find_binding(td, s_name)
                    if b_s is not None:
                        self.used_binding(owner_s, b_s)
            argc = self.bound_args(p, items_of(args)) if p is not None else self.plain_args(items_of(args))
        elif p is not None:
            argc = self.bound_args(p, items_of(args))
        elif td is not None and getattr(self.prog.find_component(td, n), "base", None) == "procedure":
            iface = self.find_iface(self.prog.find_component(td, n).tname)      # procedure pointer component
            argc = self.bound_args(iface, items_of(args), False) if iface is not None else \
                self.plain_args(items_of(args))
        else:
            self.report.add("info", "unknown-binding:" + n)
            argc = self.plain_args(items_of(args))
        spelled = cname(str(bname))             # spelling of the definition if known
        if owner is not None:
            spelled = b.spelling if b is not None else owner.generic_spelling.get(n, spelled)
            if b is not None and b.generic:
                spelled = owner.generic_spelling[b.generic]
        code = f"{self.member(obj, spelled, obj.qual)}" \
               f"({', '.join(argc)})"
        if p is not None and (p.is_ctor or p.is_dtor):
            code = self.lifetime_call(p, obj, argc, bool(obj.qual), code)
        return code, p

    def lifetime_call(self, p, obj, argc, parent, plain):
        """call of a procedure that became a constructor / destructor"""
        if p.is_dtor:
            self.report.add("todo", "destructor-call")
            return f"// {plain};  {TODO}: destructor runs automatically at the end of the lifetime " \
                   "(reset the object by hand if it is reused)"
        tn = self.prog.type_name(p.method_of, self.mod)
        ctor = f"{tn}({', '.join(argc)})"
        if parent:
            self.note("constructor", "use a base class initialiser")
            sep = "->" if obj.raw is not None else "."
            return f"{obj.raw if obj.raw is not None else par(obj)}{sep}{tn}::operator=({ctor})"
        if obj.var is not None and obj.var.polymorphic and not obj.var.is_this:
            self.note("constructor", "constructor call on polymorphic object (slicing)")
        return f"{obj.code} = {ctor}"

    # ------------------------------------------------------------------ intrinsics
    def intrinsic(self, n, args):
        kw, pos = {}, []
        for a in args:
            k, node = self.split_arg(a)
            if k:
                kw[k] = node
            else:
                pos.append(node)
        f = INTRINSICS.get(n)
        if f is None:
            raise Untranslatable("intrinsic:" + n)
        return f(self, n, pos, kw)

    # ------------------------------------------------------------------ statements
    def emit(self, ind, text):
        self.out.append("  " * ind + text if text else "")

    def declare_here(self, v, n0, ind):
        """the declaration of v merged into its assignment (output from n0 on), else in front of it"""
        t = self.cpp_type(v)
        code = [k for k in range(n0, len(self.out)) if self.out[k].strip() and not self.out[k].strip().startswith("//")]
        m = re.match(rf"(\s*){re.escape(v.cname)} = (.*)$", self.out[code[0]]) if len(code) == 1 else None
        if m:
            self.out[code[0]] = f"{m.group(1)}{t} {v.cname} = {m.group(2)}"
        else:
            self.out.insert(n0, "  " * ind + f"{t} {v.cname}{{}};")

    def internal_error(self, e, node):
        """log an internal error (stderr): where in the Fortran source and in f2cpp"""
        import traceback
        frame = traceback.extract_tb(e.__traceback__)[-1]
        proc = self.proc.cname if self.proc is not None else "(module)"
        line = last_line(node)
        print(f"f2cpp: internal error in {self.mod.spelling}::{proc}" + (f", line {line}" if line else "") +
              f": {type(e).__name__}: {e} (f2cpp/{frame.filename.split('f2cpp/')[-1]}:{frame.lineno} {frame.name});"
              " statement kept as Fortran comment (--strict: stop)", file=sys.stderr)

    def fortran_comment(self, node, ind, why):
        self.emit(ind, f"// {TODO}: {why}")
        for line in str(node).split("\n"):
            if line.strip() and BLANK_MARK not in line:
                self.emit(ind, "// F: " + line.rstrip().replace(CPP_MARK, "").replace("!F2CPP_PF", "@"))

    def comment_text(self, node):
        text = str(node)[1:]
        dox = self.cfg["doxygen"]
        return dox + text[1:] if text[:1] == ">" else dox + "<" + text[1:] if text[:1] == "<" else "//" + text

    def comment(self, node, ind, prev=None):
        """returns False for marker comments that are not emitted"""
        s = str(node)
        if s.startswith(BLANK_MARK):
            if self.out and self.out[-1] != "" and not self.out[-1].rstrip().endswith("{"):
                self.emit(0, "")
            return
        if s.startswith(PF_MARK):
            return
        if s.startswith(CPP_MARK):
            d = s[len(CPP_MARK):]
            m = re.match(r'#\s*include\s*"(.*)"', d)
            if m and m.group(1) in self.cfg["include_map"]:
                new = self.cfg["include_map"][m.group(1)]
                d = f'#include "{new}"' if new else ""
            if d:
                self.out.append(d)
            return
        if s.strip() in FORTRAN_SEPARATORS:
            return
        c = self.comment_text(node)
        trailing = prev is not None and node.item is not None and last_line(prev) == node.item.span[0]
        if trailing and self.out and self.out[-1].strip() and not self.out[-1].lstrip().startswith("//"):
            self.out[-1] += "  " + c
        else:
            self.emit(ind, c)

    def block(self, nodes, ind, prev=None):
        for n in nodes:
            if cls(n) == "Comment":
                self.comment(n, ind, prev)
            else:
                self.stmt(n, ind)
                prev = n

    HEADERS = ("If_Then_Stmt", "Else_If_Stmt", "Nonlabel_Do_Stmt", "Select_Case_Stmt", "Case_Stmt",
               "Select_Type_Stmt")
    CONSTRUCTS = ("If_Construct", "Block_Nonlabel_Do_Construct", "Case_Construct", "Select_Type_Construct")

    def err_uses(self, node):
        """does the node use the error argument other than as removable actual argument?"""
        k = cls(node)
        if k == "_Trimmed":
            k = cls(node.node)
        if k == "Name":
            return lname(node) == self.err
        if isinstance(node, str) or node is None:
            return False
        kids = getattr(node, "content", None) or getattr(node, "items", None) or []
        if k == "Call_Stmt" and lname(node.items[0]).startswith(ASSERT_PREFIX):
            return any(lname(n) == self.err for n in self.names(node.items[1]))
        if k in ("Actual_Arg_Spec_List", "Component_Spec_List", "Section_Subscript_List"):
            kids = [c for c in kids if not self.is_err(self.split_arg(c)[1])]
        elif k in ("Call_Stmt", "Function_Reference", "Part_Ref", "Structure_Constructor") \
                and len(kids) == 2 and self.is_err(self.split_arg(kids[1])[1] if kids[1] is not None else None):
            kids = kids[:1]
        elif k == "Alloc_Opt":
            return False
        return any(self.err_uses(c) for c in kids if not isinstance(c, str)
                   and not isinstance(c, (tuple, list))) or \
            any(self.err_uses(cc) for c in kids if isinstance(c, (tuple, list)) for cc in c)

    def names(self, node):
        from fparser.two.utils import walk
        from fparser.two import Fortran2003 as F
        return walk(node, F.Name) if node is not None else []

    def only_err_names(self, node):
        return all(lname(n) == self.err for n in self.names(node))

    def err_stmt(self, node, ind):
        """statement using the error flag: drop pure bookkeeping, otherwise keep as comment"""
        k = cls(node.node) if cls(node) == "_Trimmed" else cls(node)
        if k == "Assignment_Stmt" and self.is_err(node.items[0]) and \
                (not self.err_uses(node.items[2]) or self.is_err(node.items[2])):
            return          # ierr = 0, ierr = ierr (e.g. PROJECT_CHK_IERR(ierr=ierr, ierr) after a long call)
        if k == "If_Stmt" and self.only_err_names(node.items[0]) and cls(node.items[1]) == "Return_Stmt":
            return
        if k == "If_Construct" and self.only_err_names(node.content[0].items[0]) and \
                all(cls(c) in ("Return_Stmt", "Comment", "End_If_Stmt") for c in node.content[1:]):
            return
        if k == "Call_Stmt" and lname(node.items[0]).startswith(ASSERT_PREFIX + "assert") and \
                self.err_assert(node, ind):
            return
        if k == "If_Construct":
            return self.s_If_Construct(node, ind)     # condition as TODO, the body is translated
        if k == "If_Stmt":
            return self.s_If_Stmt(node, ind)
        if k == "Call_Stmt" and lname(node.items[0]) in self.macro_calls and node.items[1] is not None:
            # PROJECT_ASSERT(ierr == 0, ierr): the error flag is replaced by exceptions
            cond = str(items_of(node.items[1])[0]).replace(" ", "").lower().replace(".eq.", "==")
            good = [s.lower() for s in self.cfg["error_success"]]
            if cond in [f"{self.err}=={s}" for s in good] + [f"{s}=={self.err}" for s in good]:
                return
        self.report.add("untranslated", "error-arg:" + k)
        self.fortran_comment(node, ind, f"uses error flag '{self.err}' (replace by exception handling)")

    def err_assert(self, node, ind):
        """pFUnit assert on the error flag -> nothing (success) or *_ANY_THROW (failure expected)"""
        name = lname(node.items[0])[len(ASSERT_PREFIX) + 6:]
        args = [self.split_arg(a) for a in items_of(node.items[1])]
        pos = [str(n).replace(" ", "").lower() for kw, n in args if kw is None]
        e = self.err
        good = [s.lower() for s in self.cfg["error_success"]]
        pos = [p.replace(".eq.", "==").replace(".ne.", "/=") for p in pos]
        eq = [f"{e}=={s}" for s in good] + [f"{s}=={e}" for s in good]
        ne = [f"{e}/={s}" for s in good] + [f"{s}/={e}" for s in good]
        pair = len(pos) == 2 and e in pos and any(s in pos for s in good)
        ok = (name == "equal" and pair) or (name == "true" and pos[:1] in [[c] for c in eq]) or \
             (name == "false" and pos[:1] in [[c] for c in ne])
        fail = (name == "notequal" and pair) or (name == "true" and pos[:1] in [[c] for c in ne]) or \
               (name == "false" and pos[:1] in [[c] for c in eq])
        if ok:
            return True
        if fail and self.last_err_call is not None and self.last_err_call[0] == len(self.out) - 1:
            i, code = self.last_err_call
            lead = self.out[i][:len(self.out[i]) - len(self.out[i].lstrip())]
            self.out[i] = f"{lead}{self.cfg['gtest_assert']}_ANY_THROW({code});"
            return True
        return False

    def stmt(self, node, ind):
        k = cls(node)
        if k in self.CONSTRUCTS:
            # fparser attaches comments preceding a construct to the construct itself
            n_lead = 0
            while cls(node.content[n_lead]) == "Comment":
                self.comment(node.content[n_lead], ind)
                n_lead += 1
            node = _Trimmed(node, n_lead) if n_lead else node
        start = len(self.out)
        self.notes = []
        self.saw_transpose = False
        k = cls(node.node) if cls(node) == "_Trimmed" else k
        heads = [c for c in node.content if cls(c) in self.HEADERS] if k in self.CONSTRUCTS else [node]
        try:
            if any(self.err_uses(h) for h in heads):
                return self.err_stmt(node, ind)
            m = getattr(self, "s_" + (cls(node.node) if cls(node) == "_Trimmed" else k), None)
            if m is None:
                raise Untranslatable("stmt:" + k)
            if self.cfg["keep_original"] and k not in self.CONSTRUCTS:
                self.emit(ind, "// F: " + str(node).split("\n")[0])
            n0 = len(self.out)
            m(node, ind)
            if id(node) in self.body_decl:          # declared at this assignment (loop body)
                self.declare_here(self.body_decl[id(node)], n0, ind)
            if self.notes and k not in self.CONSTRUCTS and len(self.out) > n0:
                self.out[n0] += f"  // {TODO}: " + "; ".join(self.notes)
            self.report.add("translated", k)
        except Untranslatable as e:
            del self.out[start:]
            self.report.add("untranslated", e.category)
            self.fortran_comment(node, ind, f"not translated ({e.category})")
        except Exception as e:      # a bug of f2cpp (not an unsupported construct): logged, reported as error
            if self.cfg["strict"]:
                raise
            del self.out[start:]
            self.report.add("error", f"internal:{type(e).__name__}:{k}")
            self.internal_error(e, node)
            self.fortran_comment(node, ind, f"not translated (f2cpp internal error: {type(e).__name__})")

    # simple statements
    def s_Assignment_Stmt(self, node, ind):
        lhs_n, _, rhs_n = node.items
        if self.via_getters and cls(lhs_n) in ("Data_Ref", "Name"):
            lx = self.ref(lhs_n)
            if lx.via_accessor and lx.whole and lx.rank > 0 and self.expr(rhs_n).rank > 0:
                raise Untranslatable("wrapped-component-resize")    # may change the size (reallocation)
        if cls(lhs_n) == "Part_Ref" and getattr(self.lookup(lname(lhs_n.items[0])), "named_elements", False):
            # inputs(1) = Variable_type(...) -> Variable inputs_1(...);  (declaration of the element)
            v = self.lookup(lname(lhs_n.items[0]))
            self.named_element(v, items_of(lhs_n.items[1]))              # literal index
            if cls(rhs_n) not in ("Structure_Constructor", "Part_Ref", "Function_Reference") or \
                    lname(rhs_n.items[0]) != v.tname:
                raise Untranslatable("named-elements-assignment")
            self.brace_arrays = True
            try:
                rhs = self.expr(rhs_n)          # structure constructor T{...} or constructor function f(...)
            finally:
                self.brace_arrays = False
            t = self.elem_type(v)
            m = re.fullmatch(r"([\w:<>]+)([({])(.*)([)}])", rhs.code, re.S)
            if m is None or "({"[")}".index(m.group(4))] != m.group(2):
                raise Untranslatable("named-elements-assignment")
            cpp_cls = self.cfg["named_elements"].get(v.tname) or t
            self.emit(ind, f"{cpp_cls} {self.new_named_element(v, items_of(lhs_n.items[1]))}({m.group(3)});")
            return
        lhs = self.ref(lhs_n)
        if lhs.access == "get":                 # accessors: read-only from outside
            raise Untranslatable("member-read-only")
        if lhs.access == "set":                 # the setter (whole member only)
            if not lhs.whole or lhs.setter is None:
                raise Untranslatable("member-setter-element")
            r = self.expr(rhs_n)
            self.emit(ind, f"{lhs.setter}({self.matrix(r) if r.rank else r.code});")
            return
        v = lhs.var
        st = self.storage(v) if v is not None and lhs.whole else None
        if not lhs.whole:
            self.touch(lhs)                             # section of an integer array
        if self.int_array(v) and lhs.whole:
            # uses a std::vector<int> supports: fill, initialiser list, copy of another integer array
            if cls(rhs_n) == "Array_Constructor":
                items = self.ac_items(rhs_n)
                if st == "stdvec":
                    self.emit(ind, f"{lhs.raw or lhs.code} = {{{', '.join(i.code for i in items)}}};")
                    return
            else:
                r = self.ref(rhs_n)
                other = r.var if r.whole and self.int_array(r.var) else r.res_var
                if r.rank > 0 and self.int_array(other):
                    self.link(v, other)
                elif r.rank > 0:
                    v.needs_eigen = True
                    self.touch(r)
        if st == "vec3":
            # copy of a whole std::vector<mat>, possibly with type conversion: a = real(b)
            src_n = rhs_n
            if cls(src_n) == "Intrinsic_Function_Reference" and lname(src_n.items[0]) in \
                    ("real", "dble", "int") and src_n.items[1] is not None:
                src_n = self.split_arg(items_of(src_n.items[1])[0])[1]
            r = self.expr(src_n) if cls(src_n) in ("Name", "Data_Ref") else self.expr(rhs_n)
            if r.rank == v.rank and r.whole and r.var is not None and self.storage(r.var) == "vec3":
                dst, src = lhs.raw or lhs.code, r.raw or r.code
                if self.cpp_type(r.var) == self.cpp_type(v):
                    self.emit(ind, f"{dst} = {src};")
                else:
                    self.emit(ind, f"{dst}.resize({src}.size());")
                    self.emit(ind, f"for (std::size_t k_ = 0; k_ < {src}.size(); ++k_) "
                                   f"{dst}[k_] = {src}[k_].cast<{self.elem_type(v)}>();")
                return
            if r.rank != 0:
                raise Untranslatable("nested-vector-assignment")
            fill = ".setZero()" if is_zero(r.code) else f".setConstant({r.code})"
            src, loops = lhs.raw or lhs.code, ""
            for k in range(self.nest(v)[0]):
                loops += f"for (auto& b{k}_ : {src}) "
                src = f"b{k}_"
            self.emit(ind, f"{loops}{src}{fill};")
            return
        if st == "uptr" and v.polymorphic:
            raise Untranslatable("polymorphic-assignment")
        target = par(lhs) if lhs.code.startswith("*") and lhs.rank > 0 else lhs.code
        rhs = self.ref(rhs_n) if self.int_array(v) and lhs.whole else self.expr(rhs_n)
        if lhs.rank > 0 and rhs.rank == 0 and (self.storage(v) in ("eigen", "vec3") or
                                               (self.storage(v) == "flat" and v.base not in ("type", "character"))):
            fill = ".setZero()" if is_zero(rhs.code) else f".setConstant({rhs.code})"
            self.emit(ind, f"{par(lhs)}{fill};")
            return
        if lhs.rank > 0 and rhs.rank == 0 and v is not None and self.storage(v) == "stdvec" and lhs.whole:
            name = lhs.raw or lhs.code
            self.emit(ind, f"std::fill({name}.begin(), {name}.end(), {rhs.code});")
            return
        if lhs.rank > 0 and rhs.rank == 0 and v is not None and self.storage(v) == "objmat":
            self.emit(ind, f"{par(lhs)}.setConstant({rhs.code});")
            return
        if lhs.rank > 0 and rhs.rank == 0:
            raise Untranslatable("array=scalar:" + (self.storage(v) if v is not None else "unknown"))
        code = self.matrix(rhs)
        if lhs.rank > 0 and v is not None and self.storage(v) in ("eigen", "vec3", "flat") and \
                v.base != "character":
            alias = self.aliasing(lhs_n, rhs_n)
            if alias == "transpose":
                self.emit(ind, f"{par(lhs)}.transposeInPlace();")
                return
            if alias:           # Fortran evaluates the right-hand side completely before assigning
                code = f"{code}.eval()" if postfix_only(code) else f"({code}).eval()"
        self.emit(ind, f"{target} = {code};")

    # intrinsics whose result is a new object (products, reductions): no aliasing with their arguments
    NO_ALIAS = {"matmul", "dot_product", "sum", "product", "maxval", "minval", "norm2", "size", "any", "all",
                "count", "maxloc", "minloc", "lbound", "ubound", "allocated", "shape"}
    # intrinsics that rearrange elements: aliasing if the assigned array is an argument
    REARRANGE = {"transpose", "reshape", "cshift", "eoshift", "spread", "pack", "unpack"}

    @staticmethod
    def designator(node):
        """(path of the variable / component, subscript text of the last part or None)"""
        k = cls(node)
        if k == "Name":
            return lname(node), None
        if k == "Part_Ref":
            return lname(node.items[0]), str(node.items[1]).replace(" ", "").lower()
        if k == "Data_Ref":
            parts = [Translator.designator(p) for p in node.items]
            return "%".join(p for p, _ in parts), parts[-1][1]
        return None, None

    def aliasing(self, lhs_n, rhs_n):
        """does the right-hand side read the assigned array in a way that Eigen's lazy evaluation could
        overwrite before it is read? -> False | True | "transpose" (a = transpose(a))"""
        from fparser.two.utils import walk
        from fparser.two import Fortran2003 as F
        path, subs = self.designator(lhs_n)
        if path is None:
            return True
        if cls(rhs_n) == "Intrinsic_Function_Reference" and lname(rhs_n.items[0]) == "transpose" and \
                subs is None and self.designator(items_of(rhs_n.items[1])[0]) == (path, None):
            return "transpose"
        for node in walk(rhs_n, (F.Name, F.Part_Ref, F.Data_Ref)):
            up = node.parent
            if (cls(up) == "Part_Ref" and up.items[0] is node) or cls(up) == "Data_Ref":
                continue                            # part of a larger designator
            p, s = self.designator(node)
            if p != path:
                continue
            safe, rearranged, up = False, False, node.parent
            while up is not None and up is not rhs_n.parent:
                if cls(up) == "Intrinsic_Function_Reference":
                    fn = lname(up.items[0])
                    safe |= fn in self.NO_ALIAS
                    rearranged |= fn in self.REARRANGE
                elif cls(up) in ("Function_Reference", "Structure_Constructor"):
                    safe = True                     # result of a procedure: a new object
                elif cls(up) == "Part_Ref" and self.lookup(lname(up.items[0])) is None and \
                        self.lookup_proc(lname(up.items[0])) is not None:
                    safe = True
                up = up.parent
            if safe:
                continue
            if rearranged or (s != subs and not self.disjoint(subs, s)):
                return True
        return False

    @staticmethod
    def disjoint(a, b):
        """sections that cannot overlap partially: a dimension indexed by two different integer
        literals, or by two scalars with all other dimensions equal (a(i, :) = a(j, :): the same or
        disjoint rows)"""
        if a is None or b is None:
            return False
        da, db = a.split(","), b.split(",")
        if len(da) != len(db):
            return False
        for k, (x, y) in enumerate(zip(da, db, strict=True)):
            if ":" in x or ":" in y:
                continue
            if x != y and x.lstrip("-").isdigit() and y.lstrip("-").isdigit():
                return True
            if all(u == w for m, (u, w) in enumerate(zip(da, db, strict=True)) if m != k):
                return True
        return False

    def s_Pointer_Assignment_Stmt(self, node, ind):
        lhs, rhs = self.expr(node.items[0]), self.expr(node.items[2])
        if lhs.var is not None and lhs.var.base == "procedure":
            self.emit(ind, f"{lhs.code} = {rhs.code};")
            return
        if lhs.var is not None and lhs.var.alias:
            self.emit(ind, f"auto&& {lhs.var.cname} = {rhs.code};")
            return
        if lhs.var is not None and self.storage(lhs.var) == "uptr":
            name = lhs.raw or lhs.code
            if rhs.code == "nullptr":
                self.emit(ind, f"{name}.reset();")
                return
            if rhs.var is not None and rhs.whole and self.storage(rhs.var) == "uptr":
                self.note("pointer", "ownership is transferred (Fortran: both pointers refer to the object)")
                self.emit(ind, f"{name} = std::move({rhs.raw});")
                return
            raise Untranslatable("pointer-assignment:owned=>target")
        if lhs.var is not None and self.storage(lhs.var) != "ptr":
            raise Untranslatable("pointer-assignment:" + self.storage(lhs.var))
        self.note("pointer", "non-owning raw pointer: replace by a reference / check lifetime")
        src = rhs.raw if rhs.raw is not None else "&" + par(rhs, P_UNARY)
        self.emit(ind, f"{lhs.raw or lhs.code} = {src};")

    def s_Call_Stmt(self, node, ind):
        desig, args = node.items
        args = items_of(args)
        had_err = any(self.is_err(self.split_arg(a)[1]) for a in args)
        if cls(desig) == "Procedure_Designator":
            code, _ = self.method_call(desig, node.items[1])
        else:
            n = lname(desig)
            if n.startswith(ASSERT_PREFIX + "assert"):
                return self.pf_assert(n[len(ASSERT_PREFIX) + 6:], args, ind)
            p = self.lookup_proc(n, args)
            if n == "random_number" and len(args) == 1:
                # Fortran: uniform in [0, 1); Eigen setRandom: [-1, 1]
                x = self.expr(self.split_arg(args[0])[1])
                st = self.storage(x.var) if x.var is not None else None
                if x.rank == 0:
                    self.emit(ind, f"{x.code} = static_cast<{self.cfg['scalar']}>(std::rand()) / RAND_MAX;")
                elif st == "vec3" and x.whole:
                    self.emit(ind, f"for (auto& b0_ : {x.raw or x.code}) "
                                   "b0_ = (b0_.setRandom().array() + 1.0) / 2.0;")
                elif st in ("eigen", "vec3", "flat", None) and x.base not in ("type", "character"):
                    self.emit(ind, f"{par(x)} = ({par(x)}.setRandom().array() + 1.0) / 2.0;")
                else:
                    raise Untranslatable("random_number")
                return
            if n == "move_alloc" and len(args) == 2:
                src, dst = self.ref(args[0]), self.ref(args[1])
                if self.int_array(src.var) and self.int_array(dst.var):
                    self.link(src.var, dst.var)
                else:
                    self.touch(src)
                    self.touch(dst)
                s_name = src.raw if src.raw is not None else src.code
                self.emit(ind, f"{dst.raw if dst.raw is not None else dst.code} = std::move({s_name});")
                if src.var is not None and self.storage(src.var) == "eigen":
                    self.emit(ind, f"{s_name}.resize({', '.join(['0'] * src.var.rank)});")
                elif src.var is not None and self.storage(src.var) in ("vec3", "stdvec"):
                    self.emit(ind, f"{s_name}.clear();")
                return
            if n in self.cfg["function_map"]:
                self.emit(ind, self.mapped_call(n, args) + ";")
                return
            template = self.call_template(n)
            if template is not None:
                codes = self.plain_args(args)

                def sub(m):
                    c = codes[int(m.group(1))]
                    return c.replace("\\", "\\\\").replace('"', '\\"') if m.group(2) else c
                text = re.sub(r"\{(\d+)(:str)?\}", sub, template)
                if text.strip():                # an empty template drops the call
                    self.emit(ind, text.replace("if (!(false)) ", "", 1) if text.startswith("if (!(false)) ") else text)
                return
            if p is not None:
                self.spelled(p, desig)
                code = self.call_code(p, args)
            else:
                v = self.lookup(n)
                if v is not None and v.base == "procedure":         # call of a procedure argument
                    iface = self.find_iface(v.tname)
                    argc = self.bound_args(iface, args, False) if iface is not None else self.plain_args(args)
                    callee = f"(*{v.cname})" if v.opt_kind else v.cname     # optional: std::optional<std::function>
                    self.emit(ind, f"{callee}({', '.join(argc)});")
                    return
                if v is not None:
                    raise Untranslatable("procedure-pointer-call")
                if n not in self.macro_calls:
                    self.report.add("info", "external-call:" + n)
                    self.note("external-call", "external procedure, no interface known")
                code = f"{str(desig)}({', '.join(self.plain_args(args))})"
        if not code.strip():
            return                              # empty method template: the call is not needed in C++
        self.emit(ind, code if code.startswith("//") or code.rstrip().endswith((";", "}")) else code + ";")
        self.last_err_call = (len(self.out) - 1, code) if had_err else None

    def pf_assert(self, name, args, ind):
        A = self.cfg["gtest_assert"]
        pos, kw = [], {}
        for a in args:
            k, n = self.split_arg(a)
            if k:
                kw[k] = n
            else:
                pos.append(n)
        xs = [self.expr(n) for n in pos]
        tol = self.expr(kw["tolerance"]).code if "tolerance" in kw else None
        if tol is None and name in ("equal", "relativelyequal") and len(xs) == 3:
            tol = xs.pop().code
        msg = f" << {self.expr(kw['message']).code}" if "message" in kw else ""
        if msg == "" and name in ("true", "false") and len(xs) == 2:
            msg = f" << {xs.pop().code}"
        c = [x.code for x in xs]
        simple = {"true": "TRUE", "false": "FALSE", "lessthan": "LT", "greaterthan": "GT",
                  "lessthanorequal": "LE", "greaterthanorequal": "GE", "notequal": "NE"}
        if name == "equal" and len(xs) == 2:
            e, a = xs
            if e.rank == 0 and a.rank == 0:
                code = f"{A}_NEAR({e.code}, {a.code}, {tol})" if tol else f"{A}_EQ({e.code}, {a.code})"
            elif e.rank > 0 and a.rank > 0 and self.cfg["gtest_eigen_near"]:
                code = f"{A}_NEAR({self.matrix(e)}, {self.matrix(a)}, {tol or '0.0'})"
            else:
                if e.rank > 0 and a.rank > 0 and not (e.arr or a.arr):
                    diff = f"({par(e, P_ADD)} - {par(a, P_ADD, True)}).cwiseAbs().maxCoeff()"
                else:
                    diff = f"({self.as_array_p(e, P_ADD)} - {self.as_array_p(a, P_ADD, True)}).abs().maxCoeff()"
                code = f"{A}_LE({diff}, {tol or '0'})"
        elif name == "relativelyequal" and len(xs) == 2 and xs[0].rank == 0:
            code = f"{A}_NEAR({c[0]}, {c[1]}, {par(self.expr(kw['tolerance']), P_MUL) if 'tolerance' in kw else tol}" \
                   f" * std::abs({c[0]}))"
        elif name in ("true", "false") and len(xs) == 1 and c[0] in ("true", "false"):
            if (c[0] == "true") == (name == "true"):        # @assertTrue(.true.): nothing to check
                return
            code = ("FAIL()" if A == "ASSERT" else "ADD_FAILURE()")     # @assertTrue(.false.): this path fails
        elif name in simple and len(xs) == (1 if name in ("true", "false") else 2):
            if xs[0].rank > 0 and name in ("true", "false"):
                c[0] = f"({c[0]}).all()" if name == "true" else f"({c[0]}).any()"
            code = f"{A}_{simple[name]}({', '.join(c)})"
        elif name in ("isnan", "isfinite", "isinfinite") and len(xs) == 1:
            code = f"{A}_TRUE(std::{ {'isnan': 'isnan', 'isfinite': 'isfinite', 'isinfinite': 'isinf'}[name]}({c[0]}))"
        elif name in ("associated", "notassociated") and len(xs) == 1:
            code = f"{A}_{'NE' if name == 'associated' else 'EQ'}({xs[0].raw or c[0]}, nullptr)"
        elif name == "fail":
            code = f"FAIL() << {c[0]}" if c else "FAIL()"
        else:
            raise Untranslatable("pfunit-assert:" + name)
        self.emit(ind, code + msg + ";")

    def s_Return_Stmt(self, node, ind):
        self.emit(ind, self.return_code())

    def return_code(self):
        p = self.proc
        if p is not None and p.kind == "function":
            return f"return {p.vars[p.result].cname if p.result in p.vars else p.result};"
        return "return;"

    def s_Exit_Stmt(self, node, ind):
        if node.items[1] is not None:
            raise Untranslatable("named-exit")
        self.emit(ind, "break;")

    def s_Cycle_Stmt(self, node, ind):
        if node.items[1] is not None:
            raise Untranslatable("named-cycle")
        self.emit(ind, "continue;")

    def s_Continue_Stmt(self, node, ind):
        self.emit(ind, ";")

    def s_Stop_Stmt(self, node, ind):
        code = str(node.items[1]).strip("'\"") if node.items[1] is not None else ""
        self.emit(ind, f'throw std::runtime_error("stop {code}");')

    s_Error_Stop_Stmt = s_Stop_Stmt

    def alloc_dims(self, shape):
        sizes = []
        for s in shape:
            lo, hi = s.items
            if lo is None or str(lo).strip() == "1":
                sizes.append(self.expr(hi).code)
                continue
            if not re.fullmatch(r"-?\s*\d+", str(lo)):
                self.note("lower-bound", "variable lower bound: the index shift of this array is not known "
                                         "where it is used")
            if re.fullmatch(r"-?\s*\d+", str(lo)):
                sizes.append(self.minus(hi, int(str(lo).replace(" ", "")) - 1))
            else:
                sizes.append(f"{par(self.expr(hi), P_ADD)} - {par(self.expr(lo), P_ADD, True)} + 1")
        return sizes

    def s_Allocate_Stmt(self, node, ind):
        tspec, allocs, opts = node.items
        if self.via_getters and any(self.ref(a.items[0] if cls(a) == "Allocation" else a).via_accessor
                                    for a in items_of(allocs)):
            raise Untranslatable("wrapped-component-allocate")   # an Eigen::Map of Fortran memory
        src = [o for o in items_of(opts) if lname(o.items[0]) == "source"]
        if src and len(items_of(allocs)) == 1:  # allocate(x, source=v) of class(*): std::any holds a copy
            x = self.ref(items_of(allocs)[0])
            if x.var is not None and x.var.base == "type" and x.var.tname == "*":
                self.emit(ind, f"{x.code} = {self.expr(src[0].items[1]).code};")
                return
        named = [a for a in items_of(allocs) if cls(a) == "Allocation" and cls(a.items[0]) == "Name"
                 and getattr(self.lookup(lname(a.items[0])), "named_elements", False)]
        if named:                               # named_elements: only the size (elements declared later)
            if len(named) != len(items_of(allocs)):
                raise Untranslatable("named-elements-allocate")
            for a in named:
                shape = items_of(a.items[1])
                lo, hi = shape[0].items if cls(shape[0]) == "Allocate_Shape_Spec" else (None, shape[0])
                if len(shape) != 1 or not str(hi).strip().isdigit() or (lo is not None and str(lo).strip() != "1"):
                    raise Untranslatable("named-elements-size")
                self.lookup(lname(a.items[0])).named_size = int(str(hi).strip())
            return
        for o in items_of(opts):
            if lname(o.items[0]) != "stat":
                raise Untranslatable("allocate-option:" + lname(o.items[0]))
        for a in items_of(allocs):
            obj, shape = (a.items[0], items_of(a.items[1])) if cls(a) == "Allocation" else (a, [])
            x = self.ref(obj)
            v = x.var
            if v is None:
                raise Untranslatable("allocate-unknown")
            st = "uptr" if x.uptr_elem else self.storage(v)
            name = x.raw if x.raw is not None else x.code
            dims = self.alloc_dims(shape)
            for cv in (v.companions or {}).values() if x.whole and not x.uptr_elem else ():
                cn = self.companion_code(name, v, cv)       # companions of unwrapped types: same shape
                self.emit(ind, f"{cn}.resize({dims[0]});")
            if st in ("uptr", "ptr"):
                t = self.prog.type_name(lname(tspec), self.mod) if tspec is not None else self.elem_type(v)
                self.emit(ind, f"{name} = std::make_unique<{t}>();" if st == "uptr" else f"{name} = new {t}();")
                if st == "ptr":
                    self.note("pointer", "raw owning pointer")
            elif st in ("eigen", "objmat"):
                self.emit(ind, f"{name}.resize({', '.join(dims)});")
            elif st == "flat":
                self.note("flat-array", f"rank-{v.rank} array stored flat (column-major)")
                self.emit(ind, f"{name}.resize({' * '.join(f'({d})' if not d.isalnum() else d for d in dims)});")
            elif st == "stdvec" and v.rank == 1:
                self.emit(ind, f"{name}.resize({dims[0]});")
            elif st == "stdvec":
                # resize instead of assign: the elements need not be copyable
                code, src = f"{name}.clear(); {name}.resize({dims[0]});", name
                for k, n in enumerate(dims[1:]):
                    code += f" for (auto& r{k}_ : {src}) {{ r{k}_.resize({n});"
                    src = f"r{k}_"
                self.emit(ind, code + " }" * (len(dims) - 1))
            else:
                self.emit(ind, f"{name}.assign({self.nested_ctor(v, dims)});")

    def s_Deallocate_Stmt(self, node, ind):
        if self.via_getters and any(self.ref(a).via_accessor for a in items_of(node.items[0])):
            raise Untranslatable("wrapped-component-allocate")
        if all(cls(a) == "Name" and getattr(self.lookup(lname(a)), "named_elements", False)
               for a in items_of(node.items[0])):
            return                              # named_elements: end of scope
        for a in items_of(node.items[0]):
            x = self.ref(a)
            st = "uptr" if x.uptr_elem else self.storage(x.var) if x.var is not None else None
            name = x.raw if x.raw is not None else x.code
            for cv in (x.var.companions or {}).values() if x.var is not None and not x.uptr_elem else ():
                cn = self.companion_code(name, x.var, cv)
                self.emit(ind, f"{cn}.clear();" if self.storage(cv) == "stdvec" else f"{cn}.resize(0);")
            if st == "uptr":
                self.emit(ind, f"{name}.reset();")
            elif st in ("eigen", "objmat"):
                self.emit(ind, f"{name}.resize({', '.join(['0'] * x.var.rank)});")
            elif st == "flat" and x.var.base not in ("type", "character"):
                self.emit(ind, f"{name}.resize(0);")
            elif st in ("vec3", "stdvec", "flat"):
                self.emit(ind, f"{name}.clear();")
            else:
                raise Untranslatable("deallocate")

    def companion_code(self, name, v, cv):
        """code of a companion variable from the code of the unwrapped variable (x.arr -> x.arr_offset)"""
        if not name.endswith(v.cname):
            raise Untranslatable("unwrapped-companion")
        return name[:-len(v.cname)] + cv.cname

    def s_Nullify_Stmt(self, node, ind):
        for a in items_of(node.items[1]):
            x = self.expr(a)
            if x.var is not None and x.var.unwrapped and self.storage(x.var) == "eigen":
                self.emit(ind, f"{x.code}.resize({', '.join(['0'] * x.var.rank)});")
                continue
            self.emit(ind, f"{x.raw or x.code} = nullptr;")

    def s_If_Stmt(self, node, ind):
        cond = X(self.condition(node.items[0]))
        sub = Translator(self.prog, self.mod, self.proc, self.report)
        # same translation state: overrides, shifted loop variables, loop declarations
        sub.named, sub.via_getters = self.named, self.via_getters
        sub.overrides, sub.shift, sub.loop_local, sub.loop_decl = \
            self.overrides, self.shift, self.loop_local, self.loop_decl
        sub.body_decl = self.body_decl
        sub.stmt(node.items[1], 0)
        if len(sub.out) == 1 and not sub.out[0].startswith("//"):
            self.emit(ind, f"if ({cond.code}) {sub.out[0]}")
        else:
            self.emit(ind, f"if ({cond.code}) {{")
            for line in sub.out:
                self.emit(ind + 1, line)
            self.emit(ind, "}")

    def io_pieces(self, fmt, items):
        """output items in the order of a (simple) format -> list of X; string literals of the format
        become character X"""
        xs = [self.expr(i) for i in items]
        if any(x.rank > 0 and x.base == "character" for x in xs):
            raise Untranslatable("write:string-array")
        fmt = (fmt or "*").strip()
        if fmt == "*":
            out = []
            for x in xs:
                out += [X('" "', 0, "character"), x] if out else [x]
            return out
        if not re.fullmatch(r"""['"]\(.*\)['"]""", fmt):
            raise Untranslatable("write:format-variable")
        out, rest = [], list(xs)
        for tok in re.findall(r"""'(?:[^']|'')*'|"[^"]*"|[^,'"]+""", fmt[2:-2]):
            t = tok.strip().lower()
            if not t:
                continue
            if tok.strip()[0] in "'\"":
                out.append(X(cstring(tok.strip()), 0, "character"))
            elif re.fullmatch(r"\d*x", t):
                out.append(X('"' + " " * int(t[:-1] or 1) + '"', 0, "character"))
            elif t == "/":
                out.append(X('"\\n"', 0, "character"))
            elif re.fullmatch(r"a\d*|i0|g0", t) and rest:
                out.append(rest.pop(0))
            else:
                raise Untranslatable("write:format-descriptor")
        if rest:
            raise Untranslatable("write:format-items")
        return out

    def io_emit(self, unit, pieces, newline, ind):
        if unit is None:                                    # standard output
            code = "std::cout" + "".join(f" << {par(p, P_ADD, True)}" for p in pieces)
            self.emit(ind, code + (" << std::endl;" if newline else ";"))
            return
        parts = []                                          # internal write: build a std::string
        for p in pieces:
            if p.base not in ("character", "integer", "real", "logical") or (p.rank > 0 and p.base == "character"):
                raise Untranslatable("write:internal-item")
            parts.append(par(p, P_ADD, True) if p.base == "character" else
                         f"to_string({self.matrix(p)})" if p.rank > 0 else f"std::to_string({p.code})")
            if p.base == "real":
                self.note("format", "std::to_string formats floating point numbers differently")
        if not parts or parts[0].startswith('"'):
            parts[:1] = [f"std::string({parts[0] if parts else ''})"]
        self.emit(ind, f"{unit.raw or unit.code} = {' + '.join(parts)};")

    def s_Print_Stmt(self, node, ind):
        self.io_emit(None, self.io_pieces(str(node.items[0]), items_of(node.items[1])), True, ind)

    def s_Write_Stmt(self, node, ind):
        unit = fmt = None
        newline = True
        for i, spec in enumerate(items_of(node.items[0])):
            kw = lname(spec.items[0]) if spec.items[0] is not None else ("unit", "fmt")[min(i, 1)]
            val = spec.items[1]
            if kw == "unit":
                unit = val
            elif kw == "fmt":
                fmt = str(val)
            elif kw == "advance" and str(val).strip("'\" ").lower() == "no":
                newline = False
            elif kw != "advance":
                raise Untranslatable("write:" + kw)
        target = None
        if str(unit).strip() != "*":
            target = self.expr(unit)
            if target.base != "character" or target.rank > 0:
                raise Untranslatable("write:unit" if target.base != "character" else "write:string-array")
        self.io_emit(target, self.io_pieces(fmt, items_of(node.items[1])), newline, ind)

    def s_Where_Stmt(self, node, ind):
        mask, assign = self.expr(node.items[0]), node.items[1]
        lhs, rhs = self.expr(assign.items[0]), self.expr(assign.items[2])
        if mask.rank == 0 or lhs.rank not in (1, 2):
            raise Untranslatable("where")
        self.emit(ind, f"{lhs.code} = ({mask.code}).select({self.matrix(rhs)}, {lhs.code});")

    # constructs
    def condition(self, node):
        """condition of an if; if it cannot be translated (e.g. it tests the error flag), the body is
        still translated and the condition becomes `false` with the Fortran text as TODO"""
        try:
            if self.err_uses(node):
                raise Untranslatable("error-arg")
            return self.expr(node).code
        except Untranslatable as e:
            self.report.add("untranslated", "condition:" + e.category)
            why = "error flag, replace by try/catch" if e.category == "error-arg" else \
                f"not translated ({e.category})"
            return f"false /* {TODO}: {why}: {str(node).replace('*/', '* /')} */"

    def s_If_Construct(self, node, ind):
        for c in node.content:
            k = cls(c)
            if k == "If_Then_Stmt":
                self.emit(ind, f"if ({self.condition(c.items[0])}) {{")
            elif k == "Else_If_Stmt":
                self.emit(ind, f"}} else if ({self.condition(c.items[0])}) {{")
            elif k == "Else_Stmt":
                self.emit(ind, "} else {")
            elif k == "End_If_Stmt":
                self.emit(ind, "}")
            else:
                self.block([c], ind + 1)

    def index_uses(self, loop, n):
        """(uses of loop variable n as array index with lower bound 1, other uses, passed as argument)"""
        from fparser.two.utils import walk
        from fparser.two import Fortran2003 as F
        idx = other = 0
        arg = False
        for nm in walk(loop.content[1:-1], F.Name):
            if lname(nm) != n:
                continue
            expr, holder = nm, nm.parent
            if cls(holder) == "Level_2_Expr" and holder.items[0] is nm and holder.items[1] in "+-" and \
                    cls(holder.items[2]) == "Int_Literal_Constant":
                expr, holder = holder, holder.parent
            if cls(holder) == "Section_Subscript_List" and cls(holder.parent) == "Part_Ref":
                ref = holder.parent
                pos = list(holder.items).index(expr) if expr in holder.items else 0
                if cls(ref.parent) == "Data_Ref" and ref.parent.items[0] is not ref:
                    idx += 1                    # component array: lower bound assumed 1
                    continue
                v = self.lookup(lname(ref.items[0]))
                if v is None:
                    arg |= self.lookup_proc(lname(ref.items[0])) is not None
                    other += 1
                elif v.rank > pos and self.lower_bounds(v)[pos] is None:
                    idx += 1
                else:
                    other += 1
            elif cls(holder) in ("Actual_Arg_Spec_List", "Actual_Arg_Spec", "Component_Spec_List") and \
                    cls(holder.parent if cls(holder) == "Actual_Arg_Spec_List" else holder.parent.parent) \
                    != "Intrinsic_Function_Reference":
                arg = True                      # by reference: a shifted value is no lvalue
            else:
                other += 1
        return idx, other, arg

    def s_Block_Nonlabel_Do_Construct(self, node, ind):
        head = node.content[0]
        if "Label" in cls(head) or node.content[-1].items[1] is not None:
            raise Untranslatable("named-do")
        ctrl = head.items[1]
        n, shift = None, 0
        if ctrl is None:
            self.emit(ind, "while (true) {")
        elif ctrl.items[0] is not None:
            self.emit(ind, f"while ({self.expr(ctrl.items[0]).code}) {{")
        else:
            var, bounds = ctrl.items[1]
            n = lname(var)
            v = self.lookup(n)
            i = self.expr(var).code
            decl = f"{self.cpp_type(v)} " if n in self.loop_decl and v is not None else ""
            st = self.expr(bounds[2]).code if len(bounds) > 2 else "1"
            if n in self.loop_local and st in ("1", "-1"):
                idx, other, arg = self.index_uses(node, n)
                shift = 1 if idx > other and not arg else 0
            lo, hi = self.minus(bounds[0], shift), self.expr(bounds[1])
            if st == "1":
                cond = f"{i} < {par(hi, P_REL, True)}" if shift else f"{i} <= {par(hi, P_REL, True)}"
                inc = f"++{i}"
            elif re.fullmatch(r"-\d+", st):
                end = self.minus(bounds[1], shift) if shift else par(hi, P_REL, True)
                cond, inc = f"{i} >= {end}", f"--{i}" if st == "-1" else f"{i} -= {st[1:]}"
            elif re.fullmatch(r"\d+", st):
                cond, inc = f"{i} <= {par(hi, P_REL, True)}", f"{i} += {st}"
            else:
                raise Untranslatable("do-step-sign")
            self.emit(ind, f"for ({decl}{i} = {lo}; {cond}; {inc}) {{")
        if shift:
            self.shift[n] = shift
        try:
            self.block(node.content[1:-1], ind + 1)
        finally:
            self.shift.pop(n, None)
        self.emit(ind, "}")

    def s_Case_Construct(self, node, ind):
        sel = self.expr(node.content[0].items[0])
        s = par(sel, P_REL, True)
        first = True
        for c in node.content[1:]:
            k = cls(c)
            if k == "Case_Stmt":
                vals = c.items[0].items[0]
                if vals is None:
                    self.emit(ind, "} else {" if not first else "{")
                else:
                    conds = []
                    for val in items_of(vals):
                        if cls(val) == "Case_Value_Range":
                            lo, hi = val.items[0], val.items[1]
                            parts = ([f"{s} >= {self.expr(lo).code}"] if lo is not None else []) + \
                                    ([f"{s} <= {self.expr(hi).code}"] if hi is not None else [])
                            conds.append(" && ".join(parts))
                        else:
                            conds.append(f"{s} == {par(self.expr(val), P_REL, True)}")
                    if len(conds) > 1:
                        conds = [f"({c})" if "&&" in c else c for c in conds]
                    self.emit(ind, f"{'if' if first else '} else if'} ({' || '.join(conds)}) {{")
                first = False
            elif k == "End_Select_Stmt":
                self.emit(ind, "}")
            else:
                self.block([c], ind + 1)

    def callback_cast(self, node, ind):
        """select type of the user data in a callback implementation: only the body of the guard"""
        p = self.proc
        assoc = node.content[0].items[0]
        saved = dict(self.overrides)
        self.overrides[lname(assoc) if assoc is not None else p.cb_arg] = p.vars[p.cb_arg]
        guard, default = None, []
        for c in node.content[1:]:
            k = cls(c)
            if k == "Type_Guard_Stmt":
                guard = c.items[1] is not None
            elif k == "End_Select_Type_Stmt":
                break
            elif guard:
                self.block([c], ind)
            elif guard is False and cls(c) != "Comment":
                default.append(c)
            elif guard is False and str(c).startswith(CPP_MARK):
                self.comment(c, ind)                    # keep #ifdef ... balanced
        self.overrides = saved
        # class default: usually an assertion that cannot fail any more (the type is static in C++)
        trivial = ("Return_Stmt", "Stop_Stmt", "Error_Stop_Stmt", "Continue_Stmt")
        if any(cls(c) not in trivial and not (cls(c) == "Call_Stmt" and lname(c.items[0]) in self.macro_calls)
               and not (cls(c) == "Assignment_Stmt" and self.is_err(c.items[0])) for c in default):
            self.report.add("todo", "callback-default")
            for c in default:
                self.fortran_comment(c, ind, "class default of the user data cast (dropped)")

    def s_Select_Type_Construct(self, node, ind):
        if self.proc is not None and self.proc.cb_select is not None and \
                (node.node if cls(node) == "_Trimmed" else node) is self.proc.cb_select:
            return self.callback_cast(node, ind)
        head = node.content[0]
        assoc, sel_node = head.items
        sel = self.expr(sel_node)
        if sel.var is None and assoc is None:
            raise Untranslatable("select-type-selector")
        alias = lname(assoc) if assoc is not None else sel.var.name
        const = "const " if sel.var is not None and (sel.var.is_const or self.in_const_method(sel)) else ""
        obj = par(sel, P_UNARY)
        any_sel = sel.var is not None and sel.var.base == "type" and sel.var.tname == "*"
        first, saved = True, dict(self.overrides)
        alias_line = None

        def drop_unused_alias():
            if alias_line is not None and not any(alias_line[1] in line for line in self.out[alias_line[0] + 1:]):
                del self.out[alias_line[0]]

        for c in node.content[1:]:
            k = cls(c)
            if k in ("Type_Guard_Stmt", "End_Select_Type_Stmt"):
                drop_unused_alias()
                alias_line = None
            if k == "Type_Guard_Stmt":
                self.overrides = dict(saved)
                kind, tn = str(c.items[0]).upper(), c.items[1]
                kw = "if" if first else "} else if"
                first = False
                if tn is None:
                    self.emit(ind, "} else {" if kw != "if" else "{")
                    continue
                if any_sel:                     # class(*) as std::any: type() / any_cast
                    t = self.guard_type(tn)
                    new = f"{alias}_{re.sub(NON_WORD, '_', t.split('::')[-1])}"
                    self.emit(ind, f"{kw} ({obj}.type() == typeid({t})) {{")
                    self.emit(ind + 1, f"auto& {new} = std::any_cast<const {t}&>({obj});")   # read access
                    alias_line = (len(self.out) - 1, new)
                    self.overrides[alias] = Var(alias, new, *self.guard_var(tn), is_const=bool(const))
                    continue
                t = self.prog.type_name(lname(tn), self.mod)
                new = f"{alias}_{re.sub(NON_WORD, '_', t.split('::')[-1])}"
                if kind == "TYPE IS":
                    self.emit(ind, f"{kw} (typeid({obj}) == typeid({t})) {{")
                else:
                    self.emit(ind, f"{kw} (dynamic_cast<const {t}*>(&{obj}) != nullptr) {{")
                self.emit(ind + 1, f"auto& {new} = static_cast<{const}{t}&>({obj});")
                alias_line = (len(self.out) - 1, new)
                self.overrides[alias] = Var(alias, new, "type", lname(tn), is_const=bool(const))
            elif k == "End_Select_Type_Stmt":
                self.emit(ind, "}")
            else:
                self.block([c], ind + 1)
        self.overrides = saved

    def guard_type(self, tn):
        """C++ type of a type guard (intrinsic types of class(*) selectors)"""
        base = self.guard_var(tn)
        if base[0] != "type":
            return self.elem_type(Var("", "", base[0], kind=base[2]))
        return self.prog.type_name(base[1], self.mod)

    def guard_var(self, tn):
        """(base, tname, kind) of a type guard's type"""
        if cls(tn) == "Intrinsic_Type_Spec":
            base = str(tn.items[0]).lower().replace("double precision", "real")
            kind = None
            if tn.items[1] is not None:
                kind = re.sub(r"\s|kind=|[()]", "", str(tn.items[1]).lower())
            return base, None, kind
        return "type", lname(tn), None

    def in_const_method(self, x):
        p = self.method_proc()
        return bool(p and p.pass_arg and p.vars[p.pass_arg].intent == "in" and (x.on_this or x.code == "*this"))


def last_line(node):
    """last source line of a statement / construct (None if unknown)"""
    item = getattr(node, "item", None)
    if item is not None:
        return item.span[1]
    kids = getattr(node, "content", None)
    return last_line(kids[-1]) if kids else None


class _Trimmed:
    """construct without its leading comments"""

    def __init__(self, node, n):
        self.node, self.content = node, node.content[n:]

    def __str__(self):
        return "\n".join(str(c) for c in self.content)


def match_name(name, table):
    """value of name in table: exact key, else the first glob pattern that matches"""
    import fnmatch
    if name in table:
        return table[name]
    return next((v for k, v in table.items() if any(ch in k for ch in "*?[") and fnmatch.fnmatch(name, k)), None)


def postfix_only(code):
    """no operator outside of parentheses / brackets: a member call can be appended directly"""
    depth = 0
    for ch in code:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch in " +-*/!&|?":
            return False
    return True


def is_zero(code):
    return re.fullmatch(r"0|0?\.0*(e[+-]?\d+)?|0\.0*", code) is not None and code != "."


# ---------------------------------------------------------------------- intrinsic table
def _args(tr, pos):
    return [tr.expr(p) for p in pos]


def i_size(tr, n, pos, kw):
    x = tr.ref(pos[0])
    dim = pos[1] if len(pos) > 1 else kw.get("dim")
    st = tr.storage(x.var) if x.var is not None and x.whole else None
    b = par(x)
    depth, lr = tr.nest(x.var) if st else (0, x.rank)
    if dim is None:
        if depth and (depth > 1 or lr):
            raise Untranslatable("size-of-nested-vector")
        if depth:       # std::vector::size() is unsigned
            return X(f"static_cast<{tr.cfg['int']}>({b}.size())", 0, "integer")
        return X(f"{b}.size()", 0, "integer")
    d = tr.expr(dim).code
    if st == "flat" and d.isdigit() and 1 <= int(d) <= x.var.rank:
        return X(tr.extents(x.var)[int(d) - 1], 0, "integer")
    if depth and d.isdigit() and 1 <= int(d) <= x.var.rank:
        if int(d) <= lr:
            return X(f"{b}{'[0]' * depth}.{'rows' if d == '1' else 'cols'}()", 0, "integer")
        return X(f"static_cast<{tr.cfg['int']}>({b}{'[0]' * (int(d) - lr - 1)}.size())", 0, "integer")
    if d not in ("1", "2"):
        raise Untranslatable("size-dim")
    return X(f"{b}.size()" if x.rank == 1 else f"{b}.{'rows' if d == '1' else 'cols'}()", 0, "integer")


def i_matmul(tr, n, pos, kw):
    a, b = _args(tr, pos)
    a.code, b.code = tr.matrix(a), tr.matrix(b)
    a.prec = 100 if a.arr else a.prec
    b.prec = 100 if b.arr else b.prec
    if a.rank == 1 and b.rank == 2:
        return X(f"({par(a)}.transpose() * {par(b, P_MUL, True)}).transpose()", 1, "real")
    return X(f"{par(a, P_MUL)} * {par(b, P_MUL, True)}", 1 if b.rank == 1 else 2, "real", P_MUL)


def i_method(method, rank=None, base=None):
    def f(tr, n, pos, kw):
        x = tr.expr(pos[0])
        if len(pos) > 1 or kw:
            raise Untranslatable(f"intrinsic:{n}(dim/mask)")
        code = f"({tr.matrix(x)})" if x.arr else par(x)
        return X(f"{code}.{method}()", x.rank if rank is None else rank, base or x.base)
    return f


def i_transpose(tr, n, pos, kw):
    tr.saw_transpose = True
    return i_method("transpose", 2)(tr, n, pos, kw)


def i_reduce(method):
    def f(tr, n, pos, kw):
        if method in ("any", "all") and cls(pos[0]) == "Array_Constructor" and len(pos) == 1 and not kw:
            # any([a, b, c]) -> a || b || c
            op, p = (" || ", P_OR) if method == "any" else (" && ", P_AND)
            xs = tr.ac_items(pos[0])
            return X(op.join(par(x, P_AND, True) for x in xs), 0, "logical", p if len(xs) > 1 else xs[0].prec)
        x = tr.expr(pos[0])
        dim = pos[1] if len(pos) == 2 else kw.get("dim")
        if dim is not None and x.rank == 2 and set(kw) <= {"dim"} and not x.arr and \
                tr.expr(dim).code in ("1", "2") and method in ("sum", "prod", "maxCoeff", "minCoeff"):
            if tr.expr(dim).code == "1":
                return X(f"{par(x)}.colwise().{method}().transpose()", 1, x.base)
            return X(f"{par(x)}.rowwise().{method}()", 1, x.base)
        if len(pos) > 1 or kw:
            raise Untranslatable(f"intrinsic:{n}(dim/mask)")
        if x.rank == 0:
            raise Untranslatable(f"intrinsic:{n}(scalar)")
        return X(f"{par(x)}.{method}()", 0, x.base if method not in ("any", "all", "count") else
                 ("integer" if method == "count" else "logical"))
    return f


def i_dot(tr, n, pos, kw):
    a, b = _args(tr, pos)
    return X(f"{par(a)}.dot({tr.matrix(b)})", 0, "real")


def i_abs(tr, n, pos, kw):
    x = tr.expr(pos[0])
    if x.rank > 0:
        return X(f"{par(x)}.abs()" if x.arr else f"{par(x)}.cwiseAbs()", x.rank, x.base, arr=x.arr)
    return X(f"std::abs({x.code})", 0, x.base)


def i_math1(tr, n, pos, kw):
    x = tr.expr(pos[0])
    if x.rank > 0:
        return X(f"{tr.as_array(x) if not x.arr else par(x)}.{n}()", x.rank, "real", arr=True)
    return X(f"std::{n}({x.code})", 0, "real")


def i_std(fn, base="real"):
    def f(tr, n, pos, kw):
        xs = _args(tr, pos)
        if any(x.rank > 0 for x in xs):
            raise Untranslatable(f"intrinsic:{n}(array)")
        return X(f"std::{fn}({', '.join(x.code for x in xs)})", 0, base)
    return f


def i_minmax(tr, n, pos, kw):
    xs = _args(tr, pos)
    if any(x.rank > 0 for x in xs):
        if len(xs) != 2:
            raise Untranslatable(f"intrinsic:{n}(array)")
        a, b = xs
        if a.rank == 0:
            a, b = b, a
        return X(f"{par(a)}.cwise{n.capitalize()}({b.code})", a.rank, a.base)
    bases = {x.base for x in xs}
    t = "" if len(bases) == 1 and "unknown" not in bases else f"<{tr.cfg['scalar']}>"
    base = xs[0].base if not t else "real"
    if len(xs) == 2:
        return X(f"std::{n}{t}({xs[0].code}, {xs[1].code})", 0, base)
    return X(f"std::{n}{t}({{{', '.join(x.code for x in xs)}}})", 0, base)


def i_mod(tr, n, pos, kw):
    a, b = _args(tr, pos)
    if a.base == "integer" and b.base == "integer":
        if n == "modulo":
            return X(f"(({par(a, P_MUL)} % {par(b, P_MUL, True)}) + {par(b, P_ADD)}) % {par(b, P_MUL, True)}",
                     0, "integer", P_MUL)
        return X(f"{par(a, P_MUL)} % {par(b, P_MUL, True)}", 0, "integer", P_MUL)
    if n == "modulo":
        raise Untranslatable("intrinsic:modulo(real)")
    return X(f"std::fmod({a.code}, {b.code})", 0, "real")


def i_cast(base, wrap=None):
    def f(tr, n, pos, kw):
        x = tr.expr(pos[0])
        t = tr.cfg["scalar"] if base == "real" else tr.cfg["int"]
        kind = pos[1] if len(pos) > 1 else kw.get("kind")
        if kind is not None:
            t = tr.cfg["kind_map"].get(lname(kind), t)
        if x.rank > 0:
            if wrap or x.rank > 2:
                raise Untranslatable(f"intrinsic:{n}(array)")
            return X(f"{par(x)}.cast<{t}>()", x.rank, base, arr=x.arr)
        inner = f"std::{wrap}({x.code})" if wrap else x.code
        return X(f"static_cast<{t}>({inner})", 0, base)
    return f


def i_present(tr, n, pos, kw):
    v = tr.lookup(lname(pos[0]))
    if v is None or not v.opt_kind:
        raise Untranslatable("present")
    if v.opt_kind == "empty":
        if tr.storage(v) in ("stdvec", "vec3"):
            return X(f"!{v.cname}.empty()", 0, "logical", P_UNARY, neg=X(f"{v.cname}.empty()", 0, "logical"))
        return X(f"{v.cname}.size() > 0", 0, "logical", P_REL, neg=X(f"{v.cname}.size() == 0", 0, "logical", P_EQ))
    return X(f"{v.cname}.has_value()", 0, "logical")


def i_allocated(tr, n, pos, kw):
    x = tr.ref(pos[0])
    name = x.raw if x.raw is not None else par(x)
    if x.var is not None and x.var.base == "procedure":
        return X(name, 0, "logical", neg=X(f"!{name}", 0, "logical", P_UNARY))
    if x.var is None:       # unknown object (type from a module that is not parsed): assume an Eigen array
        tr.note("allocated", f"{n}() of an object of unknown type")
        st = "eigen"
    else:
        st = tr.storage(x.var)
    if st in ("uptr", "ptr"):
        if len(pos) > 1:
            return X(f"{name} == &{par(tr.expr(pos[1]), P_UNARY)}", 0, "logical", P_EQ)
        return X(f"{name} != nullptr", 0, "logical", P_EQ, neg=X(f"{name} == nullptr", 0, "logical", P_EQ))
    if st in ("eigen", "objmat") or (st == "flat" and x.var.base not in ("type", "character")):
        return X(f"{name}.size() > 0", 0, "logical", P_REL, neg=X(f"{name}.size() == 0", 0, "logical", P_EQ))
    return X(f"!{name}.empty()", 0, "logical", P_UNARY, neg=X(f"{name}.empty()", 0, "logical"))


def i_limits(member):
    def f(tr, n, pos, kw):
        x = tr.expr(pos[0])
        t = tr.elem_type(x.var) if x.var is not None else \
            (tr.cfg["int"] if x.base == "integer" else tr.cfg["scalar"])
        return X(f"std::numeric_limits<{t}>::{member}()", 0, x.base)
    return f


def i_merge(tr, n, pos, kw):
    a, b, c = _args(tr, pos)
    if a.rank or b.rank or c.rank:
        raise Untranslatable("intrinsic:merge(array)")
    return X(f"({par(c, P_OR)} ? {par(a, P_OR)} : {par(b, P_OR)})", 0, a.base)


def i_reshape(tr, n, pos, kw):
    src = tr.expr(pos[0])
    shape = pos[1] if len(pos) > 1 else kw.get("shape")
    dims = items_of(shape.items[1]) if cls(shape) == "Array_Constructor" else []
    if len(dims) != 2 or src.rank == 0 or len(pos) > 2 or set(kw) - {"shape"}:
        raise Untranslatable("intrinsic:reshape")
    return X(f"{par(src)}.reshaped({', '.join(tr.expr(d).code for d in dims)})", 2, src.base)


def i_sign(tr, n, pos, kw):
    a, b = _args(tr, pos)
    if a.rank or b.rank or a.base == "integer":
        raise Untranslatable("intrinsic:sign")
    return X(f"std::copysign({a.code}, {b.code})", 0, "real")


def i_len(tr, n, pos, kw):
    x = tr.expr(pos[0])
    if n == "len_trim":
        tr.note("string", "len_trim: std::string has no trailing blanks")
    return X(f"{par(x)}.size()", 0, "integer")


def i_identity(tr, n, pos, kw):
    return tr.expr(pos[0])        # std::string is not blank-padded


def i_bound(tr, n, pos, kw):
    """lbound / ubound with the Fortran values (indices are shifted where they are used)"""
    x = tr.ref(pos[0])
    dim = pos[1] if len(pos) > 1 else kw.get("dim")
    if x.var is None or not x.whole or dim is None or not tr.expr(dim).code.isdigit():
        raise Untranslatable("intrinsic:" + n)
    lb = tr.lower_bounds(x.var)[int(tr.expr(dim).code) - 1]
    lo = tr.expr(lb) if lb is not None else X("1", 0, "integer")
    if n == "lbound":
        return lo
    size = i_size(tr, n, [pos[0], dim], {})
    if lo.code == "1":
        return size
    off = int(lo.code) - 1 if re.fullmatch(r"-?\d+", lo.code) else None
    if off is None:
        return X(f"{par(size, P_ADD)} + {par(lo, P_ADD)} - 1", 0, "integer", P_ADD)
    return X(f"{par(size, P_ADD)} {'+' if off > 0 else '-'} {abs(off)}", 0, "integer", P_ADD)


def i_loc(tr, n, pos, kw):
    """maxloc / minloc: 1-based position (independent of the lower bounds, as in Fortran)"""
    x = tr.expr(pos[0])
    dim = pos[1] if len(pos) > 1 else kw.get("dim")
    if set(kw) - {"dim"} or len(pos) > 2 or x.rank not in (1, 2) or (dim is not None and x.rank != 1):
        raise Untranslatable(f"intrinsic:{n}(mask/dim)")
    fn = "maxCoeff" if n == "maxloc" else "minCoeff"
    obj = f"({tr.matrix(x)})" if x.arr else par(x)
    i, iv = tr.cfg["int"], tr.cfg["ivec"]
    if x.rank == 2:
        return X(f"[&] {{ Eigen::Index r_, c_; {obj}.{fn}(&r_, &c_); "
                 f"return ({iv}(2) << r_ + 1, c_ + 1).finished(); }}()", 1, "integer")
    scalar = f"[&] {{ Eigen::Index i_; {obj}.{fn}(&i_); return static_cast<{i}>(i_) + 1; }}()"
    if dim is not None:
        return X(scalar, 0, "integer")
    return X(f"({iv}(1) << {scalar}).finished()", 1, "integer")


def i_null(tr, n, pos, kw):
    return X("nullptr")


INTRINSICS = {
    "size": i_size, "ubound": i_bound, "lbound": i_bound,
    "matmul": i_matmul, "transpose": i_transpose, "dot_product": i_dot,
    "sum": i_reduce("sum"), "product": i_reduce("prod"), "maxval": i_reduce("maxCoeff"),
    "minval": i_reduce("minCoeff"), "norm2": i_reduce("norm"),
    "any": i_reduce("any"), "all": i_reduce("all"), "count": i_reduce("count"),
    "abs": i_abs, "max": i_minmax, "min": i_minmax, "mod": i_mod, "modulo": i_mod,
    "atan2": i_std("atan2"), "hypot": i_std("hypot"), "aint": i_std("trunc"), "anint": i_std("round"),
    "isnan": i_std("isnan", "logical"), "ieee_is_nan": i_std("isnan", "logical"),
    "ieee_is_finite": i_std("isfinite", "logical"),
    "real": i_cast("real"), "dble": i_cast("real"), "float": i_cast("real"),
    "int": i_cast("integer"), "nint": i_cast("integer", "lround"),
    "floor": i_cast("integer", "floor"), "ceiling": i_cast("integer", "ceil"),
    "present": i_present, "allocated": i_allocated, "associated": i_allocated,
    "huge": i_limits("max"), "tiny": i_limits("min"), "epsilon": i_limits("epsilon"),
    "merge": i_merge, "reshape": i_reshape, "sign": i_sign,
    "len": i_len, "len_trim": i_len, "trim": i_identity, "adjustl": i_identity,
    "null": i_null, "maxloc": i_loc, "minloc": i_loc,
}
for _n in MATH1:
    INTRINSICS[_n] = i_math1
