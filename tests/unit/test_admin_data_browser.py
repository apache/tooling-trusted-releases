# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import atr.admin as admin


def test_page_nav_beyond_end() -> None:
    page = admin._page_nav(500, 50, 123, 0)
    assert page == admin.PageNav(start=0, end=0, previous_offset=100, next_offset=None)


def test_page_nav_empty_table() -> None:
    page = admin._page_nav(0, 50, 0, 0)
    assert page == admin.PageNav(start=0, end=0, previous_offset=None, next_offset=None)


def test_page_nav_first_full_page() -> None:
    page = admin._page_nav(0, 50, 200, 50)
    assert page == admin.PageNav(start=1, end=50, previous_offset=None, next_offset=50)


def test_page_nav_partial_final_page() -> None:
    page = admin._page_nav(150, 50, 180, 30)
    assert page == admin.PageNav(start=151, end=180, previous_offset=100, next_offset=None)
