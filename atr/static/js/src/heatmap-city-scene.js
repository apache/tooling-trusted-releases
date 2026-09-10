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

import { layout } from "./heatmap-city-layout.js";

const THREE = window.THREE;
const meshes = [];
const labels = new Map();

let camera, controls, raycaster, renderer, scene;
let focusLabel, mount, selectRow, tooltip;
let selected = null;
let pointerDown = null;
let hoverEvent = null;
let hoverFrame = null;
let frame = null;

function addMeshes(city) {
	for (const [p, position] of city.positions) {
		const color =
			p.health === null
				? new THREE.Color(0xadb5bd)
				: new THREE.Color(`hsl(${Math.round(p.health * 120)},75%,48%)`);
		const mesh = new THREE.Mesh(
			new THREE.BoxGeometry(2, position.height, 2),
			new THREE.MeshLambertMaterial({ color }),
		);
		mesh.position.set(position.x, position.height / 2, position.z);
		mesh.userData = { row: p, height: position.height, matches: true };
		meshes.push(mesh);
		scene.add(mesh);
		if (city.labelled.has(p)) {
			const label = makeLabel();
			updateLabel(label, mesh);
			labels.set(mesh, label);
		}
	}
	focusLabel = makeLabel();
	focusLabel.visible = false;
}

function bindEvents() {
	renderer.domElement.addEventListener("pointermove", (event) => {
		if (pointerDown) return;
		hoverEvent = event;
		if (hoverFrame === null) hoverFrame = requestAnimationFrame(processHover);
	});
	renderer.domElement.addEventListener("pointerleave", () => {
		pointerDown = null;
		hideTooltip();
	});
	renderer.domElement.addEventListener("pointerdown", (event) => {
		pointerDown = event.isPrimary ? event : null;
	});
	renderer.domElement.addEventListener("pointerup", pointerUp);
	renderer.domElement.addEventListener("pointercancel", () => {
		pointerDown = null;
	});
	document
		.getElementById("city-clear")
		.addEventListener("click", () => select(null));
	window.addEventListener("resize", resize);
}

function draw() {
	frame = null;
	const moving = controls.update();
	renderer.render(scene, camera);
	if (moving) invalidate();
}

function hideTooltip() {
	tooltip.hidden = true;
	renderer.domElement.classList.remove("city-pointer");
	hoverEvent = null;
}

function hit(event) {
	const bounds = renderer.domElement.getBoundingClientRect();
	const mouse = new THREE.Vector2(
		((event.clientX - bounds.left) / bounds.width) * 2 - 1,
		-((event.clientY - bounds.top) / bounds.height) * 2 + 1,
	);
	raycaster.setFromCamera(mouse, camera);
	return (
		raycaster
			.intersectObjects(meshes)
			.find((h) => !h.object.material.transparent)?.object ?? null
	);
}

function hover(event) {
	const mesh = hit(event);
	if (!mesh) {
		hideTooltip();
		return;
	}
	const p = mesh.userData.row;
	tooltip.textContent = `${p.name} · ${p.version ?? "Version unknown"} · Health ${p.health?.toFixed(3) ?? "unknown"} · Criticality ${p.criticality?.toFixed(3) ?? "unknown"}`;
	tooltip.hidden = false;
	const bounds = renderer.domElement.getBoundingClientRect();
	tooltip.style.left = `${Math.max(0, Math.min(event.clientX - bounds.left + 14, bounds.width - tooltip.offsetWidth))}px`;
	tooltip.style.top = `${Math.max(0, Math.min(event.clientY - bounds.top + 14, bounds.height - tooltip.offsetHeight))}px`;
	renderer.domElement.classList.add("city-pointer");
}

