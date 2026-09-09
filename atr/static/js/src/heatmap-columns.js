/*
 *  Licensed to the Apache Software Foundation (ASF) under one
 *  or more contributor license agreements.  See the NOTICE file
 *  distributed with this work for additional information
 *  regarding copyright ownership.  The ASF licenses this file
 *  to you under the Apache License, Version 2.0 (the
 *  "License"); you may not use this file except in compliance
 *  with the License.  You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing,
 *  software distributed under the License is distributed on an
 *  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 *  KIND, either express or implied.  See the License for the
 *  specific language governing permissions and limitations
 *  under the License.
 */

/*
 * Adapted from Alpha-Omega heatmap, scripts/table_template.html.
 * Upstream: https://github.com/alpha-omega-security/heatmap/blob/4ed37981a6e5049c81d7436d90abe036d2810773/scripts/table_template.html
 * Copyright (c) 2026 Alpha-Omega. MIT licensed; see atr/static/licenses/LICENSE-Alpha-Omega-Heatmap.txt.
 * ATR changes: external assets and JSON, release/artifact context, precomputed
 * scores, version advisories, explicit unknowns, safe links and complete results.
 */

export const COLS = [
	{
		key: "name",
		label: "Package",
		numeric: false,
		fmt: (_v, p) => nameCell(p),
	},
	{
		key: "version",
		label: "Version",
		numeric: false,
		fmt: (v) => (v === null ? nullCell() : esc(v)),
	},
	{
		key: "advisory_count",
		label: "Advisories",
		numeric: true,
		fmt: (_v, p) => advisories(p),
	},
	{
		key: "health",
		label: "Health",
		numeric: true,
		fmt: (v, p) =>
			`${v === null ? '<span class="nul">Insufficient data</span>' : bar(v)}<small class="coverage">${p.health_inputs}/4 inputs</small>`,
	},
	{
		key: "archived",
		label: "Archived",
		numeric: false,
		fmt: (v) => (v === null ? nullCell() : v ? "Yes" : "No"),
	},
	{
		key: "maintained",
		label: "Maintained",
		numeric: true,
		fmt: (v) => (v === null ? nullCell() : `${fmtInt(v)}/10`),
	},
	{
		key: "criticality",
		label: "Criticality",
		numeric: true,
		fmt: (v) => score(v),
	},
	{ key: "risk", label: "Risk", numeric: true, fmt: (v) => score(v) },
	{
		key: "latest_release_at",
		label: "Latest release",
		numeric: false,
		fmt: (v) => (v ? esc(v.slice(0, 10)) : nullCell()),
	},
];

function advisories(p) {
	if (p.advisories.length > 0) {
		const links = p.advisories.map(
			(id) =>
				`<a href="https://osv.dev/vulnerability/${encodeURIComponent(id)}">${esc(id)}</a>`,
		);
		return `<details><summary>${p.advisories.length} reported</summary>${links.join("<br>")}</details>`;
	}
	return (
		{
			none: "None reported",
			version_unknown: "Version unknown",
			unsupported: "Unsupported ecosystem",
			not_found: "Version not found",
			failed: "Lookup failed",
		}[p.advisory_status] || "Unknown"
	);
}

function bar(v) {
	return `<meter min="0" max="1" low="0.34" high="0.67" optimum="1" value="${v}" aria-label="Maintenance health"></meter><span class="bar-value">${v.toFixed(3)}</span>`;
}

export function esc(s) {
	return String(s).replaceAll(
		/[&<>"']/g,
		(c) =>
			({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
				c
			],
	);
}

function fmtInt(n) {
	return n === null ? nullCell() : n.toLocaleString();
}

function nameCell(p) {
	const repository = webUrl(
		p.source_repo ? `https://${p.source_repo}` : p.repository_url,
	);
	const name = repository
		? `<a href="${esc(repository)}">${esc(p.name)}</a>`
		: esc(p.name);
	return `<div class="name">${name}<small>${esc(p.key)}</small>
    <details><summary>Observations</summary>
      <div>Repository source: ${esc(p.repository_source || "Unknown")}${p.repository_source === "gitbox_mirror" ? " (inferred)" : ""}</div>
      <div>Version published: ${esc(p.published_at || "Unknown")}</div>
      <div>Contribution distribution: ${p.dds === null ? "Unknown" : p.dds.toFixed(3)}</div>
      <div>Active maintainers: ${fmtInt(p.active_maintainers)}</div>
      <div>Guidance files: ${fmtInt(p.governance_files)} of 3</div>
      <div>Scorecard date: ${esc(p.scorecard_date || "Unknown")}</div>
      <div>Source package identifiers: ${p.source_purls.map(esc).join(", ")}</div>
    </details></div>`;
}

function nullCell() {
	return '<span class="nul">Unknown</span>';
}

function score(v) {
	return v === null ? nullCell() : v.toFixed(3);
}

function webUrl(value) {
	if (!value) return null;
	try {
		const url = new URL(value);
		const http = ["http:", "https:"].includes(url.protocol);
		return http && url.hostname ? url.href : null;
	} catch {
		return null;
	}
}
