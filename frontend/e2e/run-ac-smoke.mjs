#!/usr/bin/env node
/* global AbortSignal, clearInterval, clearTimeout, fetch, process, setInterval, setTimeout, URL */

/**
 * Dependency-light AC end-to-end smoke for the production frontend build.
 *
 * The runner serves frontend/dist plus fixture-backed API responses, launches a
 * pinned local Chrome/Chromium through the DevTools Protocol, and asserts the
 * hydraulic AC UI/API/SSE contract. It never converts a missing selector,
 * request, browser exception, or fixture into PASS.
 */

import { spawn, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import { dirname, extname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import WebSocket from 'ws'

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(SCRIPT_DIR, '..', '..')
const EXIT = { PASS: 0, FAIL: 1, BLOCKED: 2, INTERNAL: 3 }
const FIXTURE_SCHEMA = 'hydraulic-ac-e2e-v2'
const API_MANIFEST_SCHEMA = 'hydraulic-ac-e2e-api-v1'
const REPORT_SCHEMA = 'hydraulic-ac-e2e-report-v1'
const REQUIRED_BEHAVIORS = Object.freeze([
  'rainfall_edit_submit',
  'timeline_playback',
  'ab_comparison',
  'forecast_sse',
  'role_readonly',
])
const DEFAULT_TIMEOUT_MS = 120_000
const DEFAULT_SCENARIO_TIMEOUT_MS = 20_000

class E2EError extends Error {
  constructor(message, exitCode = EXIT.FAIL) {
    super(message)
    this.exitCode = exitCode
  }
}

function assert(condition, message, exitCode = EXIT.FAIL) {
  if (!condition) throw new E2EError(message, exitCode)
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string')
    return JSON.stringify(value)
  if (typeof value === 'number') {
    assert(Number.isFinite(value), 'fixture contains a non-finite number', EXIT.BLOCKED)
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(',')}}`
  }
  throw new E2EError(`unsupported JSON value: ${typeof value}`, EXIT.BLOCKED)
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
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

function utcNow() {
  return new Date().toISOString()
}

function safeFixturePath(root, rawPath, label) {
  assert(
    typeof rawPath === 'string' && rawPath && !/^[A-Za-z]:/.test(rawPath),
    `${label} must be a relative path`,
    EXIT.BLOCKED
  )
  const path = resolve(root, rawPath)
  const rel = relative(root, path)
  assert(
    rel && !rel.startsWith(`..${sep}`) && rel !== '..' && !resolve(rel).startsWith(`..${sep}`),
    `${label} escapes its fixture root`,
    EXIT.BLOCKED
  )
  return path
}

async function readJson(path, label) {
  let bytes
  try {
    bytes = await readFile(path)
  } catch (error) {
    throw new E2EError(`${label} is not readable: ${path}: ${error.message}`, EXIT.BLOCKED)
  }
  let value
  try {
    value = JSON.parse(bytes.toString('utf8'))
  } catch (error) {
    throw new E2EError(`${label} is not valid JSON: ${error.message}`, EXIT.BLOCKED)
  }
  assert(
    value && typeof value === 'object' && !Array.isArray(value),
    `${label} root must be an object`,
    EXIT.BLOCKED
  )
  return { value, bytes, sha256: sha256(bytes) }
}

function resolveCliPath(rawPath) {
  if (/^[A-Za-z]:[\\/]/.test(rawPath) || rawPath.startsWith('/')) return resolve(rawPath)
  const repoCandidate = resolve(REPO_ROOT, rawPath)
  if (existsSync(repoCandidate)) return repoCandidate
  const workspaceCandidate = resolve(REPO_ROOT, '..', rawPath)
  if (existsSync(workspaceCandidate) || rawPath.startsWith(`${REPO_ROOT.split(/[\\/]/).pop()}/`))
    return workspaceCandidate
  return repoCandidate
}

function parseArgs(argv) {
  const options = {
    timeoutMs: DEFAULT_TIMEOUT_MS,
    scenarioTimeoutMs: DEFAULT_SCENARIO_TIMEOUT_MS,
    validateOnly: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    const next = argv[index + 1]
    if (token === '--fixture') {
      assert(next, '--fixture requires a path', EXIT.BLOCKED)
      options.fixture = resolveCliPath(next)
      index += 1
    } else if (token === '--output') {
      assert(next, '--output requires a path', EXIT.BLOCKED)
      options.output = resolveCliPath(next)
      index += 1
    } else if (token === '--timeout-seconds') {
      assert(next, '--timeout-seconds requires a number', EXIT.BLOCKED)
      options.timeoutMs = Math.round(Number(next) * 1000)
      index += 1
    } else if (token === '--scenario-timeout-seconds') {
      assert(next, '--scenario-timeout-seconds requires a number', EXIT.BLOCKED)
      options.scenarioTimeoutMs = Math.round(Number(next) * 1000)
      index += 1
    } else if (token === '--run-id') {
      assert(next, '--run-id requires a value', EXIT.BLOCKED)
      options.runId = next
      index += 1
    } else if (token === '--validate-only') {
      options.validateOnly = true
    } else {
      throw new E2EError(`unknown argument: ${token}`, EXIT.BLOCKED)
    }
  }
  assert(options.fixture, '--fixture is required', EXIT.BLOCKED)
  assert(options.output, '--output is required', EXIT.BLOCKED)
  assert(
    Number.isFinite(options.timeoutMs) && options.timeoutMs > 0,
    '--timeout-seconds must be positive',
    EXIT.BLOCKED
  )
  assert(
    Number.isFinite(options.scenarioTimeoutMs) && options.scenarioTimeoutMs > 0,
    '--scenario-timeout-seconds must be positive',
    EXIT.BLOCKED
  )
  return options
}

async function loadFixture(fixturePath) {
  const fixtureFile = await readJson(fixturePath, 'AC e2e fixture')
  let fixture = fixtureFile.value
  if (fixture.extends !== undefined) {
    assert(
      typeof fixture.extends === 'string' && fixture.extends,
      'fixture.extends must be a relative fixture path',
      EXIT.BLOCKED
    )
    const basePath = safeFixturePath(dirname(fixturePath), fixture.extends, 'fixture.extends')
    const baseFile = await readJson(basePath, 'Extended AC e2e fixture')
    if (fixture.extends_sha256 !== undefined) {
      assert(
        fixture.extends_sha256 === baseFile.sha256,
        'extended AC e2e fixture SHA-256 mismatch',
        EXIT.BLOCKED
      )
    }
    fixture = { ...baseFile.value, ...fixture }
  }
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
    typeof fixture.build_dir === 'string' && fixture.build_dir,
    'fixture.build_dir is required',
    EXIT.BLOCKED
  )
  const buildDir = safeFixturePath(REPO_ROOT, fixture.build_dir, 'fixture.build_dir')
  assert(
    existsSync(join(buildDir, 'index.html')),
    `production build is missing: ${join(buildDir, 'index.html')}`,
    EXIT.BLOCKED
  )
  assert(
    Array.isArray(fixture.scenarios) && fixture.scenarios.length > 0,
    'fixture.scenarios must be non-empty',
    EXIT.BLOCKED
  )
  const scenarioIds = new Set()
  for (const scenario of fixture.scenarios) {
    assert(
      scenario && typeof scenario === 'object',
      'each scenario must be an object',
      EXIT.BLOCKED
    )
    assert(typeof scenario.id === 'string' && scenario.id, 'scenario.id is required', EXIT.BLOCKED)
    assert(!scenarioIds.has(scenario.id), `duplicate scenario id: ${scenario.id}`, EXIT.BLOCKED)
    scenarioIds.add(scenario.id)
    assert(
      typeof scenario.path === 'string' && scenario.path.startsWith('/'),
      'scenario.path must be absolute',
      EXIT.BLOCKED
    )
    assert(
      Array.isArray(scenario.steps) && scenario.steps.length > 0,
      `${scenario.id}.steps must be a non-empty behavior sequence`,
      EXIT.BLOCKED
    )
    assert(
      Array.isArray(scenario.behaviors) && scenario.behaviors.length > 0,
      `${scenario.id}.behaviors must be a non-empty array`,
      EXIT.BLOCKED
    )
    for (const behavior of scenario.behaviors) {
      assert(
        REQUIRED_BEHAVIORS.includes(behavior),
        `${scenario.id} declares unsupported behavior ${behavior}`,
        EXIT.BLOCKED
      )
    }
    for (const field of ['expected_requests', 'forbidden_requests']) {
      assert(
        Array.isArray(scenario[field] ?? []),
        `${scenario.id}.${field} must be an array`,
        EXIT.BLOCKED
      )
    }
  }

  const coveredBehaviors = new Set(
    fixture.scenarios.flatMap((scenario) => scenario.behaviors ?? [])
  )
  for (const behavior of REQUIRED_BEHAVIORS) {
    assert(
      coveredBehaviors.has(behavior),
      `fixture does not exercise required behavior ${behavior}`,
      EXIT.BLOCKED
    )
  }
  const allExpectedRequests = fixture.scenarios.flatMap(
    (scenario) => scenario.expected_requests ?? []
  )
  assert(
    allExpectedRequests.some((request) => request.query && Object.keys(request.query).length > 0),
    'fixture must assert request query parameters',
    EXIT.BLOCKED
  )
  assert(
    allExpectedRequests.some(
      (request) => request.headers && Object.keys(request.headers).length > 0
    ),
    'fixture must assert request headers',
    EXIT.BLOCKED
  )
  assert(
    allExpectedRequests.some((request) => request.body && Object.keys(request.body).length > 0),
    'fixture must assert request bodies',
    EXIT.BLOCKED
  )

  const apiRef = fixture.api_manifest
  assert(apiRef && typeof apiRef === 'object', 'fixture.api_manifest is required', EXIT.BLOCKED)
  const apiPath = safeFixturePath(dirname(fixturePath), apiRef.path, 'api_manifest.path')
  const apiFile = await readJson(apiPath, 'API manifest')
  assert(apiFile.sha256 === apiRef.sha256, 'API manifest SHA-256 mismatch', EXIT.BLOCKED)
  const apiManifest = apiFile.value
  assert(
    apiManifest.schema === API_MANIFEST_SCHEMA,
    `API manifest schema must be ${API_MANIFEST_SCHEMA}`,
    EXIT.BLOCKED
  )
  assert(
    apiManifest.responses && typeof apiManifest.responses === 'object',
    'API manifest responses are required',
    EXIT.BLOCKED
  )

  const responses = new Map()
  for (const [name, response] of Object.entries(apiManifest.responses)) {
    assert(
      typeof response.method === 'string' && response.method,
      `${name}.method is required`,
      EXIT.BLOCKED
    )
    assert(
      typeof response.path === 'string' && response.path.startsWith('/api/'),
      `${name}.path must start with /api/`,
      EXIT.BLOCKED
    )
    assert(
      Number.isInteger(response.status) && response.status >= 200 && response.status < 300,
      `${name}.status must be 2xx`,
      EXIT.BLOCKED
    )
    const bodyPath = safeFixturePath(dirname(apiPath), response.body_file, `${name}.body_file`)
    const body = await readJson(bodyPath, `${name} response body`)
    assert(
      body.sha256 === response.body_sha256,
      `${name} response body SHA-256 mismatch`,
      EXIT.BLOCKED
    )
    responses.set(`${response.method.toUpperCase()} ${response.path}`, {
      ...response,
      body: body.value,
    })
  }
  return {
    fixture,
    fixturePath,
    fixtureSha256: fixtureFile.sha256,
    buildDir,
    apiManifestPath: apiPath,
    apiManifestSha256: apiFile.sha256,
    responses,
  }
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
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean)
  const executable = candidates.find((candidate) => existsSync(candidate))
  assert(executable, 'Chrome/Chromium was not found; set CHROME_PATH in CI', EXIT.BLOCKED)
  return executable
}

function chromeVersion(executable) {
  try {
    return execFileSync(executable, ['--version'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
    }).trim()
  } catch {
    return null
  }
}

async function waitForDevToolsPort(userDataDir, child, timeoutMs) {
  const portFile = join(userDataDir, 'DevToolsActivePort')
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new E2EError(
        `browser exited before DevTools was ready (exit ${child.exitCode})`,
        EXIT.BLOCKED
      )
    try {
      const content = await readFile(portFile, 'utf8')
      const [port] = content.split(/\r?\n/)
      if (port && /^\d+$/.test(port)) return Number(port)
    } catch {
      // Chrome has not written the port yet.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
  }
  throw new E2EError('timed out waiting for Chrome DevToolsActivePort', EXIT.BLOCKED)
}

async function fetchJson(url, options = {}, timeoutMs = 10_000) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok)
    throw new E2EError(`DevTools endpoint returned HTTP ${response.status}: ${url}`, EXIT.BLOCKED)
  return response.json()
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl, { maxPayload: 32 * 1024 * 1024 })
    this.nextId = 1
    this.pending = new Map()
    this.listeners = new Map()
  }

  async open(timeoutMs) {
    await new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(
        () => rejectPromise(new E2EError('timed out opening DevTools WebSocket', EXIT.BLOCKED)),
        timeoutMs
      )
      this.socket.once('open', () => {
        clearTimeout(timer)
        resolvePromise()
      })
      this.socket.once('error', (error) => {
        clearTimeout(timer)
        rejectPromise(new E2EError(`DevTools WebSocket failed: ${error.message}`, EXIT.BLOCKED))
      })
    })
    this.socket.on('message', (data) => this.#onMessage(data.toString()))
  }

  #onMessage(raw) {
    const message = JSON.parse(raw)
    if (message.id) {
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      clearTimeout(pending.timer)
      if (message.error)
        pending.reject(new E2EError(`CDP ${pending.method} failed: ${message.error.message}`))
      else pending.resolve(message.result)
      return
    }
    for (const listener of this.listeners.get(message.method) ?? []) listener(message.params ?? {})
  }

  send(method, params = {}, timeoutMs = 30_000) {
    const id = this.nextId
    this.nextId += 1
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        rejectPromise(new E2EError(`CDP ${method} timed out`, EXIT.INTERNAL))
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

  once(method, timeoutMs) {
    return new Promise((resolvePromise, rejectPromise) => {
      const listener = (params) => {
        clearTimeout(timer)
        const listeners = this.listeners.get(method) ?? []
        this.listeners.set(
          method,
          listeners.filter((item) => item !== listener)
        )
        resolvePromise(params)
      }
      const timer = setTimeout(() => {
        const listeners = this.listeners.get(method) ?? []
        this.listeners.set(
          method,
          listeners.filter((item) => item !== listener)
        )
        rejectPromise(new E2EError(`timed out waiting for CDP event ${method}`, EXIT.INTERNAL))
      }, timeoutMs)
      this.on(method, listener)
    })
  }

  close() {
    this.socket.terminate()
  }
}

const MIME_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

function requestPath(rawUrl) {
  const url = new URL(rawUrl, 'http://127.0.0.1')
  return url.pathname
}

function createFixtureServer({ buildDir, responses }) {
  const buildRoot = resolve(buildDir)
  return createServer(async (request, response) => {
    const path = requestPath(request.url ?? '/')
    if (path === '/favicon.ico') {
      response.writeHead(204)
      response.end()
      return
    }
    if (path.startsWith('/api/')) {
      const route = responses.get(`${request.method ?? 'GET'} ${path}`)
      if (!route) {
        response.writeHead(404, { 'content-type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({ detail: `No e2e fixture for ${request.method} ${path}` }))
        return
      }
      if (route.sse) {
        response.writeHead(200, {
          'cache-control': 'no-cache',
          connection: 'keep-alive',
          'content-type': 'text/event-stream; charset=utf-8',
        })
        let sequence = 0
        let timer
        const start = () => {
          timer = setInterval(() => {
            sequence += 1
            const data = { ...(route.sse.data ?? {}) }
            if (route.sse.increment_sequence) {
              data.sequence = Number(data.sequence ?? 0) + sequence
              data.event_id = `${data.event_id ?? 'event'}-${sequence}`
            }
            response.write(
              `id: ${sequence}\nevent: ${
                route.sse.event ?? 'message'
              }\ndata: ${JSON.stringify(data)}\n\n`
            )
            if (sequence >= (route.sse.count ?? 1)) {
              clearInterval(timer)
              response.end()
            }
          }, route.sse.interval_ms ?? 25)
        }
        const initialDelay = setTimeout(start, route.sse.initial_delay_ms ?? 0)
        request.on('close', () => {
          clearTimeout(initialDelay)
          if (timer) clearInterval(timer)
        })
        return
      }
      request.resume()
      const body = JSON.stringify(route.body)
      response.writeHead(route.status, {
        'cache-control': 'no-store',
        'content-type': 'application/json; charset=utf-8',
        ...(route.headers ?? {}),
      })
      response.end(body)
      return
    }

    let filePath = resolve(buildRoot, `.${path}`)
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
      if (request.method === 'GET' && !extname(path)) filePath = join(buildRoot, 'index.html')
      else {
        response.writeHead(404)
        response.end('not found')
        return
      }
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
  if (!server.listening) return
  server.closeAllConnections?.()
  await new Promise((resolvePromise) => server.close(resolvePromise))
}

async function launchBrowser(executable, timeoutMs) {
  const userDataDir = await mkdtemp(join(os.tmpdir(), 'hydraulic-e2e-chrome-'))
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
  let port
  try {
    port = await waitForDevToolsPort(userDataDir, child, timeoutMs)
    const targets = await fetchJson(`http://127.0.0.1:${port}/json/list`)
    const page = targets.find((target) => target.type === 'page' && target.webSocketDebuggerUrl)
    assert(page, 'Chrome DevTools exposed no page target', EXIT.BLOCKED)
    const client = new CdpClient(page.webSocketDebuggerUrl)
    await client.open(timeoutMs)
    const version = await client.send('Browser.getVersion', {}, timeoutMs)
    return { child, client, userDataDir, port, version }
  } catch (error) {
    child.kill('SIGKILL')
    await rm(userDataDir, { recursive: true, force: true })
    throw error
  }
}

