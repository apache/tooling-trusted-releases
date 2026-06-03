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
	const chips = document.querySelectorAll("[data-status-filter]");
	const versions = document.querySelectorAll(".atr-catalog-version");
	if (chips.length === 0) {
		return;
	}
	chips.forEach((chip) => {
		chip.addEventListener("click", () => {
			const status = chip.dataset.statusFilter;
			chips.forEach((other) => {
				const active = other === chip;
				other.classList.toggle("btn-primary", active);
				other.classList.toggle("active", active);
				other.classList.toggle("btn-outline-secondary", !active);
			});
			versions.forEach((card) => {
				const show = status === "all" || card.dataset.status === status;
				card.classList.toggle("d-none", !show);
			});
		});
	});
});
