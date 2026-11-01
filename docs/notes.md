# Notes

## OLAP-405 · CDC (change data capture)

**The problem we saw (OLAP-404).** `simulate_changes.py` hard-deleted order 9 and its two
items. After the incremental sync they were still in Bronze. A watermark sync asks
"which rows have `updated_at > watermark`?", and a deleted row isn't there to answer.

**How log-based CDC catches it.**
- Postgres writes every change to its write-ahead log (WAL) before applying it. Inserts,
  updates *and deletes* all go through the WAL.
- With `wal_level = logical`, a replication slot decodes the WAL into a stream of row
  events: `insert`, `update` (before and after values), `delete` (the deleted key).
- A tool like Debezium reads the slot and publishes the events (often to Kafka). The
  warehouse applies them in order. A `delete` event becomes a tombstone in Bronze
  (e.g. `_deleted = true`), so downstream models know the row is gone.

**What it fixes, compared with our `updated_at` sync.**
- Hard deletes are captured.
- Every intermediate version is captured, not just the latest one at sync time
  (an order that went pending → paid → shipped between two syncs shows all three steps).
- No reliance on the app maintaining `updated_at`, and no late-commit problem: the log
  position (LSN) is the watermark, and it's assigned in commit order, so no lookback is needed.
- Light on the source: it reads the log instead of querying the tables.

**What it costs.**
- More infrastructure (Debezium, usually Kafka), plus Postgres config and privileges.
- A replication slot keeps WAL until the consumer reads it. If the consumer is down for
  days, the production database's disk fills up. That's an OLTP outage caused by analytics.
- It still needs an initial snapshot. Debezium takes one and then streams from that exact
  log position, which also fixes the "tables copied at different moments" gap in our snapshot.

**Without CDC, the usual options:** only soft deletes (an agreement with the app team),
periodic key comparison (the `ANTI JOIN` check from OLAP-404, costly on big tables),
periodic full refresh, or a delete trigger that writes tombstones to an audit table.

**Replication vs transformation.**
- *Replication* (E4, Fivetran, Debezium): copy the source faithfully, with no business logic.
- *Transformation* (dbt, E5–E6): reshape that copy for analysis.
- Keeping them separate means a transformation bug is fixed by re-running dbt on Bronze,
  without re-extracting from production, and the replication code stays generic.