async function closeBrowser(browser) {
  try {
    browser.client?.close()
  } catch {
    // Best effort; the process is terminated below.
  }
  if (browser.child && browser.child.exitCode === null) {
    browser.child.kill('SIGTERM')
    const exited = await Promise.race([
      new Promise((resolvePromise) => browser.child.once('exit', () => resolvePromise(true))),
      new Promise((resolvePromise) => setTimeout(() => resolvePromise(false), 3000)),
    ])
    if (!exited) browser.child.kill('SIGKILL')
  }
  if (browser.userDataDir) await rm(browser.userDataDir, { recursive: true, force: true })
}

async function evaluate(client, expression, timeoutMs) {
  const response = await client.send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true },
    timeoutMs
  )
  if (response.exceptionDetails) {
    const message =
      response.exceptionDetails.exception?.description ?? response.exceptionDetails.text
    throw new E2EError(`browser evaluation failed: ${message}`, EXIT.FAIL)
  }
  return response.result?.value
}

async function navigate(client, url, timeoutMs) {
  const navigated = client.once('Page.frameNavigated', timeoutMs)
  const result = await client.send('Page.navigate', { url }, timeoutMs)
  if (result.errorText) throw new E2EError(`navigation failed: ${result.errorText}`)
  await navigated
  const deadline = Date.now() + timeoutMs
  let lastState = null
  while (Date.now() < deadline) {
    try {
      lastState = await evaluate(
        client,
        `({ href: location.href, readyState: document.readyState })`,
        Math.min(5_000, timeoutMs)
      )
      if (lastState?.href === url && lastState?.readyState !== 'loading') return
    } catch {
      // Execution contexts are replaced during document navigation.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50))
  }
  throw new E2EError(
    `document did not leave loading state: ${url}; lastState=${JSON.stringify(lastState)}`
  )
}

async function waitForSelector(client, selector, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastPath = null
  let lastText = ''
  let lastAlerts = []
  while (Date.now() < deadline) {
    const state = await evaluate(
      client,
      `(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        return {
          found: Boolean(element),
          path: location.pathname,
          text: document.body?.innerText?.slice(0, 500) ?? '',
          alerts: [...document.querySelectorAll('[role="alert"]')].map((item) => item.textContent?.trim()),
        };
      })()`,
      Math.min(5000, timeoutMs)
    )
    lastPath = state?.path
    lastText = state?.text ?? ''
    lastAlerts = state?.alerts ?? []
    if (state?.found) return state
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
  }
  throw new E2EError(
    `selector did not appear: ${selector}; final path=${lastPath}; text=${JSON.stringify(
      lastText
    )}; alerts=${JSON.stringify(lastAlerts)}`
  )
}

function runAssertion(assertion) {
  assert(assertion && typeof assertion === 'object', 'assertion must be an object')
  if (assertion.type === 'selector_visible') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      'selector_visible.selector is required'
    )
    return `(() => {
      const element = document.querySelector(${JSON.stringify(assertion.selector)});
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' &&
        (rect.width > 0 || rect.height > 0 || element.getClientRects().length > 0);
    })()`
  }
  if (assertion.type === 'selector_disabled' || assertion.type === 'selector_enabled') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      `${assertion.type}.selector is required`
    )
    const shouldBeDisabled = assertion.type === 'selector_disabled'
    return `(() => {
      const root = document.querySelector(${JSON.stringify(assertion.selector)});
      if (!root) return false;
      const element = root.matches('button,input,select,textarea,fieldset')
        ? root
        : root.querySelector('button,input,select,textarea,fieldset') ?? root;
      const disabled = Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true';
      return disabled === ${shouldBeDisabled};
    })()`
  }
  if (assertion.type === 'attribute_equals') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      'attribute_equals.selector is required'
    )
    assert(
      typeof assertion.name === 'string' && assertion.name,
      'attribute_equals.name is required'
    )
    assert(typeof assertion.value === 'string', 'attribute_equals.value must be a string')
    return `(document.querySelector(${JSON.stringify(assertion.selector)})?.getAttribute(${JSON.stringify(
      assertion.name
    )}) === ${JSON.stringify(assertion.value)})`
  }
  if (assertion.type === 'attribute_number_gte') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      'attribute_number_gte.selector is required'
    )
    assert(
      typeof assertion.name === 'string' && assertion.name,
      'attribute_number_gte.name is required'
    )
    assert(Number.isFinite(assertion.value), 'attribute_number_gte.value must be finite')
    return `(Number(document.querySelector(${JSON.stringify(
      assertion.selector
    )})?.getAttribute(${JSON.stringify(assertion.name)})) >= ${assertion.value})`
  }
  if (assertion.type === 'value_equals') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      'value_equals.selector is required'
    )
    assert(typeof assertion.value === 'string', 'value_equals.value must be a string')
    return `(document.querySelector(${JSON.stringify(assertion.selector)})?.value === ${JSON.stringify(
      assertion.value
    )})`
  }
  if (assertion.type === 'text_contains') {
    const texts = assertion.texts ?? [assertion.text]
    assert(
      Array.isArray(texts) && texts.length > 0 && texts.every((text) => typeof text === 'string'),
      'text_contains.text or texts is required'
    )
    return `(${JSON.stringify(texts)}.every((text) => (document.body?.innerText ?? '').includes(text)))`
  }
  if (assertion.type === 'selector_text_contains') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      'selector_text_contains.selector is required'
    )
    assert(
      typeof assertion.text === 'string' && assertion.text,
      'selector_text_contains.text is required'
    )
    return `((document.querySelector(${JSON.stringify(assertion.selector)})?.textContent ?? '').includes(${JSON.stringify(
      assertion.text
    )}))`
  }
  if (assertion.type === 'path_equals') {
    assert(typeof assertion.path === 'string' && assertion.path, 'path_equals.path is required')
    return `(location.pathname === ${JSON.stringify(assertion.path)})`
  }
  if (assertion.type === 'count_equals' || assertion.type === 'count_at_least') {
    assert(
      typeof assertion.selector === 'string' && assertion.selector,
      `${assertion.type}.selector is required`
    )
    assert(Number.isInteger(assertion.count), `${assertion.type}.count must be an integer`)
    if (assertion.type === 'count_equals') {
      return `(document.querySelectorAll(${JSON.stringify(
        assertion.selector
      )}).length === ${assertion.count})`
    }
    assert(assertion.count >= 0, 'count_at_least.count must be non-negative')
    return `(document.querySelectorAll(${JSON.stringify(
      assertion.selector
    )}).length >= ${assertion.count})`
  }
  throw new E2EError(`unsupported assertion type: ${assertion.type}`, EXIT.BLOCKED)
}

