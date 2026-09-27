# Forecast UI implementation summary

## Scope completed

- Added an isolated forecast feature under `frontend/src/features/simulation/forecast/` with API/SSE DTOs, two-hour summary calculation, canonical risk ordering, local event reduction, and a `React.lazy`/`Suspense` Dashboard boundary.
- Added forecast card/risk-list presentation with explicit `预计` and `已发生` labels and an empty state.
- Added a bearer-authenticated `fetch` SSE reader for `/api/forecast/events?network_id=...`; it preserves `Last-Event-ID`, retries with bounded exponential backoff, aborts on unmount, and requests REST resync on disconnect or sequence gap.
- Added focused Vitest coverage for empty state, score ordering/Top-N, predicted/observed labels, observed SSE updates, and canonical SSE frame parsing.

## Contract alignment

- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1722-1727` requires ready/stale/not-ready semantics, 0–2 h horizon, and risk ordering by `risk_score DESC, expected_at ASC, id ASC`; the UI helper implements the two-hour filtering and ordering, while the backend remains authoritative for freshness and policy state.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1820-1832` fixes the canonical forecast endpoint and bearer-authenticated SSE frame.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1869-1874` requires cursor replay, resync on gaps, duplicate/older alert-version protection, and schema validation. The stream keeps `Last-Event-ID`, calls the resync callback on gap/disconnect, and the reducer rejects lower alert versions.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1903-1911` isolates simulation state and forecast snapshots from the legacy store. This implementation does not modify the legacy store or i18n files.

## Real changed files

- `frontend/src/features/simulation/forecast/types.ts`
- `frontend/src/features/simulation/forecast/ranking.ts`
- `frontend/src/features/simulation/forecast/stream.ts`
- `frontend/src/features/simulation/forecast/ForecastDashboard.tsx`
- `frontend/src/features/simulation/forecast/index.ts`
- `frontend/src/features/simulation/test/forecastDashboard.test.tsx`
- `docs/hydraulic-twin/implementation/forecast-ui.md` (workflow-required audit summary; explicitly approved as the sole documentation exception)

No API, model, store, i18n, route, or shared frontend file was modified by this task.

## Commands run and results

1. Forecast test suite:

   `npm --prefix smart-city-drainage/frontend test -- --run src/features/simulation/test/forecastDashboard.test.tsx`

   Result: **PASS** — 1 test file passed, 4 tests passed. The test process emitted the repository's ReactDOMTestUtils `act` deprecation warning, but exited successfully.

2. Owned-file formatting and final focused test:

   `cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx prettier --write "src/features/simulation/forecast/**/*.{ts,tsx}" "src/features/simulation/test/forecast*.test.*" && npm test -- --run src/features/simulation/test/forecastDashboard.test.tsx`

   Result: Prettier formatted owned files and Vitest again reported **4/4 passed**.

3. Formatting verification:

   `cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx prettier --check "src/features/simulation/forecast/**/*.{ts,tsx}" "src/features/simulation/test/forecast*.test.*"`

   Result: **PASS** — all matched files use Prettier code style.

4. Full production build:

   `npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend run build`

   Result: **FAIL / NOT PASSED** in `tsc -b`. The final rerun reported errors only in concurrently owned files outside this task, including `frontend/src/features/simulation/charts/chartOptions.ts`, `frontend/src/features/simulation/map/types.ts`, `frontend/src/features/simulation/map/viewport.ts`, and `frontend/src/features/simulation/test/chart-comparison.test.tsx`. The final build output did not report an error in the owned forecast files. A passing full build is not claimed.

## Remaining risks and integration handoff

- The component is lazy-load capable and exported through the feature barrel, but no Dashboard/route file was changed because those paths are outside this task's ownership. Integration must import `LazyForecastDashboardBoundary` from `@/features/simulation/forecast` and provide `networkId` plus the current bearer token.
- UI copy is currently Chinese literals. The frozen frontend contract calls for forecast/risk i18n namespaces, but i18n JSON files were outside ownership; integration must move these strings into the existing i18n system and verify zh/en key parity.
- The latest snapshot supplies the displayed risk rows. If the backend risk-ranking response includes fields beyond `ForecastAlert`, integration can map `/api/forecast/risk-ranking` into the existing `ForecastRiskItem` type without changing the sorting/display helpers.
- The component does not implement acknowledge/resolve actions; those are outside the requested read-only Forecast card/risk-list deliverables.
- The full frontend build remains blocked by unrelated parallel-module TypeScript errors and must be rerun by the integration owner after those modules land.
- No live forecast API or SSE backend was available/exercised in this task; SSE behavior is covered with deterministic frontend tests and canonical-frame parsing only.
