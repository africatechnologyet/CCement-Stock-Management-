# CoBuilt Production Cutover Runbook

## Scope and targets

This cutover consolidates the existing production Concrete records into the existing Cement database. It does not create a new database and does not delete either source database.

| Resource | Production target |
|---|---|
| Cement worker to become the integrated worker | `CCement-Stock-Management-` — `srv-d8e2bkcm0tmc73ef9f7g` (Frankfurt, Docker, manual deploy) |
| Existing Concrete worker to pause at cutover | `concrete-bot` — `srv-d6qliv3uibrs739cjtb0` (Oregon) |
| Shared target database | `cement-db` — `dpg-d8e5m5p9rddc73ei8epg-a` (Frankfurt) |
| Read-only migration source | `concrete-db` — `dpg-d6q3b52a214c73f9g6c0-a` (Oregon) |

The integrated worker uses `public` for Cement and `concrete_logistics` for Concrete. The original `concrete-db` remains available and unchanged after migration.

## Production data observed before cutover

- Cement: current stock 119,760 kg; 7 users; 108 receipts; 107 consumption rows; 3 stock adjustments; 227 audit rows; 3 stock settings.
- Concrete: 2,879 trips; 172 jobs; 8 trucks; 39 job orders; 8 legacy Concrete user records.
- The Cement database is approximately 8.4 MB and the Concrete database approximately 8.9 MB at inspection time.
- The target Cement database did not yet contain a `concrete_logistics` schema.
- The new production `cement_grade_factors` table will initially be empty. The Cement Admin must enter active kg/m³ factors for every concrete grade used before trips are submitted. Missing factors intentionally block deductions rather than guessing.

## Required pre-cutover safeguards

1. In the Render Dashboard, open **Backups** for both `cement-db` and `concrete-db`. Confirm a recent automatic snapshot is available and record its timestamp. If no usable snapshot is available, take and verify a logical export before proceeding.
2. Do not delete either database. Do not overwrite the Cement `public` tables or current stock.
3. Confirm all people who need Concrete access are active in the Cement bot's user list with the correct role. Concrete's old local user records are preserved for rollback, but integrated authorization reads only active Cement users.
4. The Concrete worker must be suspended immediately before the integrated worker is deployed, so two processes do not poll the production Concrete bot token simultaneously. Keep its database running.
5. Keep the Cement bot's existing `BOT_TOKEN` unchanged for rollback compatibility.

## Environment on the Cement worker

Keep its existing `DATABASE_URL` pointing to the internal connection URL for `cement-db`. Set or verify these variables on `CCement-Stock-Management-`:

| Variable | Value/source |
|---|---|
| `BOT_TOKEN` | Keep the current Cement bot token unchanged |
| `CEMENT_BOT_TOKEN` | Same current Cement bot token |
| `CONCRETE_BOT_TOKEN` | Copy the existing Concrete worker's `BOT_TOKEN` value directly in Render; never paste it into chat |
| `DATABASE_URL` | Keep the current Cement DB internal URL |
| `CONCRETE_SOURCE_DATABASE_URL` | External connection URL for `concrete-db`; the migration enforces TLS |
| `COBUILT_ENVIRONMENT` | `production` |
| `COBUILT_RUN_PRODUCTION_IMPORT` | `true` for the first cutover deploy |

Do not change or expose token values in GitHub, logs, or chat. The supervisor passes the Cement token only to the Cement child and the Concrete token only to the Concrete child. Production requires an explicit `CONCRETE_BOT_TOKEN`; it will not silently start both bots with the same token.

## Cutover sequence

1. Confirm both database snapshots and the environment values above.
2. Suspend the old Concrete worker `srv-d6qliv3uibrs739cjtb0` in Render. Do not suspend or delete either database.
3. Merge the reviewed integration branch into `main`. The production Cement worker has auto-deploy disabled, so the merge alone must not deploy it.
4. Manually deploy the latest `main` commit to `srv-d8e2bkcm0tmc73ef9f7g`.
5. Monitor the new worker. It must acquire the PostgreSQL singleton lock, start Cement, apply the production-only additive migration, import the Concrete tables, verify row counts, and then start Concrete.
6. Expected import counts: users 8, jobs 172, trucks 8, trips 2,879, job orders 39. The migration writes a completion marker and refuses to overwrite an existing unmarked schema.
7. Confirm both production bot identities in logs, no continuing Telegram polling conflict, and successful messages from both bots.
8. Through the Cement Admin bot, configure the required active grade factors. Verify one controlled Concrete trip deducts the expected cement amount exactly once, then verify stock and trip history.
9. Keep the old `concrete-db` and the old Concrete worker configuration available until the production acceptance period is complete. Do not delete either.

## Rollback

- The original `concrete-db` is not modified by the import.
- The Cement `BOT_TOKEN` and `DATABASE_URL` remain unchanged so the previous Cement deployment can be restored without losing its token/database configuration. The schema and consumption-column changes are additive.
- If the integrated worker must be rolled back after it has processed production trips, first pause the integrated worker and reconcile/export any new rows in `concrete_logistics` to the original Concrete database before resuming the old Concrete worker. Do not simply resume the old Concrete worker against its pre-cutover database and assume post-cutover trips will be present.
- Never delete the shared schema or either database during rollback.

## Post-cutover

- Confirm the Cement Admin has configured all grade factors required by live jobs.
- Confirm all intended Concrete users are active in Cement with appropriate roles.
- Keep manual Cement consumption disabled; successful Concrete trips remain the only automatic integration source.
- Remove the one-time source database URL from the integrated worker environment after the import has been verified, if the Render Dashboard supports removing that variable without replacing other service variables. The import marker makes subsequent starts safe.
