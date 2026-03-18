.DEFAULT_GOAL := help

COMPOSE := docker compose

CYAN  := \033[36m
RESET := \033[0m

.PHONY: help
help:  ## Show available commands
	@echo "$(CYAN)Available commands:$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "$(CYAN)%-15s$(RESET) %s\n", $$1, $$2}'

## ---------- Init ----------

YALLMP_REPO := git@github.com:Ingeniator/yallmp.git
CHECKR_REPO := git@github.com:Ingeniator/checkr.git
LLOGR_REPO  := git@github.com:Ingeniator/llogr.git

.PHONY: init
init:  ## Clone sub-projects (skip if already present)
	@[ -d yallmp ] || git clone $(YALLMP_REPO) yallmp
	@[ -d checkr ] || git clone $(CHECKR_REPO) checkr
	@[ -d llogr ]  || git clone $(LLOGR_REPO) llogr
	@echo "$(CYAN)All projects ready.$(RESET)"

## ---------- Docker Compose ----------

.PHONY: up
up:  ## Start all services
	$(COMPOSE) up -d --build

.PHONY: down
down:  ## Stop and remove all services
	$(COMPOSE) down

.PHONY: down-v
down-v:  ## Stop and remove all services including volumes
	$(COMPOSE) down -v

.PHONY: restart
restart:  ## Restart all services
	$(COMPOSE) restart

.PHONY: logs
logs:  ## Show logs for all services (follow)
	$(COMPOSE) logs -f

.PHONY: ps
ps:  ## Show running services
	$(COMPOSE) ps

## ---------- Individual services ----------

.PHONY: up-yallmp
up-yallmp:  ## Start yallmp only
	$(COMPOSE) up -d --build yallmp

.PHONY: up-checkr
up-checkr:  ## Start checkr only
	$(COMPOSE) up -d --build checkr

.PHONY: up-llogr
up-llogr:  ## Start llogr with minio
	$(COMPOSE) up -d --build minio createbucket llogr

.PHONY: up-minio
up-minio:  ## Start minio only
	$(COMPOSE) up -d minio createbucket

.PHONY: up-gateway
up-gateway:  ## Start nginx gateway only
	$(COMPOSE) up -d gateway

## ---------- Logs ----------

.PHONY: logs-yallmp
logs-yallmp:  ## Show yallmp logs
	$(COMPOSE) logs -f yallmp

.PHONY: logs-checkr
logs-checkr:  ## Show checkr logs
	$(COMPOSE) logs -f checkr

.PHONY: logs-llogr
logs-llogr:  ## Show llogr logs
	$(COMPOSE) logs -f llogr

## ---------- Build ----------

.PHONY: build
build:  ## Build all images without starting
	$(COMPOSE) build
