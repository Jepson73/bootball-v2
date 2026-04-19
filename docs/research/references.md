# Research References

Organized by topic for easy lookup.

---

## +EV Betting Strategies

### Core Concepts
| URL | Description |
|----|-------------|
| https://sportsmedia101.com/news/2026/04/what-is-ev-betting-a-complete-guide-to-positive-expected-value-in-sports-betting | +EV Betting Complete Guide |
| https://medium.com/@omnimahui/profitable-england-premier-league-betting-strategy-a-full-end-to-end-experiment-2a53b32ba16d | +107% ROI LightGBM experiment |
| https://wagerbase.io/blog/how-wagerbase-ensemble-prediction-engine-works | xG-Elo Ensemble |
| https://export.arxiv.org/pdf/2303.06021v3.pdf | Calibration > Accuracy (arXiv) |
| https://www.wne.uw.edu.pl/download_file/813/494 | XGBoost vs Bookmakers |
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | +EV Strategies for Beginners |
| https://ibebet.com/how-to-find-value-bets/ | CLV & EV Explained |
| https://probwin.com/guides/value-betting-only-concept-that-matters-profitable-betting/ | Value Betting Formula |
| https://blog.betcommand.ai/what-is-value-betting-the-expected-value-equation-that-separates-long-term-winners-from-everyone-else | EV Strategy 2026 |
| https://betherosports.com/blog/what-are-value-bets | +EV Guide |
| https://www.theprophound.com/blog/ev-betting-guide-2025/ | EV Betting Guide |
| https://valuebets.net/blogs/value-betting-guide | Value Betting Manual |

### Kelly Criterion
| URL | Description |
|----|-------------|
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | Kelly sizing explained |
| https://ibebet.com/how-to-find-value-bets/ | Bankroll management |

### Kelly Criterion
| URL | Description |
|----|-------------|
| https://wagerproof.ghost.io/positive-ev-betting-strategies-beginners/ | Kelly sizing explained |
| https://ibebet.com/how-to-find-value-bets/ | Bankroll management |

---

## Platform Architecture

### Sportsbook Architecture
| URL | Source |
|----|--------|
| https://betforge.io/blog/sportsbook-platform-architecture-cto-guide | BetForge CTO Guide |
| https://docs.aws.amazon.com/architecture-diagrams/latest/sports-betting-architecture/sports-betting-architecture.html | AWS Architecture |
| https://yourweb3guy.com/2026/02/11/how-we-build-a-platform-like-stake-com/ | Stake.com Architecture |
| https://www.sportsfirst.net/post/how-to-build-a-legal-sports-betting-app-development-in-2026 | Legal Betting App |

### Database Design
| URL | Source |
|----|--------|
| https://stackoverflow.com/questions/1689989/database-design-for-betting-community | StackOverflow: DB Design |

### Security
| URL | Source |
|----|--------|
| https://betforge.io/blog/sportsbook-platform-architecture-cto-guide | Security & Compliance |
| https://www.sportsfirst.net/post/how-to-build-a-legal-sports-betting-app-development-in-2026 | KYC, AML, Security |

---

## Community Features

### Social Betting
| URL | Source |
|----|--------|
| https://www.bettoredge.com/post/bettoredge-social-feed-how-to-follow-and-engage | BettorEdge Social |
| https://www.bettoredge.com/post/how-social-features-drive-betting-community-growth | Social Features |
| https://www.bettoredge.com/post/how-leaderboards-build-betting-communities | Leaderboards |
| https://yukaichou.com/advanced-gamification/how-to-design-effective-leaderboards-boosting-motivation-and-engagement/ | Leaderboard Design |

### Gamification
| URL | Source |
|----|---------|
| https://www.promoteproject.com/article/203167/gamification-in-betting-designing-leaderboards-achievements-and-loyalty-programs | Gamification in Betting |

---

## Technical Implementation

### Market Difficulty
| URL | Description |
|----|-------------|
| https://smartbettingstats.com/btts-value-betting-strategy/ | BTTS backtest +37.84 units |
| https://kingspredict.com/blog/how-over-2-5-btts-and-correct-score-markets-actually-work | Market selection |
| https://soccertips.ai/betting-guides/both-teams-to-score-btts-betting-guide/ | League BTTS rates |
| https://kcpredict.com/blog/2025/12/02/the-most-profitable-football-betting-markets-for-beginners/ | Most profitable markets |

