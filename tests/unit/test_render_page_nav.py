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

import atr.htm as htm
import atr.render as render


def test_html_page_nav_links_or_disables_each_side() -> None:
    block = htm.Block()
    render.html_page_nav(block, aria_label="Pages", previous_url=None, next_url="/checks/a/1?limit=250&offset=250")

    html = str(block.collect())

    assert '<nav aria-label="Pages">' in html
    assert '<li class="page-item disabled"><span class="page-link">Previous</span></li>' in html
    assert '<a class="page-link" href="/checks/a/1?limit=250&amp;offset=250">Next</a>' in html