function runAction(action) {
  assert(action && typeof action === 'object', 'action must be an object')
  if (action.type === 'click') {
    assert(typeof action.selector === 'string' && action.selector, 'click.selector is required')
    return `(() => {
      const element = document.querySelector(${JSON.stringify(action.selector)});
      if (!element) return { ok: false, reason: 'missing' };
      const control = element.matches('button,input,select,textarea') ? element : element;
      element.scrollIntoView({ block: 'center', inline: 'nearest' });
      const disabled = Boolean(control.disabled) || element.getAttribute('aria-disabled') === 'true';
      if (!disabled) {
        element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        if (element.matches('.ant-select-selector')) {
          // Ant Design opens its portal on mousedown; a synthetic click would close it again.
        } else if (
          element instanceof HTMLButtonElement &&
          element.type === 'submit' &&
          element.form
        ) {
          element.form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        } else {
          element.click();
        }
      }
      return {
        ok: true,
        disabled,
        disabledAfter: Boolean(element.disabled),
        value: 'value' in element ? element.value : null,
      };
    })()`
  }
  if (action.type === 'input') {
    assert(typeof action.selector === 'string' && action.selector, 'input.selector is required')
    assert(typeof action.value === 'string', 'input.value must be a string')
    return `(() => {
      const element = document.querySelector(${JSON.stringify(action.selector)});
      if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
        return { ok: false, reason: 'not-editable' };
      }
      if (element.disabled) return { ok: true, disabled: true };
      const prototype = element instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(action.value)});
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, disabled: false, value: element.value };
    })()`
  }
  if (action.type === 'key') {
    assert(typeof action.selector === 'string' && action.selector, 'key.selector is required')
    assert(typeof action.key === 'string' && action.key, 'key.key is required')
    return `(() => {
      const element = document.querySelector(${JSON.stringify(action.selector)});
      if (!element) return { ok: false, reason: 'missing' };
      const event = new KeyboardEvent('keydown', {
        key: ${JSON.stringify(action.key)},
        code: ${JSON.stringify(action.code ?? action.key)},
        shiftKey: ${Boolean(action.shiftKey)},
        ctrlKey: ${Boolean(action.ctrlKey)},
        altKey: ${Boolean(action.altKey)},
        metaKey: ${Boolean(action.metaKey)},
        bubbles: true,
        cancelable: true,
      });
      element.dispatchEvent(event);
      return { ok: true, defaultPrevented: event.defaultPrevented };
    })()`
  }
  if (action.type === 'click_text') {
    assert(typeof action.text === 'string' && action.text, 'click_text.text is required')
    const root = action.root ?? 'body'
    return `(() => {
      const container = document.querySelector(${JSON.stringify(root)});
      if (!container) return { ok: false, reason: 'missing-root' };
      const candidates = [...container.querySelectorAll('button,[role="option"],li')];
      const element = candidates.find((candidate) => {
        const text = candidate.textContent?.trim();
        return text === ${JSON.stringify(action.text)};
      });
      if (!element) {
        return {
          ok: false,
          reason: 'missing-text',
          available: candidates.map((candidate) => candidate.textContent?.trim()).filter(Boolean),
        };
      }
      element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
      element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
      element.click();
      return { ok: true, text: element.textContent?.trim() };
    })()`
  }
  throw new E2EError(`unsupported action type: ${action.type}`, EXIT.BLOCKED)
}

