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
 * Adapted from Alpha-Omega heatmap, scripts/template.html.
 * Upstream: https://github.com/alpha-omega-security/heatmap/blob/4ed37981a6e5049c81d7436d90abe036d2810773/scripts/template.html
 * Copyright (c) 2026 Alpha-Omega. MIT licensed; see atr/static/licenses/LICENSE-Alpha-Omega-Heatmap.txt.
 * ATR changes: external assets and JSON, release/artifact context, precomputed
 * scores, version advisories, explicit unknowns, bounded labels and on-demand rendering.
 */

import * as city from "./heatmap-city-scene.js";
import { COLS, esc } from "./heatmap-columns.js";
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

function applyArtifact() {
	const requested =
		new URLSearchParams(window.location.hash.slice(1)).get("sbom") || "";
	const artifact = document.getElementById("artifact");
	const valid = Array.from(artifact.options).some(
		(option) => option.value === requested,
	);
	sbomFilter = valid ? requested : "";
	artifact.value = sbomFilter;
	document.getElementById("notice").hidden = valid;
	document.getElementById("heatmap-view").hash = window.location.hash;
	render();
}

function buildFilters() {
	for (const p of projects) activeEcosystems.add(p.ecosystem);
	for (const b of HEALTH_BUCKETS)
		makePill(
			document.getElementById("healths"),
			b.label,
			b.hue,
			activeHealth,
			b.key,
			render,
		);
	makePill(
		document.getElementById("healths"),
		"Unknown",
		undefined,
		activeHealth,
		"unknown",
		render,
	);
	for (const e of activeEcosystems)
		makePill(
			document.getElementById("ecosystems"),
			e,
			undefined,
			activeEcosystems,
			e,
			render,
		);
}

async function initialise() {
	document.getElementById("heatmap-view").hash = window.location.hash;
	const response = await fetch("heatmap.json");
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	const data = await response.json();
	if (data.kind !== "sbom_heatmap" || data.analysis_version !== 1)
		throw new Error("This page is out of date; reload it to use this analysis");
	projects = data.rows;
	if (projects.length === 0) {
		document.getElementById("count").textContent =
			"No package versions to show.";
		return;
	}
	const mount = document.getElementById("city");
	mount.hidden = false;
	city.initialise(projects, selectRow);
	buildFilters();
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
		return [p.name, p.key, p.version, ...p.advisories]
			.join(" ")
			.toLowerCase()
			.includes(searchTerm);
	});
	city.show(filtered);
	const health = filtered.filter((p) => p.health !== null).length;
	const criticality = filtered.filter((p) => p.criticality !== null).length;
	document.getElementById("count").textContent =
		`${filtered.length} of ${projects.length} package versions; ${health} with health; ${criticality} with criticality.${filtered.length === 0 ? " No package versions match these filters." : ""}`;
	updateCounts(selected);
}

function selectRow(row) {
	document.getElementById("city-detail").hidden = row === null;
	if (!row) return;
	document.getElementById("city-observations").innerHTML = COLS.map(
		(c) => `<dt>${esc(c.label)}</dt><dd>${c.fmt(row[c.key], row)}</dd>`,
	).join("");
}

try {
	await initialise();
} catch (error) {
	document.getElementById("city").hidden = true;
	document.getElementById("count").textContent =
		`Could not display the 3D view: ${error.message}. Use the table view or download the analysis JSON above.`;
}
