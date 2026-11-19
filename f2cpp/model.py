"""Symbol table built from the fparser2 parse tree."""
import re
from dataclasses import dataclass, field

from fparser.two import Fortran2003 as F

CPP_KEYWORDS = set("""alignas alignof and asm auto bool break case catch char class const constexpr
continue decltype default delete do double else enum explicit export extern false float for friend goto
if inline int long mutable namespace new noexcept not nullptr operator or private protected public
register return short signed sizeof static struct switch template this throw true try typedef typeid
typename union unsigned using virtual void volatile while xor""".split())


def cls(n):
    return type(n).__name__


def lname(n):
    return str(n).strip().lower()


def items_of(n):
    """children of a *_List node; a single node is returned as one-element list"""
    if n is None:
        return []
    if cls(n).endswith("_List") or cls(n) in ("Ac_Value_List",):
        return list(n.items)
    return [n]


def cname(name):
    return name + "_" if name in CPP_KEYWORDS else name


@dataclass(eq=False)     # identity semantics: never compare parse trees
class Var:
    name: str                    # lower case
    cname: str                   # spelling used in C++
    base: str = "unknown"        # real integer logical character complex type unknown
    tname: str = None            # derived type name (lower case)
    kind: str = None
    rank: int = 0
    dims: list = field(default_factory=list)   # [(lower node|None, upper node|None)]
    attrs: set = field(default_factory=set)    # allocatable pointer optional parameter save target value
    intent: str = None
    polymorphic: bool = False
    init: object = None
    access: str = None
    is_dummy: bool = False
    is_this: bool = False
    is_const: bool = False
    force_ptr: bool = False      # select-type alias etc.
    alloc_dims: list = None      # shape of an allocate statement for this name (flat arrays)
    access_need: int = 0         # component: 0 private, 1 used by derived classes, 2 used from outside
    empty_arrays = True          # class attribute: config optional_arrays == "empty"
    needs_eigen: bool = False    # rank-1 integer array used in a way that requires an Eigen vector
    int_vec: bool = False        # rank-1 integer array stored as std::vector<int>
    unwrapped: str = None        # variable of an unwrapped wrapper type: name of the wrapped component
    companions: dict = None      # unwrapped variable: other components -> companion variables <name>_<comp>
    elem_uptr: bool = False      # array of an unwrapped type around an allocatable object: unique_ptr elements
    companion_of: tuple = None   # companion variable: (unwrapped variable, component) in Fortran
    named_elements: bool = False # local array of a named_elements type: one C++ variable per element
    named_size: int = None       # its size from a literal allocate (allocatable arrays)
    unwrapped_type: str = None   # unwrapped variable / companion: the original (wrapper) type
    mutable: bool = False        # component modified through intent(in) objects (was an owned array pointer)
    alias: bool = False          # local pointer associated exactly once -> C++ reference
    needs_ref: bool = False      # out/inout array dummy that receives slices -> Eigen::Ref
    node: object = None

    def has(self, a):
        return a in self.attrs

    @property
    def opt_kind(self):
        """representation of an optional dummy: std::optional of a value or of a reference"""
        if not (self.is_dummy and self.has("optional")):
            return None
        if Var.empty_arrays and self.intent == "in" and self.rank > 0 and \
                self.base in ("real", "integer", "logical", "complex") and \
                not (self.has("allocatable") or self.has("pointer")):
            return "empty"          # const vec& w = vec(): absent = empty
        if self.intent == "in" and self.base != "type":
            return "optional"       # std::optional<T>
        return "optref"             # std::optional<std::reference_wrapper<T>>

    @property
    def ptr_like(self):
        if self.is_this or self.force_ptr or self.opt_kind == "optional":
            return True
        if self.base == "procedure":
            return False
        if self.rank == 0 and self.base == "type" and (self.has("allocatable") or self.has("pointer")):
            return True
        return self.rank == 0 and self.has("pointer")


