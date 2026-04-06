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
	function computeCumulative(files) {
		const cumulative = [];
		let sum = 0;
		for (const file of files) {
			sum += file.size;
			cumulative.push(sum);
		}
		return { cumulative, totalFileBytes: sum };
	}

	function handleLoad(xhr, retry) {
		let data;
		try {
			data = JSON.parse(xhr.responseText);
		} catch {
			UploadUI.showError(`Unexpected server response (${xhr.status})`, retry);
			return;
		}

		if (data.ok) {
			UploadUI.showSuccess(data.message || "Upload complete");
			if (data.next_url) {
				window.location.assign(data.next_url);
			}
		} else {
			UploadUI.showError(data.message || "Upload failed", retry);
		}
	}

	function startUpload(form, container, files, formData) {
		const { cumulative, totalFileBytes } = computeCumulative(files);
		let reachedFull = false;
		const xhr = new XMLHttpRequest();

		function retry() {
			form.classList.remove("d-none");
			container.classList.add("d-none");
		}

		UploadUI.buildBatchUI(container, files, () => xhr.abort());
		UploadUI.showUploading(files.length);

		xhr.upload.addEventListener("progress", (ev) => {
			if (ev.lengthComputable) {
				if (ev.loaded >= ev.total) {
					reachedFull = true;
					UploadUI.showProcessing();
				}
				UploadUI.updateProgress(
					ev.loaded,
					ev.total,
					cumulative,
					totalFileBytes,
				);
			}
		});

		xhr.addEventListener("load", () => handleLoad(xhr, retry));

		xhr.addEventListener("error", () => {
			if (reachedFull) {
				UploadUI.showAmbiguousError();
			} else {
				UploadUI.showError("A network error occurred.", retry);
			}
		});

		xhr.addEventListener("abort", () => {
			UploadUI.showCancelled();
			form.classList.remove("d-none");
		});

		xhr.open("POST", form.action, true);
		xhr.setRequestHeader("Accept", "application/json");
		xhr.send(formData);
	}

	const form = document
		.querySelector('input[name="variant"][value="add_files"]')
		?.closest("form");
	if (!form) return;

	const fileInput = form.querySelector('input[name="file_data"]');
	const container = document.getElementById("upload-progress-container");
	if (!fileInput || !container) return;

	form.addEventListener("submit", (e) => {
		e.preventDefault();
		const files = Array.from(fileInput.files || []);
		if (files.length === 0) {
			alert("Please select files to upload.");
			return;
		}
		const formData = new FormData(form);
		form.classList.add("d-none");
		startUpload(form, container, files, formData);
	});
})();
