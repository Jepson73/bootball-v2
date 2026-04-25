import { Routes, Route, Link } from 'react-router-dom'
import PredictionsPage from './pages/PredictionsPage'

export default function App() {
  return (
    <div className="app">
      <nav className="nav">
        <div className="nav-brand">Bootball</div>
        <div className="nav-links">
          <Link to="/">Predictions</Link>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<PredictionsPage />} />
        </Routes>
      </main>
    </div>
  )
}