@dataclass(eq=False)     # identity semantics: never compare parse trees
class Proc:
    name: str
    cname: str
    module: object
    kind: str                    # subroutine | function
    args: list                   # dummy names (lower case)
    vars: dict
    result: str = None
    node: object = None
    method_of: str = None        # type name if type-bound
    bind_name: str = None
    pass_arg: str = None
    generic: str = None          # generic name this specific is emitted as
    spellings: set = field(default_factory=set)     # spellings of the references that resolve to it
    external: bool = False       # declared by an interface body only (no definition to translate)
    is_ctor: bool = False
    is_dtor: bool = False
    host: object = None          # host procedure of an internal procedure
    internal: dict = field(default_factory=dict)    # internal procedures (contains inside a procedure)
    cb_arg: str = None           # callback implementation: class(*) user data dummy cast by select type
    cb_select: object = None     #   ... the select type construct
    cb_method: bool = False      #   ... translated as method of the user data type
    is_test: str = None          # pFUnit role: test | before | after
    doc: list = field(default_factory=list)
    param_docs: list = field(default_factory=list)      # [([argument names], [text lines])]
    doc_nodes: set = field(default_factory=set)         # ids of comments used as argument documentation


@dataclass(eq=False)     # identity semantics: never compare parse trees
class Binding:
    name: str
    impl: str = None
    iface: str = None
    deferred: bool = False
    nopass: bool = False
    pass_arg: str = None
    access: str = None
    generic: str = None
    spelling: str = None         # binding name as written in the type definition
    spellings: set = field(default_factory=set)     # spellings of the references that resolve to it


@dataclass(eq=False)     # identity semantics: never compare parse trees
class TypeDef:
    name: str
    module: object
    parent: str = None
    abstract: bool = False
    comps: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    generics: dict = field(default_factory=dict)
    finals: list = field(default_factory=list)
    private_comps: bool = False
    spelling: str = None         # name as written in the type definition
    generic_spelling: dict = field(default_factory=dict)
    children: set = field(default_factory=set)
    node: object = None


@dataclass(eq=False)     # identity semantics: never compare parse trees
class Module:
    name: str
    node: object
    uses: list = field(default_factory=list)
    spelling: str = None                            # name as written in the module statement
    use_spelling: dict = field(default_factory=dict)    # used module -> name as written in the use
    use_only: dict = field(default_factory=dict)    # used module -> names of the only-lists (None: all)
    vars: dict = field(default_factory=dict)
    types: dict = field(default_factory=dict)
    procs: dict = field(default_factory=dict)
    ifaces: dict = field(default_factory=dict)      # abstract interface bodies
    generics: dict = field(default_factory=dict)    # generic name -> [specific names]
    generic_spelling: dict = field(default_factory=dict)
    default_access: str = "public"
    access: dict = field(default_factory=dict)
    is_test: bool = False

    def is_public(self, name, own=None):
        return (own or self.access.get(name) or self.default_access) == "public"


def parse_type_spec(ts, var):
    if cls(ts) == "Intrinsic_Type_Spec":
        t = str(ts.items[0]).upper()
        var.base = {"REAL": "real", "DOUBLE PRECISION": "real", "INTEGER": "integer",
                    "LOGICAL": "logical", "CHARACTER": "character",
                    "DOUBLE COMPLEX": "complex", "COMPLEX": "complex"}.get(t, "unknown")
        if t.startswith("DOUBLE"):
            var.kind = "8"
        elif ts.items[1] is not None and var.base != "character":
            var.kind = re.sub(r"\s|\(|\)", "", lname(ts.items[1])).replace("kind=", "")
    elif cls(ts) == "Declaration_Type_Spec":
        var.base = "type"
        var.polymorphic = str(ts.items[0]).upper() == "CLASS"
        var.tname = lname(ts.items[1])
    return var


def parse_array_spec(spec):
    dims = []
    for s in items_of(spec):
        its = getattr(s, "items", None)
        if cls(s) == "Assumed_Size_Spec":
            dims.extend(parse_array_spec(s.items[0]))
            dims.append((s.items[1], None))
        elif its is not None and len(its) == 2:
            dims.append((its[0], its[1]))
        else:
            dims.append((None, None))
    return dims


