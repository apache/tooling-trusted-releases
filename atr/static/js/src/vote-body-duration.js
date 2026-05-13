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

function createWarningDiv() {
	const warningDiv = document.createElement("div");
	warningDiv.className = "alert alert-warning mt-2 d-none";
	warningDiv.innerHTML =
		"<strong>Note:</strong> The vote duration cannot be changed because the message body has been customised. " +
		'<br><button type="button" class="btn btn-sm btn-outline-secondary mt-2" id="discard-body-changes">Discard changes</button>';
	return warningDiv;
}

function createModifiedStateUpdater(
	bodyTextarea,
	durationInput,
	warningDiv,
	state,
) {
	return function updateModifiedState() {
		const currentlyModified = bodyTextarea.value !== state.pristineBody;

		if (currentlyModified !== state.isModified) {
			state.isModified = currentlyModified;

			if (state.isModified) {
				durationInput.readOnly = true;
				durationInput.classList.add("bg-light");
				warningDiv.classList.remove("d-none");
			} else {
				durationInput.readOnly = false;
				durationInput.classList.remove("bg-light");
				warningDiv.classList.add("d-none");
			}
		}
	};
}

function createBodyFetcher(previewUrl, csrfToken, bodyTextarea, state) {
	return async function fetchNewBody(duration) {
		try {
			const formData = new FormData();
			formData.append("vote_duration", duration);
			const voteModeInput = document.querySelector('input[name="vote_mode"]');
			if (voteModeInput) {
				formData.append("vote_mode", voteModeInput.value);
			}
			const renderedRevisionInput = document.querySelector(
				'input[name="rendered_revision"]',
			);
			if (renderedRevisionInput) {
				formData.append("rendered_revision", renderedRevisionInput.value);
			}
			if (csrfToken) {
				formData.append("csrf_token", csrfToken);
			}

			const response = await fetch(previewUrl, {
				method: "POST",
				body: formData,
			});

			if (!response.ok) {
				console.error("Failed to fetch new body:", response.statusText);
				return;
			}

			const newBody = await response.text();
			if (state.isModified) {
				return;
			}
			bodyTextarea.value = newBody;
			state.pristineBody = bodyTextarea.value;
		} catch (error) {
			console.error("Error fetching new body:", error);
		}
	};
}

function attachEventListeners(
	bodyTextarea,
	durationInput,
	discardButton,
	state,
	updateModifiedState,
	fetchNewBody,
) {
	bodyTextarea.addEventListener("input", updateModifiedState);

	durationInput.addEventListener("change", () => {
		if (!state.isModified) {
			fetchNewBody(durationInput.value);
		}
	});

	discardButton.addEventListener("click", () => {
		bodyTextarea.value = state.pristineBody;
		updateModifiedState();
	});
}

function initVoteBodyDuration() {
	const config = document.getElementById("vote-body-config");
	if (!config) {
		return;
	}

	const previewUrl = config.dataset.previewUrl;
	const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
	const bodyTextarea = document.getElementById("body");
	const durationInput = document.getElementById("vote_duration");

	if (!bodyTextarea || !durationInput || !previewUrl) {
		return;
	}

	const state = { pristineBody: bodyTextarea.value, isModified: false };

	const warningDiv = createWarningDiv();
	bodyTextarea.parentNode.append(warningDiv);

	const discardButton = document.getElementById("discard-body-changes");
	const updateModifiedState = createModifiedStateUpdater(
		bodyTextarea,
		durationInput,
		warningDiv,
		state,
	);
	const fetchNewBody = createBodyFetcher(
		previewUrl,
		csrfToken,
		bodyTextarea,
		state,
	);

	attachEventListeners(
		bodyTextarea,
		durationInput,
		discardButton,
		state,
		updateModifiedState,
		fetchNewBody,
	);
}

document.addEventListener("DOMContentLoaded", initVoteBodyDuration);
