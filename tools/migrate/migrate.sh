#!/bin/sh
# Copies every table, row, sequence and index from OLD_DATABASE_URL to NEW_DATABASE_URL,
# then prints row counts from both so they can be compared. Never writes to the old database.
set -eu
: "${OLD_DATABASE_URL:?set OLD_DATABASE_URL}"
: "${NEW_DATABASE_URL:?set NEW_DATABASE_URL}"
echo "== safety check: new database must be empty"
n=$(psql "$NEW_DATABASE_URL" -tAc "select count(*) from information_schema.tables where table_schema='public'")
if [ "$n" != "0" ]; then echo "STOP: new database already has $n tables, nothing was changed"; exit 1; fi
echo "== dumping old database (read only)"
pg_dump --no-owner --no-privileges --format=custom "$OLD_DATABASE_URL" -f /tmp/db.dump
echo "== restoring into new database"
pg_restore --no-owner --no-privileges --exit-on-error --single-transaction -d "$NEW_DATABASE_URL" /tmp/db.dump
echo "== row counts (old | new)"
for t in $(psql "$OLD_DATABASE_URL" -tAc "select tablename from pg_tables where schemaname='public' order by 1"); do
  a=$(psql "$OLD_DATABASE_URL" -tAc "select count(*) from public.\"$t\"")
  b=$(psql "$NEW_DATABASE_URL" -tAc "select count(*) from public.\"$t\"")
  s=OK; [ "$a" = "$b" ] || s=DIFFERENT
  echo "$t: $a | $b  $s"
done
echo "== MIGRATION FINISHED"
