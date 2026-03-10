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

document.addEventListener("DOMContentLoaded", (): void => {
  const form = document.getElementById("issue-jwt-form") as HTMLFormElement | null;
  const outputContainer = document.getElementById("jwt-container")
  const output = document.getElementById("jwt-output");
  const timeField = document.getElementById("time-remaining");
  let timeoutObj: number, intervalObj: number;

  if (!form || !output || !outputContainer || !timeField) {
    return;
  }

  form.addEventListener("submit", async (e: Event): Promise<void> => {
    e.preventDefault();

    const resp = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
    });

    if (resp.ok) {
      const token = await resp.text();
      let time = 60
      clearTimeout(timeoutObj)
      clearInterval(intervalObj)
      timeField.textContent = time + "s"
      outputContainer.classList.remove("d-none");
      output.textContent = token;
      timeoutObj = setTimeout(() => {
          output.textContent = ""
          outputContainer.classList.add("d-none");
          clearInterval(intervalObj)
      }, 60000)
      intervalObj = setInterval(() => {
          time = time - 1
          timeField.textContent = time + "s"
      }, 1000)
    } else {
      alert("Failed to fetch JWT");
    }
  });
});
