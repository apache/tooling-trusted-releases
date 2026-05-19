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

document.addEventListener("DOMContentLoaded", () => {
	const container = document.getElementById("atr-notifications");
	if (!container) {
		return;
	}
	const dismissUrl = container.dataset.dismissUrl;
	if (!dismissUrl) {
		return;
	}
	for (const button of container.querySelectorAll(
		"button.atr-notification-dismiss",
	)) {
		button.addEventListener("click", (event) => {
			dismissNotification(event, dismissUrl);
		});
	}
});

async function dismissNotification(event, dismissUrl) {
	const button = event.currentTarget;
	const alert = button.closest(".atr-notification");
	if (!alert) {
		return;
	}
	const notificationId = alert.dataset.notificationId;
	if (!notificationId) {
		return;
	}
	const csrfToken = document.querySelector(
		'#atr-notifications input[name="csrf_token"]',
	)?.value;
	if (!csrfToken) {
		return;
	}
	button.disabled = true;
	try {
		const formData = new FormData();
		formData.append("csrf_token", csrfToken);
		formData.append("notification_id", notificationId);
		const response = await fetch(dismissUrl, {
			method: "POST",
			body: formData,
			credentials: "same-origin",
		});
		if (response.ok) {
			alert.remove();
		}
	} catch {
	} finally {
		if (alert.isConnected) {
			button.disabled = false;
		}
	}
}
