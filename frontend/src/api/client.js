const API_BASE = '/api'

export async function fetchPredictions({ days = 7, league = '', market = 'all' } = {}) {
  const params = new URLSearchParams({ days: days.toString() })
  if (league) params.append('league', league)
  if (market !== 'all') params.append('market', market)
  
  const res = await fetch(`${API_BASE}/predictions?${params}`)
  if (!res.ok) throw new Error(`Failed to fetch predictions: ${res.statusText}`)
  return res.json()
}

export async function fetchLeagues() {
  const res = await fetch(`${API_BASE}/leagues`)
  if (!res.ok) throw new Error(`Failed to fetch leagues: ${res.statusText}`)
  return res.json()
}