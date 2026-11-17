# CoBuilt Shared PostgreSQL Integration Plan (Development Only)

## Approved direction
- Target architecture: Option A, one shared PostgreSQL database.
- Target region: Oregon.
- Development work only. No production services, production databases, webhooks, tokens, or deployments may be changed without separate explicit approval.
- Existing live services and databases remain operational and untouched during development.

## Current known environment
- Concrete bot: Python Telegram Bot (PTB), synchronous psycopg2 access; current Render service runs in Oregon.
- Cement Stock bot: Aiogram 3, async SQLAlchemy/asyncpg; current Render service runs in Frankfurt.
- Existing databases are separate Render PostgreSQL 18 instances: concrete-db (Oregon) and cement-db (Frankfurt).
- A new development database in Oregon is the proposed target for integration testing. It has not been provisioned.

## Target design
1. Use a new development PostgreSQL database in Oregon; do not repoint either live bot.
2. Keep the two Telegram bot tokens, bot identities, command flows, user experiences, and authorization models separate.
3. Isolate application data using dedicated schemas (preferred) or collision-free table prefixes. Do not rename or drop existing tables as part of initial integration.
4. Preserve Cement Stock's existing ledger, receipts, adjustments, settings, management recipients, users, and audit logs.
5. Preserve Concrete Logistics jobs, trucks, trips, and user data.
6. Map people by Telegram ID only where the business rules explicitly support it. Do not assume application user primary keys are interchangeable.
7. Keep manual cement consumption available.
8. Store the cement factor and calculated cement quantity on each linked trip/consumption record so later factor changes cannot alter historical deductions.
9. Make trip creation and the corresponding cement ledger deduction atomic in the shared database: either both commit or neither commits.
10. Enforce sufficient stock within the transaction; do not permit negative stock.
11. Enforce idempotency with a unique external source reference (source system + concrete trip ID). Retries must not create a second deduction.
12. Send Telegram notifications only after the database transaction commits. Notification failure must not reverse a committed transaction; it should be retryable.
13. Replace destructive deletion of linked trips with an audited void/correction workflow and compensating stock ledger entry. Preserve history.

## Runtime
The two bot frameworks have different event-loop lifecycles. Do not call PTB run_polling and Aiogram start_polling inside the same event loop. Evaluate a supervised two-process worker as the initial integration runtime, with graceful SIGTERM/SIGINT handling, clear logs, restart policy, and failure behavior. Validate this in development before choosing production deployment topology.

## Migration and verification gates
- Inventory table definitions, indexes, constraints, row counts, sequences, and foreign keys in both source databases.
- Take and verify restorable backups before any migration.
- Build migration scripts for a new target database only; source databases remain read-only during extraction.
- Reconcile row counts and key aggregates after import; preserve IDs where safe and resolve collisions explicitly.
- Verify users/roles, stock balance, receipt totals, consumption totals, trip counts, and audit history.
- Test concurrent trip submissions, duplicate retries, insufficient stock, rollback, notification failure, manual consumption, user deactivation, reporting, and trip void/correction.
- Do not enable automatic deductions until the approved grade-to-kg/m3 factor schedule is supplied and validated by the business owner.
- Do not cut over production until the user separately approves the migration, service changes, region move, and deployment.

## Open decisions
- The grade-to-cement-consumption factor schedule and units.
- Whether trip creation should be rejected when stock is insufficient, or saved in an explicitly pending state. Default safe behavior: reject/rollback the trip and deduction together.
- Exact backup, restore, and data reconciliation procedure for the two Render PostgreSQL instances.
- Final supervised worker implementation and health-check strategy.


## Admin-managed cement factor configuration (approved requirement)
- Do not require the business owner to provide a fixed factor table before development can continue.
- Provide an admin-only configuration flow inside the bot interface for maintaining cement consumption factors by concrete grade (kg of cement per m3).
- Only authorized active administrators may view, add, edit, activate, or deactivate grade factors. Ordinary operators must not be able to change them.
- Validate grade and numeric factor input; reject blank, zero, negative, non-numeric, or implausible values and show the unit clearly (kg/m3). Do not silently apply a default or guessed factor.
- Record an audit entry for every change: Telegram admin ID, grade, previous value, new value, and timestamp.
- Preserve factor history/versioning. Each trip must snapshot the factor and calculated cement quantity used at the time it is recorded; later edits must never recalculate historical trips or ledger entries.
- If a grade has no active factor, block automatic deduction for that grade and clearly tell the operator/admin that the factor must first be configured. Do not silently create a zero deduction.
- Keep automatic deductions disabled in development until an administrator has configured and confirmed the factors. Test the configuration flow with sample development data only.


