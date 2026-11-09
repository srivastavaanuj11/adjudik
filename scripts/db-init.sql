-- scripts/db-init.sql — dev/CI database bootstrap (SPEC §10.3, §1.1 "Database").
--
-- Run by the compose `db` service on first start (mounted into
-- /docker-entrypoint-initdb.d), connected to the POSTGRES_DB as the superuser.
-- It creates the two runtime roles and sets default privileges so that every
-- table the OWNER role later creates (via `migrate`) is usable by the APP role.
--
-- The passwords below are DEV defaults matching `.env.example`. Production
-- operators create the same two roles themselves with their own secrets
-- (SPEC §10.3) and do NOT grant CREATEDB.
--
-- The precise per-table grant/revoke matrix (audit_event = SELECT, INSERT;
-- audit_chain_head = SELECT, UPDATE; checkpoint update guard, etc.) is applied
-- by a Django migration in M3a and checked against a committed matrix.

-- --- Roles ---
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'adjudik_owner') THEN
        -- CREATEDB in dev/CI only, so the test runner can create/drop the test DB.
        CREATE ROLE adjudik_owner LOGIN PASSWORD 'adjudik_owner_pw' CREATEDB;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'adjudik_app') THEN
        CREATE ROLE adjudik_app LOGIN PASSWORD 'adjudik_app_pw';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE adjudik TO adjudik_owner, adjudik_app;

-- --- Schema ownership and usage ---
-- The owner owns and may create in `public`; the app may only use it.
ALTER SCHEMA public OWNER TO adjudik_owner;
GRANT ALL ON SCHEMA public TO adjudik_owner;
GRANT USAGE ON SCHEMA public TO adjudik_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM adjudik_app;

-- --- Default privileges for objects the owner creates ---
ALTER DEFAULT PRIVILEGES FOR ROLE adjudik_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO adjudik_app;
ALTER DEFAULT PRIVILEGES FOR ROLE adjudik_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO adjudik_app;

-- Explicit REVOKEs: the app gets nothing broader than the defaults above; the
-- M3a migration narrows audit tables further and re-grants per the matrix.
ALTER DEFAULT PRIVILEGES FOR ROLE adjudik_owner IN SCHEMA public
    REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM adjudik_app;
