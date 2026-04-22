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

function initAnnounceConfirm() {
	const confirmInput = document.getElementById("confirm_announce");
	const announceForm = document.querySelector("form.atr-canary");

	if (!confirmInput || !announceForm) {
		return;
	}

	const submitButton = announceForm.querySelector('button[type="submit"]');
	if (!submitButton) {
		return;
	}

	const updateButtonState = () => {
		const isConfirmed = confirmInput.value === "CONFIRM";
		submitButton.disabled = !isConfirmed;
	};

	confirmInput.addEventListener("input", updateButtonState);

	updateButtonState();
}

function createBodyWarningDiv() {
	const warningDiv = document.createElement("div");
	warningDiv.className = "alert alert-warning mt-2 d-none";
	warningDiv.innerHTML =
		"<strong>Note:</strong> The message body has been customised. The download link in this message will no longer update automatically. " +
		'<br><button type="button" class="btn btn-sm btn-outline-secondary mt-2" id="discard-announce-body-changes">Discard changes</button>';
	return warningDiv;
}

function createModifiedStateUpdater(bodyTextarea, warningDiv, state) {
	return function updateModifiedState() {
		const currentlyModified = bodyTextarea.value !== state.pristineBody;

		if (currentlyModified !== state.isModified) {
			state.isModified = currentlyModified;

			if (state.isModified) {
				warningDiv.classList.remove("d-none");
			} else {
				warningDiv.classList.add("d-none");
			}
		}
	};
}

function normaliseDownloadPathSuffix(rawSuffix) {
	let suffix = rawSuffix;

	if (suffix.includes("..") || suffix.includes("//")) {
		return {
			error: "Download path suffix must not contain .. or //",
			suffix: null,
		};
	}
	if (suffix.startsWith("./")) {
		suffix = suffix.slice(1);
	} else if (suffix === ".") {
		suffix = "/";
	}
	if (!suffix.startsWith("/")) {
		suffix = `/${suffix}`;
	}
	if (!suffix.endsWith("/")) {
		suffix = `${suffix}/`;
	}
	if (suffix.includes("/.")) {
		return {
			error: "Download path suffix must not contain /.",
			suffix: null,
		};
	}

	return { error: null, suffix };
}

function normaliseTextareaValue(value) {
	const textarea = document.createElement("textarea");
	textarea.value = value;
	return textarea.value;
}

function createBodyFetcher(
	previewUrl,
	csrfToken,
	bodyTextarea,
	state,
	updateModifiedState,
) {
	return async function fetchNewBody(pathSuffix) {
		const requestId = state.requestId + 1;
		state.requestId = requestId;

		try {
			const formData = new FormData();
			formData.append("download_path_suffix", pathSuffix);
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

			const newBody = normaliseTextareaValue(await response.text());
			if (requestId !== state.requestId) {
				return;
			}

			state.pristineBody = newBody;
			if (!state.isModified) {
				bodyTextarea.value = newBody;
			}
			updateModifiedState();
		} catch (error) {
			console.error("Error fetching new body:", error);
		}
	};
}

function getAnnounceBodySyncElements() {
	const config = document.getElementById("announce-body-config");
	const bodyTextarea = document.getElementById("body");
	const pathInput = document.getElementById("download_path_suffix");
	const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;

	if (!config || !bodyTextarea || !pathInput) {
		return null;
	}

	const previewUrl = config.dataset.previewUrl;
	if (!previewUrl) {
		return null;
	}

	return {
		bodyTextarea,
		pathInput,
		csrfToken,
		previewUrl,
	};
}

function createAnnounceBodyState(bodyTextarea) {
	return {
		pristineBody: bodyTextarea.value,
		isModified: false,
		requestId: 0,
	};
}

function createPathSyncHandler(pathInput, fetchNewBody) {
	return function syncBodyWithPath() {
		const normalised = normaliseDownloadPathSuffix(pathInput.value);
		if (normalised.error || !normalised.suffix) {
			return;
		}
		fetchNewBody(normalised.suffix);
	};
}

function attachAnnounceBodySyncListeners(
	bodyTextarea,
	pathInput,
	discardButton,
	state,
	updateModifiedState,
	fetchNewBody,
) {
	const syncBodyWithPath = createPathSyncHandler(pathInput, fetchNewBody);
	let bodyDebounce;

	bodyTextarea.addEventListener("input", updateModifiedState);
	pathInput.addEventListener("input", () => {
		clearTimeout(bodyDebounce);
		bodyDebounce = setTimeout(syncBodyWithPath, 10);
	});

	discardButton.addEventListener("click", () => {
		bodyTextarea.value = state.pristineBody;
		updateModifiedState();
	});
}

function initAnnounceBodySync() {
	const elements = getAnnounceBodySyncElements();
	if (!elements) {
		return;
	}

	const { bodyTextarea, pathInput, csrfToken, previewUrl } = elements;
	const state = createAnnounceBodyState(bodyTextarea);

	const warningDiv = createBodyWarningDiv();
	bodyTextarea.parentNode.append(warningDiv);

	const discardButton = document.getElementById(
		"discard-announce-body-changes",
	);
	const updateModifiedState = createModifiedStateUpdater(
		bodyTextarea,
		warningDiv,
		state,
	);
	const fetchNewBody = createBodyFetcher(
		previewUrl,
		csrfToken,
		bodyTextarea,
		state,
		updateModifiedState,
	);
	attachAnnounceBodySyncListeners(
		bodyTextarea,
		pathInput,
		discardButton,
		state,
		updateModifiedState,
		fetchNewBody,
	);
}

function initDownloadPathValidation() {
	const pathInput = document.getElementById("download_path_suffix");
	const pathHelpText = pathInput
		? pathInput.parentElement.querySelector(".form-text")
		: null;

	if (!pathInput || !pathHelpText) {
		return;
	}

	const baseText = pathHelpText.dataset.baseText || "";
	let pathDebounce;

	const updatePathHelpText = () => {
		const normalised = normaliseDownloadPathSuffix(pathInput.value);
		if (normalised.error || !normalised.suffix) {
			pathHelpText.textContent = normalised.error;
			return;
		}
		pathHelpText.textContent = baseText + normalised.suffix;
	};

	pathInput.addEventListener("input", () => {
		clearTimeout(pathDebounce);
		pathDebounce = setTimeout(updatePathHelpText, 10);
	});
	updatePathHelpText();
}

document.addEventListener("DOMContentLoaded", () => {
	initAnnounceConfirm();
	initAnnounceBodySync();
	initDownloadPathValidation();
});
