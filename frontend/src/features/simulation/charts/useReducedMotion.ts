import { useEffect, useState } from 'react'

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

function mediaQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  return window.matchMedia(REDUCED_MOTION_QUERY)
}

/** Respects the OS preference unless the caller supplies an explicit override. */
export function useReducedMotion(override?: boolean): boolean {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(
    () => override ?? mediaQuery()?.matches ?? false
  )

  useEffect(() => {
    if (override !== undefined) return

    const query = mediaQuery()
    if (!query) return

    const update = () => setPrefersReducedMotion(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [override])

  return override ?? prefersReducedMotion
}
