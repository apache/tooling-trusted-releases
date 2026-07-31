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

import resource
import subprocess
import sys
import time

import psutil

import atr.util as util


def test_cpu_limit_arm_rearms_soft_limit() -> None:
    before = resource.getrlimit(resource.RLIMIT_CPU)
    try:
        util.cpu_limit_arm(300)
        soft_one, hard_one = resource.getrlimit(resource.RLIMIT_CPU)
        util.cpu_limit_arm(600)
        soft_two, hard_two = resource.getrlimit(resource.RLIMIT_CPU)
    finally:
        resource.setrlimit(resource.RLIMIT_CPU, before)
    assert hard_one == before[1]
    assert hard_two == before[1]
    assert soft_one >= 301
    assert soft_two > soft_one


def test_process_tree_rss_counts_children() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.5)
        own = psutil.Process().memory_info().rss
        total = util.process_tree_rss(psutil.Process())
        assert total > (own + (1024 * 1024))
    finally:
        child.kill()
        child.wait()
