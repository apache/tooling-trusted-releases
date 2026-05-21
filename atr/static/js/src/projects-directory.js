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

function filter() {
	const projectFilter = document
		.getElementById("project-filter")
		.value.toLowerCase();
	const cards = document.querySelectorAll(".page-project-card");
	let visibleCount = 0;
	for (const card of cards) {
		const nameElement = card.querySelector(".card-title");
		const name = nameElement.textContent.toLowerCase();
		if (projectFilter) {
			card.parentElement.hidden = !name.includes(projectFilter);
			if (!card.parentElement.hidden) {
				visibleCount++;
			}
		} else {
			card.parentElement.hidden = false;
			visibleCount++;
		}
	}
	document.getElementById("project-count").textContent = visibleCount;
}

// Add event listeners
document.getElementById("filter-button").addEventListener("click", filter);
document
	.getElementById("project-filter")
	.addEventListener("keydown", (event) => {
		if (event.key === "Enter") {
			filter();
			event.preventDefault();
		}
	});

// Participant filter logic
const participantButton = document.getElementById("filter-participant-button");

// Apply the participant view and sync the button text and count to match
function applyParticipantView(showParticipantOnly) {
	const cards = document.querySelectorAll(".page-project-card");
	let visibleCount = 0;
	for (const card of cards) {
		const isParticipant = card.dataset.isParticipant === "true";
		card.parentElement.hidden = showParticipantOnly && !isParticipant;
		if (!card.parentElement.hidden) {
			visibleCount++;
		}
	}
	participantButton.textContent = showParticipantOnly
		? "Show all projects"
		: "Show my projects";
	participantButton.dataset.showing = showParticipantOnly
		? "participant"
		: "all";
	// Reset text filter when toggling participant view
	document.getElementById("project-filter").value = "";
	document.getElementById("project-count").textContent = visibleCount;
}

participantButton.addEventListener("click", function () {
	const showParticipantOnly = this.dataset.showing === "all";
	applyParticipantView(showParticipantOnly);
	const url = new URL(window.location);
	if (showParticipantOnly) {
		url.searchParams.set("show", "mine");
	} else {
		url.searchParams.delete("show");
	}
	window.history.replaceState({}, "", url);
});

if (new URLSearchParams(window.location.search).get("show") === "mine") {
	const anyParticipant = [
		...document.querySelectorAll(".page-project-card"),
	].some((card) => card.dataset.isParticipant === "true");
	if (anyParticipant) {
		applyParticipantView(true);
	}
}
