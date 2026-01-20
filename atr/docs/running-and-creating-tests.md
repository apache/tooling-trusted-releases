# 3.8. Running and creating tests

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.7.` [Build processes](build-processes)

**Next**: `3.9.` [Code conventions](code-conventions)

**Sections**:

* [Running Playwright tests](#running-playwright-tests)
* [Creating Playwright tests](#creating-playwright-tests)
* [Running end-to-end tests](#running-end-to-end-tests)

## Running Playwright tests

We currently only have end-to-end browser tests, but we plan to expand these as part of [Issue #209](https://github.com/apache/tooling-trusted-releases/issues/209). Meanwhile, these browser tests serve as a simple consistency check when developing ATR.

To run the tests, you will need Docker. Other OCI runtimes should work, but you will need to edit the test scripts accordingly.

### Using Docker Compose

The simplest way to run the tests is using Docker Compose, which starts both ATR and the Playwright test container:

```shell
sh tests/run-playwright.sh
```

This uses [`tests/docker-compose.yml`](/ref/tests/docker-compose.yml) to orchestrate the test environment. The ATR server runs in one container and the Playwright tests run in another, connected via a Docker network. These tests are automatically run in our GitHub CI as part of [`.github/workflows/build.yml`](/ref/.github/workflows/build.yml).

### Using host networking

If you already have ATR running locally with `make serve-local`, you can run the Playwright tests directly against it instead of using Docker Compose:

```shell
make build-playwright && make run-playwright
```

Where the two `make` invocations correspond to:

```shell
docker build -t atr-playwright -f tests/Dockerfile.playwright playwright
docker run --net=host -it atr-playwright python3 test.py --skip-slow
```

In other words, we build [`tests/Dockerfile.playwright`](/ref/tests/Dockerfile.playwright), and then run [`playwright/test.py`](/ref/playwright/test.py) inside that container using host networking to access your locally running ATR instance. Replace `docker` with the name of your Docker-compatible OCI runtime to use an alternative runtime.

### Test duration

The tests should, as of 14 Oct 2025, take about 40 to 50 seconds to run in Docker Compose, and 20 to 25 seconds to run on the host. The last line of the test output should be `Tests finished successfully`, and if the tests do not complete successfully there should be an obvious Python backtrace.

## Creating Playwright tests

You can add tests to `playwright/test.py`. If you're feeling particularly adventurous, you can add separate unit tests etc., but it's okay to add tests only to the Playwright test script until [Issue #209](https://github.com/apache/tooling-trusted-releases/issues/209) is resolved.

### How the tests work

The browser tests use [Playwright](https://playwright.dev/), which is a cross-browser, cross-platform web testing framework. It's a bit like the older [PhantomJS](https://en.wikipedia.org/wiki/PhantomJS), now discontinued, which allows you to operate a browser through scripting. Playwright took the same concept and improved the user experience by adding better methods for polling browser state. Most interactions with a browser take some time to complete, and in PhantomJS the developer had to do that manually. Playwright makes it easier, and has become somewhat of an industry standard for browser tests.

We use the official Playwright OCI container, install a few dependencies (`apt-get` is available in the container), and then run `test.py`.

The `test.py` script calls [`run_tests`](/ref/playwright/test.py:run_tests) from its `main`, which sets up all the context, but the main action takes place in [`test_all`](/ref/playwright/test.py:test_all). This function removes any state accidentally left over from a previous run, then runs tests of certain components. Because ATR is stateful, the order of the tests is important. When adding a test, please be careful to ensure that you use the correct state and that you try not to modify that state in such a way that interferes with tests placed afterwards.

We want to make it more clear which Playwright tests depend on which, and have more isolated tests. Reusing context, however, helps to speed up the tests.

The actual test cases themselves tend to use helpers such as [`go_to_path`](/ref/playwright/test.py:go_to_path) and [`wait_for_path`](/ref/playwright/test.py:wait_for_path), and then call [`logging.info`](https://docs.python.org/3/library/logging.html#logging.info) to print information to the console. Try to keep logging messages terse and informative.

## Running end-to-end tests

To run ATR end-to-end (e2e) tests, you must first have an OCI container runtime with Compose functionality, such as Docker or Podman, installed. You will also need a POSIX shell. You can then run `tests/run-e2e.sh` to run the entire e2e test suite.

### Debugging e2e test failures

When e2e tests fail, the test script will display suggestions for debugging. You can also use the following techniques:

**View the ATR server logs:**

```shell
cd tests && docker compose logs atr-dev --tail 100
```

**View specific log files from the state volume:**

```shell
# View the Hypercorn logs
docker compose exec atr-dev cat /opt/atr/state/hypercorn/logs/hypercorn.log

# View the worker logs
docker compose exec atr-dev cat /opt/atr/state/logs/atr-worker.log

# View the worker error logs
docker compose exec atr-dev cat /opt/atr/state/logs/atr-worker-error.log
```

**Enter a shell in an already running ATR e2e container:**

```shell
cd tests && docker compose exec atr-dev sh
```

Once in a shell in the running container you can e.g. run `ls -al /opt/atr/state/logs`.

### Resetting cached state

The e2e tests use a persistent OCI container volume to store ATR state between runs. If you encounter errors due to stale or corrupted state, you need to reset this volume.

**Stop containers and remove the state volume:**

```shell
cd tests && docker compose down -v
```

The `-v` flag removes the persistent state volume (`atr-dev-state`), which resets all ATR state including the database, logs, and any uploaded files.

**Force rebuild the container images:**

If you have made changes to `Dockerfile.e2e` or any other dependencies, and the cached image is stale, run:

```shell
cd tests && docker compose build --no-cache atr-dev
```

**Perform a full reset:**

To stop the container, remove the volume, rebuild, and run the tests:

```shell
cd tests && docker compose down -v
docker compose build --no-cache atr-dev
cd .. && sh tests/run-e2e.sh
```

**Remove all test containers and images:**

To completely remove all test related containers, volumes, and images:

```shell
cd tests && docker compose down -v --rmi all
```

You probably only need to do this if you're running out of disk space.
