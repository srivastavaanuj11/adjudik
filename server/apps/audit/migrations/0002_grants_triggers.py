"""Grants, composite workspace FKs and immutability triggers (SPEC §9.3, §9.6, §10.3).

Runs after every app's initial tables exist. It:

* adds the database-level composite foreign keys ``(workspace_id, parent_id) ->
  parent(workspace_id, id)`` so no row can point into another workspace;
* grants ``adjudik_app`` the §10.3 matrix (everything except ``audit_event`` =
  SELECT/INSERT and ``audit_chain_head`` = SELECT/UPDATE);
* installs the triggers that make tampering detectable: ``audit_event`` is
  append-only for every role; a published definition, a terminal evaluation's
  decision fields, and a completed review's decision fields cannot change.
"""

from __future__ import annotations

from django.db import migrations

# (child table, fk columns, parent table) — each parent has UNIQUE (workspace_id, id).
_COMPOSITE_FKS = [
    ("decision_version", "workspace_id, decision_id", "decision"),
    ("automation_policy", "workspace_id, decision_version_id", "decision_version"),
    ("evaluation", "workspace_id, decision_id", "decision"),
    ("evaluation", "workspace_id, decision_version_id", "decision_version"),
    ("model_call", "workspace_id, evaluation_id", "evaluation"),
    ("review_task", "workspace_id, decision_id", "decision"),
    ("review_task", "workspace_id, evaluation_id", "evaluation"),
    ("webhook_endpoint", "workspace_id, decision_id", "decision"),
    ("webhook_delivery", "workspace_id, endpoint_id", "webhook_endpoint"),
]


def _composite_fk_sql() -> str:
    lines = []
    for i, (child, cols, parent) in enumerate(_COMPOSITE_FKS):
        name = f"{child}_ws_fk_{i}"
        lines.append(
            f"ALTER TABLE {child} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({cols}) REFERENCES {parent} (workspace_id, id) "
            f"DEFERRABLE INITIALLY DEFERRED;"
        )
    return "\n".join(lines)


def _composite_fk_reverse_sql() -> str:
    lines = []
    for i, (child, _cols, _parent) in enumerate(_COMPOSITE_FKS):
        name = f"{child}_ws_fk_{i}"
        lines.append(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {name};")
    return "\n".join(lines)


GRANTS_SQL = """
-- §10.3 privilege matrix for the runtime role.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO adjudik_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO adjudik_app;

-- audit_event: append-only (SELECT, INSERT).
REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM adjudik_app;

-- audit_chain_head: the head is updated in place but never inserted/deleted by the app.
REVOKE INSERT, DELETE, TRUNCATE ON audit_chain_head FROM adjudik_app;
"""

GRANTS_REVERSE_SQL = """
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM adjudik_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM adjudik_app;
"""

TRIGGERS_SQL = r"""
-- audit_event is append-only for EVERY role (owner included). TRUNCATE is left
-- to privilege, so the owner can still flush in tests (SPEC §9.3).
CREATE OR REPLACE FUNCTION adjudik_audit_event_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only (SPEC §9.3): % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_append_only
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION adjudik_audit_event_append_only();

-- A published/retired definition's body cannot change (SPEC §9.6).
CREATE OR REPLACE FUNCTION adjudik_decision_version_definition_frozen()
RETURNS trigger AS $$
BEGIN
    IF OLD.status <> 'draft' AND NEW.definition::text IS DISTINCT FROM OLD.definition::text THEN
        RAISE EXCEPTION 'decision_version.definition is frozen once published (SPEC §9.6)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER decision_version_definition_frozen
    BEFORE UPDATE ON decision_version
    FOR EACH ROW EXECUTE FUNCTION adjudik_decision_version_definition_frozen();

-- A terminal evaluation's decision fields cannot change (SPEC §9.6). Purgeable
-- columns (input, rationale, ...) may still be nulled.
CREATE OR REPLACE FUNCTION adjudik_evaluation_decision_frozen()
RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('decided', 'cancelled') AND (
        NEW.status IS DISTINCT FROM OLD.status
        OR NEW.outcome IS DISTINCT FROM OLD.outcome
        OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
        OR NEW.raw_confidence IS DISTINCT FROM OLD.raw_confidence
        OR NEW.suggested_outcome IS DISTINCT FROM OLD.suggested_outcome
    ) THEN
        RAISE EXCEPTION 'evaluation decision fields are frozen once terminal (SPEC §9.6)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evaluation_decision_frozen
    BEFORE UPDATE ON evaluation
    FOR EACH ROW EXECUTE FUNCTION adjudik_evaluation_decision_frozen();

-- A completed review task's decision fields cannot change (SPEC §9.6).
CREATE OR REPLACE FUNCTION adjudik_review_task_decision_frozen()
RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'completed' AND (
        NEW.status IS DISTINCT FROM OLD.status
        OR NEW.action IS DISTINCT FROM OLD.action
        OR NEW.final_outcome IS DISTINCT FROM OLD.final_outcome
        OR NEW.agrees_with_model IS DISTINCT FROM OLD.agrees_with_model
        OR NEW.reason_codes::text IS DISTINCT FROM OLD.reason_codes::text
        OR NEW.completed_by_id IS DISTINCT FROM OLD.completed_by_id
    ) THEN
        RAISE EXCEPTION 'completed review task decision fields are frozen (SPEC §9.6)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER review_task_decision_frozen
    BEFORE UPDATE ON review_task
    FOR EACH ROW EXECUTE FUNCTION adjudik_review_task_decision_frozen();
"""

TRIGGERS_REVERSE_SQL = """
DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event;
DROP FUNCTION IF EXISTS adjudik_audit_event_append_only();
DROP TRIGGER IF EXISTS decision_version_definition_frozen ON decision_version;
DROP FUNCTION IF EXISTS adjudik_decision_version_definition_frozen();
DROP TRIGGER IF EXISTS evaluation_decision_frozen ON evaluation;
DROP FUNCTION IF EXISTS adjudik_evaluation_decision_frozen();
DROP TRIGGER IF EXISTS review_task_decision_frozen ON review_task;
DROP FUNCTION IF EXISTS adjudik_review_task_decision_frozen();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("decisions", "0001_initial"),
        ("evaluations", "0001_initial"),
        ("reviews", "0001_initial"),
        ("webhooks", "0001_initial"),
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(_composite_fk_sql(), reverse_sql=_composite_fk_reverse_sql()),
        migrations.RunSQL(GRANTS_SQL, reverse_sql=GRANTS_REVERSE_SQL),
        migrations.RunSQL(TRIGGERS_SQL, reverse_sql=TRIGGERS_REVERSE_SQL),
    ]
