SHELL := /bin/bash

.PHONY: init keygen up down restart logs ps test lint format run clean

init:
	@if [ ! -f .env ]; then cp .env.example .env; echo ".env created from .env.example"; else echo ".env already exists"; fi

keygen:
	@python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

run:
	python -m app.main

test:
	pytest -q

lint:
	ruff check .

format:
	ruff format .

clean:
	docker compose down -v