async function mouseClickText(client, action, timeoutMs) {
  assert(action.type === 'mouse_click_text', 'mouseClickText received the wrong action')
  assert(typeof action.text === 'string' && action.text, 'mouse_click_text.text is required')
  const root = action.root ?? 'body'
  const target = await evaluate(
    client,
    `(() => {
      const container = document.querySelector(${JSON.stringify(root)});
      if (!container) return { found: false, reason: 'missing-root' };
      const candidates = [...container.querySelectorAll('.ant-select-item-option')];
      const element = candidates.find((candidate) => {
        const text = candidate.textContent?.trim();
        return text === ${JSON.stringify(action.text)};
      });
      if (!element) {
        return {
          found: false,
          reason: 'missing-text',
          available: candidates.map((candidate) => candidate.textContent?.trim()).filter(Boolean),
        };
      }
      element.scrollIntoView({ block: 'center', inline: 'nearest' });
      const rect = element.getBoundingClientRect();
      return { found: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`,
    timeoutMs
  )
  if (!target?.found) {
    throw new E2EError(`mouse_click_text target missing: ${canonicalJson({ action, target })}`)
  }
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
  const refreshed = await evaluate(
    client,
    `(() => {
      const container = document.querySelector(${JSON.stringify(root)});
      const element = [...(container?.querySelectorAll('.ant-select-item-option') ?? [])].find(
        (candidate) => candidate.textContent?.trim() === ${JSON.stringify(action.text)}
      );
      if (!element) return { found: false };
      const rect = element.getBoundingClientRect();
      return { found: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`,
    timeoutMs
  )
  if (!refreshed?.found) {
    throw new E2EError(`mouse_click_text target moved: ${canonicalJson({ action, root })}`)
  }
  target.x = refreshed.x
  target.y = refreshed.y
  await client.send(
    'Input.dispatchMouseEvent',
    { type: 'mouseMoved', x: target.x, y: target.y },
    timeoutMs
  )
  await client.send(
    'Input.dispatchMouseEvent',
    { type: 'mousePressed', x: target.x, y: target.y, button: 'left', buttons: 1, clickCount: 1 },
    timeoutMs
  )
  await client.send(
    'Input.dispatchMouseEvent',
    { type: 'mouseReleased', x: target.x, y: target.y, button: 'left', buttons: 0, clickCount: 1 },
    timeoutMs
  )
  return { ok: true, text: action.text, x: target.x, y: target.y }
}

