#!/usr/bin/env node
/* global AbortSignal, URL, clearTimeout, fetch, process, setTimeout */

/**
 * Frozen G5 frontend performance entrypoint.
 *
 * Acceptance mode is fail-closed: it requires the contracted 60 second run,
 * Node 18.20.8, four CPU cores, 8 GiB RAM, and the production browser bundle.
 * The browser workload imports the real ViewportIndex, Canvas controller and
 * FrameCache implementations. Non-target hosts may only run the explicitly
 * labelled diagnostic mode; diagnostic evidence never claims a G5 PASS.
 */

import { spawn, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createReadStream, existsSync, readFileSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import { dirname, extname, join, relative, resolve, sep } from 'node:path'
import { performance } from 'node:perf_hooks'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'
import WebSocket from 'ws'

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = resolve(SCRIPT_DIR, '..')
const REPO_ROOT = resolve(FRONTEND_ROOT, '..')
const HARNESS_HTML = resolve(SCRIPT_DIR, 'twin-map-harness.html')
const EXIT = { PASS: 0, FAIL: 1, BLOCKED: 2, INTERNAL: 3 }
const FIXTURE_SCHEMA = 'hydraulic-frontend-performance-browser-v1'
const REPORT_SCHEMA = 'hydraulic-frontend-performance-report-v2'
const EXPECTED_COUNTS = Object.freeze({ nodes: 5000, pipes: 10000, catchments: 1000 })
const EXPECTED_FRAMES = 289
const TARGET_NODE = '18.20.8'
const TARGET_CORES = 4
const TARGET_RAM_BYTES = 8 * 1024 * 1024 * 1024
const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const MANIFEST_HASH = 'a'.repeat(64)
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'

class PerformanceError extends Error {
  constructor(message, exitCode = EXIT.FAIL) {
    super(message)
    this.exitCode = exitCode
  }
}

function assert(condition, message, exitCode = EXIT.FAIL) {
  if (!condition) throw new PerformanceError(message, exitCode)
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    assert(Number.isFinite(value), 'fixture/report contains a non-finite number')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(',')}}`
  }
  throw new PerformanceError(`unsupported JSON value: ${typeof value}`)
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

async function fileSha256(path) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(path)) hash.update(chunk)
  return hash.digest('hex')
}

function utcNow() {
  return new Date().toISOString()
}

function git(args) {
  try {
    return execFileSync('git', ['-C', REPO_ROOT, ...args], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return null
  }
}

function resolveCliPath(rawPath) {
  if (/^[A-Za-z]:[\\/]/.test(rawPath) || rawPath.startsWith('/')) return resolve(rawPath)
  const frontendCandidate = resolve(FRONTEND_ROOT, rawPath)
  if (existsSync(frontendCandidate)) return frontendCandidate
  const repoCandidate = resolve(REPO_ROOT, rawPath)
  if (existsSync(repoCandidate)) return repoCandidate
  const workspaceCandidate = resolve(REPO_ROOT, '..', rawPath)
  if (existsSync(workspaceCandidate) || rawPath.startsWith(`${REPO_ROOT.split(/[\\/]/).pop()}/`)) {
    return workspaceCandidate
  }
  return frontendCandidate
}

function parseArgs(argv) {
  const options = {
    durationSeconds: 60,
    diagnostic: false,
    validateOnly: false,
    fixture: resolve(SCRIPT_DIR, 'fixtures/twin-map-5k-10k-v1.json'),
    output: resolve(REPO_ROOT, 'artifacts/hydraulic/frontend-smoke.json'),
  }
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    const next = argv[index + 1]
    if (token === '--fixture') {
      assert(next, '--fixture requires a path')
      options.fixture = resolveCliPath(next)
      index += 1
    } else if (token === '--output') {
      assert(next, '--output requires a path')
      options.output = resolveCliPath(next)
      index += 1
    } else if (token === '--duration-seconds') {
      assert(next, '--duration-seconds requires a number')
      options.durationSeconds = Number(next)
      index += 1
    } else if (token === '--diagnostic-non-target') {
      options.diagnostic = true
    } else if (token === '--validate-only') {
      options.validateOnly = true
    } else {
      throw new PerformanceError(`unknown argument: ${token}`, EXIT.BLOCKED)
    }
  }
  assert(options.fixture, '--fixture is required', EXIT.BLOCKED)
  assert(options.output, '--output is required', EXIT.BLOCKED)
  assert(
    Number.isFinite(options.durationSeconds) && options.durationSeconds > 0,
    '--duration-seconds must be positive',
    EXIT.BLOCKED
  )
  if (!options.diagnostic) {
    assert(
      options.durationSeconds === 60,
      'acceptance mode requires the frozen --duration-seconds 60',
      EXIT.BLOCKED
    )
  }
  if (options.diagnostic) {
    assert(options.durationSeconds <= 10, 'diagnostic mode is limited to 10 seconds', EXIT.BLOCKED)
  }
  return options
}

async function loadFixture(path) {
  let bytes
  try {
    bytes = await readFile(path)
  } catch (error) {
    throw new PerformanceError(`fixture is not readable: ${path}: ${error.message}`, EXIT.BLOCKED)
  }
  let fixture
  try {
    fixture = JSON.parse(bytes.toString('utf8'))
  } catch (error) {
    throw new PerformanceError(`fixture is not valid JSON: ${error.message}`, EXIT.BLOCKED)
  }
  assert(
    fixture && typeof fixture === 'object' && !Array.isArray(fixture),
    'fixture root invalid',
    EXIT.BLOCKED
  )
  assert(
    fixture.schema === FIXTURE_SCHEMA,
    `fixture.schema must be ${FIXTURE_SCHEMA}`,
    EXIT.BLOCKED
  )
  assert(
    typeof fixture.fixture_id === 'string' && fixture.fixture_id,
    'fixture.fixture_id is required',
    EXIT.BLOCKED
  )
  assert(
    fixture.data_source === 'fixture_api_production_modules_v1',
    'fixture data_source is not accepted',
    EXIT.BLOCKED
  )
  for (const [name, count] of Object.entries(EXPECTED_COUNTS)) {
    assert(
      fixture.input_counts?.[name] === count,
      `fixture.input_counts.${name} must equal ${count}`,
      EXIT.BLOCKED
    )
  }
  assert(
    fixture.frames?.count === EXPECTED_FRAMES,
    'fixture.frames.count must equal 289',
    EXIT.BLOCKED
  )
  assert(
    fixture.frames?.interval_minutes === 5,
    'fixture.frames.interval_minutes must equal 5',
    EXIT.BLOCKED
  )
  assert(
    fixture.frames?.duration_minutes === 1440,
    'fixture.frames.duration_minutes must equal 1440',
    EXIT.BLOCKED
  )
  assert(
    fixture.viewport?.width === 1920 && fixture.viewport?.height === 1080,
    'fixture viewport must equal 1920x1080',
    EXIT.BLOCKED
  )
  for (const name of [
    'pan_steps',
    'zoom_steps',
    'playback_frames',
    'layer_toggles',
    'selection_changes',
  ]) {
    assert(
      Number.isInteger(fixture.workload?.[name]) && fixture.workload[name] > 0,
      `fixture.workload.${name} must be a positive integer`,
      EXIT.BLOCKED
    )
  }
  const thresholds = fixture.thresholds
  assert(
    thresholds && typeof thresholds === 'object',
    'fixture.thresholds is required',
    EXIT.BLOCKED
  )
  const expectedThresholds = {
    min_presented_fps: 25,
    max_request_animation_frame_p95_ms: 50,
    max_long_task_ms: 200,
    max_cache_bytes: 256 * 1024 * 1024,
    max_map_api_p95_ms: 500,
    min_sample_count: 1000,
  }
  for (const [name, expected] of Object.entries(expectedThresholds)) {
    assert(
      thresholds[name] === expected,
      `fixture.thresholds.${name} must equal ${expected}`,
      EXIT.BLOCKED
    )
  }
  return { fixture, fixturePath: path, fixtureSha256: sha256(bytes) }
}

function readNumberFile(path) {
  try {
    const value = Number(readFileSync(path, 'utf8').trim())
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

function detectCgroupCpuLimit() {
  const v2 = readNumberFile('/sys/fs/cgroup/cpu.max')
  if (v2 !== null) return null
  const raw = (() => {
    try {
      return readFileSync('/sys/fs/cgroup/cpu.max', 'utf8').trim().split(/\s+/)
    } catch {
      return []
    }
  })()
  if (raw[0] === 'max' || !raw[1]) return null
  const cores = Number(raw[0]) / Number(raw[1])
  return Number.isFinite(cores) && cores > 0 ? cores : null
}

function detectCgroupMemoryLimit() {
  const v2 = readNumberSync('/sys/fs/cgroup/memory.max')
  if (v2 !== null) return v2
  const v1 = readNumberSync('/sys/fs/cgroup/memory/memory.limit_in_bytes')
  if (v1 !== null && v1 < Number.MAX_SAFE_INTEGER) return v1
  return null
}

function readNumberSync(path) {
  try {
    const raw = readFileSync(path, 'utf8').trim()
    if (raw === 'max') return null
    const value = Number(raw)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

function detectPhysicalCores() {
  if (process.platform === 'linux') {
    const cores = new Set()
    for (let cpu = 0; cpu < 1024; cpu += 1) {
      const core = readNumberSync(`/sys/devices/system/cpu/cpu${cpu}/topology/core_id`)
      const pkg = readNumberSync(`/sys/devices/system/cpu/cpu${cpu}/topology/physical_package_id`)
      if (core !== null) cores.add(`${pkg ?? 0}:${core}`)
    }
    if (cores.size > 0) return cores.size
  }
  if (process.platform === 'win32') {
    try {
      const output = execFileSync(
        'powershell.exe',
        [
          '-NoProfile',
          '-Command',
          '(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum',
        ],
        { encoding: 'utf8', timeout: 10_000, stdio: ['ignore', 'pipe', 'ignore'] }
      )
      const value = Number(output.trim())
      if (Number.isFinite(value) && value > 0) return value
    } catch {
      // Fall through to the conservative logical-core estimate.
    }
  }
  return Math.max(1, Math.floor((os.cpus().length || 1) / 2))
}

function detectEnvironment() {
  const physicalCores = detectPhysicalCores()
  const cgroupCpu = detectCgroupCpuLimit()
  const availableCores = cgroupCpu === null ? physicalCores : Math.min(physicalCores, cgroupCpu)
  const cgroupMemory = detectCgroupMemoryLimit()
  const ramBytes = cgroupMemory === null ? os.totalmem() : Math.min(os.totalmem(), cgroupMemory)
  const nodeVersion = process.version.replace(/^v/, '')
  const mismatches = []
  if (nodeVersion !== TARGET_NODE)
    mismatches.push(`node_version=${nodeVersion}; expected=${TARGET_NODE}`)
  if (Math.abs(availableCores - TARGET_CORES) > 0.01) {
    mismatches.push(`available_cores=${availableCores}; expected=${TARGET_CORES}`)
  }
  if (ramBytes !== TARGET_RAM_BYTES)
    mismatches.push(`ram_bytes=${ramBytes}; expected=${TARGET_RAM_BYTES}`)
  return {
    os: `${os.platform()} ${os.release()} ${os.arch()}`,
    cpu_model: os.cpus()[0]?.model ?? null,
    logical_cpus: os.availableParallelism?.() ?? os.cpus().length,
    physical_cores: physicalCores,
    available_cores: availableCores,
    ram_bytes: ramBytes,
    node_version: nodeVersion,
    gpu: 'disabled_software_rasterization',
    target_environment_match: mismatches.length === 0,
    target_environment_mismatches: mismatches,
  }
}

function uuid(prefix, index) {
  return `${prefix}-0000-4000-8000-${index.toString(16).padStart(12, '0')}`
}

function buildMapData() {
  const nodes = []
  const nodeCoordinates = []
  for (let row = 0; row < 50; row += 1) {
    for (let column = 0; column < 100; column += 1) {
      const index = row * 100 + column
      const coordinates = [121.3 + (column + 0.5) * 0.003, 31.15 + (row + 0.5) * 0.0034]
      nodeCoordinates.push(coordinates)
      nodes.push({
        type: 'Feature',
        id: uuid('10000000', index),
        geometry: { type: 'Point', coordinates },
        properties: { feature_index: index, name: `Node ${index}`, critical: index % 997 === 0 },
      })
    }
  }
  const pipes = []
  for (let index = 0; index < EXPECTED_COUNTS.pipes; index += 1) {
    const row = Math.floor(index / 200)
    const slot = index % 200
    const column = Math.floor(slot / 2)
    const start = row * 100 + column
    const end =
      slot % 2 === 0 ? (column < 99 ? start + 1 : start - 1) : row < 49 ? start + 100 : start - 100
    pipes.push({
      type: 'Feature',
      id: uuid('20000000', index),
      geometry: { type: 'LineString', coordinates: [nodeCoordinates[start], nodeCoordinates[end]] },
      properties: {
        feature_index: index,
        name: `Pipe ${index}`,
        major: index % 4 === 0,
        nominal_diameter_m: index % 4 === 0 ? 1.2 : 0.6,
      },
    })
  }
  const catchments = []
  for (let row = 0; row < 20; row += 1) {
    for (let column = 0; column < 50; column += 1) {
      const index = row * 50 + column
      const west = 121.3 + column * 0.006 + 0.0005
      const south = 31.15 + row * 0.0085 + 0.0005
      const east = west + 0.0048
      const north = south + 0.0068
      catchments.push({
        type: 'Feature',
        id: uuid('30000000', index),
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [west, south],
              [east, south],
              [east, north],
              [west, north],
              [west, south],
            ],
          ],
        },
        properties: { feature_index: index, name: `Catchment ${index}`, area_m2: 180000 },
      })
    }
  }
  return {
    schema_version: 'network-map-v2',
    network_id: NETWORK_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: MANIFEST_HASH,
    crs: 'EPSG:4326',
    bbox: [121.3, 31.15, 121.6, 31.32],
    zoom: 13,
    complete: true,
    simplified: false,
    truncated_layers: [],
    source_counts: { nodes: 5000, pipes: 10000, catchments: 1000, ponding: 0 },
    returned_counts: { nodes: 5000, pipes: 10000, catchments: 1000, ponding: 0 },
    nodes: { type: 'FeatureCollection', features: nodes },
    pipes: { type: 'FeatureCollection', features: pipes },
    catchments: { type: 'FeatureCollection', features: catchments },
    generated_at: '2026-09-24T00:00:00Z',
  }
}

function buildFrame(frameIndex) {
  const nodeIndexes = Array.from({ length: EXPECTED_COUNTS.nodes }, (_, index) => index)
  const pipeIndexes = Array.from({ length: EXPECTED_COUNTS.pipes }, (_, index) => index)
  const phase = frameIndex / EXPECTED_FRAMES
  return {
    schema_version: 'hydraulic-frame-v1',
    simulation_id: SIMULATION_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: MANIFEST_HASH,
    published_attempt_id: ATTEMPT_ID,
    published_attempt_no: 1,
    solver_id: 'py_mw',
    solver_version: 'g5-frontend-fixture-v1',
    frame_index: frameIndex,
    frame_time: new Date(Date.UTC(2026, 8, 24, 0, 0) + frameIndex * 300_000).toISOString(),
    interval_seconds: 300,
    nodes: {
      feature_index: nodeIndexes,
      depth_m: nodeIndexes.map((index) => Number((((index * 17) % 101) / 100 + phase).toFixed(4))),
      fill_ratio: nodeIndexes.map((index) =>
        Number((((index * 29) % 101) / 100 + phase).toFixed(4))
      ),
      surcharged: nodeIndexes.map((index) => index % 101 >= 90),
    },
    pipes: {
      feature_index: pipeIndexes,
      fill_ratio: pipeIndexes.map((index) =>
        Number((((index * 31) % 101) / 100 + phase).toFixed(4))
      ),
      flow_m3s: pipeIndexes.map((index) =>
        Number((0.05 + ((index * 7) % 100) / 100 + phase).toFixed(4))
      ),
      depth_m: pipeIndexes.map((index) =>
        Number((0.02 + ((index * 11) % 80) / 100 + phase / 2).toFixed(4))
      ),
      surcharged: pipeIndexes.map((index) => index % 101 >= 94),
    },
    quality: { status: 'verified', mass_residual_relative: 0.0001 },
    checksum: 'c'.repeat(64),
  }
}

const MIME_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
}

function createPerformanceServer({ buildDir, indexPath, mapBody, frameBodies }) {
  const buildRoot = resolve(buildDir)
  return createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1')
    const path = url.pathname
    request.resume()
    if (path === '/favicon.ico') {
      response.writeHead(204)
      response.end()
      return
    }
    if (path === '/api/map-data') {
      response.writeHead(200, {
        'cache-control': 'no-store',
        'content-type': 'application/json; charset=utf-8',
      })
      response.end(mapBody)
      return
    }
    const frameMatch = path.match(/^\/api\/simulations\/([^/]+)\/frames\/(\d+)$/)
    if (frameMatch) {
      const index = Number(frameMatch[2])
      const body = frameBodies[index]
      if (frameMatch[1] !== SIMULATION_ID || body === undefined) {
        response.writeHead(404, { 'content-type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({ detail: 'frame not found' }))
        return
      }
      response.writeHead(200, {
        'cache-control': 'no-store',
        'content-type': 'application/json; charset=utf-8',
      })
      response.end(body)
      return
    }
    let filePath =
      path === '/' || path === '/twin-map-harness.html' ? indexPath : resolve(buildRoot, `.${path}`)
    const rel = relative(buildRoot, filePath)
    if (rel.startsWith(`..${sep}`) || rel === '..') {
      response.writeHead(400)
      response.end('invalid path')
      return
    }
    try {
      const info = await stat(filePath)
      if (!info.isFile()) throw new Error('not a file')
    } catch {
      if (extname(path)) {
        response.writeHead(404)
        response.end('not found')
        return
      }
      filePath = indexPath
    }
    try {
      const bytes = await readFile(filePath)
      response.writeHead(200, {
        'cache-control': 'no-store',
        'content-type': MIME_TYPES[extname(filePath)] ?? 'application/octet-stream',
      })
      response.end(bytes)
    } catch (error) {
      response.writeHead(500)
      response.end(error.message)
    }
  })
}

async function listen(server) {
  await new Promise((resolvePromise, rejectPromise) => {
    server.once('error', rejectPromise)
    server.listen(0, '127.0.0.1', resolvePromise)
  })
  return server.address().port
}

async function closeServer(server) {
  if (!server?.listening) return
  server.closeAllConnections?.()
  await new Promise((resolvePromise) => server.close(resolvePromise))
}

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    process.platform === 'win32'
      ? join(
          process.env.PROGRAMFILES ?? 'C:\\Program Files',
          'Google\\Chrome\\Application\\chrome.exe'
        )
      : null,
    process.platform === 'win32'
      ? join(
          process.env['PROGRAMFILES(X86)'] ?? 'C:\\Program Files (x86)',
          'Google\\Chrome\\Application\\chrome.exe'
        )
      : null,
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean)
  const executable = candidates.find((candidate) => existsSync(candidate))
  assert(executable, 'Chrome/Chromium was not found; set CHROME_PATH in G5 CI', EXIT.BLOCKED)
  return executable
}

async function waitForDevToolsPort(userDataDir, child, timeoutMs) {
  const portFile = join(userDataDir, 'DevToolsActivePort')
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new PerformanceError(
        `browser exited before DevTools was ready (${child.exitCode})`,
        EXIT.BLOCKED
      )
    try {
      const [port] = (await readFile(portFile, 'utf8')).split(/\r?\n/)
      if (port && /^\d+$/.test(port)) return Number(port)
    } catch {
      // Chrome has not written the port yet.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
  }
  throw new PerformanceError('timed out waiting for Chrome DevToolsActivePort', EXIT.BLOCKED)
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl, { maxPayload: 64 * 1024 * 1024 })
    this.nextId = 1
    this.pending = new Map()
    this.listeners = new Map()
  }
  async open(timeoutMs) {
    await new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(
        () =>
          rejectPromise(new PerformanceError('timed out opening DevTools WebSocket', EXIT.BLOCKED)),
        timeoutMs
      )
      this.socket.once('open', () => {
        clearTimeout(timer)
        resolvePromise()
      })
      this.socket.once('error', (error) => {
        clearTimeout(timer)
        rejectPromise(
          new PerformanceError(`DevTools WebSocket failed: ${error.message}`, EXIT.BLOCKED)
        )
      })
    })
    this.socket.on('message', (data) => {
      const message = JSON.parse(data.toString())
      if (message.id) {
        const pending = this.pending.get(message.id)
        if (!pending) return
        this.pending.delete(message.id)
        clearTimeout(pending.timer)
        if (message.error)
          pending.reject(
            new PerformanceError(`CDP ${pending.method} failed: ${message.error.message}`)
          )
        else pending.resolve(message.result)
        return
      }
      for (const listener of this.listeners.get(message.method) ?? [])
        listener(message.params ?? {})
    })
  }
  send(method, params = {}, timeoutMs = 30_000) {
    const id = this.nextId++
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        rejectPromise(new PerformanceError(`CDP ${method} timed out`))
      }, timeoutMs)
      this.pending.set(id, { method, resolve: resolvePromise, reject: rejectPromise, timer })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }
  on(method, listener) {
    const listeners = this.listeners.get(method) ?? []
    listeners.push(listener)
    this.listeners.set(method, listeners)
  }
  close() {
    this.socket.terminate()
  }
}

async function fetchJson(url, timeoutMs = 10_000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok)
    throw new PerformanceError(`DevTools returned ${response.status}: ${url}`, EXIT.BLOCKED)
  return response.json()
}

async function launchBrowser(executable, timeoutMs) {
  const userDataDir = await mkdtemp(join(os.tmpdir(), 'hydraulic-perf-chrome-'))
  const child = spawn(
    executable,
    [
      '--headless=new',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--hide-scrollbars',
      '--mute-audio',
      '--no-first-run',
      '--no-default-browser-check',
      '--remote-debugging-address=127.0.0.1',
      '--remote-debugging-port=0',
      `--user-data-dir=${userDataDir}`,
      'about:blank',
    ],
    { stdio: 'ignore', windowsHide: true }
  )
  try {
    const port = await waitForDevToolsPort(userDataDir, child, timeoutMs)
    const targets = await fetchJson(`http://127.0.0.1:${port}/json/list`)
    const page = targets.find((target) => target.type === 'page' && target.webSocketDebuggerUrl)
    assert(page, 'Chrome DevTools exposed no page target', EXIT.BLOCKED)
    const client = new CdpClient(page.webSocketDebuggerUrl)
    await client.open(timeoutMs)
    const version = await client.send('Browser.getVersion', {}, timeoutMs)
    return { child, client, userDataDir, port, version }
  } catch (error) {
    child.kill('SIGKILL')
    await removeWithRetry(userDataDir)
    throw error
  }
}

