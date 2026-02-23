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

import pathlib
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: fix_order.py <filename>", file=sys.stderr)
        sys.exit(2)

    path = pathlib.Path(sys.argv[1])
    blocks = _parse_blocks(path.read_text(encoding="utf-8"))
    nonfunc, func = [], []
    seen_func = False

    trailing: list[tuple[int, list[str]]] = []
    while blocks and not _is_func(blocks[-1][1]):
        trailing.insert(0, blocks.pop())
    if not blocks:
        blocks = trailing
        trailing = []

    for lineno, lines in blocks:
        count = _count_defs(lines)
        if count > 1:
            sys.exit(f"{path}:{lineno}: multiple function definitions in block")
        if _is_func(lines):
            seen_func = True
            func.append((lineno, lines))
        elif seen_func:
            sys.exit(f"{path}:{lineno}: non-function block after function block")
        else:
            nonfunc.append((lineno, lines))

    func.sort(key=_sort_key)
    print(_assemble(nonfunc, func, trailing), end="")


def _assemble(
    nonfunc: list[tuple[int, list[str]]],
    func: list[tuple[int, list[str]]],
    trailing: list[tuple[int, list[str]]],
) -> str:
    nonfunc_text = "".join("".join(b) for _, b in nonfunc)
    func_parts = [_normalise(b) for _, b in func]
    func_text = "\n\n".join(func_parts)
    if nonfunc_text and func_text:
        output = nonfunc_text.rstrip("\n") + "\n\n\n" + func_text
    else:
        output = nonfunc_text + func_text
    if trailing:
        trailing_parts = [_normalise(b) for _, b in trailing]
        trailing_text = "\n\n".join(trailing_parts)
        if output:
            output = output.rstrip("\n") + "\n\n\n" + trailing_text
        else:
            output = trailing_text
    return output


def _count_defs(block: list[str]) -> int:
    return sum(1 for line in block if line.startswith(("def ", "async def ")))


def _is_func(block: list[str]) -> bool:
    for line in block:
        if not line.strip():
            continue
        if line.lstrip() != line:
            break
        if line.startswith(("def ", "async def ")):
            return True
    return False


def _normalise(block: list[str]) -> str:
    while block and (not block[-1].strip()):
        block = block[:-1]
    return "".join(block).rstrip("\n") + "\n"


def _parse_blocks(text: str) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    start = 1
    after_blank = False
    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        is_blank = not line.strip()
        is_unindented = line.lstrip() == line
        if is_unindented and (not is_blank) and after_blank and cur:
            blocks.append((start, cur))
            cur = []
            start = lineno
        cur.append(line)
        after_blank = is_blank
    if cur:
        blocks.append((start, cur))
    return blocks


def _sort_key(block: tuple[int, list[str]]) -> str:
    for line in block[1]:
        if line.startswith("def "):
            return _toggle(line[4:].split("(")[0])
        if line.startswith("async def "):
            return _toggle(line[10:].split("(")[0])
    return ""


def _toggle(name: str) -> str:
    return name[1:] if name.startswith("_") else "_" + name


if __name__ == "__main__":
    main()
