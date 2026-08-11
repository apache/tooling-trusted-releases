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

import errno
import functools
import os
import shutil
import subprocess
import sys
from typing import Final

RO_ACCESSES: Final = "read-file,read-dir"
RW_ACCESSES: Final = (
    "read-file,read-dir,write-file,truncate,refer,"
    "make-reg,make-dir,make-sym,make-fifo,make-sock,make-char,make-block,"
    "remove-file,remove-dir"
)
SYSTEM_ACCESSES: Final = "execute,read-file,read-dir"
SYSTEM_PATHS: Final = ("/bin", "/etc", "/lib", "/lib64", "/sbin", "/usr")


def command(argv: list[str], *, ro_paths: list[str] | None = None, rw_paths: list[str] | None = None) -> list[str]:
    setpriv = landlock_setpriv()
    if setpriv is None:
        return argv
    wrapped = [setpriv, "--landlock-access", "fs"]
    for path in SYSTEM_PATHS:
        if os.path.exists(path):
            wrapped += ["--landlock-rule", f"path-beneath:{SYSTEM_ACCESSES}:{path}"]
    for path in ro_paths or []:
        wrapped += ["--landlock-rule", f"path-beneath:{RO_ACCESSES}:{path}"]
    for path in rw_paths or []:
        wrapped += ["--landlock-rule", f"path-beneath:{RW_ACCESSES}:{path}"]
    wrapped.append("--")
    return wrapped + argv


@functools.cache
def landlock_setpriv() -> str | None:
    if sys.platform != "linux":
        return None
    setpriv = shutil.which("setpriv")
    if setpriv is None:
        return None
    try:
        result = subprocess.run([setpriv, "--help"], capture_output=True, check=False, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    except OSError as error:
        if error.errno in {errno.EACCES, errno.ENOENT, errno.ENOEXEC}:
            return None
        raise
    if b"--landlock-access" in (result.stdout + result.stderr):
        return setpriv
    return None