async function removeWithRetry(path, attempts = 6) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await rm(path, { recursive: true, force: true })
      return
    } catch (error) {
      if (attempt === attempts - 1) throw error
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250 * (attempt + 1)))
    }
  }
}

async function closeBrowser(browser) {
  try {
    browser?.client?.close()
  } catch {
    // Best effort; the process is terminated below.
  }
  if (browser?.child && browser.child.exitCode === null) {
    browser.child.kill('SIGTERM')
    await Promise.race([
      new Promise((resolvePromise) => browser.child.once('exit', resolvePromise)),
      new Promise((resolvePromise) => setTimeout(resolvePromise, 3_000)),
    ])
    if (browser.child.exitCode === null) browser.child.kill('SIGKILL')
    if (browser.child.exitCode === null) {
      await Promise.race([
        new Promise((resolvePromise) => browser.child.once('exit', resolvePromise)),
        new Promise((resolvePromise) => setTimeout(resolvePromise, 3_000)),
      ])
    }
  }
  if (browser?.userDataDir) await removeWithRetry(browser.userDataDir)
}

async function evaluate(client, expression, timeoutMs) {
  const response = await client.send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true },
    timeoutMs
  )
  if (response.exceptionDetails) {
    throw new PerformanceError(
      `browser evaluation failed: ${response.exceptionDetails.exception?.description ?? response.exceptionDetails.text}`
    )
  }
  return response.result?.value
}

