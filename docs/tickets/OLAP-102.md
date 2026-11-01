# OLAP-102 · Run the source Postgres database locally in Docker

**Epic:** E1 — Environment & Scaffolding
**Estimate:** 15–20 min · **Depends on:** OLAP-101 · **Blocks:** OLAP-201 and OLAP-401

## Context

Every later ingestion step needs a running Postgres to read from. In this project it plays the "production OLTP database" that the analytics side copies from. It should start with one command, keep its data across restarts, and keep credentials out of git.

## What to do

1. Add `docker-compose.yml` at the repo root with a single Postgres service.
   - Use a **specific** image version (major version 16 or 17), not `latest`. Pinning versions is a habit worth having in data work: a surprise major-version upgrade in the source can change behavior.
   - Expose the standard Postgres port to your machine.
   - Store the data in a **named volume** so it survives `docker compose down`.
   - Give the container a fixed, readable name.
2. Don't hardcode credentials in the compose file.
   - Read the user, password and database name from a `.env` file.
   - Commit a `.env.example` with placeholder values. The real `.env` is already gitignored.
   - Pick a database name that reads as "the app's production database", such as `ecommerce`.
3. Add a **healthcheck** so Docker can tell when Postgres is actually ready to accept connections, not just when the container has started. Later scripts will depend on that difference.

## Acceptance criteria

- [ ] `docker compose up -d` starts the container, and `docker compose ps` shows it as **healthy**.
- [ ] You can connect from outside the container with `psql`, DBeaver, or `docker exec … psql`, and run `select version();`.
- [ ] Data persists: create a scratch table, run `docker compose down` then `up -d`, and the table is still there. Drop it afterwards.
- [ ] Neither `docker-compose.yml` nor any committed file contains a real password.
- [ ] `.env.example` exists, and copying it to `.env` is enough for anyone to start the database.

## Hints and gotchas

- **Port conflict:** if Postgres is already installed natively on your Windows machine, port 5432 is taken. Either stop that service or map the container to a different host port. Write down which one you chose, because the ingestion scripts will need it.
- Docker Compose reads `.env` from the project root automatically for `${VAR}` substitution in the compose file. That's a different mechanism from the `env_file:` key, which passes variables into the container. Figure out which one you need here. You might need both, or just one.
- The official image runs any scripts in `/docker-entrypoint-initdb.d/` **only on first start with an empty volume**. Don't use this yet. Keep it in mind for OLAP-201, where it will matter (and possibly confuse you).
- To fully reset the database later: `docker compose down -v`. The `-v` deletes the named volume.

## Out of scope

- Tables and seed data (OLAP-201 and OLAP-202).
- Replication settings such as `wal_level=logical`. That's what real CDC would need, which this project only covers as a concept.
- pgAdmin or other extra containers.

## Questions you should be able to answer afterwards

1. What's the difference between `docker compose down` and `docker compose down -v`, and when would you use each?
2. Why does the healthcheck matter if the container already shows as "running"?
3. In a real company, would the analytics team connect to the primary production database or to something else? (Think read replicas. It connects to the outline's "why not query production directly" question.)
