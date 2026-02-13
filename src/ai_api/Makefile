.PHONY: dev-up dev-down prod-up prod-down

# Start the dev API container with hot reload.
dev-up:
	docker compose -f docker-compose.dev.yml up --build -d

# Stop the dev API container.
dev-down:
	docker compose -f docker-compose.dev.yml down

# Start the prod-like API container.
prod-up:
	docker compose -f docker-compose.prod.yml up --build -d

# Stop the prod-like API container.
prod-down:
	docker compose -f docker-compose.prod.yml down
