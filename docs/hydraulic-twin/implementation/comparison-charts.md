# Comparison charts implementation summary

## Scope completed

- Added feature-local A/B and observed/predicted comparison primitives under `frontend/src/features/simulation/comparison/` and modular ECharts components under `frontend/src/features/simulation/charts/`.
- Added `SimulationComparisonResponse` / metric DTOs that retain the frozen comparison identity fields (`compatible`, network revision, target manifest, frame grid, baseline/candidate) and backend-precomputed absolute/relative deltas, unit, and better direction; see `frontend/src/features/simulation/comparison/types.ts:16-56`.
- Added epoch-millisecond time-union alignment. Equivalent ISO instants join, invalid timestamps are ignored, and a missing sample stays `null`; pointwise `candidate - baseline` is emitted only when both values exist (`frontend/src/features/simulation/comparison/timeAlignment.ts:6-51`).
- Added a fixed blue–slate–orange diverging difference scale, a max-absolute-delta domain helper, and an accessible difference legend (`frontend/src/features/simulation/comparison/differenceScale.ts:1-58`, `frontend/src/features/simulation/charts/DifferenceLegend.tsx:21-53`).
- Added an accessible A/B metrics table that only formats backend values/deltas and translates the server-provided better direction; it does not recompute metrics (`frontend/src/features/simulation/comparison/metrics.ts:6-45`, `frontend/src/features/simulation/charts/ComparisonMetricsTable.tsx:10-65`).
- Added A/B `overlay`, `split`, and `difference` ECharts modes on a real time axis. The difference mode uses per-point colours and a zero mark line (`frontend/src/features/simulation/charts/chartOptions.ts:122-233`).
- Added the observed/predicted chart: observed solid, predicted dashed, issue-time mark line, and an available uncertainty interval implemented as stacked invisible-lower plus visible-band series (`frontend/src/features/simulation/charts/chartOptions.ts:236-337`).
- Added modular ECharts registration, one chart instance per component, resize observation, `setOption(..., { replaceMerge })`, and disposal on unmount (`frontend/src/features/simulation/charts/echartsSetup.ts:13-27`, `frontend/src/features/simulation/charts/useEChart.ts:7-38`).
- Added automatic `prefers-reduced-motion` handling plus a caller override; animation is also disabled above 500 aligned points (`frontend/src/features/simulation/charts/useReducedMotion.ts:9-28`, `frontend/src/features/simulation/charts/chartOptions.ts:70-78`).
- Added 10 focused Vitest cases covering absolute-time union/equivalence, missing values, invalid timestamps, pointwise difference, metric preservation, colour endpoints, all three A/B modes, missing-series gaps, uncertainty bounds, issue-time marking, large-data/reduced-motion animation, `replaceMerge`, disposal, accessible legend, and translated metrics (`frontend/src/features/simulation/test/chart-comparison.test.tsx`).

## Contract alignment

- `docs/hydraulic-twin/SOURCE_PRD.md:26,32` requires A/B comparison, predicted/observed with uncertainty, and queryable A/B differences for AC-6.
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:482-487` requires compatible revision/manifest/time grids, backend-precomputed absolute/relative metrics, null relative delta for a zero baseline, and no browser resampling. The pure aligner joins only exact absolute instants and never fills/interpolates gaps; the metrics table only formats supplied values.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1634,1677-1686` names the observed-comparison and A/B endpoints and keeps compatibility failure at the API boundary.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1950-1957` requires A/B/Δ presentation, modular ECharts, solid observed / dashed predicted lines, uncertainty band, issue-time mark line, >500-point animation suppression, reduced-motion suppression, resize/dispose, and non-`notMerge` updates. These behaviours are implemented at the cited chart files above.
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2320-2339` limits chart/comparison ownership to this module. No API, model, store, route, page, or i18n file was modified.

## Real changed files