async function runBrowserWorkload({
  executable,
  baseUrl,
  durationSeconds,
  maxCacheBytes,
  viewport,
  timeoutMs,
}) {
  const browser = await launchBrowser(executable, timeoutMs)
  const errors = []
  try {
    const client = browser.client
    await client.send('Page.enable', {}, timeoutMs)
    await client.send('Runtime.enable', {}, timeoutMs)
    await client.send('Log.enable', {}, timeoutMs)
    await client.send('Network.enable', {}, timeoutMs)
    await client.send(
      'Emulation.setDeviceMetricsOverride',
      {
        width: viewport.width,
        height: viewport.height,
        deviceScaleFactor: 1,
        mobile: false,
      },
      timeoutMs
    )
    client.on('Runtime.exceptionThrown', (event) => {
      errors.push(
        event.exceptionDetails?.exception?.description ??
          event.exceptionDetails?.text ??
          'unknown exception'
      )
    })
    client.on('Log.entryAdded', (event) => {
      if (event.entry?.level === 'error') {
        errors.push(`${event.entry.text ?? 'console error'} ${event.entry.url ?? ''}`.trim())
      }
    })
    client.on('Network.responseReceived', (event) => {
      if ((event.response?.status ?? 0) >= 400) {
        errors.push(`http ${event.response.status}: ${event.response.url}`)
      }
    })
    const url = `${baseUrl}/twin-map-harness.html?durationSeconds=${durationSeconds}&maxCacheBytes=${maxCacheBytes}`
    const navigation = await client.send('Page.navigate', { url }, timeoutMs)
    assert(!navigation.errorText, `navigation failed: ${navigation.errorText}`)
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const ready = await evaluate(
        client,
        'document.readyState !== "loading" && Boolean(window.__HYDRAULIC_TWIN_PERF__)',
        5_000
      )
      if (ready) break
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 50))
    }
    const measurements = await evaluate(
      client,
      'window.__HYDRAULIC_TWIN_PERF__',
      Math.max(120_000, durationSeconds * 2_000 + 60_000)
    )
    assert(
      measurements && typeof measurements === 'object',
      `browser performance result is missing: ${JSON.stringify(errors)}`
    )
    return { browser, measurements, errors }
  } catch (error) {
    await closeBrowser(browser)
    throw error
  }
}