def parse_decl(stmt, into, order=None):
    """Type_Declaration_Stmt / Data_Component_Def_Stmt -> Var objects"""
    ts, attrs, ents = stmt.items
    proto = parse_type_spec(ts, Var("", ""))
    aset, intent, access, dimspec = set(), None, None, None
    for a in items_of(attrs):
        s = re.sub(r"\s", "", str(a)).lower()
        if s.startswith("intent("):
            intent = s[7:-1]
        elif s.startswith("dimension"):
            dimspec = a.items[1]
        elif s in ("public", "private"):
            access = s
        else:
            aset.add(s)
    res = []
    for e in items_of(ents):
        name, arr, _clen, init = e.items[:4]
        n = lname(name)
        v = into.get(n) or Var(n, cname(str(name)))
        v.base, v.tname, v.kind, v.polymorphic = proto.base, proto.tname, proto.kind, proto.polymorphic
        v.attrs, v.intent, v.access, v.node = set(aset), intent, access, stmt
        v.dims = parse_array_spec(arr if arr is not None else dimspec)
        v.rank = len(v.dims)
        v.init = init.items[1] if init is not None and cls(init) == "Initialization" else None
        if init is not None and cls(init) != "Initialization":
            v.init = init
        into[n] = v
        res.append(v)
    return res


def parse_proc_decl(stmt, into):
    """procedure(interface) :: name  (dummy procedure or procedure pointer component)"""
    iface, attrs, decls = stmt.items
    aset = {re.sub(r"\s", "", str(a)).lower() for a in items_of(attrs)}
    for d in items_of(decls):
        name = d.items[0] if cls(d) == "Proc_Decl" else d
        n = lname(name)
        v = into.get(n) or Var(n, cname(str(name)))
        v.base, v.tname, v.node = "procedure", lname(iface) if iface is not None else None, stmt
        v.attrs = {a for a in aset if a in ("optional", "pointer")}
        v.intent = "in"
        into[n] = v


def collect_param_docs(p, spec):
    """documentation of dummy arguments: `!<` comments after the declaration (same or following
    lines) and `!>` comment lines directly before it"""
    flat = []
    for d in spec.content:
        flat += list(d.content) if cls(d) == "Implicit_Part" else [d]
    for i, d in enumerate(flat):
        if cls(d) != "Type_Declaration_Stmt":
            continue
        names = [lname(e.items[0]) for e in items_of(d.items[2])]
        names = [n for n in names if n in p.args or n == p.result]
        if not names:
            continue
        before, after, j = [], [], i - 1
        while j >= 0 and cls(flat[j]) == "Comment" and str(flat[j]).startswith("!>"):
            before.insert(0, flat[j])
            j -= 1
        j = i + 1
        while j < len(flat) and cls(flat[j]) == "Comment" and str(flat[j]).startswith("!<"):
            after.append(flat[j])
            j += 1
        if before or after:
            p.param_docs.append((names, [str(c)[2:].strip() for c in before + after]))
            p.doc_nodes |= {id(c) for c in before + after}


def collect_proc(node, module):
    content = [c for c in node.content if cls(c) != "Comment"]
    stmt = content[0]
    kind = "function" if "Function" in cls(stmt) else "subroutine"
    name = str(stmt.items[1])
    args = [lname(a) for a in items_of(stmt.items[2])]
    p = Proc(lname(name), cname(name), module, kind, args, {}, node=node)
    p.doc = [c for c in node.content[:node.content.index(stmt)] if cls(c) == "Comment"]
    if kind == "function":
        suffix = stmt.items[3]
        p.result = lname(suffix.items[0]) if suffix is not None and suffix.items[0] is not None else p.name
        for pre in (stmt.items[0].items if stmt.items[0] is not None else []):
            if cls(pre) in ("Intrinsic_Type_Spec", "Declaration_Type_Spec"):
                p.vars[p.result] = parse_type_spec(pre, Var(p.result, cname(p.result)))
    for c in content[1:]:
        if cls(c) == "Specification_Part":
            for d in c.content:
                if cls(d) == "Type_Declaration_Stmt":
                    parse_decl(d, p.vars)
                elif cls(d) == "Procedure_Declaration_Stmt":
                    parse_proc_decl(d, p.vars)
    for c in content[1:]:
        if cls(c) == "Specification_Part":
            collect_param_docs(p, c)
        elif cls(c) == "Internal_Subprogram_Part":
            for s in c.content:
                if cls(s) in ("Subroutine_Subprogram", "Function_Subprogram"):
                    q = collect_proc(s, module)
                    q.host = p
                    p.internal[q.name] = q
    for a in args:
        v = p.vars.setdefault(a, Var(a, cname(a)))
        v.is_dummy = True
        v.is_const = v.intent == "in"
    return p


