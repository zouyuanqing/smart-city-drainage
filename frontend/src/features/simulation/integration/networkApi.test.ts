import { describe, expect, it, vi } from 'vitest'
import { canAccessRoute } from '@/auth/routeAccess'
import { listHydraulicNetworks } from './networkApi'

vi.mock('@/services/api', () => ({
  default: {
    get: vi.fn(),
  },
}))

import api from '@/services/api'

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const PARAMETER_SET_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'

describe('simulation integration network boundary', () => {
  it('normalizes the backend network envelope and keeps revision identity', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: NETWORK_ID,
            name: ' downtown ',
            status: 'active',
            current_revision_id: REVISION_ID,
            current_revision: { parameter_set_id: PARAMETER_SET_ID },
            bounds: [121.3, 31.15, 121.6, 31.32],
            counts: { nodes: 2 },
            simulation_ready: true,
          },
        ],
        next_cursor: null,
      },
    } as never)

    await expect(listHydraulicNetworks()).resolves.toEqual({
      items: [
        expect.objectContaining({
          id: NETWORK_ID,
          network_revision_id: REVISION_ID,
          parameter_set_id: PARAMETER_SET_ID,
          simulation_ready: true,
        }),
      ],
      nextCursor: null,
    })
  })

  it('does not make a viewer a writer', () => {
    expect(canAccessRoute('/simulation', 'viewer')).toBe(true)
    expect(canAccessRoute('/settings', 'viewer')).toBe(false)
  })
})