async function buildHarness() {
  const outDir = await mkdtemp(join(os.tmpdir(), 'hydraulic-perf-build-'))
  await build({
    root: FRONTEND_ROOT,
    configFile: false,
    logLevel: 'error',
    resolve: { alias: { '@': resolve(FRONTEND_ROOT, 'src') } },
    build: {
      outDir,
      emptyOutDir: true,
      target: 'es2022',
      minify: 'esbuild',
      sourcemap: false,
      rollupOptions: { input: HARNESS_HTML },
    },
  })
  const candidates = [
    join(outDir, 'twin-map-harness.html'),
    join(outDir, 'performance', 'twin-map-harness.html'),
  ]
  const indexPath = candidates.find((candidate) => existsSync(candidate))
  assert(indexPath, 'Vite did not emit twin-map-harness.html')
  const files = []
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) await walk(path)
      else files.push({ path, bytes: await fileSha256(path) })
    }
  }
  await walk(outDir)
  return {
    outDir,
    indexPath,
    build_root: outDir,
    assets: Object.fromEntries(
      files.map((entry) => [relative(outDir, entry.path).replaceAll('\\', '/'), entry.bytes])
    ),
    engine_sha256: sha256(
      files
        .map((entry) => `${relative(outDir, entry.path)}:${entry.bytes}`)
        .sort()
        .join('|')
    ),
  }
}

