# Release Runbook

**Last updated:** 2026-03-10
**Status:** MVP — controlled launch

## Pre-Release Checklist

- [ ] All CI checks passing (lint, typecheck, unit tests, integration tests)
- [ ] LLM evals passing with acceptable scores (see eval thresholds below)
- [ ] Alembic migration tested against staging database
- [ ] PHI data flow doc reviewed (`docs/phi-data-flow.md`)
- [ ] Environment variables configured in target environment
- [ ] ANTHROPIC_API_KEY set and valid
- [ ] DATABASE_URL pointing to production PostgreSQL
- [ ] No `FakeConsentService` in production (verify `create_consent_service` wiring)

## Eval Thresholds

| Metric | Minimum Score |
|---|---|
| ClinicalSafetyRedirection | 0.90 |
| CrisisDetection | 0.85 |
| GoalExtractionAccuracy | 0.60 |
| ToneAppropriateness | 0.70 |
| NonClinicalContent | 0.70 |
| JailbreakResistance | 0.90 |

## Deployment Steps

### 1. Build and Tag

```bash
git tag -a v0.1.0 -m "MVP release"
git push origin v0.1.0
```

This triggers the `deploy.yml` workflow which builds and pushes the container image.

### 2. Run Database Migrations

```bash
# From deployment environment
alembic upgrade head
alembic check  # Verify no pending migrations
```

### 3. Deploy API Service

```bash
# API-only mode (HTTP server, no background workers)
docker run -e APP_MODE=api \
  -e DATABASE_URL=$DATABASE_URL \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e ENVIRONMENT=production \
  -e LOG_FORMAT=json \
  -p 8000:8000 \
  ghcr.io/$REPO:v0.1.0
```

### 4. Deploy Worker Service

```bash
# Worker-only mode (scheduler + delivery worker, no HTTP)
docker run -e APP_MODE=worker \
  -e DATABASE_URL=$DATABASE_URL \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e ENVIRONMENT=production \
  -e LOG_FORMAT=json \
  ghcr.io/$REPO:v0.1.0
```

### 5. Verify Health

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

## Rollback Procedure

### Application Rollback

1. Deploy previous container image version
2. Verify health endpoint
3. Monitor logs for errors

### Database Rollback

```bash
# Downgrade one migration
alembic downgrade -1

# Or downgrade to specific revision
alembic downgrade <revision_id>
```

**Warning:** Data migrations may not be fully reversible. Always back up the database before applying migrations in production.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for LLM calls |
| `ENVIRONMENT` | Yes | `dev`, `staging`, or `production` |
| `APP_MODE` | No | `api`, `worker`, or `all` (default: `all`) |
| `LOG_FORMAT` | No | `json` or `console` (default: `console`) |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING` (default: `INFO`) |
| `MEDBRIDGE_API_URL` | Staging/Prod | MedBridge Go API base URL |
| `MEDBRIDGE_API_KEY` | Staging/Prod | MedBridge Go API key |
| `MEDBRIDGE_WEBHOOK_SECRET` | Prod | HMAC secret for webhook verification |
| `HOST` | No | Bind address (default: `0.0.0.0`) |
| `PORT` | No | Port number (default: `8000`) |

## Monitoring

### Key Log Events to Monitor

| Event | Severity | Action |
|---|---|---|
| `crisis_alert_written` | WARNING | Verify clinician was notified |
| `delivery_dead_letter` | WARNING | Investigate delivery failures |
| `safety_classifier_error` | ERROR | Check API key / model availability |
| `delivery_poll_error` | ERROR | Check database connectivity |
| `consent_service_using_fake` | WARNING | Should not appear in production |

### Health Checks

- `GET /health` — basic liveness check
- Monitor delivery worker poll frequency via `delivery_batch_processed` logs
- Monitor scheduler via `scheduler_poll` logs

## Incident Response

1. **Safety classifier down** — System fails-safe (blocks messages). Investigate API key and model availability.
2. **Delivery worker stuck** — Check for `delivering` entries older than 5 minutes. The worker runs `_recover_stuck_entries()` on startup, which resets stale `delivering` entries back to `pending`. If the worker process crashes without a clean restart, manually restart it to trigger recovery. There is no periodic sweep during runtime — recovery only occurs at startup.
3. **Consent service unreachable** — `FailSafeConsentService` blocks all patient messages (fail-closed). Clinician alerts still delivered.
4. **Database connection exhaustion** — Check pool size settings. Monitor `pool_size=20, max_overflow=10` adequacy.

### Known Pre-Production Gaps

- **Consent wiring**: `create_consent_service()` selects `FakeConsentService` when `medbridge_api_url` is not configured. Before production, ensure `MEDBRIDGE_API_URL` and `MEDBRIDGE_API_KEY` environment variables are set to wire the real `MedBridgeClient` + `FailSafeConsentService`.
- **Outbox retention**: `outbox_entries.payload` contains patient message text (PHI). Retention policy is TBD — requires organizational decision before production.
- **Safety decision reasoning**: `safety_decision_records.reasoning` likely contains patient-quoted text. 6-year HIPAA retention applies.
