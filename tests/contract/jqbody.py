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

import json
import re

DYNAMIC = object()

_BINDING_RE = re.compile(r"--(arg|argjson)\s+(\w+)\s+")
_LITERAL_RE = re.compile(r"'(\{.*?\})'", re.DOTALL)
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_VAR_RE = re.compile(r'"\$(\w+)"|\$(\w+)')


def parse_body(run):
    if run.count("jq -n") != 1:
        raise ValueError(f"expected one jq -n invocation, found {run.count('jq -n')}")
    bindings = _bindings(run)
    body = {}
    for entry in _entries(_object_literal(run)):
        field, _, value = entry.partition(":")
        field = field.strip()
        value = value.strip()
        if value.startswith("$"):
            if value[1:] not in bindings:
                raise ValueError(f"no --arg or --argjson binding for {value}")
            body[field] = bindings[value[1:]]
        else:
            body[field] = json.loads(value)
    return body


def steps(doc):
    runs = doc.get("runs")
    if isinstance(runs, dict):
        steps_list = runs.get("steps")
        return steps_list if isinstance(steps_list, list) else []
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        flat = []
        for job in jobs.values():
            if isinstance(job, dict):
                job_steps = job.get("steps")
                if isinstance(job_steps, list):
                    flat.extend(job_steps)
        return flat
    return []


def _binding_value(kind, name, rest, run):
    if kind == "argjson":
        var_match = _VAR_RE.match(rest)
        if var_match is None:
            raise ValueError(f"--argjson {name} is not bound to a variable")
        var = var_match.group(1) or var_match.group(2)
        assignment = re.search(rf"^\s*{var}=(\S+)", run, re.MULTILINE)
        if assignment is None:
            raise ValueError(f"no assignment for {var} in the run block")
        return json.loads(assignment.group(1))
    if rest.startswith(('"$', "$")):
        return DYNAMIC
    if rest.startswith('"'):
        quoted = _QUOTED_RE.match(rest)
        if quoted is None:
            raise ValueError(f"unterminated literal for --arg {name}")
        return quoted.group(1)
    return rest.split(None, 1)[0].rstrip("\\")


def _bindings(run):
    bindings = {}
    for match in _BINDING_RE.finditer(run):
        kind, name = match.group(1), match.group(2)
        bindings[name] = _binding_value(kind, name, run[match.end() :], run)
    return bindings


def _entries(literal):
    entries = []
    current = []
    quoted = False
    for char in literal.strip()[1:-1]:
        if char == '"':
            quoted = not quoted
        if (char == ",") and (not quoted):
            entries.append("".join(current))
            current = []
        else:
            current.append(char)
    entries.append("".join(current))
    return [entry.strip() for entry in entries if entry.strip()]


def _object_literal(run):
    literals = _LITERAL_RE.findall(run)
    if len(literals) != 1:
        raise ValueError(f"expected one jq object literal, found {len(literals)}")
    return literals[0]
