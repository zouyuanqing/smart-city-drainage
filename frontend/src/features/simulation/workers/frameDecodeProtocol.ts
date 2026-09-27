import type { CachedFrame } from '../map/frameCache'
import { encodeFrame } from '../map/frameCache'
import type { FrameFields, SimulationFrame } from '../map/types'

export interface FrameDecodeRequest {
  type: 'decode'
  request_id: string
  frame: SimulationFrame
  fields?: FrameFields
  expected_checksum?: string
}

export interface FrameDecodeSuccess {
  type: 'decoded'
  request_id: string
  frame: CachedFrame
}

export interface FrameDecodeFailure {
  type: 'error'
  request_id: string
  message: string
}

export type FrameDecodeResponse = FrameDecodeSuccess | FrameDecodeFailure

function keepFields(frame: SimulationFrame, fields?: FrameFields): SimulationFrame {
  if (!fields) return frame
  const nodes = frame.nodes
    ? {
        feature_index: frame.nodes.feature_index,
        ...(fields.nodes?.includes('depth_m') ? { depth_m: frame.nodes.depth_m } : {}),
        ...(fields.nodes?.includes('fill_ratio') ? { fill_ratio: frame.nodes.fill_ratio } : {}),
        ...(fields.nodes?.includes('surcharged') ? { surcharged: frame.nodes.surcharged } : {}),
      }
    : undefined
  const pipes = frame.pipes
    ? {
        feature_index: frame.pipes.feature_index,
        ...(fields.pipes?.includes('fill_ratio') ? { fill_ratio: frame.pipes.fill_ratio } : {}),
        ...(fields.pipes?.includes('flow_m3s') ? { flow_m3s: frame.pipes.flow_m3s } : {}),
        ...(fields.pipes?.includes('depth_m') ? { depth_m: frame.pipes.depth_m } : {}),
        ...(fields.pipes?.includes('surcharged') ? { surcharged: frame.pipes.surcharged } : {}),
      }
    : undefined
  const ponding = frame.ponding
    ? {
        feature_index: frame.ponding.feature_index,
        ...(fields.ponding?.includes('depth_m') ? { depth_m: frame.ponding.depth_m } : {}),
        ...(fields.ponding?.includes('flow_m3s') ? { flow_m3s: frame.ponding.flow_m3s } : {}),
      }
    : undefined
  return { ...frame, nodes, pipes, ponding }
}

export function decodeFrameLocally(request: FrameDecodeRequest): CachedFrame {
  const frame = keepFields(request.frame, request.fields)
  const checksum = frame.checksum ?? frame.payload_sha256
  if (request.expected_checksum && checksum !== request.expected_checksum) {
    throw new Error('frame checksum does not match the index')
  }
  return encodeFrame(`worker/${request.request_id}`, frame)
}

export function decodeFrameResponse(request: FrameDecodeRequest): FrameDecodeResponse {
  try {
    return { type: 'decoded', request_id: request.request_id, frame: decodeFrameLocally(request) }
  } catch (error) {
    return {
      type: 'error',
      request_id: request.request_id,
      message: error instanceof Error ? error.message : String(error),
    }
  }
}

export function transferableBuffers(frame: CachedFrame): ArrayBuffer[] {
  const buffers: ArrayBuffer[] = []
  for (const layer of [frame.nodes, frame.pipes, frame.ponding]) {
    for (const series of Object.values(layer)) {
      buffers.push(
        series.featureIndex.buffer as ArrayBuffer,
        series.values.buffer as ArrayBuffer,
        series.flags.buffer as ArrayBuffer
      )
    }
  }
  return buffers
}
