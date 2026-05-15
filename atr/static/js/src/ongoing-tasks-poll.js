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

(() => {
	function handleCollapseToggle() {
		this.textContent = this.textContent.trim() === "More" ? "Less" : "More";
	}

	// Handle More and Less toggle buttons for collapse sections
	document.querySelectorAll(".page-collapse-toggle").forEach((button) => {
		button.addEventListener("click", handleCollapseToggle);
	});

	const banner = document.getElementById("ongoing-tasks-banner");
	if (!banner) return;

	const statusUrl = banner.dataset.statusUrl;
	const legacyApiUrl = banner.dataset.apiUrl;
	const pollUrl = statusUrl || legacyApiUrl;
	if (!pollUrl) return;
	const isComposeStatus = Boolean(statusUrl);

	const textSpan = document.getElementById("ongoing-tasks-text");
	const countSpan = document.getElementById("ongoing-tasks-count");
	const progress = document.getElementById("poll-progress");
	const quarantineContainer = document.getElementById(
		"quarantine-status-container",
	);
	const checksSummaryContainer = document.getElementById(
		"checks-summary-container",
	);
	const filesTableContainer = document.getElementById("files-table-container");
	const releaseInfoContainer = document.getElementById(
		"release-info-container",
	);
	const filesCardHeaderText = document.getElementById("files-card-header-text");
	const pollInterval = 3000;

	const initialOngoing = parseInt(banner.dataset.ongoingCount || "0", 10) || 0;
	const initialQuarantinePending =
		parseInt(banner.dataset.quarantinePendingCount || "0", 10) || 0;
	const initialPollingActive = banner.dataset.pollingActive === "true";

	const shouldStart =
		initialPollingActive || initialOngoing > 0 || initialQuarantinePending > 0;
	if (!shouldStart) return;

	function restartProgress() {
		if (!progress) return;
		progress.style.animation = "none";
		// Force a reflow to reset the animation
		void progress.offsetHeight;
		progress.style.animation = `poll-grow ${pollInterval}ms linear forwards`;
	}

	function setProgressPolling() {
		if (!progress) return;
		progress.style.animation = "none";
		progress.style.width = "100%";
		progress.classList.remove("bg-warning");
		progress.classList.add(
			"bg-info",
			"progress-bar-striped",
			"progress-bar-animated",
		);
	}

	function setProgressIdle() {
		if (!progress) return;
		progress.classList.remove(
			"bg-info",
			"progress-bar-striped",
			"progress-bar-animated",
		);
		progress.classList.add("bg-warning");
	}

	function updateBannerHtml(html) {
		if (!textSpan) return;
		if (typeof html !== "string") return;
		textSpan.innerHTML = html;
	}

	function updateBannerCount(count) {
		if (!textSpan) return;
		const taskWord = count === 1 ? "task" : "tasks";
		const isAre = count === 1 ? "is" : "are";
		const strong = document.createElement("strong");
		strong.id = "ongoing-tasks-count";
		strong.textContent = String(count);
		textSpan.textContent = "";
		textSpan.append(
			`There ${isAre} currently `,
			strong,
			` background verification ${taskWord} running for the latest revision. Results shown below may be incomplete or outdated until the tasks finish.`,
		);
	}

	function hideBanner() {
		banner.classList.add("d-none");
	}

	function swapHtml(element, html) {
		if (!element || typeof html !== "string") return;
		element.innerHTML = html;
	}

	function updatePageContent(data) {
		swapHtml(releaseInfoContainer, data.release_info_html);
		swapHtml(quarantineContainer, data.quarantine_html);
		swapHtml(checksSummaryContainer, data.checks_summary_html);
		swapHtml(filesCardHeaderText, data.files_card_header_html);
		if (filesTableContainer && typeof data.files_table_html === "string") {
			filesTableContainer.innerHTML = data.files_table_html;
			reattachCollapseToggleListeners();
		}
	}

	function reattachCollapseToggleListeners() {
		document.querySelectorAll(".page-collapse-toggle").forEach((button) => {
			button.removeEventListener("click", handleCollapseToggle);
			button.addEventListener("click", handleCollapseToggle);
		});
	}

	function scheduleNext(delay) {
		restartProgress();
		setTimeout(pollOngoingTasks, delay);
	}

	function shouldRetryStatus(status) {
		if (status >= 500) return true;
		return status === 408 || status === 429;
	}

	function isPollingActive(data) {
		if (typeof data.polling_active === "boolean") return data.polling_active;
		return (data.ongoing || 0) > 0;
	}

	function handleSuccess(data) {
		if (isComposeStatus) {
			updateBannerHtml(data.banner_html);
			updatePageContent(data);
		} else if (countSpan) {
			updateBannerCount(data.ongoing || 0);
			if (
				checksSummaryContainer &&
				typeof data.checks_summary_html === "string"
			) {
				checksSummaryContainer.innerHTML = data.checks_summary_html;
			}
			if (filesTableContainer && typeof data.files_table_html === "string") {
				filesTableContainer.innerHTML = data.files_table_html;
				reattachCollapseToggleListeners();
			}
		}
		if (isPollingActive(data)) {
			scheduleNext(pollInterval);
			return;
		}
		hideBanner();
	}

	function handleResponse(ok, status, data) {
		setProgressIdle();
		if (!data) {
			console.error("Polling response was not JSON:", status);
			scheduleNext(pollInterval * 2);
			return;
		}
		if (typeof data.redirect_url === "string" && data.redirect_url) {
			window.location.assign(data.redirect_url);
			return;
		}
		if (!ok) {
			console.error("Polling status:", status, data.error);
			if (shouldRetryStatus(status)) {
				scheduleNext(pollInterval * 2);
			} else {
				hideBanner();
			}
			return;
		}
		handleSuccess(data);
	}

	function pollOngoingTasks() {
		setProgressPolling();
		fetch(pollUrl)
			.then(async (response) => {
				let data = null;
				try {
					data = await response.json();
				} catch {
					data = null;
				}
				return { ok: response.ok, status: response.status, data };
			})
			.then(({ ok, status, data }) => {
				handleResponse(ok, status, data);
			})
			.catch((error) => {
				console.error("Error polling ongoing tasks:", error);
				setProgressIdle();
				scheduleNext(pollInterval * 2);
			});
	}

	restartProgress();
	setTimeout(pollOngoingTasks, pollInterval);
})();
