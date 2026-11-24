"""Command line interface."""
import argparse
import os
import re
import sys
import threading

from fparser.common.readfortran import FortranStringReader
from fparser.two.parser import ParserFactory
from fparser.two.utils import walk
from fparser.two import Fortran2003 as F

from . import config
from .emit import ModuleEmitter
from .model import Program, collect_module
from .prepass import prepass
from .report import Report


def parse_file(path, cfg, parser):
    with open(path) as f:
        text = prepass(f.read(), cfg)
    tree = parser(FortranStringReader(text, ignore_comments=False))
    mods = []
    for node in walk(tree, F.Module):
        m = collect_module(node)
        # pFUnit test: by file extension or because the module uses the test framework
        m.is_test = path.lower().endswith(tuple(cfg["test_extensions"])) or \
            any(u in cfg["test_modules"] for u in m.uses)
        mods.append(m)
    return mods


def translate(paths, cfg, outdir, interface_only=(), report=None, wrap=()):
    """translate all files in paths; interface_only files are parsed for their symbols only;
    wrap files get the C++ interface of the translation, implemented by calls of the Fortran code"""
    report = report or Report()
    paths = list(paths) + [w for w in wrap if w not in paths]
    parser = ParserFactory().create(std="f2008")
    prog = Program(cfg)
    todo = []
    for path in list(paths) + list(interface_only):
        try:
            mods = parse_file(path, cfg, parser)
        except Exception as e:      # fparser syntax errors
            dump = os.path.join(outdir, os.path.basename(path) + ".f2cpp_prepass")    # not into the sources
            print(f"f2cpp: cannot parse {path} (line numbers refer to the pre-processed text, "
                  f"written to {dump}):\n{e}", file=sys.stderr)
            os.makedirs(outdir, exist_ok=True)
            with open(path) as f, open(dump, "w") as g:
                g.write(prepass(f.read(), cfg))
            report.start(path)
            report.add("untranslated", "parse-error")
            continue
        if not mods:
            print(f"f2cpp: {path} contains no module (programs, submodules and external procedures are "
                  "not translated)", file=sys.stderr)
            report.start(path)
            report.add("untranslated", "no-module")
        for m in mods:
            stem = os.path.splitext(os.path.basename(path))[0]
            if path in paths and stem != prog.file_stem(m.name) and m.name not in cfg["module_headers"] \
                    and stem != m.spelling:
                print(f"f2cpp: {path} defines module {m.spelling}: the output is {m.spelling}{cfg['header_ext']} / "
                      f"{m.spelling}{cfg['source_ext']}", file=sys.stderr)
            if m.name in prog.modules:
                print(f"f2cpp: module {m.name} is defined more than once; ignoring the one in {path}",
                      file=sys.stderr)
                continue
            prog.add(m)
            if path in paths:
                todo.append((path, m))
    prog.finish()
    wrapped = [m for path, m in todo if path in wrap]
    prog.wrapped = {m.name for m in wrapped}
    from .wrap import WrapEmitter, register_accessors, check_dependencies
    register_accessors(prog, wrapped)
    check_dependencies(prog, wrapped, [m for _, m in todo if m not in wrapped], report)
    os.makedirs(outdir, exist_ok=True)
    written = []
    for path, m in todo:        # analysis pass: array dummies that receive slices, uses of integer arrays
        ModuleEmitter(prog, m, Report(), path).generate()
    prog.decide_int_vectors([m for _, m in todo])
    for path, m in todo:
        if m in wrapped:
            emitter = WrapEmitter(prog, m, report, path)
            hpp, cpp, binding, cwrapper, files = emitter.generate()
            for suffix, text in files.items():          # C++ CWrapper<Thing>
                out = os.path.join(outdir, prog.file_stem(m.name) + suffix)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w") as f:
                    f.write(text)
                written.append(out)
            out = os.path.join(outdir, prog.file_stem(m.name) + "_c_binding.F90")
            os.makedirs(os.path.dirname(out), exist_ok=True)    # module_headers may contain a directory
            with open(out, "w") as f:
                f.write(binding)
            written.append(out)
            if cwrapper is not None:                    # Fortran types implemented by C++ objects
                out = os.path.join(outdir, os.path.dirname(prog.file_stem(m.name)), emitter.cwrapper_module(m) + ".F90")
                with open(out, "w") as f:
                    f.write(cwrapper)
                written.append(out)
                binding += "\n" + cwrapper             # its C declarations are in the same .h
            from .wrap import c_header                  # normally generated from the binding by the build
            stem = prog.file_stem(m.name)
            out = os.path.join(outdir, stem + ".h")
            with open(out, "w") as f:
                f.write(c_header(binding, re.sub(r"\W", "_", os.path.basename(stem)).upper() + "_H"))
            written.append(out)
        else:
            hpp, cpp = ModuleEmitter(prog, m, report, path).generate()
        for text, ext in ((hpp, cfg["header_ext"]), (cpp, cfg["source_ext"])):
            if text is not None:
                out = os.path.join(outdir, prog.file_stem(m.name) + ext)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w") as f:
                    f.write(text)
                written.append(out)
    return report, written


def main(argv=None):
    ap = argparse.ArgumentParser(prog="f2cpp", description="Fortran 2003 -> C++ (Eigen, googletest)")
    ap.add_argument("files", nargs="*", help="Fortran module files (.F90) and pFUnit tests (.pf, .pfunit)")
    ap.add_argument("-o", "--outdir", default="f2cpp_out")
    ap.add_argument("-c", "--config", action="append", metavar="TOML",
                    help="configuration file; may be given several times (later files take precedence)")
    ap.add_argument("-n", "--namespace", help="C++ namespace (overrides config)")
    ap.add_argument("-I", "--interface", action="append", default=[], metavar="FILE",
                    help="parse FILE only for its symbols (used modules), no output")
    ap.add_argument("-x", "--exclude", action="append", default=[], metavar="MODULE:NAME",
                    help="do not emit NAME of MODULE (procedure, type, variable, type%%binding; glob patterns)")
    ap.add_argument("-W", "--wrap", action="append", default=[], metavar="FILE",
                    help="module that stays in Fortran: generate its C++ interface, glue code and C binding")
    ap.add_argument("--keep-original", action="store_true", default=None,
                    help="emit each Fortran statement as comment above its translation")
    ap.add_argument("--report", metavar="JSON", help="write construct coverage report (no source text)")
    ap.add_argument("--strict", action="store_true", default=None,
                    help="stop at internal errors (f2cpp bugs) instead of keeping the statement as comment")
    args = ap.parse_args(argv)
    if not args.files and not args.wrap:
        ap.error("no files to translate or wrap")
    cfg = config.load(args.config, namespace=args.namespace, keep_original=args.keep_original, strict=args.strict)
    cfg["exclude"] += [e.lower().replace(" ", "") for e in args.exclude]
    # parsing and translation recurse over the expression trees: very long expressions (hundreds of
    # operands) need more than the default recursion limit and stack
    result = []
    sys.setrecursionlimit(100000)
    threading.stack_size(512 * 1024 * 1024)
    worker = threading.Thread(target=lambda: result.append(translate(args.files, cfg, args.outdir,
                                                                     args.interface, wrap=args.wrap)))
    worker.start()
    worker.join()
    if not result:
        raise SystemExit(1)
    report, written = result[0]
    for w in written:
        print(w)
    print(report.summary(), file=sys.stderr)
    if args.report:
        report.write_json(args.report)
    if any(sum(c["error"].values()) for c in report.files.values()):
        raise SystemExit(2)                     # internal errors: the output is complete, but f2cpp has bugs
