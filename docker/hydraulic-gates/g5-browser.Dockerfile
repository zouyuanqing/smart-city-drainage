# Frozen G5 frontend browser measurement environment.
#
# WHAT THIS IMAGE IS
#   A container that *is* the contracted G5 target: Node 18.20.8, four CPU
#   cores, 8 GiB of RAM, a real headless Chromium, and the production Vite
#   toolchain the frozen harness builds the browser bundle with.
#
# WHY IT HAS TO BE A CONTAINER, AND WHY `--cpuset-cpus` MATTERS
#   `frontend/performance/run-twin-map.mjs` fail-closes before it measures
#   anything unless the process it runs in satisfies three frozen constants
#   (TARGET_NODE / TARGET_CORES / TARGET_RAM_BYTES at `run-twin-map.mjs:35-37`,
#   evaluated by `detectEnvironment` at `:333-360`):
#     * `process.version` must be exactly 18.20.8
#     * available cores must be 4  -> `min(physicalCores, cgroupCpuLimit)`
#     * RAM must be 8 GiB exactly -> `min(os.totalmem(), cgroupMemoryLimit)`
#   Both limits are read from the Linux cgroup files only
#   (`detectCgroupCpuLimit` at `:269`, `detectCgroupMemoryLimit` at `:284`).
#   On a Windows or macOS host the harness reports the *machine's* physical core
#   count and total memory no matter how the process is confined, so a bare host
#   run records `target_environment_mismatch` and exits 2 (BLOCKED) without ever
#   producing a G5 verdict. `--cpus=4 --memory=8g` is what makes
#   `/sys/fs/cgroup/cpu.max` read `400000 100000` and `memory.max` read
#   8589934592, which is the only way `detectEnvironment` returns
#   `target_environment_match: true`.
#
#   `--cpus=4` alone satisfies the harness but is NOT a four-core machine: it is
#   a CPU *time quota*. The renderer, raster and compositor threads and the
#   harness's own Node process keep 32 schedulable CPUs on a big host and are
#   only throttled in aggregate, so they never contend with each other the way
#   four real cores force them to. `--cpuset-cpus=0-3` restricts the affinity
#   mask to four CPUs, which is the contention the contract describes: inside
#   such a container `nproc` and `os.availableParallelism()` both report 4 while
#   `cpu.max` still reads `400000 100000`, so the harness still sees four cores.
#   Use both. The measured difference on a 32-logical-CPU host is small (the
#   per-frame work here is dominated by one main-thread raster), but on a host
#   where the browser has to share four real cores with the harness it is the
#   difference between the contracted environment and a merely quota'd one.
#
# WHAT THIS IMAGE MUST NOT DO
#   It changes nothing about the measurement:
#     * the entrypoint stays the frozen `node performance/run-twin-map.mjs`
#       (i.e. `npm run test:perf` in `frontend/package.json`),
#     * the frozen fixture `performance/fixtures/twin-map-5k-10k-v1.json` is
#       copied verbatim and is the default `--fixture`,
#     * the 60 s acceptance duration and the 25 fps / 50 ms rAF p95 / 200 ms
#       long-task / 256 MiB cache budgets in that fixture are untouched,
#     * the browser runs with the harness's own flags including `--disable-gpu`,
#       so the measurement stays a software-rasterisation pass.
#   `npm ci` installs the frozen `package-lock.json` byte-for-byte; the harness
#   hashes that file into `environment.dependency_lock_sha256`, so a rewritten
#   lockfile would be visible in the report.
#
# BUILD (context is the REPOSITORY ROOT, and the layout below is forced by that)
#   docker build -f docker/hydraulic-gates/g5-browser.Dockerfile \
#     -t scn-g5-browser:node18.20.8-4c-8g .
#
#   The context is the repository root, not `frontend/`. That is where the G5
#   gate builds this image from, so every COPY below is rooted at the
#   repository and the frontend project is placed at /workspace/frontend
#   rather than at /workspace. The two facts are load-bearing together:
#     * a `frontend/`-relative COPY fails the build under a repository-root
#       context, because BuildKit resolves COPY sources against the context
#       and there is no package.json / src / performance at the repository
#       root;
#     * `npm --prefix frontend run test:perf` resolves `frontend` against
#       WORKDIR, so the project has to sit at /workspace/frontend. Sourcing
#       it into /workspace instead would build but then fail at run time.
#   A repository-root context also needs .dockerignore at the repository root,
#   or the 2.4 GiB working tree (backend/.venv, frontend/node_modules) is
#   uploaded on every build.
#
# RUN (the contracted 4 CPU / 8 GiB command)
#   docker run --rm --cpus=4 --cpuset-cpus=0-3 --memory=8g --memory-swap=8g \
#     -v "$PWD/artifacts/hydraulic:/artifacts" \
#     -e CHROME_PATH=/usr/bin/chromium \
#     scn-g5-browser:node18.20.8-4c-8g \
#     npm --prefix frontend run test:perf
#   `npm run test:perf` is the frozen script and is invoked verbatim with no
#   arguments, which is why it needs none: parseArgs already defaults
#   --fixture to the frozen fixture beside the harness, --duration-seconds to
#   60, and --output to <frontend>/../artifacts/hydraulic/frontend-smoke.json
#   (`run-twin-map.mjs:112-118`). The CMD below supplies the same three
#   explicitly so a plain `docker run` and the gate's `npm run test:perf` are
#   the same measurement.
#
#   `--cpuset-cpus=0-3` is required for the four cores to be four *cores*
#   (see the header). Adjust the list if the host has fewer than four; on a
#   four-vCPU runner `--cpuset-cpus` may simply be omitted.
#
#   Acceptance (60 s, G5 verdict) is the default. `--diagnostic-non-target` is
#   the harness's own 10 s diagnostic mode and is explicitly *not* G5 evidence.
#
# Mirrors are build arguments for the same reason PIP_INDEX_URL and
# DEBIAN_MIRROR are in docker/backend/Dockerfile.cpu: a network that cannot
# reach a canonical host should be a build argument, not a hard-coded endpoint.
# The defaults are the canonical hosts; a mirror serving the *same image* is an
# equivalent base and is what an air-gapped builder passes in.
#
# The base is a tag plus a *build-time assertion* rather than an `@sha256:`
# FROM pin. `image:tag@sha256:<hex>` in a FROM instruction is rejected by the
# builders shipped with Docker Desktop 29.4 / buildx 0.33
# ("failed to parse stage name ... invalid checksum digest length", both with
# BuildKit and with DOCKER_BUILDKIT=0), so a digest-pinned FROM would make this
# image unbuildable on the very toolchain this gate runs on. The pin is therefore
# enforced where it actually matters: `RUN` below *fails the build* unless the
# runtime in the image is exactly 18.20.8, and the expected digest is recorded as
# a label so a report or an SBOM can attest which base was used.
ARG NODE_IMAGE=node:18.20.8-alpine
ARG NODE_IMAGE_SHA256=sha256:8d6421d663b4c28fd3ebc498332f249011d118945588d0a35cb9bc4bf8b8ca09d9e
ARG NODE_VERSION_PIN=18.20.8
ARG ALPINE_MIRROR=https://dl-cdn.alpinelinux.org
ARG NPM_REGISTRY=https://registry.npmjs.org/

