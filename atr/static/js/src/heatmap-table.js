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

import { COLS } from "./heatmap-columns.js";
import {
	bucketFor,
	HEALTH_BUCKETS,
	makePill,
	updateCounts,
} from "./heatmap-filters.js";

const activeHealth = new Set(["good", "mid", "bad", "unknown"]);
const activeEcosystems = new Set();

let projects = [];
let searchTerm = "";
let sbomFilter = "";
let sortKey = "";
let sortDir = -1;

function applyArtifact() {
	document.getElementById("heatmap-view").hash = window.location.hash;
	const requested =
		new URLSearchParams(window.location.hash.slice(1)).get("sbom") || "";
	const artifact = document.getElementById("artifact");
	const valid = Array.from(artifact.options).some(
		(option) => option.value === requested,
	);
	sbomFilter = valid ? requested : "";
	artifact.value = sbomFilter;
	document.getElementById("notice").hidden = valid;
	render();
}

function buildHeader() {
	const headEl = document.getElementById("head");
	COLS.forEach((c) => {
		const th = document.createElement("th");
		th.scope = "col";
		const button = document.createElement("button");
		button.type = "button";
		button.className = "btn btn-link p-0 fw-semibold text-body text-nowrap";
		button.textContent = c.label;
		th.append(button);
		if (c.numeric) th.classList.add("numeric");
		th.addEventListener("click", () => {
			if (sortKey === c.key) sortDir = -sortDir;
			else {
				sortKey = c.key;
				sortDir = c.numeric ? -1 : 1;
			}
			render();
		});
		th.dataset.key = c.key;
		headEl.append(th);
	});
}

function compare(a, b) {
	if (a === null) return b === null ? 0 : 1;
	if (b === null) return -1;
	if (typeof a === "number") return sortDir * (a - b);
	return (
		sortDir * String(a).localeCompare(String(b), undefined, { numeric: true })
	);
}

async function initialise() {
	document.getElementById("heatmap-view").hash = window.location.hash;
	const response = await fetch("heatmap.json");
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	const data = await response.json();
	const wrongKind = data.kind !== "sbom_heatmap";
	const wrongVersion = data.analysis_version !== 1;
	if (wrongKind || wrongVersion)
		throw new Error("This page is out of date; reload it to use this analysis");
	projects = data.rows;
	for (const p of projects) {
		p.advisory_count = ["found", "none"].includes(p.advisory_status)
			? p.advisories.length
			: null;
	}
	const ecosystems = [...new Set(projects.map((p) => p.ecosystem))].toSorted(
		(a, b) =>
			projects.filter((p) => p.ecosystem === b).length -
			projects.filter((p) => p.ecosystem === a).length,
	);
	for (const e of ecosystems) activeEcosystems.add(e);
	const healths = document.getElementById("healths");
	const ecosystemPills = document.getElementById("ecosystems");
	for (const b of HEALTH_BUCKETS)
		makePill(healths, b.label, b.hue, activeHealth, b.key, render);
	makePill(healths, "Unknown", undefined, activeHealth, "unknown", render);
	for (const e of ecosystems)
		makePill(ecosystemPills, e, undefined, activeEcosystems, e, render);
	buildHeader();
	document.getElementById("filters").addEventListener("submit", (event) => {
		event.preventDefault();
		searchTerm = document.getElementById("search").value.trim().toLowerCase();
		render();
	});
	document.getElementById("artifact").addEventListener("change", (event) => {
		window.location.hash = new URLSearchParams({
			sbom: event.target.value,
		}).toString();
	});
	window.addEventListener("hashchange", applyArtifact);
	applyArtifact();
	document.getElementById("filters").hidden = false;
}

function render() {
	const selected = projects.filter((p) =>
		sbomFilter ? p.artifacts.includes(sbomFilter) : true,
	);
	const filtered = selected.filter((p) => {
		if (!activeHealth.has(bucketFor(p.health))) return false;
		if (!activeEcosystems.has(p.ecosystem)) return false;
		if (searchTerm) {
			const hay = [p.name, p.key, p.version, ...p.advisories]
				.join(" ")
				.toLowerCase();
			if (!hay.includes(searchTerm)) return false;
		}
		return true;
	});
	if (sortKey) filtered.sort((a, b) => compare(a[sortKey], b[sortKey]));
	const headEl = document.getElementById("head");
	[...headEl.children].forEach((th) => {
		th.classList.remove("sorted-asc", "sorted-desc");
		th.setAttribute("aria-sort", "none");
		if (th.dataset.key === sortKey) {
			th.classList.add(sortDir > 0 ? "sorted-asc" : "sorted-desc");
			th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
		}
	});
	const rowsEl = document.getElementById("rows");
	rowsEl.innerHTML = "";
	const frag = document.createDocumentFragment();
	filtered.forEach((p) => {
		const tr = document.createElement("tr");
		COLS.forEach((c) => {
			const td = document.createElement("td");
			if (c.numeric) td.classList.add("numeric");
			td.innerHTML = c.fmt(p[c.key], p);
			tr.append(td);
		});
		frag.append(tr);
	});
	rowsEl.append(frag);
	updateSummary(filtered);
	updateCounts(selected);
}

function updateSummary(filtered) {
	const countEl = document.getElementById("count");
	const direction = sortDir > 0 ? "↑" : "↓";
	const order = sortKey
		? `Sorted by ${COLS.find((c) => c.key === sortKey).label} ${direction}`
		: "Packages with any advisories first, by highest risk";
	const advisoryCount = filtered.filter((p) => p.advisories.length).length;
	const health = filtered.filter((p) => p.health !== null).length;
	const failed = filtered.filter((p) => p.advisory_status === "failed").length;
	const missing = filtered.filter(
		(p) => p.advisory_status === "not_found",
	).length;
	countEl.textContent = `${filtered.length} of ${projects.length} package versions; ${advisoryCount} with advisories; ${health} with health; ${failed} failed lookups; ${missing} versions not found. ${order}.`;
	if (filtered.length === 0)
		countEl.textContent += " No package versions to show.";
}

try {
	await initialise();
} catch (error) {
	document.getElementById("count").textContent =
		`Could not load the analysis: ${error.message}. The JSON download remains available above.`;
}
