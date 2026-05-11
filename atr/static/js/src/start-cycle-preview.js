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

function buildLifecycleLink(lifecycleUrl) {
	const link = document.createElement("a");
	link.href = lifecycleUrl;
	link.textContent = "lifecycle tab";
	return link;
}

function readConfig() {
	const config = document.getElementById("start-cycle-config");
	if (!config) {
		return null;
	}
	return {
		cycleMatch: config.dataset.cycleMatch,
		lifecycleUrl: config.dataset.lifecycleUrl,
	};
}

function renderPreview(preview, alertClass, prefixNodes, lifecycleUrl, suffix) {
	preview.className = `alert ${alertClass} mt-2`;
	while (preview.firstChild) {
		preview.firstChild.remove();
	}
	for (const node of prefixNodes) {
		preview.append(node);
	}
	preview.append(" Visit the ");
	preview.append(buildLifecycleLink(lifecycleUrl));
	preview.append(suffix);
}

function updatePreview(versionInput, preview, regex, lifecycleUrl) {
	const version = versionInput.value.trim();
	if (!version) {
		renderPreview(
			preview,
			"alert-light",
			[
				document.createTextNode(
					"Enter a version to see which cycle it lands in.",
				),
			],
			lifecycleUrl,
			" to change the cycle pattern.",
		);
		return;
	}

	// Mirror Python's re.fullmatch by requiring the regex to consume the
	// entire version string, and by treating an empty capture as no match.
	const match = version.match(regex);
	if (!match || match[0] !== version || !match[1]) {
		renderPreview(
			preview,
			"alert-warning",
			[
				document.createTextNode(
					"This version doesn't match the project's cycle pattern.",
				),
			],
			lifecycleUrl,
			" to adjust the pattern.",
		);
		return;
	}

	const cycleName = document.createElement("strong");
	cycleName.textContent = match[1];
	renderPreview(
		preview,
		"alert-info",
		[
			document.createTextNode("This release will be added to cycle "),
			cycleName,
			document.createTextNode("."),
		],
		lifecycleUrl,
		" if you'd like to change which cycle this version belongs to.",
	);
}

function initStartCyclePreview() {
	const config = readConfig();
	if (!config) {
		return;
	}

	let regex;
	try {
		regex = new RegExp(config.cycleMatch);
	} catch (e) {
		console.error("Invalid cycle_match regex:", e);
		return;
	}

	const versionInput = document.getElementById("version_key");
	const preview = document.getElementById("start-cycle-preview");
	if (!versionInput || !preview) {
		return;
	}

	const refresh = () =>
		updatePreview(versionInput, preview, regex, config.lifecycleUrl);
	versionInput.addEventListener("input", refresh);
	refresh();
}

document.addEventListener("DOMContentLoaded", initStartCyclePreview);
