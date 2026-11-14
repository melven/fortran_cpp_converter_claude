"""Expression and statement translation (Fortran parse tree -> C++ text)."""
import re
from dataclasses import dataclass

from .model import cls, lname, items_of, cname, Var
from .prepass import CPP_MARK, PF_MARK, BLANK_MARK, ASSERT_PREFIX

TODO = "TODO(f2cpp)"


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
    fwd: str = None          # optional dummy: the std::optional object itself
    obj: bool = False        # element of std::vector<mat>: a real matrix object, not a block


P_UNARY, P_MUL, P_ADD, P_REL, P_EQ, P_AND, P_OR, P_TERN = 90, 80, 70, 60, 55, 40, 30, 20
REL_OPS = {".lt.": "<", ".le.": "<=", ".gt.": ">", ".ge.": ">=", ".eq.": "==", ".ne.": "!=",
           "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "/=": "!="}
MATH1 = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh"}
EIGEN_ARRAY_FN = {"asin": "asin", "acos": "acos", "atan": "atan"}


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
        self.cfg = program.cfg
        self.err = self.cfg["error_arg"].lower()
        # macros that stay macros in C++ (replacement "call NAME(...)")
        self.macro_calls = {m.group(1).lower() for t in self.cfg["macros"].values()
                            for m in [re.match(r"\s*call\s+(\w+)", t, re.I)] if m}
        self.overrides = {}
        self.out = []
        self.notes = []            # soft TODOs of the current statement
        self.last_err_call = None  # (line index, code) of the last call with removed error arg

    # ------------------------------------------------------------------ lookup
    def lookup(self, n):
        if n in self.overrides:
            return self.overrides[n]
        if self.proc is not None and n in self.proc.vars:
            return self.proc.vars[n]
        for m in self.prog.visible(self.mod):
            if n in m.vars:
                return m.vars[n]
        return None

    def lookup_proc(self, n):
        for m in self.prog.visible(self.mod):
            if n in m.procs:
                return m.procs[n]
            if n in m.generics:
                for s in m.generics[n]:
                    if s in m.procs:
                        return m.procs[s]
        return None

    def find_type(self, tname):
        return self.prog.find_type(tname, self.mod) if tname else None

    def note(self, category, msg):
        self.report.add("todo", category)
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
        if v.base == "type":
            return self.prog.type_name(v.tname, self.mod)
        return "auto"

    def storage(self, v):
        """kind of container: scalar eigen vec3 stdvec uptr ptr"""
        if v.rank == 0:
            if v.base == "type" and v.has("allocatable"):
                return "uptr"
            return "ptr" if v.has("pointer") else "scalar"
        if v.base in ("real", "integer", "logical", "complex"):
            return "eigen" if v.rank <= 2 else "vec3"
        return "stdvec"

    def nest(self, v):
        """(depth of the std::vector nesting, rank of the Eigen object stored in it)

        numeric arrays of rank n > 2: (nested) std::vector of matrices, a(:, :, i, j) -> a[i][j]
        arrays of objects / strings of rank n: n nested std::vector, a(i, j) -> a[i][j]"""
        st = self.storage(v)
        return (v.rank - 2, 2) if st == "vec3" else (v.rank, 0) if st == "stdvec" else (0, v.rank)

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
        t = self.leaf_type(v)
        for _ in range(self.nest(v)[0]):
            t = f"std::vector<{t}>"
        return t

    def param_type(self, v):
        """type of a dummy argument"""
        t, st = self.cpp_type(v), self.storage(v)
        const = v.intent == "in"
        if v.opt_kind == "optional":
            return f"std::optional<{t}>" if st == "scalar" and v.base != "character" else \
                f"const std::optional<{t}>&"
        if v.opt_kind == "optref":
            return f"std::optional<std::reference_wrapper<{'const ' if const else ''}{t}>>"
        if v.has("allocatable") or v.has("pointer") or st in ("vec3", "stdvec"):
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
        v = self.lookup(n)
        if v is None:
            if n == self.err:
                raise Untranslatable("error-arg")
            p = self.lookup_proc(n)
            if p is None:
                self.report.add("info", "unknown-name")
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
            return X(code, r.rank, r.base, 100, tname=r.tname)
        return X(code)

    def ref_or_call(self, name_node, args, node):
        n = lname(name_node)
        v = self.lookup(n)
        if v is not None and cls(node) != "Intrinsic_Function_Reference" or (v is not None and v.rank > 0):
            return self.subscript(self.x_Name(name_node), v, items_of(args))
        if n in self.cfg["function_map"]:
            fn = self.cfg["function_map"][n]
            return X(f"{fn}({', '.join(self.plain_args(items_of(args)))})", 0,
                     "character" if fn == "std::to_string" else "unknown")
        p = self.lookup_proc(n)
        if p is not None:
            return self.result_x(self.call_code(p, items_of(args)), p)
        if n in INTRINSICS or cls(node) == "Intrinsic_Function_Reference":
            return self.intrinsic(n, items_of(args))
        t = self.find_type(n)
        if t is not None:
            self.note("structure-constructor", "structure constructor: check member order")
            args_c = ", ".join(self.expr(a.items[1] if cls(a) == "Component_Spec" else a).code
                               for a in items_of(args))
            return X(f"{self.prog.type_name(n, self.mod)}{{{args_c}}}", 0, "type", tname=n)
        self.report.add("info", "external-function")
        return X(f"{cname(str(name_node))}({', '.join(self.plain_args(items_of(args)))})")

    def x_Data_Ref(self, node):
        parts = list(node.items)
        cur = self.expr(parts[0])
        for p in parts[1:]:
            pname, subs = (p.items[0], items_of(p.items[1])) if cls(p) == "Part_Ref" else (p, None)
            n = lname(pname)
            if cur.rank > 0:
                raise Untranslatable("component-of-array")
            td = self.find_type(cur.tname)
            sep = "->" if cur.raw is not None else "."
            base = (cur.raw if cur.raw is not None else par(cur)) + sep + cur.qual
            comp = self.prog.find_component(td, n) if td else None
            if comp is None:
                if td and any(a.name == n for a in self.prog.ancestors(td)):
                    cur = X(cur.code, 0, "type", cur.prec, tname=n, raw=cur.raw,
                            qual=self.prog.type_name(n, self.mod) + "::")
                    continue
                self.report.add("info", "unknown-component")
                x = X(base + cname(str(pname)))
                if subs is not None:
                    x = X(f"{x.code}({', '.join(self.plain_args(subs))})")
            else:
                x = self.var_ref(comp, base + comp.cname)
                if subs is not None:
                    x = self.subscript(x, comp, subs)
            cur = x
        return cur

    # index handling
    def minus(self, node, c):
        """translate integer expression 'node - c' with constant folding"""
        if c == 0:
            return self.expr(node).code
        k = cls(node)
        if k == "Int_Literal_Constant":
            return str(int(str(node.items[0])) - c)
        if k == "Level_2_Expr" and node.items[1] in "+-" and cls(node.items[2]) == "Int_Literal_Constant":
            lhs = self.expr(node.items[0])
            val = int(str(node.items[2].items[0])) * (1 if node.items[1] == "+" else -1) - c
            if val == 0:
                return lhs.code
            return f"{par(lhs, P_ADD)} {'+' if val > 0 else '-'} {abs(val)}"
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

    def sub_code(self, s, lb):
        """-> ('idx'|'all'|'seq', code)"""
        if cls(s) != "Subscript_Triplet":
            x = self.expr(s)
            if x.rank == 1 and (lb is None or cls(lb) == "Int_Literal_Constant"):
                # vector subscript (e.g. permutation); Eigen accepts index arrays
                off = 1 if lb is None else int(str(lb.items[0]))
                return "vidx", f"({self.as_array(x)} - {off})" if off else par(x)
            return "idx", self.index0(s, lb)
        lo, hi, step = s.items
        if lo is None and hi is None and step is None:
            return "all", "Eigen::all"
        a = self.index0(lo, lb) if lo is not None else "0"
        b = self.index0(hi, lb) if hi is not None else "Eigen::last"
        c = f", {self.expr(step).code}" if step is not None else ""
        return "seq", f"Eigen::seq({a}, {b}{c})"

    def subscript(self, x, v, subs):
        st = self.storage(v)
        if st in ("scalar", "uptr", "ptr") or len(subs) != v.rank:
            raise Untranslatable("substring" if v.base == "character" else "subscript")
        lbs = [d[0] for d in v.dims]
        base = par(x)
        res = X("", 0, v.base, 100, v, v.tname)
        depth, lr = self.nest(v)
        if depth:
            trail = [self.sub_code(s, lb) for s, lb in zip(subs[lr:], lbs[lr:])]
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
        sc = [self.sub_code(s, lb) for s, lb in zip(subs, lbs)]
        kinds = [k for k, _ in sc]
        res.rank = sum(k != "idx" for k in kinds)
        if len(sc) == 1:
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
            return X(f"{par(a, P_ADD)} + {par(b, P_ADD, True)}", 0, "character", P_ADD)
        if o in REL_OPS:
            c, p = REL_OPS[o], P_EQ if REL_OPS[o] in ("==", "!=") else P_REL
            if rank > 0:
                return X(f"{self.as_array_p(a, p)} {c} {self.as_array_p(b, p)}", rank, "logical", p, arr=True)
            return X(f"{par(a, p)} {c} {par(b, p, True)}", 0, "logical", p)
        if o in (".and.", ".or.", ".eqv.", ".neqv."):
            c, p = {".and.": ("&&", P_AND), ".or.": ("||", P_OR), ".eqv.": ("==", P_EQ),
                    ".neqv.": ("!=", P_EQ)}[o]
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
        if b.code in ("2", "3") and re.fullmatch(r"[\w.\[\]]+|[\w.\[\]]*(->[\w.\[\]]+)+", a.code):
            return X(" * ".join([a.code] * int(b.code)), 0, a.base, P_MUL)
        if a.base == "integer" and b.base == "integer":
            self.note("int-power", "integer power: std::pow returns floating point")
        return X(f"std::pow({a.code}, {b.code})", 0, "real")

    def matrix(self, x):
        """Matrix-world value of an expression (for assignment / function arguments)"""
        return f"({x.code}).matrix()" if x.arr and x.base != "logical" else x.code

    def x_Array_Constructor(self, node):
        vals = items_of(node.items[1])
        if cls(node.items[1]) == "Ac_Spec":
            vals = items_of(node.items[1].items[1])
        xs = [self.expr(v) for v in vals]
        if not xs or any(x.rank > 0 for x in xs):
            raise Untranslatable("array-constructor")
        if all(x.base == "character" for x in xs):
            return X("std::vector<std::string>{" + ", ".join(x.code for x in xs) + "}", 1, "character")
        base = "integer" if all(x.base == "integer" for x in xs) else "real"
        t = self.cfg["ivec"] if base == "integer" else self.cfg["vec"]
        return X(f"({t}({len(xs)}) << {', '.join(x.code for x in xs)}).finished()", 1, base)

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
            if self.is_err(node):
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
        res = []
        last = max([i for i, d in enumerate(dummies) if d in given], default=-1)
        for d in dummies[:last + 1]:
            dv = p.vars[d]
            if d not in given:
                res.append("std::nullopt" if dv.opt_kind else "nullptr")
                continue
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

    def call_code(self, p, args):
        """call of a known module procedure (free function or method called by implementation name)"""
        name = cname(p.generic or p.cname)
        if not p.method_of and self.proc is not None and self.proc.method_of:
            # a member of the same name would hide the free function
            t = self.find_type(self.proc.method_of)
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
        sep = "->" if obj.raw is not None else "."
        if p.is_ctor or p.is_dtor:
            plain = f"{obj.raw if obj.raw is not None else par(obj)}{sep}{p.cname}()"
            return self.lifetime_call(p, obj, self.bound_args(p, rest),
                                      obj.tname is not None and obj.tname != p.method_of, plain)
        return f"{obj.raw if obj.raw is not None else par(obj)}{sep}{qual}{p.bind_name}" \
               f"({', '.join(self.bound_args(p, rest))})"

    def method_call(self, desig, args):
        """obj%binding(args) -> (code, procedure or None)"""
        base_node, _, bname = desig.items
        obj = self.expr(base_node)
        n = lname(bname)
        td = self.find_type(obj.tname)
        owner, b = self.prog.find_binding(td, n) if td else (None, None)
        p = self.prog.binding_proc(owner, b) if owner else None
        if p is None and owner is not None and n in owner.generics:
            p0 = owner.generics[n][0]
            p = self.prog.binding_proc(owner, owner.bindings.get(p0))
            argc = self.plain_args(items_of(args))
        elif p is not None:
            argc = self.bound_args(p, items_of(args))
        else:
            self.report.add("info", "unknown-binding")
            argc = self.plain_args(items_of(args))
        sep = "->" if obj.raw is not None else "."
        code = f"{obj.raw if obj.raw is not None else par(obj)}{sep}{obj.qual}{cname(str(bname))}" \
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

    def fortran_comment(self, node, ind, why):
        self.emit(ind, f"// {TODO}: {why}")
        for line in str(node).split("\n"):
            if line.strip() and BLANK_MARK not in line:
                self.emit(ind, "// F: " + line.rstrip().replace(CPP_MARK, "").replace("!F2CPP_PF", "@"))

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
        text = s[1:]
        c = "///" + text[1:] if text[:1] == ">" else "///<" + text[1:] if text[:1] == "<" else "//" + text
        trailing = prev is not None and node.item is not None and last_line(prev) == node.item.span[0]
        if trailing and self.out and self.out[-1].strip() and not self.out[-1].lstrip().startswith("//"):
            self.out[-1] += "  " + c
        else:
            self.emit(ind, c)

    def block(self, nodes, ind):
        prev = None
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
        if k == "Assignment_Stmt" and self.is_err(node.items[0]) and not self.err_uses(node.items[2]):
            return
        if k == "If_Stmt" and self.only_err_names(node.items[0]) and cls(node.items[1]) == "Return_Stmt":
            return
        if k == "If_Construct" and self.only_err_names(node.content[0].items[0]) and \
                all(cls(c) in ("Return_Stmt", "Comment", "End_If_Stmt") for c in node.content[1:]):
            return
        if k == "Call_Stmt" and lname(node.items[0]).startswith(ASSERT_PREFIX + "assert") and \
                self.err_assert(node, ind):
            return
        self.report.add("untranslated", "error-arg:" + k)
        self.fortran_comment(node, ind, f"uses error flag '{self.err}' (replace by exception handling)")

    def err_assert(self, node, ind):
        """pFUnit assert on the error flag -> nothing (success) or *_ANY_THROW (failure expected)"""
        name = lname(node.items[0])[len(ASSERT_PREFIX) + 6:]
        args = [self.split_arg(a) for a in items_of(node.items[1])]
        pos = [str(n).replace(" ", "").lower() for kw, n in args if kw is None]
        e = self.err
        ok = (name == "equal" and sorted(pos) == ["0", e]) or \
             (name == "true" and pos in ([f"{e}==0"], [f"0=={e}"])) or \
             (name == "false" and pos in ([f"{e}/=0"], [f"0/={e}"]))
        fail = (name == "notequal" and sorted(pos) == ["0", e]) or \
               (name == "true" and pos in ([f"{e}/=0"], [f"0/={e}"])) or \
               (name == "false" and pos in ([f"{e}==0"], [f"0=={e}"]))
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
            if self.notes and k not in self.CONSTRUCTS and len(self.out) > n0:
                self.out[n0] += f"  // {TODO}: " + "; ".join(self.notes)
            self.report.add("translated", k)
        except Untranslatable as e:
            del self.out[start:]
            self.report.add("untranslated", e.category)
            self.fortran_comment(node, ind, f"not translated ({e.category})")
        except Exception as e:      # robust against unexpected parse tree shapes
            del self.out[start:]
            self.report.add("untranslated", f"internal:{type(e).__name__}:{k}")
            self.fortran_comment(node, ind, f"not translated (internal error: {type(e).__name__})")

    # simple statements
    def s_Assignment_Stmt(self, node, ind):
        lhs_n, _, rhs_n = node.items
        lhs = self.expr(lhs_n)
        v = lhs.var
        st = self.storage(v) if v is not None and lhs.whole else None
        if st == "vec3":
            r = self.expr(rhs_n)
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
        rhs = self.expr(rhs_n)
        if lhs.rank > 0 and rhs.rank == 0 and self.storage(v) in ("eigen", "vec3"):
            fill = ".setZero()" if is_zero(rhs.code) else f".setConstant({rhs.code})"
            self.emit(ind, f"{par(lhs)}{fill};")
            return
        if lhs.rank > 0 and rhs.rank == 0:
            raise Untranslatable("array=scalar")
        code = self.matrix(rhs)
        if lhs.rank > 0 and v is not None and self.saw_transpose and \
                re.search(rf"(?<![\w.>]){re.escape(v.cname)}\b", code.replace("this->", "")):
            self.note("aliasing", "check aliasing (transpose of the assigned variable)")
        self.emit(ind, f"{target} = {code};")

    def s_Pointer_Assignment_Stmt(self, node, ind):
        lhs, rhs = self.expr(node.items[0]), self.expr(node.items[2])
        if lhs.var is not None and lhs.var.alias:
            self.emit(ind, f"auto&& {lhs.var.cname} = {rhs.code};")
            return
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
            p = self.lookup_proc(n)
            if n == "move_alloc" and len(args) == 2:
                src, dst = self.expr(args[0]), self.expr(args[1])
                s_name = src.raw if src.raw is not None else src.code
                self.emit(ind, f"{dst.raw if dst.raw is not None else dst.code} = std::move({s_name});")
                if src.var is not None and self.storage(src.var) == "eigen":
                    self.emit(ind, f"{s_name}.resize({', '.join(['0'] * src.var.rank)});")
                elif src.var is not None and self.storage(src.var) in ("vec3", "stdvec"):
                    self.emit(ind, f"{s_name}.clear();")
                return
            if n in self.cfg["call_templates"]:
                codes = self.plain_args(args)

                def sub(m):
                    c = codes[int(m.group(1))]
                    return c.replace("\\", "\\\\").replace('"', '\\"') if m.group(2) else c
                self.emit(ind, re.sub(r"\{(\d+)(:str)?\}", sub, self.cfg["call_templates"][n]))
                return
            if p is not None:
                code = self.call_code(p, args)
            else:
                v = self.lookup(n)
                if v is not None:
                    raise Untranslatable("procedure-pointer-call")
                if n not in self.macro_calls:
                    self.report.add("info", "external-call:" + n)
                    self.note("external-call", "external procedure, no interface known")
                code = f"{str(desig)}({', '.join(self.plain_args(args))})"
        self.emit(ind, code if code.startswith("//") else code + ";")
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
            else:
                if e.rank > 0 and a.rank > 0 and not (e.arr or a.arr):
                    diff = f"({par(e, P_ADD)} - {par(a, P_ADD, True)}).cwiseAbs().maxCoeff()"
                else:
                    diff = f"({self.as_array_p(e, P_ADD)} - {self.as_array_p(a, P_ADD, True)}).abs().maxCoeff()"
                code = f"{A}_LE({diff}, {tol or '0'})"
        elif name == "relativelyequal" and len(xs) == 2 and xs[0].rank == 0:
            code = f"{A}_NEAR({c[0]}, {c[1]}, {par(self.expr(kw['tolerance']), P_MUL) if 'tolerance' in kw else tol}" \
                   f" * std::abs({c[0]}))"
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
            if lo is None:
                sizes.append(self.expr(hi).code)
            else:
                self.note("lower-bound", "array with non-default lower bound: check indices")
                sizes.append(f"{par(self.expr(hi), P_ADD)} - {par(self.expr(lo), P_ADD, True)} + 1")
        return sizes

    def s_Allocate_Stmt(self, node, ind):
        tspec, allocs, opts = node.items
        for o in items_of(opts):
            if lname(o.items[0]) != "stat":
                raise Untranslatable("allocate-option:" + lname(o.items[0]))
        for a in items_of(allocs):
            obj, shape = (a.items[0], items_of(a.items[1])) if cls(a) == "Allocation" else (a, [])
            x = self.expr(obj)
            v = x.var
            if v is None:
                raise Untranslatable("allocate-unknown")
            st = self.storage(v)
            name = x.raw if x.raw is not None else x.code
            dims = self.alloc_dims(shape)
            if st in ("uptr", "ptr"):
                t = self.prog.type_name(lname(tspec), self.mod) if tspec is not None else self.elem_type(v)
                self.emit(ind, f"{name} = std::make_unique<{t}>();" if st == "uptr" else f"{name} = new {t}();")
                if st == "ptr":
                    self.note("pointer", "raw owning pointer")
            elif st == "eigen":
                self.emit(ind, f"{name}.resize({', '.join(dims)});")
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
        for a in items_of(node.items[0]):
            x = self.expr(a)
            st = self.storage(x.var) if x.var is not None else None
            name = x.raw if x.raw is not None else x.code
            if st == "uptr":
                self.emit(ind, f"{name}.reset();")
            elif st == "eigen":
                self.emit(ind, f"{name}.resize({', '.join(['0'] * x.var.rank)});")
            elif st in ("vec3", "stdvec"):
                self.emit(ind, f"{name}.clear();")
            else:
                raise Untranslatable("deallocate")

    def s_Nullify_Stmt(self, node, ind):
        for a in items_of(node.items[1]):
            x = self.expr(a)
            self.emit(ind, f"{x.raw or x.code} = nullptr;")

    def s_If_Stmt(self, node, ind):
        cond = self.expr(node.items[0])
        sub = Translator(self.prog, self.mod, self.proc, self.report)
        sub.overrides = self.overrides
        sub.stmt(node.items[1], 0)
        if len(sub.out) == 1 and not sub.out[0].startswith("//"):
            self.emit(ind, f"if ({cond.code}) {sub.out[0]}")
        else:
            self.emit(ind, f"if ({cond.code}) {{")
            for line in sub.out:
                self.emit(ind + 1, line)
            self.emit(ind, "}")

    def s_Where_Stmt(self, node, ind):
        mask, assign = self.expr(node.items[0]), node.items[1]
        lhs, rhs = self.expr(assign.items[0]), self.expr(assign.items[2])
        if mask.rank == 0 or lhs.rank not in (1, 2):
            raise Untranslatable("where")
        self.emit(ind, f"{lhs.code} = ({mask.code}).select({self.matrix(rhs)}, {lhs.code});")

    # constructs
    def s_If_Construct(self, node, ind):
        for c in node.content:
            k = cls(c)
            if k == "If_Then_Stmt":
                self.emit(ind, f"if ({self.expr(c.items[0]).code}) {{")
            elif k == "Else_If_Stmt":
                self.emit(ind, f"}} else if ({self.expr(c.items[0]).code}) {{")
            elif k == "Else_Stmt":
                self.emit(ind, "} else {")
            elif k == "End_If_Stmt":
                self.emit(ind, "}")
            else:
                self.block([c], ind + 1)

    def s_Block_Nonlabel_Do_Construct(self, node, ind):
        head = node.content[0]
        if "Label" in cls(head) or node.content[-1].items[1] is not None:
            raise Untranslatable("named-do")
        ctrl = head.items[1]
        if ctrl is None:
            self.emit(ind, "while (true) {")
        elif ctrl.items[0] is not None:
            self.emit(ind, f"while ({self.expr(ctrl.items[0]).code}) {{")
        else:
            var, bounds = ctrl.items[1]
            i = self.expr(var).code
            lo, hi = self.expr(bounds[0]).code, self.expr(bounds[1])
            if len(bounds) == 2:
                cond, inc = f"{i} <= {par(hi, P_REL, True)}", f"++{i}"
            else:
                st = self.expr(bounds[2]).code
                if re.fullmatch(r"-\d+", st):
                    cond, inc = f"{i} >= {par(hi, P_REL, True)}", f"--{i}" if st == "-1" else f"{i} -= {st[1:]}"
                elif re.fullmatch(r"\d+", st):
                    cond, inc = f"{i} <= {par(hi, P_REL, True)}", f"{i} += {st}"
                else:
                    raise Untranslatable("do-step-sign")
            self.emit(ind, f"for ({i} = {lo}; {cond}; {inc}) {{")
        self.block(node.content[1:-1], ind + 1)
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

    def s_Select_Type_Construct(self, node, ind):
        head = node.content[0]
        assoc, sel_node = head.items
        sel = self.expr(sel_node)
        if sel.var is None and assoc is None:
            raise Untranslatable("select-type-selector")
        alias = lname(assoc) if assoc is not None else sel.var.name
        const = "const " if sel.var is not None and (sel.var.is_const or self.in_const_method(sel)) else ""
        obj = par(sel, P_UNARY)
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
                t = self.prog.type_name(lname(tn), self.mod)
                new = f"{alias}_{re.sub(r'\W', '_', t.split('::')[-1])}"
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

    def in_const_method(self, x):
        p = self.proc
        return bool(p and p.pass_arg and p.vars[p.pass_arg].intent == "in" and (x.code.startswith("this->")
                                                                               or x.code == "*this"))


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


def is_zero(code):
    return re.fullmatch(r"0|0?\.0*(e[+-]?\d+)?|0\.0*", code) is not None and code != "."


# ---------------------------------------------------------------------- intrinsic table
def _args(tr, pos):
    return [tr.expr(p) for p in pos]


def i_size(tr, n, pos, kw):
    x = tr.expr(pos[0])
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
            if wrap:
                raise Untranslatable(f"intrinsic:{n}(array)")
            return X(f"{par(x)}.cast<{t}>()", x.rank, base, arr=x.arr)
        inner = f"std::{wrap}({x.code})" if wrap else x.code
        return X(f"static_cast<{t}>({inner})", 0, base)
    return f


def i_present(tr, n, pos, kw):
    v = tr.lookup(lname(pos[0]))
    if v is None or not v.opt_kind:
        raise Untranslatable("present")
    return X(f"{v.cname}.has_value()", 0, "logical")


def i_allocated(tr, n, pos, kw):
    x = tr.expr(pos[0])
    if x.var is None:
        raise Untranslatable("allocated")
    st = tr.storage(x.var)
    name = x.raw if x.raw is not None else par(x)
    if st in ("uptr", "ptr"):
        if len(pos) > 1:
            return X(f"{name} == &{par(tr.expr(pos[1]), P_UNARY)}", 0, "logical", P_EQ)
        return X(f"{name} != nullptr", 0, "logical", P_EQ)
    if st == "eigen":
        return X(f"{name}.size() > 0", 0, "logical", P_REL)
    return X(f"!{name}.empty()", 0, "logical", P_UNARY)


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
    if n == "lbound":
        x = tr.expr(pos[0])
        if x.var is None or len(pos) < 2:
            raise Untranslatable("intrinsic:lbound")
        lb = x.var.dims[int(tr.expr(pos[1]).code) - 1][0]
        return X(tr.expr(lb).code if lb is not None else "1", 0, "integer")
    x = tr.expr(pos[0])
    if x.var is not None and any(d[0] is not None for d in x.var.dims):
        raise Untranslatable("intrinsic:ubound(lower-bound)")
    return i_size(tr, n, pos, kw)


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
    "null": i_null,
}
for _n in MATH1:
    INTRINSICS[_n] = i_math1