FROM ${NODE_IMAGE}

# Re-declared: an ARG given a default before FROM is only in scope for the FROM
# instruction itself, not for RUN or LABEL.
ARG NODE_IMAGE
ARG NODE_IMAGE_SHA256
ARG NODE_VERSION_PIN
ARG ALPINE_MIRROR
ARG NPM_REGISTRY

# Fail the build on any Node drift, whatever the base tag or mirror resolves to.
# `detectEnvironment` compares `process.version` against TARGET_NODE
# (`run-twin-map.mjs:35`, `:341-342`), so an image with the wrong runtime can
# only ever produce a BLOCKED run; refusing to build it is the cheaper failure.
RUN set -eux; \
    test "$(node --version)" = "v${NODE_VERSION_PIN}"; \
    node --version

# The harness launches a real headless browser (`findChrome` at
# `run-twin-map.mjs:585-608` probes /usr/bin/chromium among others) with
# --disable-gpu, so the measurement is a software rasterisation pass and the
# browser binary must exist in the image.
RUN set -eux; \
    if [ "$ALPINE_MIRROR" != "https://dl-cdn.alpinelinux.org" ]; then \
        sed -i "s|https://dl-cdn.alpinelinux.org|$ALPINE_MIRROR|g" /etc/apk/repositories; \
        grep -q "$ALPINE_MIRROR" /etc/apk/repositories; \
    fi; \
    apk add --no-cache chromium; \
    chromium --version