function matchesSubset(actual, expected) {
  if (expected && typeof expected === 'object' && !Array.isArray(expected)) {
    if (expected.$regex) {
      return (
        typeof actual === 'string' &&
        new RegExp(expected.$regex, expected.$flags ?? '').test(actual)
      )
    }
    if (Object.prototype.hasOwnProperty.call(expected, '$exists')) {
      return (actual !== undefined) === Boolean(expected.$exists)
    }
    if (actual === undefined || actual === null || typeof actual !== 'object') return false
    return Object.entries(expected).every(([key, value]) => matchesSubset(actual[key], value))
  }
  if (Array.isArray(expected)) {
    return (
      Array.isArray(actual) &&
      actual.length === expected.length &&
      expected.every((entry, index) => matchesSubset(actual[index], entry))
    )
  }
  return actual === expected
}

function requestMatches(actual, expected) {
  return (
    String(actual.method).toUpperCase() === String(expected.method).toUpperCase() &&
    actual.path === expected.path &&
    matchesSubset(actual.query ?? {}, expected.query ?? {}) &&
    matchesSubset(actual.headers ?? {}, expected.headers ?? {}) &&
    (expected.body === undefined || matchesSubset(actual.body, expected.body))
  )
}

function captureRequest(event, baseUrl) {
  const url = new URL(event.request.url)
  if (url.origin !== new URL(baseUrl).origin || !url.pathname.startsWith('/api/')) return null
  let body = null
  if (event.request.postData) {
    try {
      body = JSON.parse(event.request.postData)
    } catch {
      body = event.request.postData
    }
  }
  return {
    method: String(event.request.method ?? 'GET').toUpperCase(),
    path: url.pathname,
    query: Object.fromEntries(
      [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b))
    ),
    headers: Object.fromEntries(
      Object.entries(event.request.headers ?? {})
        .map(([name, value]) => [name.toLowerCase(), String(value)])
        .sort(([a], [b]) => a.localeCompare(b))
    ),
    body,
  }
}

