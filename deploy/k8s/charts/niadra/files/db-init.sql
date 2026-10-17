-- Roles and databases of a cell, run as the cluster's master user before every deploy. Idempotent:
-- existing roles only get their password re-set, so rotating one is a secret change and a deploy.
--   niadra_owner   owns the schemas; the migrations run as it, and it creates and drops the space databases
--   niadra_app     serves requests, under row-level security
--   niadra_worker  the login of the workers and the loops: it becomes niadra_app at login, so it has the
--                  same privileges and policies, but PgBouncer gives it a pool of its own (data.yaml)
--   niadra_system  the cell database, and maintenance inside a space database (relay, partitions)
--   niadra_site    the site's contact form, in its own database
-- Databases: niadra_cell (the cell's own state), niadra_control, niadra_site. The space databases
-- (s_<space id>) are not created here: the cell's provisioner creates them from the configuration.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN', r) FROM unnest(ARRAY['niadra_owner', 'niadra_app', 'niadra_system', 'niadra_site']) AS r
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) \gexec

ALTER ROLE niadra_owner PASSWORD :'owner_password';
ALTER ROLE niadra_app PASSWORD :'app_password';
ALTER ROLE niadra_system PASSWORD :'system_password';
ALTER ROLE niadra_site PASSWORD :'site_password';

-- The workers' login shares the application role's password: it is the same principal under another
-- name, which only PgBouncer tells apart.
SELECT 'CREATE ROLE niadra_worker LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_worker') \gexec
ALTER ROLE niadra_worker PASSWORD :'app_password';
GRANT niadra_app TO niadra_worker;
ALTER ROLE niadra_worker SET role = 'niadra_app';

-- Every write transaction is capped; maintenance gets more room.
ALTER ROLE niadra_app SET transaction_timeout = '60s';
ALTER ROLE niadra_worker SET transaction_timeout = '60s';
ALTER ROLE niadra_system SET transaction_timeout = '300s';

-- Maintenance reads and rewrites whole tables (relay, sweeps, dedupe rotation). A managed PostgreSQL
-- may refuse BYPASSRLS to the master user; then membership in the owner does the same, since table
-- owners are not bound by the policies.
DO $$
BEGIN
  ALTER ROLE niadra_system BYPASSRLS;
EXCEPTION WHEN insufficient_privilege THEN
  GRANT niadra_owner TO niadra_system;
END $$;

-- The provisioner in the cell creates one database per space, as the owner.
ALTER ROLE niadra_owner CREATEDB;

-- Dropping a space database (DROP DATABASE ... WITH (FORCE)) and the restore swap terminate the
-- backends of niadra_app and niadra_system in that database, which needs pg_signal_backend. A managed
-- PostgreSQL may not let the master user grant it; the cell then relies on PgBouncer's KILL and PAUSE
-- to empty the database first, so this is best effort.
DO $$
BEGIN
  GRANT pg_signal_backend TO niadra_owner;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'pg_signal_backend not granted to niadra_owner: the cell empties a database through PgBouncer';
END $$;

-- The master user creates the databases on the owners' behalf.
GRANT niadra_owner TO CURRENT_USER;
GRANT niadra_site TO CURRENT_USER;

SELECT format('CREATE DATABASE %I OWNER %I', d, o)
FROM (VALUES ('niadra_cell', 'niadra_owner'), ('niadra_control', 'niadra_owner'), ('niadra_site', 'niadra_site')) AS t(d, o)
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = d) \gexec

-- The same grants the provisioner gives every space database (server:
-- adapters/outbound/postgres/provisioning.py, DATABASE_GRANTS; tests/test_db_init.py keeps them equal).
\connect niadra_cell
REVOKE ALL ON DATABASE niadra_cell FROM PUBLIC;
GRANT CONNECT ON DATABASE niadra_cell TO niadra_app, niadra_system;
GRANT USAGE ON SCHEMA public TO niadra_app, niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO niadra_app;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO niadra_app, niadra_system;
ALTER DEFAULT PRIVILEGES FOR ROLE niadra_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO niadra_app, niadra_system;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

\connect niadra_control
REVOKE ALL ON DATABASE niadra_control FROM PUBLIC;

\connect niadra_site
REVOKE ALL ON DATABASE niadra_site FROM PUBLIC;