function evaluateThresholds(fixture, observed) {
  const thresholds = fixture.thresholds
  const failures = []
  if (!(observed.actual_execution_seconds >= observed.requested_duration_seconds * 0.95))
    failures.push('actual_execution_seconds')
  if (!(observed.presented_fps >= thresholds.min_presented_fps)) failures.push('presented_fps')
  if (!(observed.request_animation_frame_p95_ms <= thresholds.max_request_animation_frame_p95_ms))
    failures.push('request_animation_frame_p95_ms')
  if (!(observed.long_task_max_ms <= thresholds.max_long_task_ms)) failures.push('long_task_max_ms')
  if (!(observed.cache_peak_bytes <= thresholds.max_cache_bytes)) failures.push('cache_peak_bytes')
  if (!(observed.api_p95_ms <= thresholds.max_map_api_p95_ms)) failures.push('api_p95_ms')
  const durationRatio = observed.requested_duration_seconds / 60
  const minimumSamples = Math.max(1, Math.floor(thresholds.min_sample_count * durationRatio))
  if (!(observed.render_sample_count >= minimumSamples)) failures.push('render_sample_count')
  if (!observed.canvas?.present || !observed.canvas?.context_present)
    failures.push('canvas_renderer')
  if (!(observed.visible_fraction > 0 && observed.visible_fraction < 1))
    failures.push('viewport_culling')
  if (observed.cache_frame_count !== EXPECTED_FRAMES) failures.push('cache_frame_count')
  return failures
}