## Development progress — admin-managed cement factors
- Added a versioned `cement_grade_factors` table to the Cement Stock SQLAlchemy model. Updating a grade deactivates the prior version and inserts a new version; history is retained.
- Added admin-only menu actions to view configured factors and set/update a factor through a guided Telegram conversation (grade, then kg/m³).
- Each factor change is recorded in the existing audit log with the administrator Telegram ID and old/new values.
- Manual Cement Stock consumption now uses the configured active factor for the selected grade. If no factor is configured, the operator is told to ask an administrator; no guessed or zero factor is applied.
- This work is committed only to `cobuilt-integration-dev`. It has not been deployed or run against a production database.
- Remaining: test the bot in an isolated development environment; add source-trip idempotency and factor snapshots to consumption records; implement the atomic shared-database trip/stock transaction; build and test the supervised two-bot runtime.


## Development progress — atomic trip/stock transaction
- Concrete development code now supports an explicit opt-in integration flag: `COBUILT_CEMENT_INTEGRATION_ENABLED=true`. It remains disabled by default.
- Integrated mode requires a dedicated Concrete schema (`DB_SCHEMA=concrete_logistics`) and a separate Cement schema (`CEMENT_SCHEMA=public`). Concrete's connection search path is restricted to its own schema so the two applications' `users` tables cannot collide.
- When enabled, a Concrete trip insert, Cement stock-row lock/update, and Cement consumption-ledger insert execute on the same PostgreSQL connection and transaction. If the factor is missing, the Concrete user is not registered in Cement Stock, or stock is insufficient, the whole transaction rolls back and the trip is not saved.
- The Cement factor version ID and exact kg/m3 value are snapshotted in the consumption row. A unique source reference is recorded as `source_system='concrete-bot'` plus the Concrete trip ID.
- Concrete submissions use the Telegram message ID with user ID as an idempotency key in the dedicated schema, preventing the same Telegram update from creating a second trip/deduction on retry.
- Both bot processes still retain their own Telegram token and handler stack. This code does not combine polling loops.
- Development only: do not set the integration flag or point either live service at the shared database. The shared Oregon development database, schema migrations, supervisor, and runtime tests are not yet provisioned/completed.


### Status clarification (supersedes earlier remaining-work bullets)
The development branch now contains the first opt-in atomic trip/stock implementation and Telegram-update idempotency key. These are code changes only and have not been executed against a development PostgreSQL database. Outstanding work is to provision the isolated Oregon database, apply/verify the Cement migration, configure a development-only pair of bot tokens and user mappings, run automated transaction/concurrency tests, and implement/test the supervised two-process Render runtime. The integration flag must remain false until those checks pass.


## Combined development worker milestone
- The Cement Stock development repository now contains a vendored snapshot of Concrete Logistics at `concrete_app/concrete_logistics_bot.py`, plus `supervisor.py`. The supervisor starts Cement Stock first, waits for its tables, applies the additive development-only consumption-reference migration, then starts Concrete Logistics. If either bot exits unexpectedly, the supervisor stops the other so Render can restart the worker as a unit.
- The two bot processes retain separate tokens: `BOT_TOKEN` for Concrete Logistics and `CEMENT_BOT_TOKEN` for Cement Stock. Cement config supports the dedicated token while retaining the old `BOT_TOKEN` fallback for standalone use.
- The shared Render blueprint is now an Oregon development Background Worker named `cobuilt-integrated-worker-dev`. It uses one `DATABASE_URL`, with Cement tables in `public` and Concrete tables in `concrete_logistics`.
- Concrete's PostgreSQL URL is normalized for psycopg2 when the shared URL uses SQLAlchemy's `postgresql+asyncpg://` form.
- Integrated mode hides and blocks destructive trip-report deletion, preserving the trip-to-consumption audit trail. Webhook cleanup no longer discards queued Telegram updates.
- A GitHub Actions development workflow was added for Python compilation and dependency consistency checks. No successful run is confirmed yet.
- All work remains on `cobuilt-integration-dev`. The new worker blueprint has not been deployed or connected to any database. Do not point it at either production database.
- Still required before any trial: create a separate Oregon development PostgreSQL database; configure its URL and two development-only bot tokens in a new Render worker; verify the supervisor starts both processes; run transaction, duplicate, insufficient-stock, factor-history, permissions, and shutdown tests. The Concrete source is vendored into the Cement integration repository, so future code changes must be synchronized deliberately.
