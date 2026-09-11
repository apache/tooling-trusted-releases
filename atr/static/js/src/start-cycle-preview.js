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

function initStartCyclePreview() {
	const input = document.getElementById("version_key");
	const output = document.getElementById("start-cycle-preview-text");
	if (!input || !output) return;
	const emptyMessage = output.textContent;
	const url = output.dataset.previewUrl;

	let timer;
	const refresh = () => {
		clearTimeout(timer);
		const version = input.value;
		if (!version.trim()) {
			output.textContent = emptyMessage;
			return;
		}
		output.textContent = "";
		timer = setTimeout(() => updatePreview(input, output, url, version), 300);
	};
	input.addEventListener("input", refresh);
	refresh();
}

async function updatePreview(input, output, url, version) {
	let message;
	try {
		const response = await fetch(`${url}?${new URLSearchParams({ version })}`, {
			redirect: "error",
		});
		if (!response.ok) throw new Error("Cycle preview unavailable");
		const { cycle } = await response.json();
		message =
			cycle === null
				? "Enter a valid version matching the project's cycle pattern."
				: `Cycle: ${cycle}.`;
	} catch {
		message = "Cycle preview unavailable.";
	}
	if (input.value === version) output.textContent = message;
}

document.addEventListener("DOMContentLoaded", initStartCyclePreview);
