import { useState, useEffect } from 'react'
import { fetchPredictions, fetchLeagues } from '../api/client'

const MARKET_LABELS = {
  h2h: 'Match Outcome',
  btts: 'Both Teams Score',
  ou25: 'Over 2.5 Goals',
  ou15: 'Over 1.5 Goals',
}

function MarketBadge({ market }) {
  return (
    <span className={`market-badge market-${market}`}>
      {MARKET_LABELS[market] || market}
    </span>
  )
}

function SweetSpotBadge() {
  return (
    <span className="sweet-spot-badge" title="High value at long odds">
      ★ Sweet Spot
    </span>
  )
}

function EVIndicator({ ev }) {
  const isPositive = ev > 0
  const pct = Math.round(ev * 100)
  return (
    <span className={`ev-indicator ${isPositive ? 'positive' : 'negative'}`}>
      {isPositive ? '+' : ''}{pct}% EV
    </span>
  )
}

function ConfidenceBadge({ confidence }) {
  const level = confidence?.level || 'LOW'
  return (
    <span className={`confidence-badge confidence-${level.toLowerCase()}`}>
      {level} confidence
    </span>
  )
}

function FixtureCard({ pred }) {
  const isSweetSpot = pred.ev_positive && pred.odds >= 3.5
  
  return (
    <div className="fixture-card">
      <div className="fixture-header">
        <div className="league-info">
          {pred.league_flag && (
            <img src={pred.league_flag} alt="" className="league-flag" />
          )}
          <span className="league-name">{pred.league_name}</span>
        </div>
        <span className="fixture-date">{pred.date}</span>
      </div>
      
      <div className="teams-row">
        <div className="team home-team">
          {pred.home_logo && <img src={pred.home_logo} alt="" className="team-logo" />}
          <span className="team-name">{pred.home_name}</span>
        </div>
        <div className="vs">vs</div>
        <div className="team away-team">
          {pred.away_logo && <img src={pred.away_logo} alt="" className="team-logo" />}
          <span className="team-name">{pred.away_name}</span>
        </div>
      </div>
      
      <div className="prediction-row">
        <div className="pick-info">
          <MarketBadge market={pred.market} />
          <span className="pick">{pred.pick}</span>
        </div>
        
        <div className="prob-info">
          <span className="prob-label">Our Prob:</span>
          <span className="prob-value">{Math.round(pred.calibrated_prob * 100)}%</span>
        </div>
        
        <div className="odds-info">
          <span className="odds-label">Odds:</span>
          <span className="odds-value">{pred.odds?.toFixed(2)}</span>
        </div>
        
        <div className="ev-info">
          <EVIndicator ev={pred.ev} />
        </div>
      </div>
      
      <div className="footer-row">
        <ConfidenceBadge confidence={pred.confidence} />
        {isSweetSpot && <SweetSpotBadge />}
      </div>
    </div>
  )
}

export default function PredictionsPage() {
  const [predictions, setPredictions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filters, setFilters] = useState({
    days: 7,
    market: 'all',
    league: '',
  })

  useEffect(() => {
    loadPredictions()
  }, [filters])

  async function loadPredictions() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchPredictions(filters)
      setPredictions(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function handleFilterChange(key, value) {
    setFilters(f => ({ ...f, [key]: value }))
  }

  return (
    <div className="predictions-page">
      <header className="page-header">
        <h1>Predictions</h1>
        <div className="filters">
          <label>
            Days:
            <select 
              value={filters.days} 
              onChange={e => handleFilterChange('days', e.target.value)}
            >
              <option value={1}>1 day</option>
              <option value={3}>3 days</option>
              <option value={7}>7 days</option>
              <option value={14}>14 days</option>
            </select>
          </label>
          
          <label>
            Market:
            <select 
              value={filters.market} 
              onChange={e => handleFilterChange('market', e.target.value)}
            >
              <option value="all">All Markets</option>
              <option value="h2h">Match Outcome</option>
              <option value="btts">Both Teams Score</option>
              <option value="ou25">Over 2.5</option>
              <option value="ou15">Over 1.5</option>
            </select>
          </label>
        </div>
      </header>

      {loading && <div className="loading">Loading predictions...</div>}
      
      {error && <div className="error">Error: {error}</div>}
      
      {!loading && !error && predictions.length === 0 && (
        <div className="empty">No predictions found</div>
      )}

      <div className="predictions-grid">
        {predictions.map((pred, idx) => (
          <FixtureCard key={`${pred.fixture_id}-${pred.market}-${idx}`} pred={pred} />
        ))}
      </div>
    </div>
  )
}