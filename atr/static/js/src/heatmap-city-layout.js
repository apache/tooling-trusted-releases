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

const PHI = Math.PI * (3 - Math.sqrt(5));

export function layout(rows) {
	const projects = rows.toSorted(
		(a, b) => (b.criticality ?? -1) - (a.criticality ?? -1),
	);
	const known = projects.filter((p) => p.criticality !== null);
	const minimum = known.at(-1)?.criticality ?? 0;
	const span = Math.max((known[0]?.criticality ?? 0) - minimum, 1e-6);
	const rng = makeRng(0xc1a1);
	const byEco = new Map();
	for (const p of projects) {
		if (!byEco.has(p.ecosystem)) byEco.set(p.ecosystem, []);
		byEco.get(p.ecosystem).push(p);
	}
	const clusters = [...byEco.entries()].map(([name, items]) => ({
		name,
		projects: orderOwners(items),
		radius: 4.5 * Math.sqrt(items.length) + 2,
	}));
	const radius = pack(clusters, rng);
	const positions = new Map();
	for (const c of clusters) {
		c.projects.forEach((p, i) => {
			const r = 4.5 * Math.sqrt(i);
			const jitter = 1.5 + (i / Math.max(c.projects.length - 1, 1)) * 10;
			const x = c.cx + r * Math.cos(i * PHI) + (rng() - 0.5) * 2 * jitter;
			const z = c.cz + r * Math.sin(i * PHI) + (rng() - 0.5) * 2 * jitter;
			const height =
				p.criticality === null
					? 1
					: 1 + ((p.criticality - minimum) / span) ** 2.5 * 79;
			positions.set(p, { x, z, height });
		});
	}
	return {
		positions,
		radius,
		labelled: new Set(known.slice(0, 20)),
	};
}

function makeRng(seed) {
	let value = seed;
	return () => {
		value = (value * 1664525 + 1013904223) % 4294967296;
		return value / 4294967296;
	};
}

function orderOwners(projects) {
	const weights = new Map();
	for (const p of projects)
		weights.set(
			owner(p),
			Math.max(weights.get(owner(p)) ?? -1, p.criticality ?? -1),
		);
	return projects.toSorted((a, b) => {
		if (owner(a) !== owner(b))
			return weights.get(owner(b)) - weights.get(owner(a));
		return (b.criticality ?? -1) - (a.criticality ?? -1);
	});
}

function owner(p) {
	return p.source_repo?.split("/")[1] ?? "";
}

function pack(clusters, rng) {
	for (let i = clusters.length - 1; i > 0; i--) {
		const j = Math.floor(rng() * (i + 1));
		[clusters[i], clusters[j]] = [clusters[j], clusters[i]];
	}
	const area = clusters.reduce(
		(sum, c) => sum + Math.PI * c.radius * c.radius,
		0,
	);
	const width = Math.sqrt(area) * 1.3;
	let rowHeight = 0,
		rowX = 0,
		rowZ = 0;
	for (const c of clusters) {
		const slot = c.radius * 2 - 2;
		if (rowX > 0 && rowX + slot > width) {
			rowZ += rowHeight;
			rowX = 0;
			rowHeight = 0;
		}
		c.cx = rowX + c.radius;
		c.cz = rowZ + c.radius;
		rowX += slot;
		rowHeight = Math.max(rowHeight, slot);
	}
	const edgeX = Math.max(0, ...clusters.map((c) => c.cx + c.radius));
	const edgeZ = Math.max(0, ...clusters.map((c) => c.cz + c.radius));
	for (const c of clusters) {
		c.cx -= edgeX / 2;
		c.cz -= edgeZ / 2;
		c.cx += (rng() - 0.5) * c.radius;
		c.cz += (rng() - 0.5) * c.radius;
	}
	return Math.max(edgeX, edgeZ) / 2 + 12;
}
