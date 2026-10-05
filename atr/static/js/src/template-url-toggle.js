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

// While a template URL is set, ATR fetches the template from there, so the
// inline template beside it has no effect. Make that textarea read only
// rather than disabled, so its stored text is still submitted and kept in case
// the URL is cleared later. The server ignores the inline text either way.

document.addEventListener("DOMContentLoaded", () => {
	document
		.querySelectorAll("input[id$='_template_url']")
		.forEach((urlInput) => {
			const template = document.getElementById(
				urlInput.id.slice(0, -"_url".length),
			);
			if (!template) return;

			const apply = () => {
				const inUse = urlInput.value.trim() !== "";
				template.readOnly = inUse;
				template.classList.toggle("bg-light", inUse);
				template.title = inUse
					? "Not used while a template URL is set. Clear the URL to edit this."
					: "";
			};

			urlInput.addEventListener("input", apply);
			apply();
		});
});
