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

let allCommitteeCards = [];

function filterCommitteesByText() {
	const projectFilter = document.getElementById("project-filter").value;
	const cards = allCommitteeCards;
	let visibleCount = 0;

	if (
		participantButton &&
		participantButton.dataset.showing === "participant"
	) {
		participantButton.dataset.showing = "all";
		participantButton.textContent = "Show my committees";
		participantButton.setAttribute("aria-pressed", "false");
	}

	for (const card of cards) {
		const nameElement = card.querySelector(".card-title");
		const name = nameElement.textContent.trim();
		if (projectFilter) {
			let regex;
			try {
				regex = new RegExp(projectFilter, "i");
			} catch {
				const escapedFilter = projectFilter.replaceAll(
					/[.*+?^${}()|[\]\\]/g,
					"\\$&",
				);
				regex = new RegExp(escapedFilter, "i");
			}
			card.parentElement.hidden = !regex.test(name);
			if (!card.parentElement.hidden) {
				visibleCount++;
			}
		} else {
			card.parentElement.hidden = false;
			visibleCount++;
		}
	}
	document.getElementById("committee-count").textContent = visibleCount;
}

document
	.getElementById("filter-button")
	.addEventListener("click", filterCommitteesByText);
document
	.getElementById("project-filter")
	.addEventListener("keydown", (event) => {
		if (event.key === "Enter") {
			filterCommitteesByText();
			event.preventDefault();
		}
	});

const participantButton = document.getElementById("filter-participant-button");
if (participantButton) {
	participantButton.addEventListener("click", function () {
		const showing = this.dataset.showing;
		const cards = allCommitteeCards;
		let visibleCount = 0;

		if (showing === "all") {
			cards.forEach((card) => {
				const isParticipant = card.dataset.isParticipant === "true";
				card.parentElement.hidden = !isParticipant;
				if (!card.parentElement.hidden) {
					visibleCount++;
				}
			});
			this.textContent = "Show all committees";
			this.dataset.showing = "participant";
			this.setAttribute("aria-pressed", "true");
		} else {
			cards.forEach((card) => {
				card.parentElement.hidden = false;
				visibleCount++;
			});
			this.textContent = "Show my committees";
			this.dataset.showing = "all";
			this.setAttribute("aria-pressed", "false");
		}
		document.getElementById("project-filter").value = "";
		document.getElementById("committee-count").textContent = visibleCount;
	});
}

function applyImageFallback(img) {
	const fallbackSrc = img.dataset.fallbackSrc;
	if (fallbackSrc && img.dataset.fallbackApplied !== "true") {
		img.dataset.fallbackApplied = "true";
		img.src = fallbackSrc;
	} else {
		img.style.display = "none";
	}
}

function setupImageFallbackHandlers() {
	document.querySelectorAll(".page-logo").forEach((img) => {
		img.addEventListener("error", function () {
			applyImageFallback(this);
		});
		if (img.complete && img.naturalWidth === 0) {
			applyImageFallback(img);
		}
	});
}

function initCommitteeVisibility() {
	allCommitteeCards = Array.from(
		document.querySelectorAll(".page-project-card"),
	);
	const cards = allCommitteeCards;
	const committeeCountSpan = document.getElementById("committee-count");
	let initialVisibleCount = 0;
	const initialShowingMode = participantButton
		? participantButton.dataset.showing
		: "all";

	if (participantButton) {
		if (initialShowingMode === "participant") {
			participantButton.setAttribute("aria-pressed", "true");
		} else {
			participantButton.setAttribute("aria-pressed", "false");
		}
	}

	if (initialShowingMode === "participant") {
		cards.forEach((card) => {
			const isParticipant = card.dataset.isParticipant === "true";
			card.parentElement.hidden = !isParticipant;
			if (!card.parentElement.hidden) {
				initialVisibleCount++;
			}
		});
	} else {
		cards.forEach((card) => {
			card.parentElement.hidden = false;
			initialVisibleCount++;
		});
	}
	committeeCountSpan.textContent = initialVisibleCount;
}

function setupSubcardNavigation() {
	document.querySelectorAll(".page-project-subcard").forEach((subcard) => {
		subcard.addEventListener("click", function () {
			if (this.dataset.projectUrl) {
				window.location.href = this.dataset.projectUrl;
			}
		});
	});
}

function setupProjectToggleButtons() {
	document
		.querySelectorAll(".page-toggle-committee-projects")
		.forEach((button) => {
			button.addEventListener("click", function () {
				const projectListContainer = this.closest(
					".page-project-list-container",
				);
				if (projectListContainer) {
					const extraProjects = projectListContainer.querySelectorAll(
						".page-project-extra",
					);
					extraProjects.forEach((proj) => {
						proj.classList.toggle("d-none");
					});

					const isExpanded = this.getAttribute("aria-expanded") === "true";
					if (isExpanded) {
						this.textContent = this.dataset.textShow;
						this.setAttribute("aria-expanded", "false");
					} else {
						this.textContent = this.dataset.textHide;
						this.setAttribute("aria-expanded", "true");
					}
				}
			});
		});
}

document.addEventListener("DOMContentLoaded", () => {
	setupImageFallbackHandlers();
	initCommitteeVisibility();
	// Add a click listener to project subcards to handle navigation
	// Note that we should improve accessibility here
	setupSubcardNavigation();
	// Add a click listener for toggling project visibility within each committee
	setupProjectToggleButtons();
});
