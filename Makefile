.PHONY: install dev build test lint deploy clean

# ── Setup ─────────────────────────────────────────────────────────────────
install:
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	cd frontend && npm install

# ── Development ───────────────────────────────────────────────────────────
dev-backend:
	.venv/bin/flask --app backend/app.py run --host 0.0.0.0 --port 5000 --debug

dev-frontend:
	cd frontend && npm run dev

# Runs both in parallel (requires tmux or two terminals — see README)
dev:
	@echo "Start backend:  make dev-backend"
	@echo "Start frontend: make dev-frontend"

# ── Database ──────────────────────────────────────────────────────────────
db-init:
	mkdir -p data
	.venv/bin/python -c "from backend.db.connection import init_db; init_db()"

db-migrate:
	@echo "Running pending migrations..."
	@for f in migrations/*.sql; do \
		echo "Applying $$f"; \
		sqlite3 data/betting.db < $$f; \
	done

# ── ML ────────────────────────────────────────────────────────────────────
train:
	.venv/bin/python -m backend.ml.train

evaluate:
	.venv/bin/python -m backend.ml.evaluate

predict:
	.venv/bin/python -m backend.ml.predict

# ── Testing ───────────────────────────────────────────────────────────────
test:
	.venv/bin/pytest

test-fast:
	.venv/bin/pytest -m "not ml and not api"

test-cov:
	.venv/bin/pytest --cov=backend --cov-report=html

# ── Linting ───────────────────────────────────────────────────────────────
lint:
	.venv/bin/flake8 backend tests
	.venv/bin/black --check backend tests

format:
	.venv/bin/black backend tests

# ── Production build ──────────────────────────────────────────────────────
build-frontend:
	cd frontend && npm run build

deploy: build-frontend
	rsync -av frontend/dist/ /var/www/betting/frontend/dist/
	sudo systemctl restart betting-backend
	sudo systemctl reload nginx
	@echo "Deployed."

# ── Cleanup ───────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	rm -rf frontend/dist frontend/.vite htmlcov .coverage
