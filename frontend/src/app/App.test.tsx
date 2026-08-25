import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { App } from './App'

describe('App', () => {
  it('renders the trading workspace instead of a marketing placeholder', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'A 股交易辅助决策' })).toBeInTheDocument()
    expect(screen.getByText('尚无有效数据批次')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '同步数据' })).toBeEnabled()
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '行情看板' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: '股票筛选' })).toHaveAttribute('href', '/screener')
    expect(screen.getByText('本工具仅用于个人研究和信息整理，不构成投资建议。')).toBeInTheDocument()
  })
})
