# Site Setup Research: Production Deployment

---

## Architecture Overview

### Single Server (MVP)
```
                    ┌─────────────┐
                    │  Flask/    │
    Internet ───────►│  Python   │
                    │  App      │
                    └─────────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         ┌─────────┐ ┌────────┐ ┌──────────┐
         │ SQLite  │ │ Nginx  │ │  Redis  │
         │ (data)  │ │(proxy) │ │ (cache) │
         └─────────┘ └────────┘ └──────────┘
```

### Production (Multi-User)
```
                    ┌─────────────┐
                    │  Cloudflare│ (WAF, DDoS)
                    └─────────────┘
                         │
              ┌──────────┼──────────┐
              ▼                       ▼
         ┌─────────┐             ┌─────────┐
         │  Nginx  │◄─────────►│  Flask  │
         │ (SSL)  │  (proxy)  │  (app)  │
         └─────────┘             └─────────┘
              │          ┌──────────┼──────────┐
              ▼          ▼          ▼          ▼
         ┌────────┐   ┌────────┐  ┌────────┐  ┌────────┐
         │ Postgres│   │ Redis  │  │ Celery │  │ Backup │
         │(main)  │   │(cache) │  │(tasks) │  │(s3)   │
         └────────┘   └────────┘  └────────┘  └────────┘
```

---

## Required Components

### 1. Web Server (Reverse Proxy)
- **Nginx**: Popular, efficient
- **Caddy**: Auto HTTPS
- **Traefik**: Docker-friendly

### 2. Application Server
- **Gunicorn/uvicorn**: WSGI/ASGI runner
- **Workers**: 2-4 x CPU cores

### 3. Database
- **SQLite**: Development only
- **PostgreSQL**: Production (ACID compliant)
- **Redis**: Cache, sessions, real-time

### 4. Task Queue (Background Jobs)
- **Celery**: Python native
- **RQ**: Simpler alternative

---

## Deployment Options

### Option 1: DigitalOcean Droplet (Simplest)
1. Create Ubuntu droplet
2. Install nginx, postgresql
3. Clone repo
4. Set up systemd service
5. Configure SSL (Let's Encrypt)

### Option 2: Docker (Recommended)
1. Create Droplet with Docker
2. docker-compose.yml
3. Build and run

### Option 3: Kubernetes (Scale)
- More complex
- Auto-scaling
- For when you need it

---

## Environment Setup

### Required Environment Variables
```bash
# App
FLASK_ENV=production
SECRET_KEY=<generate-secure-random>

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Security
BOOTBALL_PASSWORD=<secure-password>

# API
API_FOOTBALL_KEY=<key>
```

### Development vs Production
| Setting | Dev | Prod |
|--------|-----|------|
| DEBUG | true | false |
| LOG_LEVEL | DEBUG | INFO |
| Cache | Memory | Redis |
| Sessions | Cookie | Redis |
| Static | Local | CDN |

---

## SSL/HTTPS Setup

### Let's Encrypt (Free)
```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d yourdomain.com

# Auto-renew
sudo certbot renew --dry-run
```

### Nginx Config
```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

---

## Monitoring

### Essential Metrics
- Response time (p95, p99)
- Error rate
- Active users
- API calls remaining
- Database connections

### Tools
- **Prometheus + Grafana**: Metrics dashboard
- **Sentry**: Error tracking
- **Uptime Robot**: Health checks

### Logging
- Structure: JSON (for parsing)
- Levels: DEBUG, INFO, WARNING, ERROR
- Rotate: Daily, max 7 days

---

## Backup Strategy

### Database
```bash
# Daily PostgreSQL backup
pg_dump -U bootball bootball > backup_$(date +%Y%m%d).sql

# Retention: 7 days locally, 30 days offsite
```

### Offsite
- AWS S3
- Google Cloud Storage
- B2 (Backblaze)

---

## Checklist

### Pre-Production
- [ ] All settings in environment variables
- [ ] Database indexed properly
- [ ] Debug FALSE
- [ ] Logging configured

### Security
- [ ] SSL certificate installed
- [ ] HTTPS forced
- [ ] Rate limiting enabled
- [ ] Firewall configured (ufw/fail2ban)

### Operations
- [ ] Monitoring setup
- [ ] Backup automation
- [ ] Health check endpoint
- [ ] Log rotation

---

## References

### Deployment
- https://flask.palletsprojects.com/en/2.3.x/deploying/
- https://www.digitalocean.com/community/tutorials/how-to-set-up-flask-with-postgresql

### Security
- https://mozilla.github.io/server-side-tls/ (SSL config)

---

*Last Updated: 2026-04-12*  
*Category: Site Setup*