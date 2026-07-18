# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-XX-XX

### Added

- Official Docker image and `Dockerfile` (multi-stage, non-root) for running mock
  servers in containers.
- `docker-compose.yml` example that serves a mounted spec (defaults to the Morpheus mock).
- `PHANTOM_API_HOST` and `PHANTOM_API_PORT` environment variables for `serve`, `record`,
  and `replay` (the image binds to `0.0.0.0:3000` by default).
- CI now builds the Docker image; releases publish it to Docker Hub.

### Changed

- Raised the maximum spec size to 64 MB to support large dereferenced real-world specs.

## [1.0.0] - 2026-XX-XX

### Added

- Parse OpenAPI 3.x and Swagger 2.0 specs into mock servers.
- Parse JSON files into auto-generated CRUD endpoints.
- Parse Postman Collection v2.1 into mock routes.
- Schema-aware fake data generation via Faker (format-, pattern-, and name-aware).
- Custom delay simulation per request (global or via `X-Mock-Delay` / `?__delay`).
- Error injection via `X-Mock-Status` header or `?__status` query parameter.
- Record & replay mode (proxy real APIs, store responses, serve offline).
- Hot reload on spec file changes (`--watch`).
- Admin dashboard endpoints (`/__admin/routes`, `/__admin/stats`, `/__admin/reset`).
- FastAPI-powered server with async support.
- `serve`, `record`, `replay`, `routes`, and `validate` CLI commands.