LABEL org.opencontainers.image.title="G5 frontend browser measurement environment" \
      org.opencontainers.image.base.name="${NODE_IMAGE}" \
      org.opencontainers.image.base.digest="${NODE_IMAGE_SHA256}" \
      io.smartcity-drainage.g5.node="${NODE_VERSION_PIN}" \
      io.smartcity-drainage.g5.cpus="4" \
      io.smartcity-drainage.g5.ram="8GiB"

# WORKDIR stays the workspace root so `npm --prefix frontend` resolves the
# project the way the gate invokes it; the project itself is copied into
# frontend/ under it.
WORKDIR /workspace

# .npmrc carries engine-strict=true, so a wrong runtime fails fast here instead
# of warning, exactly as in docker/frontend/Dockerfile. COPY it before npm ci.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./frontend/
# --replace-registry-host keeps the frozen lockfile byte-identical (the harness
# hashes it into dependency_lock_sha256) while resolving the tarballs named in
# it through the reachable registry. node_modules must land in
# /workspace/frontend so the harness resolves vite/ws from beside itself.
RUN cd frontend \
    && npm ci --no-audit --no-fund \
        --registry="$NPM_REGISTRY" \
        --replace-registry-host=always \
    && node -e "require.resolve('vite'); import('ws').then(() => console.log('vite + ws resolve'))" \
    && node -e "console.log('vitest', require('vitest/package.json').version)"

# The browser bundle the harness builds compiles the real ViewportIndex, Canvas
# controller and FrameCache sources, so the measured code is the shipped code.
# Nothing here rewrites the entrypoint or the fixture.
COPY frontend/src ./frontend/src
COPY frontend/public ./frontend/public
COPY frontend/performance ./frontend/performance
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json \
     frontend/tsconfig.node.json frontend/postcss.config.js \
     frontend/tailwind.config.js ./frontend/


# findChrome() probes this first when CHROME_PATH is set; pinning it keeps the
# recorded report.browser.executable / executable_sha256 stable across hosts.
ENV CHROME_PATH=/usr/bin/chromium
# The frozen fixture and the frozen acceptance duration, as defaults for the
# runner. The harness still fails closed if either is edited: the fixture schema,
# input_counts, frame count, viewport and every threshold are asserted at
# `run-twin-map.mjs:163-258` and the 60 s acceptance duration at `:150-156`.
ENV G5_FIXTURE=performance/fixtures/twin-map-5k-10k-v1.json
ENV G5_DURATION_SECONDS=60
ENV G5_OUTPUT=/artifacts/frontend-smoke-g5.json

# `npm run test:perf` verbatim, as the default command. `npm` is not used at
# runtime so the process tree is node -> node, one fewer process on a 4-core box.
# `cd frontend` because the project lives at /workspace/frontend; everything
# past it is the frozen entrypoint and the same three arguments the gate's
# argument-free `npm --prefix frontend run test:perf` gets from parseArgs.
CMD ["sh", "-c", "cd frontend && exec node performance/run-twin-map.mjs --fixture \"$G5_FIXTURE\" --duration-seconds \"$G5_DURATION_SECONDS\" --output \"$G5_OUTPUT\" \"$@\"", "--"]
