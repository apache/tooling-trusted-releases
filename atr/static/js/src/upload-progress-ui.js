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

window.UploadUI = {
	buildBatchUI(container, files, onCancel) {
		container.innerHTML = "";
		container.classList.remove("d-none");

		container.append(this.buildHeader(onCancel));
		container.append(this.buildProgressBar());

		const list = document.createElement("div");
		list.id = "upload-file-list";
		files.forEach((file, i) => {
			const row = document.createElement("div");
			row.className =
				"d-flex justify-content-between align-items-center py-1 border-bottom";
			row.dataset.fileIndex = String(i);
			const nameSpan = document.createElement("span");
			nameSpan.textContent = `${file.name} (${this.formatBytes(file.size)})`;
			const statusSpan = document.createElement("small");
			statusSpan.className = "upload-file-status text-muted";
			statusSpan.textContent = "Pending";
			row.append(nameSpan, statusSpan);
			list.append(row);
		});
		container.append(list);

		const msgArea = document.createElement("div");
		msgArea.id = "upload-message-area";
		container.append(msgArea);
	},

	buildHeader(onCancel) {
		const header = document.createElement("div");
		header.className = "d-flex justify-content-between align-items-center mb-3";
		const statusEl = document.createElement("strong");
		statusEl.id = "upload-batch-status";
		statusEl.textContent = "Preparing upload";
		const cancelBtn = document.createElement("button");
		cancelBtn.type = "button";
		cancelBtn.id = "upload-cancel-btn";
		cancelBtn.className = "btn btn-sm btn-outline-secondary";
		cancelBtn.textContent = "Cancel upload";
		cancelBtn.addEventListener("click", onCancel);
		header.append(statusEl, cancelBtn);
		return header;
	},

	buildProgressBar() {
		const wrap = document.createElement("div");
		wrap.className = "mb-3";
		const progress = document.createElement("progress");
		progress.id = "upload-overall-progress";
		progress.className = "w-100 mb-1";
		progress.value = 0;
		progress.max = 100;
		const info = document.createElement("div");
		info.className = "d-flex justify-content-between";
		const bytes = document.createElement("small");
		bytes.id = "upload-progress-bytes";
		bytes.className = "text-muted";
		const percent = document.createElement("small");
		percent.id = "upload-progress-percent";
		percent.textContent = "0%";
		info.append(bytes, percent);
		wrap.append(progress, info);
		return wrap;
	},

	formatBytes(b) {
		if (b < 1024) return `${b} B`;
		if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
		if (b < 1073741824) return `${(b / 1048576).toFixed(1)} MB`;
		return `${(b / 1073741824).toFixed(2)} GB`;
	},

	showAmbiguousError() {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) statusEl.textContent = "Upload failed";
		const cancelBtn = document.getElementById("upload-cancel-btn");
		if (cancelBtn) cancelBtn.classList.add("d-none");
		const msgArea = document.getElementById("upload-message-area");
		if (msgArea) {
			msgArea.innerHTML = "";
			const alert = document.createElement("div");
			alert.className = "alert alert-warning mt-3";
			alert.textContent =
				"Your files may already have been uploaded. Check compose before retrying.";
			msgArea.append(alert);
		}
	},

	showCancelled() {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) statusEl.textContent = "Upload cancelled";
		const cancelBtn = document.getElementById("upload-cancel-btn");
		if (cancelBtn) cancelBtn.classList.add("d-none");
	},

	showError(message, onRetry) {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) statusEl.textContent = "Upload failed";
		const cancelBtn = document.getElementById("upload-cancel-btn");
		if (cancelBtn) cancelBtn.classList.add("d-none");
		const msgArea = document.getElementById("upload-message-area");
		if (msgArea) {
			msgArea.innerHTML = "";
			const alert = document.createElement("div");
			alert.className = "alert alert-danger mt-3";
			const msgEl = document.createElement("span");
			msgEl.textContent = message;
			alert.append(msgEl);
			if (onRetry) {
				const retryBtn = document.createElement("button");
				retryBtn.type = "button";
				retryBtn.className = "btn btn-outline-primary ms-3";
				retryBtn.textContent = "Return to upload form";
				retryBtn.addEventListener("click", onRetry);
				alert.append(retryBtn);
			}
			msgArea.append(alert);
		}
	},

	showProcessing() {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) statusEl.textContent = "Processing revision";
		const cancelBtn = document.getElementById("upload-cancel-btn");
		if (cancelBtn) cancelBtn.classList.add("d-none");
		document
			.querySelectorAll("#upload-file-list .upload-file-status")
			.forEach((el) => {
				el.textContent = "Done";
				el.className = "upload-file-status text-success";
			});
	},

	showSuccess(message) {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) statusEl.textContent = "Upload complete";
		const cancelBtn = document.getElementById("upload-cancel-btn");
		if (cancelBtn) cancelBtn.classList.add("d-none");
		const progressBar = document.getElementById("upload-overall-progress");
		if (progressBar) progressBar.value = 100;
		const percentEl = document.getElementById("upload-progress-percent");
		if (percentEl) percentEl.textContent = "100%";
		const msgArea = document.getElementById("upload-message-area");
		if (msgArea) {
			const alert = document.createElement("div");
			alert.className = "alert alert-success mt-3";
			alert.textContent = message;
			msgArea.append(alert);
		}
	},

	showUploading(fileCount) {
		const statusEl = document.getElementById("upload-batch-status");
		if (statusEl) {
			const word = fileCount === 1 ? "file" : "files";
			statusEl.textContent = `Uploading ${fileCount} ${word}`;
		}
	},

	updateProgress(loaded, total, cumulative, totalFileBytes) {
		let pct = 0;
		if (total > 0) {
			pct = loaded >= total ? 100 : Math.floor((loaded / total) * 100);
		}
		const progressBar = document.getElementById("upload-overall-progress");
		const percentEl = document.getElementById("upload-progress-percent");
		const bytesEl = document.getElementById("upload-progress-bytes");

		if (progressBar) progressBar.value = pct;
		if (percentEl) percentEl.textContent = `${pct}%`;
		if (bytesEl) {
			bytesEl.textContent = `${this.formatBytes(loaded)} / ${this.formatBytes(total)}`;
		}

		if (totalFileBytes > 0) {
			const scaled = loaded * (totalFileBytes / total);
			document
				.querySelectorAll("#upload-file-list [data-file-index]")
				.forEach((row, i) => {
					const start = i > 0 ? cumulative[i - 1] : 0;
					const end = cumulative[i];
					const statusSpan = row.querySelector(".upload-file-status");
					if (!statusSpan) return;
					if (scaled >= end) {
						statusSpan.textContent = "Done";
						statusSpan.className = "upload-file-status text-success";
					} else if (scaled > start) {
						statusSpan.textContent = "Sending";
						statusSpan.className = "upload-file-status text-primary";
					} else {
						statusSpan.textContent = "Pending";
						statusSpan.className = "upload-file-status text-muted";
					}
				});
		}
	},
};
