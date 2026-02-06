#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

TEMPLATE_SUFFIXES = {".html", ".htm", ".j2", ".jinja"}
JINJA_REF_RE = re.compile(
    r"""{%\s*
        (?:include|extends|import|from)
        \s+["']([^"']+)["']
        """,
    re.VERBOSE,
)


class TemplateVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.found = []  # (template_name, lineno)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "render":
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self.found.append((arg.value, node.lineno))
        self.generic_visit(node)


def detect_cycles(graph):
    cycles = []
    visiting = set()
    visited = set()

    def visit(node, stack):
        if node in visiting:
            cycle = [*stack[stack.index(node) :], node]
            cycles.append(cycle)
            return
        if node in visited:
            return

        visiting.add(node)
        for nxt in graph.get(node, ()):
            visit(nxt, [*stack, nxt])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [node])

    return cycles


def find_template_references(locations):
    """
    locations: {template_name: [Path, ...]}
    Returns: {template_name: set(referenced_template_names)}
    """
    refs = defaultdict(set)

    missing_includes = defaultdict(set)

    known = set(locations.keys())

    for name, paths in locations.items():
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue

            for match in JINJA_REF_RE.findall(text):
                ref = Path(match).name
                refs[name].add(ref)
                if ref not in known:
                    missing_includes[name].add(ref)

    return refs, missing_includes


def find_templates_in_code(source_root: Path):
    used = set()
    origins = defaultdict(list)

    for pyfile in source_root.rglob("*.py"):
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        visitor = TemplateVisitor(pyfile)
        visitor.visit(tree)

        for name, lineno in visitor.found:
            filename_only = Path(name).name
            used.add(filename_only)
            origins[filename_only].append((pyfile, lineno))

    used.add("blank.html")
    origins["blank.html"].append(("atr/template.py", 42))

    return used, origins


def find_template_dirs(source_root: Path):
    return [p for p in source_root.rglob("templates") if p.is_dir()]


def find_templates_on_disk(source_root: Path):
    present = set()
    locations = defaultdict(list)

    for tdir in find_template_dirs(source_root):
        for p in tdir.rglob("*"):
            if p.suffix in TEMPLATE_SUFFIXES:
                name = p.name  # filename-only
                present.add(name)
                locations[name].append(p)

    return present, locations


def resolve_used_templates(python_used, template_refs):
    reachable = set()
    stack = list(python_used)

    while stack:
        current = stack.pop()
        if current in reachable:
            continue

        reachable.add(current)
        for ref in template_refs.get(current, ()):
            if ref not in reachable:
                stack.append(ref)

    return reachable


def reverse_refs(refs):
    rev = defaultdict(set)
    for src, targets in refs.items():
        for t in targets:
            rev[t].add(src)
    return rev


def print_map(reachable, origins, rev_refs, refs):
    print("\n== Template usage map ==")
    for name in sorted(reachable):
        print(f"\n{name}")

        if name in origins:
            for file, line in origins[name]:
                print(f"  rendered from {file}:{line}")

        for parent in sorted(rev_refs.get(name, [])):
            print(f"  included by {parent}")

        for child in sorted(refs.get(name, [])):
            print(f"  includes {child}")


def main():  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", help="Source tree root")
    parser.add_argument(
        "--usage-map",
        action="store_true",
        help="Display template usage map",
    )
    args = parser.parse_args()

    root = Path(args.source_root)

    used, origins = find_templates_in_code(root)
    present, locations = find_templates_on_disk(root)
    refs, missing_includes = find_template_references(locations)
    reachable = resolve_used_templates(used, refs)
    rev_refs = reverse_refs(refs)

    missing = used - present

    duplicates = {k: v for k, v in locations.items() if len(v) > 1}
    unreachable = present - reachable
    cycles = detect_cycles(refs)

    if args.usage_map:
        print_map(reachable, origins, rev_refs, refs)

    errors = False

    if missing:
        errors = True
        print("\nMissing templates")
        for t in sorted(missing):
            print(f"  {t}")
            for file, line in origins.get(t, []):
                print(f"      referenced at {file}:{line}")

    if missing_includes:
        errors = True
        print("\nMissing included templates")
        for src, refs in missing_includes.items():
            for ref in refs:
                print(f"  {src} includes missing {ref}")

    if duplicates:
        errors = True
        print("\nDuplicate template definitions")
        for name, paths in duplicates.items():
            print(f"  {name}")
            for p in paths:
                print(f"      {p}")

    if unreachable:
        errors = True
        print("\nUnreachable templates")
        for t in sorted(unreachable):
            print(f"  {t}")
            for p in locations[t]:
                print(f"      {p}")

    if cycles:
        errors = True
        print("\nTemplate includes cycles")
        for cycle in cycles:
            print("  " + " → ".join(cycle))

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
