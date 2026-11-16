"""Textual pre-pass: make preprocessed Fortran / pFUnit sources parsable by fparser.

- user regex patches
- project macros are replaced by plain Fortran (see config "macros")
- preprocessor directives and blank lines are hidden in marker comments
- pFUnit directives: @assertX(...) -> call f2cpp_assertX(...), @test etc. -> marker comments
"""
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


def prepass(text, cfg):
    for pattern, repl in cfg["pre_patches"]:
        text = re.sub(pattern, repl, text, flags=re.MULTILINE | re.IGNORECASE)
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
