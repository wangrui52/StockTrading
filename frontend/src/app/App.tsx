import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import { DashboardPage } from '../features/dashboard/DashboardPage'
import { ReportsPage } from '../features/reports/ReportsPage'
import { ScreenerPage } from '../features/screener/ScreenerPage'
import { StockDetailPage } from '../features/stock-detail/StockDetailPage'
import { WatchlistPage } from '../features/watchlist/WatchlistPage'

import './app.css'

function Workspace() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">日线研究工作台</p>
          <h1>A 股交易辅助决策</h1>
        </div>
        <span className="data-date">仅使用已完成交易日日线</span>
      </header>

      <nav aria-label="主导航" className="main-nav">
        <NavLink to="/">行情看板</NavLink>
        <NavLink to="/screener">股票筛选</NavLink>
        <NavLink to="/watchlist">自选股</NavLink>
        <NavLink to="/reports">分析报告</NavLink>
        <NavLink to="/settings">系统设置</NavLink>
      </nav>

      <main>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/screener" element={<ScreenerPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/stocks/:market/:code" element={<StockDetailPage />} />
          <Route path="/settings" element={<section className="panel"><h2>系统设置</h2><p className="muted">同步计划与规则参数将在 P1 中提供。</p></section>} />
        </Routes>
      </main>

      <footer>本工具仅用于个人研究和信息整理，不构成投资建议。</footer>
    </div>
  )
}

export function App() {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  }))
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Workspace />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
