import { Routes, Route } from 'react-router-dom'
import SetupPage from './pages/SetupPage'
import RunningPage from './pages/RunningPage'
import ResultsPage from './pages/ResultsPage'

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>OrderForge</h1>
        <span className="subtitle">Backtest Simulator</span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SetupPage />} />
          <Route path="/running/:jobId" element={<RunningPage />} />
          <Route path="/results/:jobId" element={<ResultsPage />} />
        </Routes>
      </main>
    </div>
  )
}
