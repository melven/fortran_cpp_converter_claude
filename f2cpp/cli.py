"""Command line interface."""
import argparse
import os
import sys

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
        m.is_test = path.endswith(".pf")
        mods.append(m)
    return mods


def translate(paths, cfg, outdir, interface_only=(), report=None):
    """translate all files in paths; interface_only files are parsed for their symbols only"""
    report = report or Report()
    parser = ParserFactory().create(std="f2008")
    prog = Program(cfg)
    todo = []
    for path in list(paths) + list(interface_only):
        try:
            mods = parse_file(path, cfg, parser)
        except Exception as e:      # fparser syntax errors
            print(f"f2cpp: cannot parse {path} (line numbers refer to the pre-processed text, "
                  f"written to {path}.f2cpp_prepass):\n{e}", file=sys.stderr)
            with open(path) as f, open(path + ".f2cpp_prepass", "w") as g:
                g.write(prepass(f.read(), cfg))
            report.start(path)
            report.add("untranslated", "parse-error")
            continue
        for m in mods:
            prog.add(m)
            if path in paths:
                todo.append((path, m))
    prog.finish()
    os.makedirs(outdir, exist_ok=True)
    written = []
    for path, m in todo:        # first pass: find array dummies that receive slices
        ModuleEmitter(prog, m, Report(), path).generate()
    for path, m in todo:
        hpp, cpp = ModuleEmitter(prog, m, report, path).generate()
        for text, ext in ((hpp, cfg["header_ext"]), (cpp, cfg["source_ext"])):
            if text is not None:
                out = os.path.join(outdir, m.name + ext)
                with open(out, "w") as f:
                    f.write(text)
                written.append(out)
    return report, written


def main(argv=None):
    ap = argparse.ArgumentParser(prog="f2cpp", description="Fortran 2003 -> C++ (Eigen, googletest)")
    ap.add_argument("files", nargs="+", help="Fortran module files (.F90) and pFUnit tests (.pf)")
    ap.add_argument("-o", "--outdir", default="f2cpp_out")
    ap.add_argument("-c", "--config", help="TOML configuration file")
    ap.add_argument("-n", "--namespace", help="C++ namespace (overrides config)")
    ap.add_argument("-I", "--interface", action="append", default=[], metavar="FILE",
                    help="parse FILE only for its symbols (used modules), no output")
    ap.add_argument("--keep-original", action="store_true", default=None,
                    help="emit each Fortran statement as comment above its translation")
    ap.add_argument("--report", metavar="JSON", help="write construct coverage report (no source text)")
    args = ap.parse_args(argv)
    cfg = config.load(args.config, namespace=args.namespace, keep_original=args.keep_original)
    report, written = translate(args.files, cfg, args.outdir, args.interface)
    for w in written:
        print(w)
    print(report.summary(), file=sys.stderr)
    if args.report:
        report.write_json(args.report)
