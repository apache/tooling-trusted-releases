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

// Shared behaviour for the committees, releases and projects grids. A page
// calls window.initCardGrid() with its own selectors and noun.
//
// The text filter and the mine/all toggle override each other: searching drops
// back to the full set, toggling clears the search.

function applyImageFallback(img) {
	const fallback = img.dataset.fallbackSrc;
	if (fallback && img.dataset.fallbackApplied !== "true") {
		img.dataset.fallbackApplied = "true";
		img.src = fallback;
	} else {
		img.classList.add("d-none");
	}
}

function setupImageFallbackHandlers() {
	for (const img of document.querySelectorAll(".page-logo")) {
		img.addEventListener("error", () => applyImageFallback(img));
		if (img.complete && img.naturalWidth === 0) {
			applyImageFallback(img);
		}
	}
}

function setupSubcardNavigation() {
	for (const subcard of document.querySelectorAll(".page-project-subcard")) {
		subcard.addEventListener("click", () => {
			if (subcard.dataset.projectUrl) {
				window.location.href = subcard.dataset.projectUrl;
			}
		});
	}
}

function setupProjectToggleButtons() {
	for (const button of document.querySelectorAll(
		".page-toggle-committee-projects",
	)) {
		button.addEventListener("click", () => {
			const container = button.closest(".page-project-list-container");
			if (!container) {
				return;
			}
			for (const extra of container.querySelectorAll(".page-project-extra")) {
				extra.classList.toggle("d-none");
			}
			const expanded = button.getAttribute("aria-expanded") === "true";
			button.textContent = expanded
				? button.dataset.textShow
				: button.dataset.textHide;
			button.setAttribute("aria-expanded", expanded ? "false" : "true");
		});
	}
}

function cardMatchesText(card, query) {
	const title = card.querySelector(".card-title");
	if (title?.textContent.trim().toLowerCase().includes(query)) {
		return true;
	}
	// Catalogue cards bundle projects, so match their names too.
	for (const named of card.querySelectorAll("[data-project-name]")) {
		if (named.dataset.projectName.toLowerCase().includes(query)) {
			return true;
		}
	}
	return false;
}

function updateUrl(mineOnly) {
	// Write the view explicitly, so a reload can tell "chose all" apart from
	// "fresh visit, use default".
	const url = new URL(window.location);
	url.searchParams.set("show", mineOnly ? "mine" : "all");
	window.history.replaceState({}, "", url);
}

function updateRetiredUrl(showRetired) {
	const url = new URL(window.location);
	url.searchParams.set("retired", showRetired ? "show" : "hide");
	window.history.replaceState({}, "", url);
}

function setCount(ctx, visible) {
	if (ctx.countSpan) {
		ctx.countSpan.textContent = visible;
	}
}

function showingMine(ctx) {
	return ctx.participantButton?.dataset.showing === "participant";
}

function showingRetired(ctx) {
	return ctx.archivedButton?.dataset.showing === "retired";
}

function cardEligible(ctx, card) {
	return showingRetired(ctx) || card.dataset.isArchived !== "true";
}

function setRetiredButton(ctx, showRetired) {
	ctx.archivedButton.dataset.showing = showRetired ? "retired" : "current";
	ctx.archivedButton.textContent = showRetired
		? `Hide retired ${ctx.config.noun}`
		: `Show retired ${ctx.config.noun}`;
	ctx.archivedButton.setAttribute(
		"aria-pressed",
		showRetired ? "true" : "false",
	);
}

function setParticipantMode(ctx, mineOnly, keepText) {
	let visible = 0;
	for (const card of ctx.cards) {
		const show =
			cardEligible(ctx, card) &&
			(!mineOnly || card.dataset.isParticipant === "true");
		card.parentElement.hidden = !show;
		if (show) {
			visible++;
		}
	}
	if (ctx.participantButton) {
		ctx.participantButton.dataset.showing = mineOnly ? "participant" : "all";
		ctx.participantButton.textContent = mineOnly
			? `Show all ${ctx.config.noun}`
			: `Show my ${ctx.config.noun}`;
		ctx.participantButton.setAttribute(
			"aria-pressed",
			mineOnly ? "true" : "false",
		);
	}
	if (!keepText && ctx.filterInput) {
		ctx.filterInput.value = "";
	}
	setCount(ctx, visible);
}

function filterByText(ctx) {
	const query = (ctx.filterInput ? ctx.filterInput.value : "").toLowerCase();
	// A search spans the whole grid, so drop the mine-only view (keep the text).
	if (showingMine(ctx)) {
		setParticipantMode(ctx, false, true);
	}
	let visible = 0;
	for (const card of ctx.cards) {
		const show =
			cardEligible(ctx, card) && (!query || cardMatchesText(card, query));
		card.parentElement.hidden = !show;
		if (show) {
			visible++;
		}
	}
	setCount(ctx, visible);
}

function applyInitialView(ctx) {
	// The URL wins if it names a view, else the button's default.
	const params = new URLSearchParams(window.location.search);
	const shown = params.get("show");
	let wantMine = showingMine(ctx);
	if (shown === "mine") {
		wantMine = true;
	} else if (shown === "all") {
		wantMine = false;
	}
	if (ctx.archivedButton) {
		setRetiredButton(ctx, params.get("retired") === "show");
	}
	setParticipantMode(ctx, wantMine, true);
}

window.initCardGrid = function initCardGrid(config) {
	const ctx = {
		config,
		cards: Array.from(document.querySelectorAll(config.cardSelector)),
		countSpan: document.getElementById(config.countId),
		filterInput: document.getElementById("project-filter"),
		filterButton: document.getElementById("filter-button"),
		participantButton: document.getElementById("filter-participant-button"),
		archivedButton: document.getElementById("filter-archived-button"),
	};

	if (ctx.filterButton) {
		ctx.filterButton.addEventListener("click", () => filterByText(ctx));
	}
	if (ctx.filterInput) {
		ctx.filterInput.addEventListener("keydown", (event) => {
			if (event.key === "Enter") {
				filterByText(ctx);
				event.preventDefault();
			}
		});
	}
	if (ctx.participantButton) {
		ctx.participantButton.addEventListener("click", () => {
			const mineOnly = !showingMine(ctx);
			setParticipantMode(ctx, mineOnly);
			updateUrl(mineOnly);
		});
	}
	if (ctx.archivedButton) {
		ctx.archivedButton.addEventListener("click", () => {
			const showRetired = !showingRetired(ctx);
			setRetiredButton(ctx, showRetired);
			if (ctx.filterInput?.value) {
				filterByText(ctx);
			} else {
				setParticipantMode(ctx, showingMine(ctx), true);
			}
			updateRetiredUrl(showRetired);
		});
	}

	applyInitialView(ctx);
	setupImageFallbackHandlers();
	setupSubcardNavigation();
	setupProjectToggleButtons();
};