- `frontend/src/features/simulation/comparison/types.ts`
- `frontend/src/features/simulation/comparison/timeAlignment.ts`
- `frontend/src/features/simulation/comparison/differenceScale.ts`
- `frontend/src/features/simulation/comparison/metrics.ts`
- `frontend/src/features/simulation/comparison/index.ts`
- `frontend/src/features/simulation/charts/chartOptions.ts`
- `frontend/src/features/simulation/charts/echartsSetup.ts`
- `frontend/src/features/simulation/charts/useEChart.ts`
- `frontend/src/features/simulation/charts/useReducedMotion.ts`
- `frontend/src/features/simulation/charts/SimulationComparisonChart.tsx`
- `frontend/src/features/simulation/charts/ObservedPredictedChart.tsx`
- `frontend/src/features/simulation/charts/ComparisonMetricsTable.tsx`
- `frontend/src/features/simulation/charts/DifferenceLegend.tsx`
- `frontend/src/features/simulation/charts/index.ts`
- `frontend/src/features/simulation/test/chart-comparison.test.tsx`
- `docs/hydraulic-twin/implementation/comparison-charts.md` (workflow-required audit summary; explicitly approved as the sole documentation exception)

## Commands run and results

1. Focused chart test suite:

   `npm --prefix 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' test -- src/features/simulation/test/chart-comparison.test.tsx`

   Result: **PASS** — 1 test file passed, 10 tests passed. Vitest emitted the repository/tooling warning that `ReactDOMTestUtils.act` is deprecated in favour of `React.act`; the process exited 0.

2. Focused production TypeScript check:

   `files=('C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/charts/'*.ts 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/charts/'*.tsx 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/comparison/'*.ts); node 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/node_modules/typescript/bin/tsc' --noEmit --target ES2020 --module ESNext --moduleResolution bundler --jsx react-jsx --strict --skipLibCheck --lib ES2020,DOM,DOM.Iterable "${files[@]}"`

   Result: **PASS** — exit 0 with no diagnostics for owned production files.

3. Owned-file ESLint:

   `node 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/node_modules/eslint/bin/eslint.js' --config 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/eslint.config.js' --report-unused-disable-directives --max-warnings 0 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/charts' 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/comparison' 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/chart-comparison.test.tsx'`

   Result: **PASS** — exit 0, no warnings/errors.

4. Owned-file Prettier check:

   `node 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/node_modules/prettier/bin/prettier.cjs' --check 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/charts/**/*.{ts,tsx}' 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/comparison/**/*.{ts,tsx}' 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/chart*.test.*'`

   Result: **PASS** — all matched files use Prettier code style.

5. Full frontend Vitest suite:

   `npm --prefix 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' test`

   Result: **FAIL / full zero-regression gate not green** — 10 files passed and 4 failed; 67 tests passed and 5 failed. This task's chart file passed 10/10. Failures were outside owned paths: `src/test/__tests__/LoginPage.test.tsx` (2 `localStorage.clear` failures), `src/features/simulation/test/mapCanvasLayer.test.ts` (1 hit-test expectation), `src/features/simulation/test/taskController.test.tsx` (1 idempotency-key expectation), and `src/features/simulation/test/taskPanel.test.tsx` (1 missing error-text expectation).

6. Full production build:

   `npm --prefix 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' run build`

   Result: **FAIL in `tsc -b`** before Vite bundling. The final output reported only concurrently owned test typing errors in `src/features/simulation/test/taskController.test.tsx`, `src/features/simulation/test/taskStream.test.tsx`, and `src/features/simulation/test/timelinePlayer.test.ts`; it reported no owned comparison/chart diagnostics. A passing full build is not claimed.

## Remaining risks and integration handoff

- Route/page wiring and i18n key registration are outside this task. Integration must render the feature-barrel exports and pass translated labels/ARIA labels from the `comparison` namespace; no chart text is hard-coded except numeric missing-value formatting when the pure formatter is used directly.
- The integration/API owner must map the concrete observed-comparison response into `ObservedPredictedPoint[]` and map raw simulation frame timestamps into `SimulationSeriesPoint[]`, then call `alignSimulationSeries` before selecting `SimulationComparisonChart`. No runtime API decoder was added because API/model ownership is outside this module.
- Full frontend tests and build are currently blocked by the unrelated files listed above; rerun both after the parallel owners land their fixes.
- No live API, browser screenshot, or real ECharts canvas rendering was exercised in this task. The ECharts option builders and React lifecycle are covered with deterministic Vitest mocks, but integration should perform a visual smoke test with real API payloads.
- No commit or push was performed.