async function writeReport(path, report) {
  await mkdir(dirname(path), { recursive: true })
  const artifactHash = sha256(canonicalJson(report))
  report.evidence_sha256 = artifactHash
  report.artifact_sha256 = artifactHash
  const text = `${JSON.stringify(report, null, 2)}\n`
  const temporary = `${path}.tmp`
  await writeFile(temporary, text, 'utf8')
  await rename(temporary, path)
  const persisted = JSON.parse(await readFile(path, 'utf8'))
  const {
    artifact_sha256: persistedArtifactHash,
    evidence_sha256: persistedEvidenceHash,
    ...payload
  } = persisted
  assert(
    persistedArtifactHash === artifactHash &&
      persistedEvidenceHash === artifactHash &&
      sha256(canonicalJson(payload)) === artifactHash,
    'persisted performance artifact hash self-check failed'
  )
  const fileHash = sha256(text)
  await writeFile(`${path}.sha256`, `${fileHash}  ${path.split(/[\\/]/).pop()}\n`, 'ascii')
  return artifactHash
}

async function main() {
  const argv = process.argv.slice(2)
  const options = parseArgs(argv)
  const runId =
    options.runId ||
    `${new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, 'Z')}-${git(['rev-parse', '--short=8', 'HEAD']) || 'unknown'}`
  const environment = detectEnvironment()
  const dependencyLockSha256 = await fileSha256(resolve(FRONTEND_ROOT, 'package-lock.json'))
  const report = {
    schema: REPORT_SCHEMA,
    artifact_hash_scope: 'canonical_json_without_evidence_and_artifact_hash_fields',
    run_id: runId,
    utc_timestamp: utcNow(),
    commit: git(['rev-parse', 'HEAD']),
    dirty_state: Boolean(git(['status', '--porcelain'])),
    command: [process.execPath, fileURLToPath(import.meta.url), ...argv],
    exit_code: EXIT.INTERNAL,
    status: 'RUNNING',
    acceptance_scope: options.diagnostic ? 'DIAGNOSTIC_ONLY' : 'PRD_G5',
    prd_gate_status: 'NOT_RUN',
    validate_only: options.validateOnly,
    diagnostic_non_target: options.diagnostic,
    duration_seconds: options.durationSeconds,
    fixture: null,
    environment: {
      ...environment,
      python_version: null,
      dependency_lock_sha256: dependencyLockSha256,
      image_digests: [],
      database_extensions: [],
      alembic_revision: null,
      spatial_backend: null,
      solver_id: 'py_mw',
      solver_version: 'g5-frontend-fixture-v1',
      engine_sha256: null,
    },
    browser: null,
    build: null,
    measurements: null,
    failures: [],
    result_summary: null,
  }
  let buildInfo
  let server
  let browser
  let artifactHash = null
  try {
    const loaded = await loadFixture(options.fixture)
    report.fixture = {
      fixture_id: loaded.fixture.fixture_id,
      fixture_path: loaded.fixturePath,
      fixture_sha256: loaded.fixtureSha256,
      input_counts: EXPECTED_COUNTS,
      frame_count: EXPECTED_FRAMES,
      data_source: loaded.fixture.data_source,
    }
    if (options.validateOnly) {
      report.status = 'VALIDATED'
      report.prd_gate_status = 'NOT_RUN'
      report.exit_code = EXIT.PASS
      report.result_summary =
        'Fixture, hashes, scale, duration and production build entry validated.'
    } else if (!options.diagnostic && !environment.target_environment_match) {
      report.status = 'BLOCKED'
      report.prd_gate_status = 'NOT_RUN'
      report.exit_code = EXIT.BLOCKED
      report.failures = ['target_environment_mismatch']
      report.result_summary =
        'G5 was not run because the host does not match the frozen target environment.'
    } else {
      const started = performance.now()
      buildInfo = await buildHarness()
      const mapBody = JSON.stringify(buildMapData())
      const frameBodies = Array.from({ length: EXPECTED_FRAMES }, (_, index) =>
        JSON.stringify(buildFrame(index))
      )
      const executable = findChrome()
      server = createPerformanceServer({
        buildDir: buildInfo.outDir,
        indexPath: buildInfo.indexPath,
        mapBody,
        frameBodies,
      })
      const port = await listen(server)
      const baseUrl = `http://127.0.0.1:${port}`
      const browserRun = await runBrowserWorkload({
        executable,
        baseUrl,
        durationSeconds: options.durationSeconds,
        maxCacheBytes: loaded.fixture.thresholds.max_cache_bytes,
        viewport: loaded.fixture.viewport,
        timeoutMs: 120_000,
      })
      browser = browserRun.browser
      report.environment.engine_sha256 = buildInfo.engine_sha256
      report.build = {
        mode: 'production',
        index_path: buildInfo.indexPath,
        index_sha256:
          buildInfo.assets[relative(buildInfo.outDir, buildInfo.indexPath).replaceAll('\\', '/')],
        assets: buildInfo.assets,
      }
      report.browser = {
        executable,
        executable_sha256: await fileSha256(executable),
        product: browser.version.product,
        user_agent: browser.version.userAgent,
        js_version: browser.version.jsVersion,
        headless: true,
        gpu: 'disabled_software_rasterization',
        viewport: loaded.fixture.viewport,
      }
      report.measurements = browserRun.measurements
      report.failures = [
        ...evaluateThresholds(loaded.fixture, browserRun.measurements),
        ...browserRun.errors.map(() => 'browser_error'),
      ]
      if (options.diagnostic) {
        report.status = report.failures.length === 0 ? 'DIAGNOSTIC_PASS' : 'DIAGNOSTIC_FAIL'
        report.prd_gate_status = 'NOT_EVALUATED'
        report.exit_code = report.failures.length === 0 ? EXIT.PASS : EXIT.FAIL
        report.result_summary = 'Diagnostic browser workload completed; this is not G5 evidence.'
      } else {
        report.status = report.failures.length === 0 ? 'PASS' : 'FAIL'
        report.prd_gate_status = report.status
        report.exit_code = report.failures.length === 0 ? EXIT.PASS : EXIT.FAIL
        report.result_summary =
          report.status === 'PASS'
            ? 'Frozen G5 frontend browser gate passed.'
            : 'Frozen G5 frontend browser gate failed.'
      }
      report.preparation_and_measurement_seconds = (performance.now() - started) / 1000
    }
  } catch (error) {
    report.status = error.exitCode === EXIT.BLOCKED ? 'BLOCKED' : 'FAIL'
    report.prd_gate_status = options.diagnostic ? 'NOT_EVALUATED' : report.prd_gate_status
    report.exit_code = error.exitCode ?? EXIT.INTERNAL
    report.failures = [error.message]
    report.result_summary = 'Frontend performance harness did not complete.'
    report.error = { name: error.name, message: error.message, stack: error.stack }
  } finally {
    if (browser) await closeBrowser(browser)
    if (buildInfo) await rm(buildInfo.outDir, { recursive: true, force: true })
    if (server) await closeServer(server)
    artifactHash = await writeReport(options.output, report)
  }
  process.stdout.write(`${JSON.stringify(report)}\n`)
  process.stdout.write(`FRONTEND_PERFORMANCE_STATUS=${report.status}\n`)
  process.stdout.write(`FRONTEND_PERFORMANCE_SMOKE_STATUS=${report.status}\n`)
  process.stdout.write(`FRONTEND_PERFORMANCE_EXIT=${report.exit_code}\n`)
  process.stdout.write(`FRONTEND_PERFORMANCE_ARTIFACT_SHA256=${artifactHash}\n`)
  return report.exit_code
}

main().then(
  (exitCode) => process.exit(exitCode),
  (error) => {
    process.stdout.write(
      `${JSON.stringify({ schema: REPORT_SCHEMA, status: 'FAIL', exit_code: EXIT.INTERNAL, error: { name: error.name, message: error.message } })}\n`
    )
    process.exit(EXIT.INTERNAL)
  }
)
