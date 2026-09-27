import {
  decodeFrameResponse,
  type FrameDecodeRequest,
  type FrameDecodeResponse,
  transferableBuffers,
} from './frameDecodeProtocol'

interface WorkerScope {
  addEventListener?: (
    type: 'message',
    listener: (event: MessageEvent<FrameDecodeRequest>) => void
  ) => void
  postMessage?: (message: FrameDecodeResponse, transfer?: Transferable[]) => void
}

const scope = globalThis as unknown as WorkerScope
const isWorker =
  typeof document === 'undefined' &&
  typeof (globalThis as unknown as { importScripts?: unknown }).importScripts === 'function'

if (isWorker && scope.addEventListener && scope.postMessage) {
  scope.addEventListener('message', (event) => {
    const response = decodeFrameResponse(event.data)
    const transfer = response.type === 'decoded' ? transferableBuffers(response.frame) : []
    scope.postMessage?.(response, transfer)
  })
}

export {}
