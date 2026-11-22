#!/bin/sh
# Read-only check: compares row counts of every table in every schema, old vs new.
set -eu
Q="select table_schema||'.'||table_name from information_schema.tables where table_type='BASE TABLE' and table_schema not in ('pg_catalog','information_schema') order by 1"
ok=1
for t in $(psql "$OLD_DATABASE_URL" -tAc "$Q"); do
  s=${t%%.*}; n=${t#*.}
  a=$(psql "$OLD_DATABASE_URL" -tAc "select count(*) from \"$s\".\"$n\"")
  b=$(psql "$NEW_DATABASE_URL" -tAc "select count(*) from \"$s\".\"$n\"" 2>/dev/null || echo MISSING)
  r=OK; [ "$a" = "$b" ] || { r=DIFFERENT; ok=0; }
  echo "$t: $a | $b  $r"
done
echo "== sequences (new db, next ids)"
psql "$NEW_DATABASE_URL" -tAc "select schemaname||'.'||sequencename||' = '||coalesce(last_value::text,'-') from pg_sequences order by 1"
[ $ok = 1 ] && echo "== VERIFY: ALL TABLES MATCH" || echo "== VERIFY: SOME TABLES DIFFER"
