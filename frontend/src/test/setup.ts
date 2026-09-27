import '@testing-library/jest-dom'
import { afterEach, beforeEach } from 'vitest'

interface StorageLike {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
  removeItem: (key: string) => void
  clear: () => void
  key?: (index: number) => string | null
  readonly length?: number
}

function createMemoryStorage(): StorageLike {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
    get length() {
      return values.size
    },
  }
}

beforeEach(() => {
  const current = globalThis.localStorage as Partial<StorageLike> | undefined
  if (!current || typeof current.clear !== 'function') {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: createMemoryStorage(),
    })
  }
})

afterEach(() => {
  const current = globalThis.localStorage as Partial<StorageLike> | undefined
  if (current && typeof current.clear === 'function') current.clear()
})
