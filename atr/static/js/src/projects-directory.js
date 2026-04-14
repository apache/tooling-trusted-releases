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
	const projectFilter = document.getElementById("project-filter").value;
	if (projectFilter.length > 100) {
	    throw new Error("Input too long");
	}
	const regex = new RegExp(projectFilter, 'i');
	const cards = document.querySelectorAll(".page-project-card");
	let visibleCount = 0;
	for (const card of cards) {
		const nameElement = card.querySelector(".card-title");
		const name = nameElement.textContent;
		if (projectFilter) {
			card.parentElement.hidden = !regex.test(name);
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
participantButton.addEventListener("click", function () {
	const showing = this.dataset.showing;
	const cards = document.querySelectorAll(".page-project-card");
	let visibleCount = 0;

	if (showing === "all") {
		// Switch to showing only participant projects
		cards.forEach((card) => {
			const isParticipant = card.dataset.isParticipant === "true";
			card.parentElement.hidden = !isParticipant;
			if (!card.parentElement.hidden) {
				visibleCount++;
			}
		});
		this.textContent = "Show all projects";
		this.dataset.showing = "participant";
	} else {
		// Switch to showing all projects
		cards.forEach((card) => {
			card.parentElement.hidden = false;
			visibleCount++;
		});
		this.textContent = "Show my projects";
		this.dataset.showing = "all";
	}
	// Reset text filter when toggling participant view
	document.getElementById("project-filter").value = "";
	// Update count
	document.getElementById("project-count").textContent = visibleCount;
});
