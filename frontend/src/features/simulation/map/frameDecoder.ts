import type { CachedFrame } from './frameCache'
import { encodeFrame } from './frameCache'
import type { FrameFields, SimulationFrame } from './types'
import {
  decodeFrameLocally,
  type FrameDecodeRequest,
  type FrameDecodeResponse,
} from '../workers/frameDecodeProtocol'

export interface FrameDecoderOptions {
  useWorker?: boolean
  maxPending?: number
}

interface PendingDecode {
  request: FrameDecodeRequest
  resolve: (frame: CachedFrame) => void
  reject: (error: Error) => void
}

/** Worker-backed decoder with a deterministic main-thread fallback. */
export class FrameDecoder {
  private readonly useWorker: boolean
  private readonly maxPending: number
  private worker: Worker | null = null
  private sequence = 0
  private readonly pending = new Map<string, PendingDecode>()

  constructor(options: FrameDecoderOptions = {}) {
    this.useWorker = options.useWorker ?? true
    this.maxPending = Math.max(1, options.maxPending ?? 8)
  }

  get pendingCount(): number {
    return this.pending.size
  }

  decode(
    frame: SimulationFrame,
    fields?: FrameFields,
    expectedChecksum?: string
  ): Promise<CachedFrame> {
    if (this.pending.size >= this.maxPending) {
      return Promise.reject(new Error('Frame decoder queue is full'))
    }
    const requestId = `frame-${++this.sequence}`
    const request: FrameDecodeRequest = {
      type: 'decode',
      request_id: requestId,
      frame,
      ...(fields ? { fields } : {}),
      ...(expectedChecksum ? { expected_checksum: expectedChecksum } : {}),
    }
    if (!this.useWorker || typeof Worker === 'undefined') return this.decodeLocally(request)
    return new Promise<CachedFrame>((resolve, reject) => {
      this.pending.set(requestId, { request, resolve, reject })
      try {
        const worker = this.ensureWorker()
        worker.postMessage(request)
      } catch {
        this.pending.delete(requestId)
        this.decodeLocally(request).then(resolve, reject)
      }
    })
  }

  dispose(): void {
    this.worker?.terminate()
    this.worker = null
    for (const pending of this.pending.values()) {
      pending.reject(new Error('FrameDecoder disposed'))
    }
    this.pending.clear()
  }

  private decodeLocally(request: FrameDecodeRequest): Promise<CachedFrame> {
    try {
      return Promise.resolve(decodeFrameLocally(request))
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error(String(error)))
    }
  }

  private ensureWorker(): Worker {
    if (this.worker) return this.worker
    this.worker = new Worker(new URL('../workers/frameDecode.worker.ts', import.meta.url), {
      type: 'module',
    })
    this.worker.addEventListener('message', (event: MessageEvent<FrameDecodeResponse>) =>
      this.handleMessage(event.data)
    )
    this.worker.addEventListener('error', (event) => this.handleWorkerError(event))
    return this.worker
  }

  private handleMessage(response: FrameDecodeResponse): void {
    const pending = this.pending.get(response.request_id)
    if (!pending) return
    this.pending.delete(response.request_id)
    if (response.type === 'error') pending.reject(new Error(response.message))
    else pending.resolve(response.frame)
  }

  private handleWorkerError(event: ErrorEvent): void {
    this.worker?.terminate()
    this.worker = null
    const workerError = new Error(event.message || 'Frame decode worker failed')
    for (const [requestId, pending] of this.pending) {
      this.pending.delete(requestId)
      try {
        pending.resolve(decodeFrameLocally(pending.request))
      } catch (fallbackError) {
        pending.reject(
          fallbackError instanceof Error
            ? new Error(`${workerError.message}; fallback failed: ${fallbackError.message}`)
            : workerError
        )
      }
    }
  }
}

export function decodeFrameOnMainThread(frame: SimulationFrame): CachedFrame {
  return encodeFrame('main-thread', frame)
}
