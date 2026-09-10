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

import { esc } from "./heatmap-columns.js";

export const HEALTH_BUCKETS = [
	{ key: "good", label: "Good", hue: 120, test: (h) => h >= 0.67 },
	{ key: "mid", label: "Mid", hue: 60, test: (h) => h >= 0.34 },
	{ key: "bad", label: "Bad", hue: 0, test: (h) => h < 0.34 },
];

export function bucketFor(h) {
	if (h === null) return "unknown";
	const bucket = HEALTH_BUCKETS.find((b) => b.test(h));
	return bucket ? bucket.key : "mid";
}

export function makePill(parent, label, dotHue, set, key, render) {
	const btn = document.createElement("button");
	btn.type = "button";
	btn.className = "pill btn btn-sm btn-outline-secondary rounded-pill active";
	btn.dataset.key = key;
	btn.setAttribute("aria-pressed", "true");
	let html = "";
	if (dotHue !== undefined) html += '<span class="dot"></span>';
	html += `${esc(label)}<span class="count"></span>`;
	btn.innerHTML = html;
	if (dotHue !== undefined)
		btn.querySelector(".dot").style.backgroundColor = `hsl(${dotHue},75%,48%)`;
	btn.addEventListener("click", () => {
		if (set.has(key)) {
			set.delete(key);
			btn.classList.remove("active");
		} else {
			set.add(key);
			btn.classList.add("active");
		}
		btn.setAttribute("aria-pressed", String(set.has(key)));
		render();
	});
	parent.append(btn);
}

export function updateCounts(selected) {
	const eCount = Object.create(null);
	const hCount = Object.create(null);
	selected.forEach((p) => {
		hCount[bucketFor(p.health)] = (hCount[bucketFor(p.health)] || 0) + 1;
		eCount[p.ecosystem] = (eCount[p.ecosystem] || 0) + 1;
	});
	document.querySelectorAll("#healths button").forEach((btn) => {
		btn.querySelector(".count").textContent = hCount[btn.dataset.key] || 0;
	});
	document.querySelectorAll("#ecosystems button").forEach((btn) => {
		btn.querySelector(".count").textContent = eCount[btn.dataset.key] || 0;
	});
}
