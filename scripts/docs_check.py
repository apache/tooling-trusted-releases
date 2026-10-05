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
import concurrent.futures
import pathlib
import re
import sys
import urllib.parse
from typing import Final, NamedTuple

import asfquart.base as base
import requests
import werkzeug.exceptions as exceptions
import werkzeug.routing as routing

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import docs_post_process as post_process

import atr.blueprints.get as blueprints_get
import atr.get.docs as docs


class Link(NamedTuple):
    source_file: str
    line_number: int
    text: str
    target: str
    anchor: str | None


class Heading(NamedTuple):
    text: str
    anchor: str


class RefLink(NamedTuple):
    source_file: str
    line_number: int
    text: str
    target: str


class UrlLink(NamedTuple):
    source_file: str
    line_number: int
    url: str


# TODO: Should think more about whether scripts should use the _ convention or not
# The rationale for using it is that then we can port to non-script code more easily
# But for scripts *per se*, it does not make sense


_LINK_PATTERN: Final = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING_PATTERN: Final = re.compile(r"^#+\s+(.+)$")
_REF_LINK_PATTERN: Final = re.compile(r"\[([^\]]+)\]\(/ref/([^)]+)\)")
_EXTERNAL_TIMEOUT: Final = 20
_EXTERNAL_WORKERS: Final = 8
_HEAD_REFUSED: Final = frozenset({"HTTP 403", "HTTP 405", "HTTP 501"})
_SIGN_IN_REQUIRED: Final = frozenset({"HTTP 401", "HTTP 403"})


def _check_external_url(url: str) -> str | None:
    error = _request_url(url, "HEAD")
    # Some servers refuse HEAD, so try GET before calling the link broken
    if error in _HEAD_REFUSED:
        error = _request_url(url, "GET")
    # Pages behind a sign in, such as Slack archives, exist even though we can't see them
    if error in _SIGN_IN_REQUIRED:
        return None
    return error


def _extract_absolute_links(file_path: pathlib.Path) -> tuple[list[UrlLink], list[UrlLink]]:
    content = file_path.read_text(encoding="utf-8")
    app_links = []
    external_links = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        for match in _LINK_PATTERN.finditer(line):
            target = match.group(2)
            if target.startswith("/ref/"):
                # These are checked against the source tree instead
                continue
            if target.startswith("/"):
                app_links.append(UrlLink(file_path.name, line_number, target))
            elif target.startswith("http://") or target.startswith("https://"):
                external_links.append(UrlLink(file_path.name, line_number, target))

    return app_links, external_links


def _extract_links(file_path: pathlib.Path) -> list[Link]:
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    links = []

    for line_number, line in enumerate(lines, start=1):
        for match in _LINK_PATTERN.finditer(line):
            text = match.group(1)
            target = match.group(2)

            if target.startswith("/"):
                continue

            if target.startswith("http://") or target.startswith("https://") or target.startswith("mailto:"):
                continue

            anchor = None
            if "#" in target:
                target, anchor = target.split("#", 1)

            links.append(Link(file_path.name, line_number, text, target, anchor))

    return links


def _extract_headings(file_path: pathlib.Path) -> list[Heading]:
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    headings = []

    for line in lines:
        match = _HEADING_PATTERN.match(line)
        if match:
            text = match.group(1)
            anchor = post_process.generate_heading_id(text)
            headings.append(Heading(text, anchor))

    return headings


def _extract_ref_links(file_path: pathlib.Path) -> list[RefLink]:
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    links = []

    for line_number, line in enumerate(lines, start=1):
        for match in _REF_LINK_PATTERN.finditer(line):
            text = match.group(1)
            target = match.group(2)

            if ":" in target:
                target = target.split(":", 1)[0]

            links.append(RefLink(file_path.name, line_number, text, target))

    return links


def _validate_links(docs_dir: pathlib.Path, all_links: list[Link]) -> list[str]:
    errors = []
    existing_files = {f.stem for f in docs_dir.glob("*.md")}
    heading_cache: dict[str, set[str]] = {}

    for link in all_links:
        if link.target == ".":
            target_file = "index"
        elif link.target:
            if link.target.endswith(".html"):
                errors.append(
                    f"{link.source_file}:{link.line_number}: Link should not include '.html' extension: '{link.target}'"
                )
                target_file = link.target.removesuffix(".html")
            else:
                target_file = link.target
        else:
            target_file = link.source_file.replace(".md", "")

        if target_file not in existing_files:
            errors.append(
                f"{link.source_file}:{link.line_number}: "
                f"Link to non-existent file '{link.target}' "
                f"(expected {target_file}.md)"
            )
            continue

        if link.anchor:
            if target_file not in heading_cache:
                target_path = docs_dir / f"{target_file}.md"
                headings = _extract_headings(target_path)
                heading_cache[target_file] = {h.anchor for h in headings}

            if link.anchor not in heading_cache[target_file]:
                errors.append(
                    f"{link.source_file}:{link.line_number}: "
                    f"Link to non-existent anchor '#{link.anchor}' in '{target_file}'"
                )

    return errors


