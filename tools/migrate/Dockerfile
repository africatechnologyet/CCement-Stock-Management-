# One-time copy of the CoBuilt database to a new, smaller Render Postgres instance.
# Used by a temporary Render cron job; safe to delete after the move.
FROM postgres:18-alpine
COPY tools/migrate/migrate.sh /migrate.sh
RUN chmod +x /migrate.sh
CMD ["/migrate.sh"]
