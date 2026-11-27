"""Textual pre-pass: make preprocessed Fortran / pFUnit sources parsable by fparser.

- user regex patches
- project macros are replaced by plain Fortran (see config "macros")
- preprocessor directives and blank lines are hidden in marker comments
- pFUnit directives: @assertX(...) -> call f2cpp_assertX(...), @test etc. -> marker comments
"""
import itertools
import re

CPP_MARK = "!F2CPP_CPP "
PF_MARK = "!F2CPP_PF "
BLANK_MARK = "!F2CPP_BLANK"
ASSERT_PREFIX = "f2cpp_"


def _find_close(text, i):
    """index of the parenthesis matching the one at text[i]"""
    depth, quote = 0, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "!":
            i = text.find("\n", i)
            if i < 0:
                break
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced parentheses in macro call")


def split_args(s):
    args, depth, quote, cur = [], 0, None, ""
    for c in s:
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += c
    args.append(cur.strip())
    return args


def _join_continuations(s):
    s = re.sub(r"&[ \t]*(![^\n]*)?\n[ \t]*&?", "", s)
    return s


def expand_macros(text, macros):
    if not macros:
        return text
    pat = re.compile(r"(?<![\w%])(" + "|".join(map(re.escape, macros)) + r")[ \t]*\(")
    pos = 0
    while True:
        m = pat.search(text, pos)
        if not m:
            return text
        line_start = text.rfind("\n", 0, m.start()) + 1
        if "!" in text[line_start:m.start()] or text[line_start:line_start + 1] == "#":
            pos = m.end()       # inside a comment or a preprocessor line
            continue
        close = _find_close(text, m.end() - 1)
        inner = expand_macros(text[m.end():close], macros)     # nested macros
        repl = macros[m.group(1)].format(*split_args(_join_continuations(inner)))
        text = text[:m.start()] + repl + text[close + 1:]
        pos = m.start() + len(repl)


def cpp_condition(cond, defined, undefined):
    """value of a #if / #elif condition, or None if it depends on macros that are neither defined nor undefined:
    decided if it has the same value for all values of the unknown macros (e.g. true || unknown)"""
    unknown = []

    def name(n, as_defined):
        if n in defined:
            return "True" if as_defined else "1"
        if n in undefined:
            return "False" if as_defined else "0"
        if n.isdigit():
            return "True" if n != "0" else "False"
        if n not in unknown:
            unknown.append(n)
        return f"_u{unknown.index(n)}"
    expr = re.sub(r"\bdefined\s*\(\s*(\w+)\s*\)|\bdefined\s+(\w+)",
                  lambda m: " " + name(m.group(1) or m.group(2), True) + " ", cond)
    expr = re.sub(r"\b(?!_u\d)(?!True\b|False\b)(\w+)\b", lambda m: name(m.group(1), False), expr)
    expr = expr.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    names = sorted(set(re.findall(r"_u\d+", expr)))
    if len(names) > 8 or not re.fullmatch(r"[\s\w()]*", expr) or \
            set(re.findall(r"[A-Za-z_]\w*", expr)) - {"True", "False", "and", "or", "not", *names}:
        return None
    try:
        values = {bool(eval(expr, {"__builtins__": {}}, dict(zip(names, combo, strict=True))))     # noqa: S307
                  for combo in itertools.product((True, False), repeat=len(names))}
    except SyntaxError:
        return None
    return values.pop() if len(values) == 1 else None


def resolve_conditionals(text, defined, undefined):
    """#if / #ifdef / #ifndef blocks that depend only on known macros: the active branch without the directives;
    other conditionals stay (also inside an active branch)"""
    if not defined and not undefined:
        return text
    out, stack = [], []         # stack: [known, active, taken] per #if (known: False -> directives kept)
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(.*)", s)
        live = all(f[1] for f in stack if f[0])
        if not m:
            if live:
                out.append(line)
            continue
        d, arg = m.group(1), re.sub(r"/\*.*?\*/|//.*", "", m.group(2)).strip()
        if d in ("ifdef", "ifndef", "if"):
            cond = f"defined({arg})" if d == "ifdef" else f"!defined({arg})" if d == "ifndef" else arg
            v = cpp_condition(cond, defined, undefined) if live else False
            if not live:
                stack.append([True, False, True])
            elif v is None:
                stack.append([False, True, False])
                out.append(line)
            else:
                stack.append([True, v, v])
        elif d == "elif" and stack:
            f = stack[-1]
            if not f[0]:
                if live:
                    out.append(line)
            else:
                v = cpp_condition(arg, defined, undefined)
                if v is None and not f[2]:      # unknown condition after known false branches: keep as #if
                    f[0], f[1] = False, True
                    out.append(line.replace("#elif", "#if", 1))
                else:
                    f[1] = bool(v) and not f[2]
                    f[2] = f[2] or bool(v)
        elif d == "else" and stack:
            f = stack[-1]
            if not f[0]:
                if live:
                    out.append(line)
            else:
                f[1], f[2] = not f[2], True
        elif d == "endif" and stack:
            f = stack.pop()
            if not f[0] and all(g[1] for g in stack if g[0]):
                out.append(line)
        elif live:
            out.append(line)
    return "\n".join(out)


def prepass(text, cfg):
    for pattern, repl in cfg["pre_patches"]:
        text = re.sub(pattern, repl, text, flags=re.MULTILINE | re.IGNORECASE)
    text = resolve_conditionals(text, set(cfg["cpp_defined"]), set(cfg["cpp_undefined"]))
    text = expand_macros(text, cfg["macros"])
    out, blank, continued = [], False, False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            out.append(CPP_MARK + s)
        elif s.startswith("@"):
            m = re.match(r"@(\w+)(.*)$", s)
            if m.group(1).lower().startswith("assert"):
                out.append(f"call {ASSERT_PREFIX}{m.group(1)}{m.group(2)}")
            else:
                out.append(f"{PF_MARK}{m.group(1).lower()} {m.group(2)}")
        elif not s:
            if not blank and not continued:
                out.append(BLANK_MARK)
            blank = True
            continue
        else:
            out.append(line)
            code = s.split("!")[0].rstrip() if "'" not in s and '"' not in s else s
            continued = code.endswith("&")
        blank = False
    return "\n".join(out) + "\n"
