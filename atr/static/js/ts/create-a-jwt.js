"use strict";
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
    const form = document.getElementById("issue-jwt-form");
    const outputContainer = document.getElementById("jwt-container");
    const output = document.getElementById("jwt-output");
    const timeField = document.getElementById("time-remaining");
    let timeoutObj = null;
    let intervalObj = null;
    if (!form || !output || !outputContainer || !timeField) {
        return;
    }
    const jwtOutput = output;
    const jwtOutputContainer = outputContainer;
    const jwtTimeField = timeField;
    function clearJwtDisplay() {
        if (timeoutObj !== null) {
            clearTimeout(timeoutObj);
            timeoutObj = null;
        }
        if (intervalObj !== null) {
            clearInterval(intervalObj);
            intervalObj = null;
        }
        jwtOutput.textContent = "";
        jwtTimeField.textContent = "";
        jwtOutputContainer.classList.add("d-none");
    }
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") {
            clearJwtDisplay();
        }
    });
    window.addEventListener("pagehide", () => {
        clearJwtDisplay();
    });
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) {
            clearJwtDisplay();
        }
    });
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const resp = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
        });
        if (resp.ok) {
            const token = await resp.text();
            let time = 60;
            clearJwtDisplay();
            jwtOutputContainer.classList.remove("d-none");
            jwtOutput.textContent = token;
            jwtTimeField.textContent = time + "s";
            timeoutObj = setTimeout(() => {
                clearJwtDisplay();
            }, 60000);
            intervalObj = setInterval(() => {
                time = time - 1;
                jwtTimeField.textContent = time + "s";
            }, 1000);
        }
        else {
            alert("Failed to fetch JWT");
        }
    });
});
//# sourceMappingURL=create-a-jwt.js.map