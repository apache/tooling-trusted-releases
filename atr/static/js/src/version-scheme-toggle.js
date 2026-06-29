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

// Cycle config is per version method: semver projects fill in a cycle_match
// regex, calver projects a date format, simple projects neither. Show whichever
// applies to the selected radio. This is only a convenience - the server
// validates per method regardless, so if the script doesn't run both fields
// just stay visible.

function selectedMethod() {
	const checked = document.querySelector(
		"input[name='version_method']:checked",
	);
	return checked ? checked.value : null;
}

document.addEventListener("DOMContentLoaded", () => {
	const radios = document.querySelectorAll("input[name='version_method']");
	if (radios.length === 0) return;

	const cycleMatchRow = document.getElementById("cycle_match")?.closest(".row");
	const calverFormatRow = document
		.getElementById("calver_format")
		?.closest(".row");
	if (!cycleMatchRow || !calverFormatRow) return;

	const apply = () => {
		const method = selectedMethod();
		// Toggle Bootstrap's .d-none rather than the hidden attribute: these are
		// .row elements, and .row { display: flex } would otherwise win over the
		// user-agent [hidden] rule and leave the field showing.
		cycleMatchRow.classList.toggle("d-none", method !== "semver");
		calverFormatRow.classList.toggle("d-none", method !== "calver");
	};

	radios.forEach((radio) => {
		radio.addEventListener("change", apply);
	});
	apply();
});
