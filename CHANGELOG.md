# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