def collect_type(node, module):
    stmt = node.content[0]
    t = TypeDef(lname(stmt.items[1]), module, node=node)
    t.spelling = str(stmt.items[1]).strip()
    for a in items_of(stmt.items[0]):
        s = lname(a)
        if s.startswith("extends"):
            t.parent = lname(a.items[1])
        elif s == "abstract":
            t.abstract = True
    for c in node.content[1:]:
        k = cls(c)
        if k == "Private_Components_Stmt":
            t.private_comps = True
        elif k == "Component_Part":
            for d in c.content:
                if cls(d) == "Data_Component_Def_Stmt":
                    parse_decl(d, t.comps)
                elif cls(d) == "Proc_Component_Def_Stmt":
                    parse_proc_decl(d, t.comps)
        elif k == "Type_Bound_Procedure_Part":
            for b in c.content:
                if cls(b) == "Specific_Binding":
                    iface, attrs, _, bname, impl = b.items
                    bd = Binding(lname(bname), lname(impl) if impl is not None else lname(bname),
                                 lname(iface) if iface is not None else None)
                    bd.spelling = cname(str(bname).strip())
                    for a in items_of(attrs):
                        s = re.sub(r"\s", "", lname(a))
                        if s == "deferred":
                            bd.deferred = True
                        elif s == "nopass":
                            bd.nopass = True
                        elif s.startswith("pass("):
                            bd.pass_arg = s[5:-1]
                        elif s in ("public", "private"):
                            bd.access = s
                    t.bindings[bd.name] = bd
                elif cls(b) == "Generic_Binding":
                    t.generics[lname(b.items[1])] = [lname(x) for x in items_of(b.items[2])]
                    t.generic_spelling[lname(b.items[1])] = cname(str(b.items[1]).strip())
                elif cls(b) == "Final_Binding":
                    t.finals += [lname(x) for x in items_of(b.items[1])]
    return t


