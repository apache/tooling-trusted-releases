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

// Some platforms have no owner or namespace, so hide the field when one of
// those is selected. This is only a convenience - the server validates the
// namespace per platform regardless, so if the script doesn't run the field
// just stays visible.

document.addEventListener("DOMContentLoaded", () => {
	const platforms = document.getElementById("distribution-platforms");
	const select = document.querySelector("select[name='platform']");
	const input = document.getElementById("owner_namespace");
	const row = input?.closest(".row");
	if (!platforms || !select || !input || !row) return;

	const withoutNamespace = JSON.parse(platforms.dataset.withoutNamespace);

	const apply = () => {
		const hidden = withoutNamespace.includes(select.value);
		// Toggle Bootstrap's .d-none rather than the hidden attribute: this is a
		// .row element, and .row { display: flex } would otherwise win over the
		// user-agent [hidden] rule and leave the field showing.
		row.classList.toggle("d-none", hidden);
		// A namespace left over from another platform would be refused
		if (hidden) input.value = "";
	};

	select.addEventListener("change", apply);
	apply();
});
