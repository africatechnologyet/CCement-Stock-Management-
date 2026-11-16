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
