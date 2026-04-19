# Security Threat Model - Bootball Prediction System

**Date:** 2026-04-19
**Author:** Bootball Dev
**Version:** 0.1

---

## System Overview

Bootball is a football prediction and betting automation platform that:
1. Fetches fixture/odds data from API-Football
2. Generates ML predictions for betting markets
3. Provides web UI for predictions, betting, and tracking
4. Uses event-driven architecture for real-time updates

---

## Attack Surfaces

### 1. Web UI Endpoints (Public Attack Surface)

| Endpoint | Attack Vector | Severity | Notes |
|----------|---------------|----------|-------|
| `/` | XSS, CSRF | High | Serves HTML/JS |
| `/api/predictions` | Data exposure | Medium | Contains ML predictions |
| `/api/leagues` | Data exposure | Low | Public reference data |
| `/betting` | Unauthorized betting | Critical | Could place bets |
| `/admin` | System control | Critical | Server management |
| `/debug` | Info disclosure | Medium | Exposes internals |

### 2. Authentication

| Component | Current State | Risk |
|-----------|---------------|------|
| Basic Auth | Single shared password | Anyone with password = full access |
| Session Cookies | No expiry, no rotation | Session hijacking risk |
| API Auth | API key in .env | Key exposure via logs/git |

### 3. Event Layer (Internal)

| Component | Attack Vector | Severity |
|-----------|--------------|----------|
| Event Emission | Malformed events | Handler crashes |
| Event Handling | Replay attacks | Invalid state |
| Event Signing | No verification | Handler processes fake events |

### 4. External APIs

| API | Risk | Mitigation |
|-----|------|------------|
| API-Football | Key exposure | Use env vars, not hardcoded |
| Discord Alerts | Webhook hijacking | Secure webhook URL storage |

### 5. Database

| Risk | Severity | Mitigation |
|------|----------|------------|
| SQL Injection | Critical | Use SQLAlchemy ORM (parameterized) |
| Data exposure | High | Row-level permissions (future) |
| Backup security | Medium | Encrypt backups |

### 6. File System

| Risk | Mitigation |
|------|----------|
| Log injection | Sanitize log inputs |
| Config exposure | .env not in git |
| Static files | Validate paths |

---

## Threat Categories

### A. Injection Attacks

**SQL Injection**
- Status: ✅ Protected by SQLAlchemy ORM
- Test: Verify all queries use parameterized inputs

**XSS (Cross-Site Scripting)**
- Status: ⚠️ Not validated - user data rendered in HTML
- Attack: Malicious team names with `<script>` tags
- Test: Render `<script>alert(1)</script>` as team name

**Log Injection**
- Status: ⚠️ Not validated - logs accept raw strings
- Attack: Newlines in team names to fake log entries
- Test: Team name with `\n2026-04-19 ADMIN: CREATE USER...`

### B. Authentication Attacks

**Brute Force**
- Status: ❌ No rate limiting on auth
- Attack: Rapid password guessing
- Mitigation: Rate limit auth endpoint

**Session Hijacking**
- Status: ❌ No HttpOnly, Secure flags
- Attack: Steal session cookie via XSS
- Mitigation: Set security flags on cookies

**Credential Theft**
- Status: ⚠️ Password in env var
- Attack: Read .env via file inclusion
- Mitigation: Use secrets manager (future)

### C. Authorization Attacks

**Privilege Escalation**
- Status: ❌ Single auth level - admin or nothing
- Attack: Access `/admin` without proper authorization
- Mitigation: Role-based access control

**Horizontal Privilege Escalation**
- Status: N/A - no multi-user yet
- Future: User A accessing User B's bets

### D. Information Disclosure

**Path Traversal**
- Status: ⚠️ Static file serving could escape
- Attack: `/static/../../../etc/passwd`
- Mitigation: Validate all paths

**Stack Traces**
- Status: ❌ Debug mode may expose traces
- Attack: Trigger errors to see internals
- Mitigation: Disable debug in production

**API Structure Disclosure**
- Status: ⚠️ `/api/leagues` returns internal IDs
- Attack: Map internal structure
- Mitigation: Abstract internal IDs

### E. Denial of Service

**Rate Limiting**
- Status: ❌ No rate limiting
- Attack: Flood API endpoints
- Mitigation: Per-IP, per-user rate limits

**Resource Exhaustion**
- Status: ⚠️ No limits on DB queries
- Attack: Fetch huge result sets
- Mitigation: Add query limits

**Event Storm**
- Status: ⚠️ No handler throttling
- Attack: Rapid event emission
- Mitigation: Handler rate limits

### F. Event Layer Attacks

**Fake Event Injection**
- Status: ❌ No signature verification
- Attack: Emit fake `BET_SETTLED` events
- Mitigation: HMAC event signatures

**Replay Attack**
- Status: ❌ No replay protection
- Attack: Replay old events to corrupt state
- Mitigation: Sequence numbers + timestamps

**Event Amplification**
- Status: ⚠️ One event could trigger many
- Attack: `OddsUpdated` triggers prediction cascade
- Mitigation: Debounce/reduce event frequency

### G. API Key Attacks

**Key Exposure**
- Status: ⚠️ API key in .env
- Attack: Accidental git commit, log exposure
- Mitigation: Never log keys, audit git history

**Quota Exhaustion**
- Status: ⚠️ Basic tracking only
- Attack: Make many API calls to exhaust quota
- Mitigation: Better tracking + alerts

---

## Mitigation Priority

### P0 (Critical - Fix Now)
1. XSS prevention - sanitize all user-rendered data
2. Rate limiting on auth endpoint
3. Security headers in responses
4. SQL injection already protected ✅

### P1 (High - Fix Before Production)
1. HMAC event signatures
2. Event replay protection
3. Session security flags
4. API key audit (check git history)
5. Log injection prevention

### P2 (Medium - Fix Before Multi-User)
1. Role-based access control
2. JWT implementation
3. Per-user audit logging
4. Secrets manager integration

### P3 (Low - Future)
1. Path traversal validation
2. API response abstraction
3. Database backup encryption

---

## Testing Checklist

```bash
# Injection Tests
pytest tests/security/test_injection.py -v

# Auth Tests  
pytest tests/security/test_auth.py -v

# Rate Limit Tests
pytest tests/security/test_rate_limit.py -v

# Event Security Tests
pytest tests/security/test_event_signing.py -v
```

---

## Next Actions

1. [ ] Fix XSS in web_ui.py - sanitize team names, league names
2. [ ] Add rate limiting to all endpoints
3. [ ] Add security headers
4. [ ] Implement event signing
5. [ ] Create security test suite
