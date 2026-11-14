"""Construct coverage report: node types and categories only, never source text."""
import json
from collections import Counter


class Report:
    def __init__(self):
        self.files = {}
        self.cur = None

    def start(self, name):
        self.cur = self.files.setdefault(name, {k: Counter() for k in
                                                ("translated", "untranslated", "todo", "info")})

    def add(self, kind, category):
        if self.cur is not None:
            self.cur[kind][category] += 1

    def summary(self):
        lines = []
        for name, c in self.files.items():
            nt, nu = sum(c["translated"].values()), sum(c["untranslated"].values())
            lines.append(f"{name}: {nt} statements translated, {nu} left as Fortran comment, "
                         f"{sum(c['todo'].values())} flagged for review")
            for kind in ("untranslated", "todo", "info"):
                for cat, n in c[kind].most_common():
                    lines.append(f"    {kind:12s} {n:4d}  {cat}")
        return "\n".join(lines)

    def write_json(self, path):
        with open(path, "w") as f:
            json.dump({n: {k: dict(v) for k, v in c.items()} for n, c in self.files.items()}, f, indent=2)