function sanitizeRequest(request) {
  const headers = { ...request.headers }
  for (const name of ['authorization', 'cookie', 'set-cookie']) {
    if (headers[name]) headers[name] = '<redacted>'
  }
  return { ...request, headers }
}

async function waitForAssertion(client, assertion, timeoutMs) {
  const expression = runAssertion(assertion)
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await evaluate(client, expression, Math.min(5_000, timeoutMs))) return true
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50))
  }
  const selector = typeof assertion.selector === 'string' ? assertion.selector : null
  const diagnostics = selector
    ? await evaluate(
        client,
        `(() => { const element = document.querySelector(${JSON.stringify(selector)}); return { found: Boolean(element), text: element?.textContent?.trim() ?? null, value: element?.value ?? null, html: element?.outerHTML?.slice(0, 500) ?? null }; })()`,
        Math.min(5_000, timeoutMs)
      )
    : null
  throw new E2EError(
    `assertion timed out: ${canonicalJson(assertion)}; diagnostics=${canonicalJson(diagnostics)}`
  )
}

async function runScenario(client, context, scenario) {
  const started = Date.now()
  const requests = []
  const errors = []
  const stepResults = []
  const previousRequestHandler = context.onRequest
  const previousErrorHandler = context.onError
  context.onRequest = (request) => {
    if (request) requests.push(request)
  }
  context.onError = (error) => errors.push(error)
  try {
    await navigate(client, `${context.baseUrl}/login`, context.scenarioTimeoutMs)
    await waitForSelector(client, 'input[type="password"]', context.scenarioTimeoutMs)
    await evaluate(
      client,
      `(() => { localStorage.clear(); for (const [key, value] of Object.entries(${JSON.stringify(
        scenario.local_storage ?? {}
      )})) localStorage.setItem(key, value); return true; })()`,
      context.scenarioTimeoutMs
    )
    const scenarioUrl = new URL(scenario.path, context.baseUrl)
    scenarioUrl.searchParams.set('ac_scenario', scenario.id)
    await navigate(client, scenarioUrl.toString(), context.scenarioTimeoutMs)

    for (const step of scenario.steps) {
      if (step.type === 'wait_for_selector') {
        assert(
          typeof step.selector === 'string' && step.selector,
          'wait_for_selector.selector is required'
        )
        await waitForSelector(client, step.selector, context.scenarioTimeoutMs)
        stepResults.push({ step, status: 'PASS' })
        continue
      }
      if (step.type === 'wait_for_text') {
        const assertion = { type: 'text_contains', texts: step.texts ?? [step.text] }
        const passed = await waitForAssertion(client, assertion, context.scenarioTimeoutMs)
        stepResults.push({ step, status: passed ? 'PASS' : 'FAIL' })
        if (!passed) throw new E2EError(`wait_for_text failed: ${canonicalJson(step)}`)
        continue
      }
      if (step.type === 'assert') {
        const passed = Boolean(
          await evaluate(client, runAssertion(step.assertion), context.scenarioTimeoutMs)
        )
        stepResults.push({ step, status: passed ? 'PASS' : 'FAIL' })
        if (!passed) throw new E2EError(`assertion failed: ${canonicalJson(step.assertion)}`)
        continue
      }
      if (step.type === 'wait_until') {
        const passed = await waitForAssertion(client, step.assertion, context.scenarioTimeoutMs)
        stepResults.push({ step, status: passed ? 'PASS' : 'FAIL' })
        if (!passed) throw new E2EError(`wait_until failed: ${canonicalJson(step.assertion)}`)
        continue
      }
      if (step.type === 'sleep') {
        assert(
          Number.isFinite(step.seconds) && step.seconds >= 0 && step.seconds <= 5,
          'sleep.seconds must be between 0 and 5'
        )
        await new Promise((resolvePromise) => setTimeout(resolvePromise, step.seconds * 1_000))
        stepResults.push({ step, status: 'PASS' })
        continue
      }
      if (step.type === 'mouse_click_text') {
        const result = await mouseClickText(client, step, context.scenarioTimeoutMs)
        stepResults.push({ step, status: 'PASS', result })
        continue
      }
      const result = await evaluate(client, runAction(step), context.scenarioTimeoutMs)
      const passed = result && result.ok !== false && !result.reason
      stepResults.push({ step, status: passed ? 'PASS' : 'FAIL', result })
      if (!passed) throw new E2EError(`action failed: ${canonicalJson(step)}: ${result?.reason}`)
    }

    const expectedRequests = scenario.expected_requests ?? []
    const requestDeadline = Date.now() + context.scenarioTimeoutMs
    let missingRequests = expectedRequests.filter(
      (expected) => !requests.some((request) => requestMatches(request, expected))
    )
    while (missingRequests.length > 0 && Date.now() < requestDeadline) {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 50))
      missingRequests = expectedRequests.filter(
        (expected) => !requests.some((request) => requestMatches(request, expected))
      )
    }
    if (missingRequests.length > 0) {
      throw new E2EError(`missing API requests: ${canonicalJson(missingRequests)}`)
    }
    const forbiddenRequests = (scenario.forbidden_requests ?? []).filter((forbidden) =>
      requests.some((request) => requestMatches(request, forbidden))
    )
    if (forbiddenRequests.length > 0) {
      throw new E2EError(`forbidden API requests observed: ${canonicalJson(forbiddenRequests)}`)
    }
    if (scenario.require_no_browser_errors !== false && errors.length > 0) {
      throw new E2EError(`browser errors: ${JSON.stringify(errors)}`)
    }
    return {
      id: scenario.id,
      acceptance_criteria: scenario.acceptance_criteria,
      behaviors: scenario.behaviors,
      path: scenario.path,
      status: 'PASS',
      duration_ms: Date.now() - started,
      requests: requests.map(sanitizeRequest),
      expected_requests: expectedRequests,
      forbidden_requests: scenario.forbidden_requests ?? [],
      steps: stepResults,
      browser_errors: errors,
    }
  } catch (error) {
    return {
      id: scenario.id,
      acceptance_criteria: scenario.acceptance_criteria,
      behaviors: scenario.behaviors,
      path: scenario.path,
      status: 'FAIL',
      duration_ms: Date.now() - started,
      requests: requests.map(sanitizeRequest),
      expected_requests: scenario.expected_requests ?? [],
      forbidden_requests: scenario.forbidden_requests ?? [],
      missing_requests: (scenario.expected_requests ?? []).filter(
        (expected) => !requests.some((request) => requestMatches(request, expected))
      ),
      steps: stepResults,
      browser_errors: errors,
      error: { name: error.name, message: error.message },
    }
  } finally {
    context.onRequest = previousRequestHandler
    context.onError = previousErrorHandler
  }
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
    'persisted e2e artifact hash self-check failed'
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
  const report = {
    schema: REPORT_SCHEMA,
    run_id: runId,
    utc_timestamp: utcNow(),
    command: [process.execPath, fileURLToPath(import.meta.url), ...argv],
    commit: git(['rev-parse', 'HEAD']),
    dirty_state: Boolean(git(['status', '--porcelain'])),
    status: 'RUNNING',
    exit_code: EXIT.INTERNAL,
    browser_executed: false,
    validate_only: options.validateOnly,
    fixture: null,
    build: null,
    environment: {
      os: `${os.platform()} ${os.release()} ${os.arch()}`,
      cpu_count: os.availableParallelism?.() ?? os.cpus().length,
      ram_bytes: os.totalmem(),
      node_version: process.version,
      git_commit: git(['rev-parse', 'HEAD']),
      git_dirty: Boolean(git(['status', '--porcelain'])),
    },
    browser: null,
    scenarios: [],
  }
  let browser
  let server
  let artifactHash = null
  try {
    const loaded = await loadFixture(options.fixture)
    report.fixture = {
      fixture_id: loaded.fixture.fixture_id,
      path: options.fixture,
      sha256: loaded.fixtureSha256,
      api_manifest_path: loaded.apiManifestPath,
      api_manifest_sha256: loaded.apiManifestSha256,
    }
    const indexBytes = await readFile(join(loaded.buildDir, 'index.html'))
    report.build = {
      directory: loaded.buildDir,
      index_sha256: sha256(indexBytes),
      production_build_present: true,
    }
    if (options.validateOnly) {
      report.status = 'VALIDATED'
      report.exit_code = EXIT.PASS
    } else {
      const chrome = findChrome()
      server = createFixtureServer({
        buildDir: loaded.buildDir,
        responses: loaded.responses,
      })
      const port = await listen(server)
      const baseUrl = `http://127.0.0.1:${port}`
      browser = await launchBrowser(chrome, options.timeoutMs)
      report.browser = {
        executable: chrome,
        version_string: chromeVersion(chrome),
        product: browser.version.product,
        user_agent: browser.version.userAgent,
        js_version: browser.version.jsVersion,
        devtools_port: browser.port,
      }
      runtimeContext.baseUrl = baseUrl
      runtimeContext.scenarioTimeoutMs = options.scenarioTimeoutMs
      runtimeContext.onRequest = null
      runtimeContext.onError = null
      await browser.client.send('Page.enable')
      await browser.client.send('Runtime.enable')
      await browser.client.send('Log.enable')
      await browser.client.send('Network.enable')
      await browser.client.send('Network.setCacheDisabled', { cacheDisabled: true })
      await browser.client.send('Fetch.enable', {
        patterns: [{ urlPattern: '*basemaps.cartocdn.com*' }],
      })
      browser.client.on('Network.requestWillBeSent', (event) => {
        const request = captureRequest(event, runtimeContext.baseUrl)
        if (request) runtimeContext.onRequest?.(request)
      })
      browser.client.on('Network.responseReceived', (event) => {
        if (event.response?.status >= 400) {
          runtimeContext.onError?.(`http ${event.response.status}: ${event.response.url}`)
        }
      })
      browser.client.on('Fetch.requestPaused', (event) => {
        void browser.client
          .send('Fetch.fulfillRequest', {
            requestId: event.requestId,
            responseCode: 200,
            responseHeaders: [
              { name: 'content-type', value: 'image/png' },
              { name: 'cache-control', value: 'no-store' },
            ],
            body: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
          })
          .catch(() => undefined)
      })
      await browser.client.send('Emulation.setDeviceMetricsOverride', {
        width: loaded.fixture.viewport?.width ?? 1920,
        height: loaded.fixture.viewport?.height ?? 1080,
        deviceScaleFactor: 1,
        mobile: false,
      })
      browser.client.on('Runtime.exceptionThrown', (event) => {
        runtimeContext.onError?.(
          `exception: ${event.exceptionDetails?.exception?.description ?? event.exceptionDetails?.text ?? 'unknown'}`
        )
      })
      browser.client.on('Log.entryAdded', (event) => {
        if (event.entry?.level !== 'error') return
        const text = event.entry.text ?? ''
        if (
          text.includes('ERR_CONNECTION_CLOSED') &&
          (event.entry.url ?? '').includes('/api/forecast/events')
        ) {
          return
        }
        runtimeContext.onError?.(`console: ${text}`)
      })
      for (const scenario of loaded.fixture.scenarios) {
        report.scenarios.push(await runScenario(browser.client, runtimeContext, scenario))
      }
      report.browser_executed = true
      const failed = report.scenarios.filter((scenario) => scenario.status !== 'PASS')
      report.status = failed.length === 0 ? 'PASS' : 'FAIL'
      report.exit_code = failed.length === 0 ? EXIT.PASS : EXIT.FAIL
    }
  } catch (error) {
    report.status = error.exitCode === EXIT.BLOCKED ? 'BLOCKED' : 'FAIL'
    report.exit_code = error.exitCode ?? EXIT.INTERNAL
    report.error = { name: error.name, message: error.message, stack: error.stack }
  } finally {
    if (browser) await closeBrowser(browser)
    if (server) await closeServer(server)
    artifactHash = await writeReport(options.output, report)
  }
  process.stdout.write(`${JSON.stringify(report)}\n`)
  process.stdout.write(`AC_E2E_STATUS=${report.status}\n`)
  process.stdout.write(`AC_E2E_EXIT=${report.exit_code}\n`)
  process.stdout.write(`AC_E2E_ARTIFACT_SHA256=${artifactHash}\n`)
  return report.exit_code
}

// Defined before main so the fixture server can call the currently active
// scenario request/error handlers without a module-level mutable singleton.
const runtimeContext = {
  baseUrl: null,
  scenarioTimeoutMs: DEFAULT_SCENARIO_TIMEOUT_MS,
  onRequest: null,
  onError: null,
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