def collect_module(node):
    m = Module(lname(node.content[0].items[1]), node)
    m.spelling = str(node.content[0].items[1]).strip()
    for part in node.content:
        if cls(part) == "Specification_Part":
            for s in part.content:
                k = cls(s)
                if k == "Use_Stmt":
                    pass                                # collected below (also inside procedures)
                elif k == "Access_Stmt":
                    acc = str(s.items[0]).lower()
                    if s.items[1] is None:
                        m.default_access = acc
                    for n in items_of(s.items[1]):
                        m.access[lname(n)] = acc
                elif k == "Type_Declaration_Stmt":
                    parse_decl(s, m.vars)
                elif k == "Derived_Type_Def":
                    t = collect_type(s, m)
                    m.types[t.name] = t
                elif k == "Interface_Block":
                    head = s.content[0].items[0]
                    for b in s.content[1:-1]:
                        if cls(b) in ("Function_Body", "Subroutine_Body"):
                            p = collect_proc(b, m)
                            m.ifaces[p.name] = p
                            if str(head).upper() != "ABSTRACT":
                                # explicit interface of an external procedure (e.g. bind(C)): callable
                                p.external = True
                                m.procs.setdefault(p.name, p)
                                if head is not None and cls(head) == "Name":
                                    m.generic_spelling[lname(head)] = cname(str(head).strip())
                                    m.generics.setdefault(lname(head), []).append(p.name)
                        elif cls(b) == "Procedure_Stmt" and head is not None and cls(head) == "Name":
                            m.generic_spelling[lname(head)] = cname(str(head).strip())
                            m.generics.setdefault(lname(head), []).extend(
                                lname(x) for x in items_of(b.items[0]))
        elif cls(part) == "Module_Subprogram_Part":
            for s in part.content:
                if cls(s) in ("Subroutine_Subprogram", "Function_Subprogram"):
                    p = collect_proc(s, m)
                    m.procs[p.name] = p
    from fparser.two.utils import walk
    for s in walk(node, F.Use_Stmt):
        u = lname(s.items[2])
        m.use_spelling.setdefault(u, str(s.items[2]).strip())
        if u not in m.uses:
            m.uses.append(u)
        if s.items[4] is None or "only" not in str(s.items[3]).lower():
            m.use_only[u] = None
        elif m.use_only.get(u, []) is not None:
            names = [lname(n).split("=>")[-1].strip() for n in items_of(s.items[4])]
            m.use_only[u] = m.use_only.get(u, []) + names
    # generic interfaces -> C++ overloads
    for g, specifics in m.generics.items():
        for sname in specifics:
            if sname in m.procs:
                m.procs[sname].generic = m.generic_spelling[g]
    # type-bound procedures -> methods
    for t in m.types.values():
        for g, specifics in t.generics.items():
            for b in specifics:
                if b in t.bindings:
                    t.bindings[b].generic = g
        for b in t.bindings.values():
            if b.deferred:
                p = m.ifaces.get(b.iface)
                if p is not None and not b.nopass and p.args and not p.pass_arg:
                    p.pass_arg = b.pass_arg or p.args[0]
                    p.vars[p.pass_arg].is_this = True
                continue
            p = m.procs.get(b.impl)
            if p is None or p.method_of:
                continue
            p.method_of = t.name
            p.bind_name = t.generic_spelling[b.generic] if b.generic else b.spelling
            if not b.nopass and p.args:
                p.pass_arg = b.pass_arg or p.args[0]
                p.vars[p.pass_arg].is_this = True
        for f in t.finals:
            if f in m.procs and m.procs[f].args:
                p = m.procs[f]
                p.method_of, p.bind_name, p.pass_arg = t.name, "~", p.args[0]
                p.vars[p.pass_arg].is_this = True
    return m


def _target_name(node):
    """name of the (last component of the) object designated by node"""
    while cls(node) in ("Data_Ref", "Part_Ref", "Allocation", "Data_Pointer_Object", "Proc_Component_Ref"):
        node = node.items[-1] if cls(node) in ("Data_Ref", "Data_Pointer_Object", "Proc_Component_Ref") \
            else node.items[0]
    return lname(node)


def classify_pointers(m):
    """Fortran pointers: owned (allocated somewhere) -> std::unique_ptr, dummies -> references,
    locals associated exactly once at the top level of the procedure -> local references"""
    from fparser.two.utils import walk
    allocated, shapes = set(), {}
    for a in walk(m.node, F.Allocate_Stmt):
        allocated |= {_target_name(o) for o in items_of(a.items[1])}
        for o in items_of(a.items[1]):
            if cls(o) == "Allocation" and o.items[1] is not None:
                shapes.setdefault(_target_name(o), [tuple(s.items) for s in items_of(o.items[1])])
    scopes = [m.vars] + [t.comps for t in m.types.values()] + [p.vars for p in m.procs.values()]
    for scope in scopes:
        for v in scope.values():
            if v.name in shapes and len(shapes[v.name]) == v.rank:
                v.alloc_dims = shapes[v.name]
    # names that are pointer-associated with a target somewhere (p => x, not p => null())
    associated = {_target_name(a.items[0]) for a in walk(m.node, F.Pointer_Assignment_Stmt)
                  if "null" not in lname(a.items[2])}
    for t in m.types.values():
        for v in t.comps.values():
            if v.has("pointer") and v.rank == 0 and v.name in allocated:
                v.attrs = (v.attrs - {"pointer"}) | {"allocatable"}
            elif v.has("pointer") and v.rank > 0 and v.name in allocated and v.name not in associated:
                # array pointer that is only allocated / deallocated: the Fortran idiom for a
                # member that can be modified through an intent(in) object -> mutable array
                v.attrs = (v.attrs - {"pointer"}) | {"allocatable"}
                v.mutable = True
    for p in m.procs.values():
        execs = [c for c in p.node.content if cls(c) == "Execution_Part"]
        body = execs[0] if execs else None
        assigns = walk(body, F.Pointer_Assignment_Stmt) if body else []
        nullified = {_target_name(o) for n in (walk(body, F.Nullify_Stmt) if body else [])
                     for o in items_of(n.items[1])}
        tested = {lname(items_of(f.items[1])[0]) for f in
                  (walk(body, F.Intrinsic_Function_Reference) if body else [])
                  if lname(f.items[0]) == "associated" and f.items[1] is not None}
        for v in p.vars.values():
            if not v.has("pointer"):
                continue
            if v.is_dummy:
                v.attrs.discard("pointer")          # non-owning: plain reference
            elif v.rank == 0 and v.name in allocated:
                v.attrs = (v.attrs - {"pointer"}) | {"allocatable"}
            else:
                mine = [a for a in assigns if cls(a.items[0]) == "Name" and lname(a.items[0]) == v.name]
                if len(mine) == 1 and any(mine[0] is c for c in body.content) and v.init is None \
                        and v.name not in nullified and v.name not in tested and v.name not in allocated:
                    v.alias = True
                    v.attrs.discard("pointer")


