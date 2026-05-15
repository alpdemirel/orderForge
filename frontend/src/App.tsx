import { Routes, Route, Link } from 'react-router-dom'
import SetupPage from './pages/SetupPage'
import RunningPage from './pages/RunningPage'
import ResultsPage from './pages/ResultsPage'
import HistoryPage from './pages/HistoryPage'

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title-link">
          <h1>OrderForge</h1>
          <span className="subtitle">Backtest Simulator</span>
        </Link>
        <nav className="app-nav">
          <Link to="/" className="app-nav-link">Setup</Link>
          <Link to="/history" className="app-nav-link">History</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SetupPage />} />
          <Route path="/running/:jobId" element={<RunningPage />} />
          <Route path="/results/:jobId" element={<ResultsPage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Routes>
      </main>
    </div>
  )
}