export function initialise(rows, onSelect) {
	mount = document.getElementById("city");
	tooltip = document.getElementById("city-tooltip");
	selectRow = onSelect;
	const city = layout(rows);
	scene = new THREE.Scene();
	scene.background = new THREE.Color(0xf8f9fa);
	camera = new THREE.PerspectiveCamera(
		45,
		mount.clientWidth / mount.clientHeight,
		0.1,
		2000,
	);
	const middle =
		Math.max(...Array.from(city.positions.values(), (p) => p.height)) / 2;
	const distance =
		Math.max(city.radius, middle * 1.25) / Math.min(camera.aspect, 1);
	camera.position.set(distance * 1.9, middle + distance, distance * 1.9);
	renderer = new THREE.WebGLRenderer({ antialias: true });
	renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
	renderer.setSize(mount.clientWidth, mount.clientHeight);
	renderer.domElement.setAttribute(
		"aria-label",
		"3D dependency heatmap; use the table view for accessible package details",
	);
	mount.prepend(renderer.domElement);
	controls = new THREE.OrbitControls(camera, renderer.domElement);
	controls.target.set(0, middle, 0);
	controls.enableDamping = true;
	controls.dampingFactor = 0.08;
	controls.addEventListener("change", invalidate);
	controls.addEventListener("start", hideTooltip);
	scene.add(new THREE.AmbientLight(0xffffff, 0.45));
	const light = new THREE.DirectionalLight(0xffffff, 0.85);
	light.position.set(city.radius, city.radius * 1.5, city.radius * 0.5);
	scene.add(light);
	scene.add(new THREE.HemisphereLight(0xeaeaea, 0x1e293b, 0.3));
	const ground = new THREE.Mesh(
		new THREE.CircleGeometry(city.radius * 2.5, 64),
		new THREE.MeshLambertMaterial({ color: 0xdee2e6 }),
	);
	ground.rotation.x = -Math.PI / 2;
	scene.add(ground);
	addMeshes(city);
	raycaster = new THREE.Raycaster();
	bindEvents();
	invalidate();
}

function invalidate() {
	if (frame === null) frame = requestAnimationFrame(draw);
}

function makeLabel() {
	const canvas = document.createElement("canvas");
	canvas.width = 512;
	canvas.height = 96;
	const texture = new THREE.CanvasTexture(canvas);
	texture.minFilter = THREE.LinearFilter;
	const sprite = new THREE.Sprite(
		new THREE.SpriteMaterial({
			map: texture,
			transparent: true,
			depthTest: false,
			depthWrite: false,
		}),
	);
	sprite.scale.set(8, 1.5, 1);
	sprite.renderOrder = 999;
	scene.add(sprite);
	return sprite;
}

function pointerUp(event) {
	if (!pointerDown) return;
	const distance = Math.hypot(
		event.clientX - pointerDown.clientX,
		event.clientY - pointerDown.clientY,
	);
	pointerDown = null;
	if (distance > 4) return;
	if (event.button !== 0) return;
	const mesh = hit(event);
	select(mesh === selected ? null : mesh);
}

function processHover() {
	hoverFrame = null;
	if (hoverEvent) hover(hoverEvent);
	hoverEvent = null;
}

function resize() {
	camera.aspect = mount.clientWidth / mount.clientHeight;
	camera.updateProjectionMatrix();
	renderer.setSize(mount.clientWidth, mount.clientHeight);
	hideTooltip();
	invalidate();
}

function select(mesh) {
	selected = mesh;
	focusLabel.visible = Boolean(mesh) && !labels.has(mesh);
	if (focusLabel.visible) updateLabel(focusLabel, mesh);
	selectRow(mesh?.userData.row ?? null);
	hideTooltip();
	visibility();
}

export function show(rows) {
	const visible = new Set(rows);
	for (const mesh of meshes)
		mesh.userData.matches = visible.has(mesh.userData.row);
	select(null);
}

function updateLabel(label, mesh) {
	const p = mesh.userData.row;
	const canvas = label.material.map.image;
	const ctx = canvas.getContext("2d");
	ctx.fillStyle = "#f8f9fa";
	ctx.fillRect(0, 0, 512, 96);
	ctx.strokeStyle = "#adb5bd";
	ctx.lineWidth = 2;
	ctx.strokeRect(1, 1, 510, 94);
	ctx.font = "bold 36px sans-serif";
	ctx.fillStyle = "#212529";
	ctx.textAlign = "center";
	ctx.textBaseline = "middle";
	ctx.fillText(`${p.name} ${p.version ?? "(version unknown)"}`, 256, 48, 496);
	label.material.map.needsUpdate = true;
	label.position.set(
		mesh.position.x,
		mesh.userData.height + 1.5,
		mesh.position.z,
	);
}

function visibility() {
	for (const mesh of meshes) {
		const visible = selected ? mesh === selected : mesh.userData.matches;
		mesh.material.transparent = !visible;
		mesh.material.opacity = visible ? 1 : 0.12;
		mesh.material.depthWrite = visible;
		const label = labels.get(mesh);
		if (label) label.visible = visible;
	}
	invalidate();
}
