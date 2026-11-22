#!/bin/sh
# Copies everything from OLD_DATABASE_URL into NEW_DATABASE_URL (refreshing the new one),
# then compares row counts in every schema. Never writes to the old database.
set -eu
: "${OLD_DATABASE_URL:?set OLD_DATABASE_URL}"
: "${NEW_DATABASE_URL:?set NEW_DATABASE_URL}"
TARGET_NAME="cement_db_1gb"
old_name=$(psql "$OLD_DATABASE_URL" -tAc "select current_database()")
new_name=$(psql "$NEW_DATABASE_URL" -tAc "select current_database()")
echo "== old database: $old_name | new database: $new_name"
if [ "$new_name" != "$TARGET_NAME" ] || [ "$old_name" = "$new_name" ]; then
  echo "STOP: new database is not $TARGET_NAME (or both links point to the same database). Nothing was changed."; exit 1
fi
echo "== clearing the earlier copy in $new_name only"
psql "$NEW_DATABASE_URL" -v ON_ERROR_STOP=1 -c "drop schema if exists concrete_logistics cascade; drop schema if exists public cascade; create schema public;"
echo "== dumping old database (read only)"
pg_dump --no-owner --no-privileges --format=custom "$OLD_DATABASE_URL" -f /tmp/db.dump
echo "== restoring into new database"
pg_restore --no-owner --no-privileges --exit-on-error --single-transaction -d "$NEW_DATABASE_URL" /tmp/db.dump
echo "== row counts, every schema (old | new)"
Q="select table_schema||'.'||table_name from information_schema.tables where table_type='BASE TABLE' and table_schema not in ('pg_catalog','information_schema') order by 1"
ok=1
for t in $(psql "$OLD_DATABASE_URL" -tAc "$Q"); do
  s=${t%%.*}; n=${t#*.}
  a=$(psql "$OLD_DATABASE_URL" -tAc "select count(*) from \"$s\".\"$n\"")
  b=$(psql "$NEW_DATABASE_URL" -tAc "select count(*) from \"$s\".\"$n\"" 2>/dev/null || echo MISSING)
  r=OK; [ "$a" = "$b" ] || { r=DIFFERENT; ok=0; }
  echo "$t: $a | $b  $r"
done
[ $ok = 1 ] && echo "== MIGRATION FINISHED: ALL TABLES MATCH" || { echo "== MIGRATION FINISHED: SOME TABLES DIFFER"; exit 1; }