def _validate_ref_links(project_root: pathlib.Path, all_ref_links: list[RefLink]) -> list[str]:
    errors = []

    for link in all_ref_links:
        file_path = project_root / link.target
        if not file_path.exists():
            errors.append(f"{link.source_file}:{link.line_number}: Ref link to non-existent file '/ref/{link.target}'")

    return errors


def _request_url(url: str, method: str) -> str | None:
    try:
        with requests.request(
            method,
            url,
            headers={"User-Agent": "atr-docs-check"},
            timeout=_EXTERNAL_TIMEOUT,
            allow_redirects=True,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                return f"HTTP {response.status_code}"
            return None
    except requests.RequestException as e:
        return str(e)


def _validate_app_links(project_root: pathlib.Path, all_app_links: list[UrlLink]) -> list[str]:
    errors = []
    app = base.QuartApp("docs_check", token_file=None)
    blueprints_get.register(app)
    adapter = app.url_map.bind("localhost")

    for link in all_app_links:
        path = urllib.parse.urlsplit(link.url).path
        if path.startswith("/static/"):
            if not (project_root / "atr" / path.lstrip("/")).is_file():
                errors.append(f"{link.source_file}:{link.line_number}: Link to non-existent static file '{link.url}'")
            continue
        try:
            adapter.match(path, method="GET")
        except routing.RequestRedirect:
            # The route exists, it only differs by a trailing slash
            continue
        except exceptions.HTTPException:
            errors.append(f"{link.source_file}:{link.line_number}: Link to non-existent route '{link.url}'")

    return errors


def _validate_external_links(all_external_links: list[UrlLink]) -> list[str]:
    urls = sorted({link.url for link in all_external_links})
    with concurrent.futures.ThreadPoolExecutor(max_workers=_EXTERNAL_WORKERS) as executor:
        failures = dict(zip(urls, executor.map(_check_external_url, urls)))

    errors = []
    for link in all_external_links:
        failure = failures[link.url]
        if failure is not None:
            errors.append(f"{link.source_file}:{link.line_number}: External link '{link.url}' failed: {failure}")
    return errors


def _validate_moved_pages(docs_dir: pathlib.Path) -> list[str]:
    errors = []
    existing_files = {f.stem for f in docs_dir.glob("*.md")}

    for old_page, new_page in docs.MOVED_PAGES.items():
        if old_page in existing_files:
            errors.append(f"Moved page '{old_page}' still exists, so its redirect would hide it")
        if new_page not in existing_files:
            errors.append(f"Moved page '{old_page}' redirects to non-existent page '{new_page}'")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Check links in the ATR documentation")
    parser.add_argument("--external", action="store_true", help="also fetch every external link")
    args = parser.parse_args()

    docs_dir = pathlib.Path("atr/docs")

    if not docs_dir.exists():
        print(f"Error: {docs_dir} not found", file=sys.stderr)
        sys.exit(1)

    project_root = docs_dir.parent.parent

    all_links = []
    all_ref_links = []
    all_app_links = []
    all_external_links = []
    for md_file in docs_dir.glob("*.md"):
        all_links.extend(_extract_links(md_file))
        all_ref_links.extend(_extract_ref_links(md_file))
        app_links, external_links = _extract_absolute_links(md_file)
        all_app_links.extend(app_links)
        all_external_links.extend(external_links)

    errors = _validate_links(docs_dir, all_links)
    errors.extend(_validate_ref_links(project_root, all_ref_links))
    errors.extend(_validate_app_links(project_root, all_app_links))
    errors.extend(_validate_moved_pages(docs_dir))
    if args.external:
        errors.extend(_validate_external_links(all_external_links))

    if errors:
        print("Documentation link validation errors:\n", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        suffix = "s" if (len(errors) > 1) else ""
        print(f"\nFound {len(errors)} error{suffix}", file=sys.stderr)
        sys.exit(1)

    print(f"Validated {len(all_links)} links across {len(list(docs_dir.glob('*.md')))} files")
    print(f"Validated {len(all_ref_links)} ref links")
    print(f"Validated {len(all_app_links)} app links")
    if args.external:
        print(f"Validated {len(all_external_links)} external links")
    else:
        print(f"Skipped {len(all_external_links)} external links, use --external to check them")
    print("All links are valid")


if __name__ == "__main__":
    main()
