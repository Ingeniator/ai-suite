HOST        ?= http://localhost:8888
USERS       ?= 50
RATE        ?= 5
DURATION    ?= 2m
WORKERS     ?= 4
PROFILE     ?= load

.PHONY: install run smoke load stress spike soak headless docker docker-down single-service

install:
	pip install -r requirements.txt

## ─── Local (headless) ───────────────────────────────────────────────

smoke:
	locust -f locustfile.py --host $(HOST) --headless -u 1 -r 1 -t 30s

load:
	locust -f locustfile.py --host $(HOST) --headless -u 50 -r 5 -t 2m

stress:
	locust -f locustfile.py --host $(HOST) --headless -u 200 -r 10 -t 5m

spike:
	LOAD_PROFILE=spike locust -f locustfile.py,profiles.py --host $(HOST) --headless

soak:
	LOAD_PROFILE=soak locust -f locustfile.py,profiles.py --host $(HOST) --headless

headless:
	locust -f locustfile.py --host $(HOST) --headless -u $(USERS) -r $(RATE) -t $(DURATION)

## ─── Web UI ─────────────────────────────────────────────────────────

run:
	locust -f locustfile.py --host $(HOST)

## ─── Single service ─────────────────────────────────────────────────

single-service:
	@echo "Usage: make single-service SVC=test_yallmp.py"
	locust -f $(SVC) --host $(HOST) --headless -u $(USERS) -r $(RATE) -t $(DURATION)

## ─── Docker (distributed) ───────────────────────────────────────────

docker:
	docker compose -f ../docker-compose.yaml -f docker-compose.locust.yaml \
		up locust-master locust-worker --scale locust-worker=$(WORKERS) -d
	@echo "Locust Web UI: http://localhost:8089"

docker-down:
	docker compose -f ../docker-compose.yaml -f docker-compose.locust.yaml \
		down locust-master locust-worker
