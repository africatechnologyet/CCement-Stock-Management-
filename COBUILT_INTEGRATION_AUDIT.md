# CoBuilt Integrated Telegram System — Cement Stock Audit

Status: development-only audit. No production services or databases are modified by this document.

## Selected architecture

- Preserve the existing Cement Stock Telegram bot, original token, roles, and interface.
- Preserve the existing Concrete Logistics Telegram bot and its original token/interface.
- Target one supervised Render worker and one PostgreSQL database only after development and migration tests pass.
- Keep Cement Stock's existing tables and historical records intact. Isolate Concrete Logistics tables in a dedicated PostgreSQL schema after validating permissions and migrations.

## Cement Stock source facts inspected

- Framework: Aiogram 3.13.1.
- ORM/database layer: SQLAlchemy 2.0.36 with asyncpg and aiosqlite dependencies.
- Main entry point is `bot.py`; handlers are split across modules.
- Database models are in `database/models.py`.
- Existing tables: `users`, `receipts`, `consumption`, `stock_adjustments`, `stock_settings`, `management_recipients`, and `audit_logs`.
- `User` has a unique Telegram ID, role, and `is_active` flag.
- `CementConsumption` currently records operator, quantity, cubic meters, kg/m³, project, grade, stock_after, note, and timestamp.
- `deactivate_user(session, tid)` already exists in `database/queries.py`; an admin-facing user deactivation workflow needs to be verified/implemented.
- Current manual consumption is handled by `handlers/operator.py` and calls `add_consumption()`.
- `add_consumption()` checks stock and then calls a helper that updates the stock setting; this path needs stronger transaction/locking semantics for concurrent integrated deductions.
- `render.yaml` currently describes a worker named `cement-bot` and a SQLite database on a persistent disk. This file is not evidence of the currently deployed Render database configuration.

## Existing database model names

| Model | Existing table |
|---|---|
| User | users |
| CementReceipt | receipts |
| CementConsumption | consumption |
| StockAdjustment | stock_adjustments |
| StockSettings | stock_settings |
| ManagementRecipient | management_recipients |
| AuditLog | audit_logs |

Do not rename, drop, or recreate these historical tables as part of integration.

## Integration requirements

1. Add a nullable unique external-source reference to the consumption ledger, with an appropriate migration. Example: `source_system` + `source_trip_id`, uniquely constrained for non-null source IDs.
2. Automatic deduction must insert a consumption ledger entry and update stock in one SQLAlchemy transaction, with row-level locking or a safe atomic conditional update.
3. Use the same PostgreSQL database as Concrete Logistics only after an isolated copy has been prepared and validated.
4. Keep existing manual consumption commands and role checks. Add a clear warning when a manual entry may overlap with a concrete trip.
5. Preserve stock history and audit logs. Do not rewrite historical stock_after values.
6. User removal should deactivate the account and preserve all associated records. Protect the last active administrator.
7. Add administrator visibility for registered users, roles, active/inactive status, and a confirmed deactivation action.
8. Add notifications through the existing Cement Stock bot, with failures logged/retried without undoing an already committed stock transaction.
9. Do not invent grade cement factors. They require explicit business approval.
10. Preserve current low-stock alerts and management-recipient behavior.

## Development-only validation checklist

- [ ] Confirm the real Render service configuration and production database engine using read-only inspection.
- [ ] Create a sanitized, isolated database copy for development.
- [ ] Verify all model/table row counts and stock totals before and after migration.
- [ ] Test receipt, manual consumption, adjustments, low-stock alerts, audit logs, and reports.
- [ ] Test duplicate source-trip references and concurrent deductions.
- [ ] Test insufficient stock and rollback.
- [ ] Test user deactivation and last-admin protection.
- [ ] Test that existing Telegram users, roles, and invite behavior remain intact.

## Production protection

This branch is a development workspace only. Do not change production Render services, production databases, tokens, or webhooks as part of the audit or development work.