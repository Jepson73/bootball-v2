# Security Research: Multi-User Betting Platform

---

## Authentication

### Password Hashing
- **NEVER store plain text passwords**
- Use **bcrypt** or **argon2**
- Salt each password
- Work factor: 10-12 (CPU vs cost tradeoff)

```python
# Python bcrypt example
import bcrypt

# Hash
password = b"password123"
hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12))

# Verify
if bcrypt.checkpw(password, hashed):
    print("Match!")
```

### JWT Tokens
- Short expiry (15-60 minutes)
- Refresh tokens with longer expiry
- Store in httpOnly, secure cookies

```python
# JWT Payload
{
    "user_id": 123,
    "exp": 1713000000,  # expiry timestamp
    "type": "access"   # or "refresh"
}
```

---

## Session Management

### Secure Cookie Settings
| Setting | Value | Purpose |
|---------|-------|---------|
| httpOnly | TRUE | Prevent XSS access |
| secure | TRUE | HTTPS only |
| sameSite | "strict" | CSRF prevention |
| max-age | 86400 | 24 hours |

### Session Storage
- Redis for fast access
- PostgreSQL for persistence
- Include: user_id, created, last_activity, ip

---

## CSRF Protection

### Implementation
1. Generate token per session
2. Include in forms (hidden field)
3. Validate on submit

```html
<form method="POST">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <!-- form fields -->
</form>
```

---

## Rate Limiting

### Login Protection
- 5 attempts per 15 minutes → 15 minute lockout
- 10 attempts → 1 hour lockout
- Use Redis for distributed tracking

### API Rate Limiting
- 100 requests/hour (authenticated)
- 20 requests/hour (anonymous)
- Return 429 Too Many Requests

---

## Input Validation

### SQL Injection Prevention
```python
# WRONG - SQL injection vulnerable
query = f"SELECT * FROM users WHERE id = {user_id}"

# CORRECT - Parameterized
query = "SELECT * FROM users WHERE id = :id"
session.execute(query, {"id": user_id})
```

### XSS Prevention
```python
# Sanitize HTML
import html
safe_content = html.escape(user_input)
```

---

## Audit Logging

### Log Everything Important
| Event | What to Log |
|-------|-------------|
| Login success | user_id, timestamp, IP |
| Login failed | user_id, timestamp, IP, attempt |
| Bet placed | user_id, fixture, stake, odds |
| Bet settled | user_id, fixture, result, profit |
| Password change | user_id, timestamp |
| Admin action | admin_id, action, target |

### Log Format (JSON)
```json
{
    "timestamp": "2026-04-12T10:00:00Z",
    "event": "bet_placed",
    "user_id": 123,
    "fixture_id": 456,
    "stake": 100.00,
    "odds": 2.50,
    "ip": "192.168.1.1",
    "user_agent": "Mozilla/5.0..."
}
```

---

## Security Checklist

### Authentication
- [ ] bcrypt password hashing (work factor 10+)
- [ ] JWT with expiry
- [ ] Secure cookie settings
- [ ] Password reset tokens (one-time, expiring)

### Authorization
- [ ] Role-based access (admin vs user)
- [ ] Session validation per request
- [ ] API key management

### Protection
- [ ] Rate limiting (login + API)
- [ ] CSRF tokens
- [ ] Input sanitization
- [ ] SQL injection prevention

### Monitoring
- [ ] Audit logging
- [ ] Failed login alerts
- [ ] Unusual activity detection
- [ ] Account lockout

---

## References

### Auth Security
- https://cheatsheets.owasp.org/cheatsheets/Authentication_Cheat_Sheet/
- https://jwt.io/ (JWT library)
- https://passlib.readthedocs.io/ (password hashing)

### OWASP
- https://owasp.org/www-project-top-ten/
- https://cheatsheetseries.cheat.sh/ (security cheatsheets)

---

*Last Updated: 2026-04-12*
*Category: Security*