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

function applyPublicationStatus(data, button, message, whenReady) {
	const publishing = document.getElementById("finish-svn-publishing");
	if (publishing && data.svn_html) {
		document.getElementById("finish-svn-publish").innerHTML = data.svn_html;
	}
	message.textContent = data.message;
	message.hidden = !data.message;
	// A button offering to queue the announcement stays usable while waiting
	const disabled = !data.ready && !whenReady;
	button.classList.toggle("disabled", disabled);
	button.setAttribute("aria-disabled", String(disabled));
	// The download page can be checked once the published files are known
	const check = document.getElementById("finish-download-page");
	if (check && data.download_page_ready) {
		check.disabled = false;
		check.title = check.dataset.readyTitle;
	}
	if (!data.ready) return;
	button.href = button.dataset.announceUrl;
	button.removeAttribute("tabindex");
	// Nothing is left to wait for, so it becomes the ordinary button
	button.classList.replace("btn-outline-success", "btn-success");
	const label = document.getElementById("finish-announce-label");
	if (label) label.textContent = " Announce";
}

function initFinishDownloads() {
	const status = document.getElementById("finish-publication-status");
	const button = document.getElementById("finish-announce");
	// The button either waits disabled, or offers to queue the announcement
	const whenReady = button?.dataset.whenReady === "true";
	if (!status || !button) return;
	if (!button.classList.contains("disabled") && !whenReady) return;
	const message = document.getElementById("finish-publication-message");
	const refresh = document.getElementById("finish-publication-refresh");

	async function poll() {
		let retry = true;
		let failed = false;
		try {
			const response = await fetch(status.dataset.statusUrl, {
				redirect: "manual",
			});
			if (response.ok) {
				const data = await response.json();
				applyPublicationStatus(data, button, message, whenReady);
				retry = !data.ready;
			} else {
				failed = true;
				retry = response.status === 429 || response.status >= 500;
			}
		} catch {
			failed = true;
		}
		refresh.hidden = !retry && !failed;
		refresh.textContent = failed
			? `Could not refresh publication status. ${retry ? "Refreshing every 30s." : "Reload the page."}`
			: "Refreshing every 30s.";
		if (retry) setTimeout(poll, 30000);
	}

	refresh.hidden = false;
	setTimeout(poll, 30000);
}

document.addEventListener("DOMContentLoaded", initFinishDownloads);
