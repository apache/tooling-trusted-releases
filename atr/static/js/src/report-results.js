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

function toggleAllDetails() {
	const details = document.querySelectorAll("details");
	// Check if any are closed
	const anyClosed = Array.from(details).some((detail) => !detail.open);
	// If any are closed, open all
	// Otherwise, close all
	details.forEach((detail) => {
		detail.open = anyClosed;
	});
}

const statusButtonClasses = {
	note: { filled: "btn-success", outline: "btn-outline-success" },
	suggestion: { filled: "btn-warning", outline: "btn-outline-warning" },
	concern: { filled: "btn-danger", outline: "btn-outline-danger" },
	blocker: { filled: "atr-btn-blocker", outline: "atr-btn-outline-blocker" },
	exception: { filled: "btn-danger", outline: "btn-outline-danger" },
};

function toggleStatusVisibility(type, status) {
	const btn = document.getElementById(`btn-toggle-${type}-${status}`);
	const targets = document.querySelectorAll(
		`.atr-result-${type}.atr-result-status-${status}`,
	);
	if (targets.length === 0) return;
	const elementsCurrentlyHidden = targets[0].classList.contains("atr-hide");
	targets.forEach((el) => {
		if (elementsCurrentlyHidden) {
			el.classList.remove("atr-hide");
		} else {
			el.classList.add("atr-hide");
		}
	});
	const mapping = statusButtonClasses[status];
	if (!mapping) {
		console.error("Unknown status:", status);
		return;
	}
	const filledClass = mapping.filled;
	const outlineClass = mapping.outline;
	const cntMatch = btn.textContent.match(/\((\d+)\)/);
	if (!cntMatch) {
		console.error("Button text regex mismatch for:", btn.textContent);
		return;
	}
	const newButtonAction = elementsCurrentlyHidden ? "Hide" : "Show";
	btn.querySelector("span").textContent = newButtonAction;
	if (newButtonAction === "Hide") {
		btn.classList.remove(outlineClass);
		btn.classList.add(filledClass);
	} else {
		btn.classList.remove(filledClass);
		btn.classList.add(outlineClass);
	}
	if (type === "member") {
		updateMemberStriping();
	} else if (type === "primary") {
		updatePrimaryStriping();
	}
}

function restripeVisibleRows(rowSelector, stripeClass) {
	let visibleIdx = 0;
	document.querySelectorAll(rowSelector).forEach((row) => {
		row.classList.remove(stripeClass);
		const hidden =
			row.classList.contains("atr-hide") ||
			row.classList.contains("page-member-path-hide");
		if (!hidden) {
			if (visibleIdx % 2 === 0) row.classList.add(stripeClass);
			visibleIdx++;
		}
	});
}

function updatePrimaryStriping() {
	restripeVisibleRows(".atr-result-primary", "page-member-visible-odd");
}

function updateMemberStriping() {
	restripeVisibleRows(".atr-result-member", "page-member-visible-odd");
}

// Toggle status visibility buttons
document.querySelectorAll(".page-toggle-status").forEach((btn) => {
	btn.addEventListener("click", function () {
		const type = this.dataset.type;
		const status = this.dataset.status;
		toggleStatusVisibility(type, status);
	});
});

// Toggle all details button
const toggleAllBtn = document.getElementById("btn-toggle-all-details");
if (toggleAllBtn) {
	toggleAllBtn.addEventListener("click", toggleAllDetails);
}

// Member path filter
const mpfInput = document.getElementById("member-path-filter");
if (mpfInput) {
	mpfInput.addEventListener("input", function () {
		const filterText = this.value.toLowerCase();
		document.querySelectorAll(".atr-result-member").forEach((row) => {
			const pathCell = row.cells[0];
			const hide =
				filterText && !pathCell.textContent.toLowerCase().includes(filterText);
			row.classList.toggle("page-member-path-hide", hide);
		});
		updateMemberStriping();
	});
}

updatePrimaryStriping();
updateMemberStriping();
