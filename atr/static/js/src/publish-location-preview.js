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

// The publish form on the finish page is replaced in place when a publish attempt fails,
// so the fields are looked up as they change and not held on to
function publishLocationFields() {
	const note = document.getElementById("publish-location-preview");
	const suffixInput = document.getElementById("download_path_suffix");
	if (!note || !suffixInput) return null;
	// A page for an existing release states the version, and the start form has a field for it
	const fixedVersion = note.dataset.version;
	const versionInput =
		fixedVersion === undefined ? document.getElementById("version_key") : null;
	return { note, suffixInput, versionInput, fixedVersion };
}

function publishLocationValues() {
	const fields = publishLocationFields();
	if (!fields) return null;
	return {
		version: fields.versionInput
			? fields.versionInput.value
			: fields.fixedVersion,
		suffix: fields.suffixInput.value,
	};
}

// Set while a change to the fields has yet to be reflected in the note
let publishLocationStale = false;

function initPublishLocationPreview() {
	let timer;
	const refresh = () => {
		publishLocationStale = true;
		clearTimeout(timer);
		timer = setTimeout(updateLocation, 300);
	};
	document.addEventListener("input", (event) => {
		const id = event.target.id;
		if (id === "download_path_suffix" || id === "version_key") refresh();
	});
	document.addEventListener("submit", (event) => {
		const fields = publishLocationFields();
		if (fields?.note.dataset.confirmMismatch !== "true") return;
		if (!event.target.contains(fields.note)) return;
		if (!publishLocationStale) {
			if (!confirmPublishLocation()) event.preventDefault();
			return;
		}
		// The location was edited a moment ago, so check it before asking
		event.preventDefault();
		clearTimeout(timer);
		updateLocation().then(() => {
			if (confirmPublishLocation()) event.target.submit();
		});
	});
	// The browser or a failed submission may have filled the start form in already
	const fields = publishLocationFields();
	if (fields?.versionInput) refresh();
}

function elementText(id) {
	return document.getElementById(id)?.textContent ?? "";
}

function confirmPublishLocation() {
	const mismatch = document.getElementById("publish-location-mismatch");
	if (!mismatch || mismatch.classList.contains("d-none")) return true;
	return window.confirm(
		`This will publish to ${elementText("publish-location-path")}, a different layout from your last release: ` +
			`${elementText("publish-location-previous-version")} is in ${elementText("publish-location-previous-path")}.\n\n` +
			"Files cannot be moved once published. Publish here anyway?",
	);
}

async function updateLocation() {
	const fields = publishLocationFields();
	const requested = publishLocationValues();
	if (!fields || !requested) return;
	const { note } = fields;
	let result = { location: null };
	try {
		const params = new URLSearchParams({
			...requested,
			mode: note.dataset.mode,
		});
		const response = await fetch(`${note.dataset.previewUrl}?${params}`, {
			redirect: "error",
		});
		if (!response.ok) throw new Error("Publish location preview unavailable");
		result = await response.json();
	} catch {
		result = { location: null };
	}
	const latest = publishLocationValues();
	if (
		!latest ||
		latest.version !== requested.version ||
		latest.suffix !== requested.suffix
	)
		return;
	publishLocationStale = false;
	showLocation(result.location);
	showMismatch(result.previous_version, result.previous_location);
}

function showMismatch(previousVersion, previousLocation) {
	const mismatch = document.getElementById("publish-location-mismatch");
	const version = document.getElementById("publish-location-previous-version");
	const path = document.getElementById("publish-location-previous-path");
	if (!mismatch || !version || !path) return;
	if (previousLocation) {
		version.textContent = previousVersion;
		path.textContent = previousLocation;
	}
	mismatch.classList.toggle("d-none", !previousLocation);
}

function showLocation(location) {
	const known = document.getElementById("publish-location-known");
	const unknown = document.getElementById("publish-location-unknown");
	const path = document.getElementById("publish-location-path");
	if (!known || !unknown || !path) return;
	if (location) path.textContent = location;
	known.classList.toggle("d-none", !location);
	unknown.classList.toggle("d-none", Boolean(location));
}

document.addEventListener("DOMContentLoaded", initPublishLocationPreview);
