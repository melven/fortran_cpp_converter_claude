"""Construct coverage report: node types and categories only, never source text."""
import json
from collections import Counter


class Report:
    CONFIG = "<config>"                         # pseudo file: config entries not used by the files of the run

    def __init__(self):
        self.files = {}
        self.cur = None

    def start(self, name):
        self.cur = self.files.setdefault(name, {k: Counter() for k in
                                                ("translated", "untranslated", "todo", "info", "missing", "error")})

    def add_once(self, kind, category, key):
        """count category once per key and file (e.g. a note on a variable that is translated several times)"""
        if self.cur is not None and (kind, category, key) not in self.cur.setdefault("_seen", set()):
            self.cur["_seen"].add((kind, category, key))
            self.cur[kind][category] += 1

    def add(self, kind, category):
        if self.cur is not None:
            self.cur[kind][category] += 1

    def summary(self):
        lines = []
        for name, c in self.files.items():
            if name == self.CONFIG:
                lines.append(f"config: {sum(c['info'].values())} entries not used by the files of this run")
                lines += [f"    info         {n:4d}  {cat}" for cat, n in sorted(c["info"].items())]
                continue
            nt, nu = sum(c["translated"].values()), sum(c["untranslated"].values())
            ne = sum(c["error"].values())
            lines.append(f"{name}: {nt} statements translated, {nu} left as Fortran comment, "
                         f"{sum(c['todo'].values())} flagged for review" +
                         (f", {ne} INTERNAL ERRORS (f2cpp bugs, see the log)" if ne else ""))
            for cat in c["missing"]:
                lines.append(f"    missing module {cat}")
            for kind in ("error", "untranslated", "todo", "info"):
                for cat, n in c[kind].most_common():
                    lines.append(f"    {kind:12s} {n:4d}  {cat}")
        return "\n".join(lines)

    def write_json(self, path):
        with open(path, "w") as f:
            json.dump({n: {k: dict(v) for k, v in c.items() if not k.startswith("_")} for n, c in self.files.items()},
                      f, indent=2)
