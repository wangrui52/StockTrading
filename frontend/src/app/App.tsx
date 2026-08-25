import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import './app.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

function DashboardEmptyState() {
  return (
    <section className="empty-state" aria-labelledby="empty-title">
      <p className="eyebrow">数据状态</p>
      <h2 id="empty-title">尚无有效数据批次</h2>
      <p>同步完成后，这里会展示指数、市场概览、候选股和自选异动。</p>
      <button type="button">同步数据</button>
    </section>
  )
}

function Workspace() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">日线研究工作台</p>
          <h1>A 股交易辅助决策</h1>
        </div>
        <span className="data-date">最新完成交易日：--</span>
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
          <Route path="*" element={<DashboardEmptyState />} />
        </Routes>
      </main>

      <footer>本工具仅用于个人研究和信息整理，不构成投资建议。</footer>
    </div>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Workspace />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