### League Analysis
| URL | Description |
|----|-------------|
| https://fcstats.com - Global leagues | Over/Under by league |
| https://footymetrics.com/statistics/goals | Over 2.5 League Rankings |
| https://www.performanceodds.com/how-to-guides/first-half-stats-power-guide | First half analysis |
| https://footystats.org/usa/mls | MLS over/under stats |

### Halftime Markets
| URL | Description |
|----|-------------|
| https://www.performanceodds.com/betting-tricks/first-half-football-stats | First half patterns |
| https://www.ontheballbets.com/betting-guides/football/betting-markets/both-teams-score-half | BTTS 1H/2H both halves |

### Corners & Cards (Advanced Markets)
| URL | Description |
|----|-------------|
| https://performanceodds.com/football-stats-trends | Stats hub (goals, corners, cards) |
| https://thepuntlab.com/set-piece-efficiency-corner-kick-edge | Corner edges |
| https://thewagertheorem.com/football-leagues-corner-betting | Best corner leagues |
| https://statshub.com/betting-academy/betting-on-corners | UK league data |
| https://blog.20bet.com/betting-guide/small-markets-betting-guide-corners-cards-props | Cards strategy |

### Stack Recommendations
| Component | Technology |
|-----------|------------|
| Backend | Python (FastAPI), Node.js, or Go |
| Database | PostgreSQL, Redis |
| Real-time | WebSocket, Kafka |
| Auth | JWT, bcrypt/argon2 |
| API Rate Limiting | Redis + token bucket |

### Security Requirements
- [ ] bcrypt password hashing
- [ ] JWT with expiry
- [ ] CSRF tokens
- [ ] Rate limiting
- [ ] Input sanitization
- [ ] Audit logging
- [ ] HTTPS only
- [ ] DDoS protection (Cloudflare)

---

## Security References

### Authentication
| URL | Description |
|----|-------------|
| https://cheatsheets.owasp.org/cheatsheets/Authentication_Cheat_Sheet/ | OWASP Auth Guide |
| https://jwt.io/ | JWT library |
| https://passlib.readthedocs.io/ | Password hashing |

### OWASP
| URL | Description |
|----|-------------|
| https://owasp.org/www-project-top-ten/ | Top 10 Vulnerabilities |
| https://cheatsheetseries.cheat.sh/ | Security Cheat Sheets |

---

## Deployment References

### Flask Deployment
| URL | Description |
|----|-------------|
| https://flask.palletsprojects.com/en/2.3.x/deploying/ | Flask Official |
| https://www.digitalocean.com/community/tutorials/how-to-set-up-flask-with-postgresql | DO PostgreSQL |

### SSL
| URL | Description |
|----|-------------|
| https://mozilla.github.io/server-side-tls/ | Mozilla SSL Config |

---

## Chat/Communication References

### WebSocket
| URL | Description |
|----|-------------|
| https://flask-socketio.readthedocs.io/ | Flask-SocketIO |
| https://socket.io/ | Socket.IO |

### Real-Time
| URL | Description |
|----|-------------|
| https://ably.com/topic/websockets-vs-sse | WebSockets vs SSE |

---

## Sports Data Providers

| Provider | Type |
|----------|------|
| API-Football | Odds, fixtures, results |
| Sportradar | Live odds (enterprise) |
| OddsJam | +EV opportunities |
| BetRadar | Enterprise odds |

---

## Market/Competition Tools

| URL | Purpose |
|----|---------|
| https://www.bettoredge.com | Social betting + leaderboards |
| https://www.pinnacle.com | Sharp lines |
| https://www.oddsjam.com | Odds comparison, +EV |

---

## Research Papers & Books

- "Scarne's Complete Guide to Gambling" - Classic gambling math
- "Halnev's Sports Betting" - Model building
- "Pinnacle's Betting Resources" - Sharp betting education

---

*Last Updated: 2026-04-12*