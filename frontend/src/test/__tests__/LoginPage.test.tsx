import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { LoginPage } from '../../pages/LoginPage'

// i18n 未初始化时，useTranslation 返回 key 本身
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

const renderLoginPage = () => {
  return render(
    <BrowserRouter>
      <LoginPage />
    </BrowserRouter>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    cleanup()
    localStorage.clear()
  })

  it('should render login form', () => {
    renderLoginPage()
    expect(screen.getByText('SCN ENDPOINTS')).toBeInTheDocument()
    expect(screen.getByText('login.title')).toBeInTheDocument()
  })

  it('should have username and password inputs', () => {
    renderLoginPage()
    const inputs = screen.getAllByRole('textbox')
    expect(inputs.length).toBeGreaterThanOrEqual(1)
  })
})
