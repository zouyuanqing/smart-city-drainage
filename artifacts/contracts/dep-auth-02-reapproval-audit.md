# DEP-AUTH-02 Controlled Re-Approval Audit

- **Verdict:** RE-APPROVED (gates green) — with one hygiene finding recorded below.
- **Scope:** `.env.example` recovery evidence, DEP-AUTH-02 `result_sha256`, expected fixture,
  handoff reference, and this audit. No change to `config.py`, solver, worker, checker or tests.
- **Executed (UTC):** 2026-09-27T10:37:55Z
- **Repo root:** `C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
- **Python:** `C:\Python314\python.exe`

## 1. Incident

The DEP-AUTH-02 approval recorded `result_sha256 = 05ac209729db47aa5d528a311697941e5b1c691a2e82b794dac986f364d4540c`.
The live `.env.example` hashes to `fac68149630b3ef489120f1b2a1da2e29bba44acb8fd51de0d044dd569cea6cc`.
The originally-approved, never-committed bytes are unrecoverable (`artifacts/contracts/*.json` and the
expected fixture are untracked: `git ls-files --error-unmatch` reports "did not match any file(s) known to git").
Re-approval therefore certifies the **current** rebuilt bytes against the authoritative
`Settings` registry and the frozen contract, and re-derives every digest. No old hash was reused or forged.

## 2. Recovery evidence

```
cp -f .env.example artifacts/contracts/env-example-recovered.txt
cmp .env.example artifacts/contracts/env-example-recovered.txt   # -> BYTE_IDENTICAL
```

| Artifact | Bytes | SHA-256 |
|---|---|---|
| `.env.example` | 4914 | `fac68149630b3ef489120f1b2a1da2e29bba44acb8fd51de0d044dd569cea6cc` |
| `artifacts/contracts/env-example-recovered.txt` | 4914 | `fac68149630b3ef489120f1b2a1da2e29bba44acb8fd51de0d044dd569cea6cc` |
| `artifacts/contracts/env-example-recovered.txt.sha256` | 93 | sidecar, `sha256  name` form |

## 3. Field-by-field verification (105 Settings fields)

Source: `backend/app/core/config.py:127` (`class Settings`). Checker: `scripts/check_dependency_handoff.py`.

| Dimension | Result | Evidence |
|---|---|---|
| Field count | 105 = 54 authorized + 51 preserve-only | `_live_settings_contract` asserts `len == 105`, `check_dependency_handoff.py:880` |
| Registry partition | `AUTHORIZED_SETTINGS` ∩ `UNAUTHORIZED_SETTINGS` = ∅; union == `model_fields` | checker:887-898 |
| Name coverage | `sorted(.env.example keys) == sorted(Settings.model_fields)` → True | checker:883-886 |
| Type | `_canonical_type` per field; Literals, `list[str]`, `str \| list[int]`, `float`, `int`, `bool` all canonical | checker:487-507 |
| Default | 101/105 equal to the template value; 4 intentional divergences (below) | audit run |
| Order (enforced) | `settings.fields` == `sorted(AUTHORIZED)`; `unauthorized_fields` == `sorted(UNAUTHORIZED)`; `auth_ids` == DEP-AUTH-01..13; `files` == DEP-AUTH-01..12; every `paths` list sorted by path | all True |
| Order (not enforced) | `.env.example` key order ≠ `Settings` declaration order (template groups `DEPLOYMENT_ENVIRONMENT` first). Checker compares **sorted sets**, not order — no gate depends on this. | audit run |
| Fixture consistency | all 54 authorized field entries byte-equal across live / handoff / expected fixture; `unauthorized_fields` equal; 32/54 carry constraint strings; all 54 carry `env_example` + `validators` | audit run |
| `Settings()` actual parse | `Settings(_env_file=.env.example)` constructs; values come from the template, not defaults (e.g. `JWT_ISSUER='smart-city-drainage'`, `SIM_RETRY_BACKOFF_SECONDS=[10, 60]`, `CORS_ORIGINS=['http://localhost:5173','http://localhost:3000']`) | audit run |
| Duplicate keys | none; checker additionally hard-fails on duplicates | checker:543 |
| Invalid-case battery | `_validate_invalid_cases` executes every `INVALID_CASES` entry inside `build_expected_contract_snapshot`; the snapshot build succeeded | checker:827-867 |
| Secrets | 0 hits for PEM private key, AWS AKIA, GitHub/Slack tokens, 32+ hex blobs. Finding F-1 below. | audit run |

**4 intentional template-vs-default divergences** (already baselined in the frozen contract, so
`handoff.settings == live_settings` holds and no gate fails):

| Field | `config.py` default | `.env.example` |
|---|---|---|
| `DATABASE_URL` | `...:Dr@inage_S3cur3_2024!@...` | `...:YOUR_PASSWORD@...` |
| `DATABASE_URL_SYNC` | `...:Dr@inage_S3cur3_2024!@...` | `...:YOUR_PASSWORD@...` |
| `JWT_ISSUER` | `''` | `smart-city-drainage` |
| `JWT_AUDIENCE` | `''` | `smart-city-api` |

### Finding F-1 (hygiene, non-blocking) — 3 preserve-only keys now carry config.py dev defaults

The rebuild replaced base-commit placeholders with the real `config.py` defaults, inside the
`# --- Preserve-only (do not change) ---` block (`.env.example:97-141`):

| Key | base `ca99e04` | current `.env.example` | `config.py` default |
|---|---|---|---|
| `INFLUXDB_TOKEN` | `your-influxdb-token` | `scn-influx-token-2024-secure` | `config.py:185` |
| `REDIS_URL` | `redis://:YOUR_PASSWORD@localhost:6379/0` | `redis://:R3d1s_S3cur3_2024!@localhost:6379/0` | `config.py:190` |
| `JWT_SECRET_KEY` | `your-jwt-secret-key-change-in-production` | `scn-jwt-secret-key-change-in-production-2024` | `config.py:196` |

Severity: **low — no new secret material is disclosed**, because all three literals are already
committed verbatim in `backend/app/core/config.py`. What regressed is template hygiene: `.env.example`
is the file operators copy to `.env`, and it now ships working-shaped dev credentials instead of
placeholders. `DATABASE_URL`/`DATABASE_URL_SYNC` correctly kept `YOUR_PASSWORD`.

These three are preserve-only, and the frozen contract records them as
`{"authorized": false, "preserve_only": true}` with **no `env_example` value**
(checker:918-921) — so the contract provides no coverage to verify them against. This re-approval
therefore cannot certify these three values field-by-field; it only observes them. Left unchanged
deliberately: altering approved credential material during a re-approval is a scope change for the
run owner, and `config.py` (the authoritative source of these literals) is out of scope.
The production gate at `config.py:545-557` already rejects `scn-influx-token-2024-secure` and
`R3d1s_S3cur3_2024!` under `DEPLOYMENT_ENVIRONMENT=production`.

## 4. Regenerated digests

Rebuilt with the checker's own pure helper `build_expected_contract_snapshot` (checker:956-982);
serialization pinned to the existing convention `json.dumps(..., ensure_ascii=False, indent=2,
sort_keys=True) + "\n"` with CRLF endings.

| File | Before | After |
|---|---|---|
| `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json` | `c0054611e950d6b0b6e748491c043c6aa0123a331dd083ca7451d48dc20d9b49` | `750552939e6cf9f3c74d598dc198b309ca5d05ef8c928a09d81cd819bb7064ac` |
| handoff `files.DEP-AUTH-02.paths[0].result_sha256` | `05ac209729db47aa5d528a311697941e5b1c691a2e82b794dac986f364d4540c` | `fac68149630b3ef489120f1b2a1da2e29bba44acb8fd51de0d044dd569cea6cc` |
| handoff `expected_fixture.sha256` | `c0054611e950…` | `750552939e6c…` |
| `artifacts/contracts/dependency-config-handoff.json` (whole file) | — | `8ba6039d3f52d21450bf4c4566ad606dab327e6b510a6002370404dfa553e4aa` |

**Delta is provably minimal.** A structural diff of the freshly built snapshot against the on-disk
expected fixture reported exactly **1** differing node (`/files/DEP-AUTH-02/paths/0/result_sha256`).
Byte-level re-substitution of the old digest reproduces the pre-change fixture with **1** differing
line; re-substituting both old digests in the handoff leaves **2** differing lines. No other field,
including all 105 Settings entries, moved.

Preserved verbatim: handoff `schema` (`dependency-config-handoff-v1`), `r0_review`, and the
expected fixture's `schema` (`dependency-handoff-expectation-v1`).

## 5. Commands run and results

Baseline, before regeneration:

```
cd backend && python -m pytest \
  tests/test_simulation_settings.py::test_env_example_contains_every_contract_variable \
  tests/test_contract_gate_scripts.py::test_live_dependency_handoff_matches_fixture_and_settings \
  tests/test_contract_gate_scripts.py::test_dependency_handoff_cli_accepts_workspace_prefixed_paths -v
-> 2 failed, 1 passed
   DEPENDENCY_HANDOFF=FAIL: result SHA-256 mismatch: .env.example
```

After regeneration — the three named tests:

```
cd backend && python -m pytest <same three node ids> -v
-> tests/test_simulation_settings.py::test_env_example_contains_every_contract_variable PASSED [ 33%]
   tests/test_contract_gate_scripts.py::test_live_dependency_handoff_matches_fixture_and_settings PASSED [ 66%]
   tests/test_contract_gate_scripts.py::test_dependency_handoff_cli_accepts_workspace_prefixed_paths PASSED [100%]
   3 passed, 1268 warnings in 1.09s
```

Full related suites (collateral-damage check):

```
cd backend && python -m pytest tests/test_simulation_settings.py \
  tests/test_contract_gate_scripts.py tests/test_requirements_matrix.py -rs
-> 88 passed, 2 skipped, 1656 warnings in 5.65s
   SKIPPED [2] tests/test_requirements_matrix.py:172: fallback-only profile   (pre-existing, unrelated)
```

The gate itself, repository-relative paths:

```
cd smart-city-drainage && python scripts/check_dependency_handoff.py \
  --handoff artifacts/contracts/dependency-config-handoff.json \
  --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json \
  --python-version-file .python-version --node-version-file frontend/.node-version \
  --npmrc frontend/.npmrc --package frontend/package.json \
  --lock frontend/package-lock.json --env-example .env.example
-> DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
   DEFERRED=frontend-target-runtime-evidence
   G0_G5_BLOCKED_BY_DEFERRED=true
   EXIT=0
```

And from the workspace parent with workspace-prefixed paths (the form the CLI test exercises):

```
cd .. && python smart-city-drainage/scripts/check_dependency_handoff.py \
  --handoff smart-city-drainage/artifacts/contracts/dependency-config-handoff.json ... \
  --env-example smart-city-drainage/.env.example
-> DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED ; EXIT=0
```

## 6. Status

DEP-AUTH-02 is re-approved against the current rebuilt bytes. `frontend-target-runtime-evidence`
remains **DEFERRED**, so G0–G5 stay blocked by that deferred item — unchanged by this re-approval.
No test, checker, `config.py`, solver or worker file was modified.
