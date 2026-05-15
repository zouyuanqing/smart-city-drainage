import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import LoginPage from '../../pages/LoginPage'

const renderLoginPage = () => {
  return render(
    <BrowserRouter>
      <LoginPage />
    </BrowserRouter>
  )
}

describe('LoginPage', () => {
  it('should render login form', () => {
    renderLoginPage()
    expect(screen.getByText(/登录/i)).toBeInTheDocument()
  })

  it('should have username and password inputs', () => {
    renderLoginPage()
    const inputs = screen.getAllByRole('textbox')
    expect(inputs.length).toBeGreaterThanOrEqual(1)
  })
})