class Program:
    """all parsed modules"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.modules = {}
        self.spellings = {}
        Var.empty_arrays = cfg["optional_arrays"] == "empty"
        self.links = []             # pairs of integer arrays that must have the same C++ type

    def add(self, module):
        from fparser.two.utils import walk
        self.modules[module.name] = module
        for n in walk(module.node, (F.Type_Name, F.Type_Attr_Spec)):     # spellings of type names
            if cls(n) == "Type_Attr_Spec":
                if str(n.items[0]).upper() != "EXTENDS":
                    continue
                n = n.items[1]
            self.spellings.setdefault(lname(n), set()).add(str(n).strip())

    def inconsistent_case(self, tname):
        return len(self.spellings.get(tname, ())) > 1

    def classify_callbacks(self, m):
        """callback implementations: proc(userData, ...) with a class(*) user data dummy (drop_args)
        that is cast with `select type` to exactly one derived type T -> method of T (or, if T is
        an existing C++ class, a free function with a `T` parameter)"""
        from fparser.two.utils import walk
        for p in m.procs.values():
            if p.method_of or p.external:
                continue
            ud = [a for a in p.args if a in self.cfg["drop_args"] and a in p.vars
                  and p.vars[a].polymorphic and p.vars[a].tname == "*"]
            execs = [c for c in p.node.content if cls(c) == "Execution_Part"]
            if len(ud) != 1 or not execs:
                continue
            sts = [c for c in execs[0].content if cls(c) == "Select_Type_Construct"
                   and lname([h for h in c.content if cls(h) == "Select_Type_Stmt"][0].items[1]) == ud[0]]
            if len(sts) != 1:
                continue
            guards = [g for g in walk(sts[0], F.Type_Guard_Stmt) if g.items[1] is not None]
            if len(guards) != 1 or cls(guards[0].items[1]) == "Intrinsic_Type_Spec":
                continue
            tname = lname(guards[0].items[1])
            t = self.find_type(tname, m)
            v = p.vars[ud[0]]
            v.tname, v.polymorphic, v.base = tname, False, "type"
            p.cb_arg, p.cb_select = ud[0], sts[0]
            if t is not None and tname not in self.cfg["type_map"]:
                p.method_of, p.bind_name, p.pass_arg, p.cb_method = t.name, p.cname, ud[0], True
                v.is_this = True

    def finish(self):
        err = self.cfg["error_arg"].lower()
        for m in self.modules.values():
            self.classify_callbacks(m)
        for m in self.modules.values():
            classify_pointers(m)
            for t in m.types.values():
                for b in t.bindings.values():
                    p = m.procs.get(b.impl)
                    if b.deferred or p is None or p.method_of != t.name or not p.pass_arg \
                            or p.kind != "subroutine":
                        continue
                    if b.name in self.cfg["constructor_bindings"] and p.vars[p.pass_arg].intent == "out":
                        p.is_ctor = True
                    elif b.name in self.cfg["destructor_bindings"] and \
                            not [a for a in p.args if a not in (p.pass_arg, err)]:
                        p.is_dtor, p.bind_name = True, "~"
        self.unwrap_types()
        self.mark_named_elements()
        for m in self.modules.values():
            for t in m.types.values():
                p = self.find_type(t.parent, m) if t.parent else None
                if p:
                    p.children.add(t.name)

    def unwrap_types(self):
        """variables / components of wrapper types (unwrap_types) take the type of the wrapped
        component; members become mutable (the Fortran idiom: a pointer component can be changed
        through an intent(in) object)"""
        wrap = self.cfg["unwrap_types"]
        if not wrap:
            return

        def procs(ps):
            for p in ps:
                yield p
                yield from procs(p.internal.values())
        added = []
        for m in self.modules.values():
            scopes = [(m.vars, False)] + [(t.comps, True) for t in m.types.values()] + \
                [(p.vars, False) for p in procs([*m.procs.values(), *m.ifaces.values()])]
            for scope, member in scopes:
                for v in scope.values():
                    if v.base != "type" or v.tname not in wrap or v.unwrapped:
                        continue
                    t = self.find_type(v.tname, m)
                    inner = t.comps.get(wrap[v.tname]) if t is not None else None
                    if inner is None or (v.rank and (inner.rank or v.rank > 1)):
                        continue
                    tname, array = v.tname, bool(v.rank)
                    v.unwrapped, v.unwrapped_type = wrap[v.tname], tname
                    keep = {"optional", "target", "value"}
                    if array:                   # array of the type: array of the inner object
                        v.elem_uptr = inner.base == "type" and (inner.has("allocatable") or inner.polymorphic)
                        v.attrs = (v.attrs & (keep | {"allocatable", "pointer"}))
                    else:
                        v.dims, v.rank = list(inner.dims), inner.rank
                        v.attrs = (v.attrs & keep) | ({"allocatable"} if inner.rank or inner.has("allocatable")
                                                       or inner.has("pointer") else set())
                    v.base, v.tname, v.kind, v.polymorphic = inner.base, inner.tname, inner.kind, inner.polymorphic
                    v.mutable = member and not array        # the wrapper idiom (pointer component), not arrays
                    for c in self.cfg.get("unwrap_companions", {}).get(tname, []):
                        comp = t.comps.get(c)
                        if v.is_dummy or comp is None or comp.rank or comp.base not in ("real", "integer", "logical"):
                            continue
                        cv = Var(f"{v.name}_{c}", f"{v.cname}_{comp.cname}")
                        cv.base, cv.kind, cv.dims, cv.rank = comp.base, comp.kind, list(v.dims), v.rank
                        cv.attrs = v.attrs & {"allocatable", "pointer", "optional", "target"}
                        cv.is_dummy, cv.intent, cv.access, cv.node, cv.mutable = \
                            v.is_dummy, v.intent, v.access, v.node, False
                        cv.companion_of, cv.unwrapped_type = (v.name, c), tname
                        v.companions = (v.companions or {}) | {c: cv}
                        added.append((scope, cv))
        for scope, cv in added:                 # companions: visible like the variable
            scope.setdefault(cv.name, cv)

    def mark_named_elements(self):
        """local rank-1 arrays of named_elements types (not dummies, not components)"""
        types = self.cfg.get("named_elements", {})
        if not types:
            return

        def procs(ps):
            for p in ps:
                yield p
                yield from procs(p.internal.values())
        for m in self.modules.values():
            for p in procs(m.procs.values()):
                for v in p.vars.values():
                    if v.base == "type" and v.tname in types and v.rank == 1 and not v.is_dummy:
                        v.named_elements = True

    def file_stem(self, modname):
        """output file name (without extension) of a module: module name, or the name of its
        header in module_headers"""
        import os
        h = self.cfg["module_headers"].get(modname)
        if h:
            return os.path.splitext(h)[0]
        m = self.modules.get(modname)
        return m.spelling if m is not None and m.spelling else modname

    def decide_int_vectors(self, translated):
        """after the analysis pass: integer arrays without Eigen-only uses become std::vector<int>"""
        if self.cfg["int_arrays"] != "auto":
            return
        for m in self.modules.values():
            scopes = [m.vars] + [t.comps for t in m.types.values()] + [p.vars for p in m.ifaces.values()]
            for p in m.procs.values():
                t = self.find_type(p.method_of, m) if p.method_of else None
                if m not in translated or p.external or (t is not None and self.polymorphic(t)):
                    scopes.append({a: p.vars[a] for a in p.args if a in p.vars})   # fixed signatures
            if m not in translated:             # existing C++ code: keep Eigen vectors
                scopes += [p.vars for p in m.procs.values()]
                for scope in scopes:
                    for v in scope.values():
                        v.needs_eigen = True
            else:
                for scope in scopes[1 + len(m.types):]:
                    for v in scope.values():
                        v.needs_eigen = True
        changed = True
        while changed:
            changed = False
            for a, b in self.links:
                if a.needs_eigen != b.needs_eigen:
                    a.needs_eigen = b.needs_eigen = changed = True
        for m in translated:
            for scope in [m.vars] + [t.comps for t in m.types.values()] + [p.vars for p in m.procs.values()]:
                for v in scope.values():
                    v.int_vec = v.base == "integer" and v.rank == 1 and not v.needs_eigen

    def visible(self, module):
        seen, todo = [], [module]
        while todo:
            m = todo.pop(0)
            if m in seen:
                continue
            seen.append(m)
            todo += [self.modules[u] for u in m.uses if u in self.modules]
        return seen

    def find_type(self, tname, module):
        for m in self.visible(module):
            if tname in m.types:
                return m.types[tname]
        for m in self.modules.values():
            if tname in m.types:
                return m.types[tname]
        return None

    def ancestors(self, t):
        while t is not None and t.parent:
            t = self.find_type(t.parent, t.module)
            if t is not None:
                yield t

    def polymorphic(self, t):
        return t.abstract or bool(t.children) or bool(t.parent)

    def find_component(self, t, name):
        for tt in [t, *self.ancestors(t)]:
            if name in tt.comps:
                return tt.comps[name]
        return None

    def find_binding(self, t, name):
        for tt in [t, *self.ancestors(t)]:
            if name in tt.bindings:
                return tt, tt.bindings[name]
            if name in tt.generics:
                return tt, None
        return None, None

    def binding_proc(self, t, b):
        """procedure providing the signature of a binding"""
        if b is None:
            return None
        if b.deferred:
            return t.module.ifaces.get(b.iface)
        return t.module.procs.get(b.impl)

    def overrides(self, t, bname):
        return any(bname in a.bindings for a in self.ancestors(t))

    def descendants(self, t):
        for c in sorted(t.children):
            ct = self.find_type(c, t.module)
            if ct is not None and ct is not t:
                yield ct
                yield from self.descendants(ct)

    def overridden(self, t, bname):
        """is the binding overridden in a (known) derived type?"""
        return any(bname in d.bindings for d in self.descendants(t))

    # naming -----------------------------------------------------------
    def type_name(self, tname, from_module=None):
        cfg = self.cfg
        if tname in cfg["type_map"]:
            return cfg["type_map"][tname]
        # spelling of the type definition; for unknown types the spelling of its uses if unique
        defs = [m.types[tname].spelling for m in self.modules.values() if tname in m.types]
        uses = self.spellings.get(tname, ())
        n = defs[0] if defs else next(iter(uses)) if len(uses) == 1 else tname
        if cfg["type_suffix"] and n.lower().endswith(cfg["type_suffix"].lower()):
            n = n[:-len(cfg["type_suffix"])]
        if cfg["type_case"] == "camel":
            n = "".join(w.capitalize() for w in n.lower().split("_"))
        n = cname(n)
        t = self.find_type(tname, from_module) if from_module else None
        if t is not None and from_module is not None:
            ns = self.namespace(t.module.name)
            if ns != self.namespace(from_module.name):
                n = f"{ns}::{n}"
        return n

    def namespace(self, modname):
        ns = self.cfg["namespace"]
        for pattern, child in self.cfg["namespace_map"]:
            if re.fullmatch(pattern, modname):
                return f"{ns}::{child}" if child else ns
        return ns
