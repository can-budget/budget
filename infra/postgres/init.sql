\set ON_ERROR_STOP on
-- The password is read inside PostgreSQL, never interpolated into command arguments.
DO $bootstrap$
DECLARE
    app_password text := rtrim(pg_read_file('/run/secrets/database_password'), E'\r\n');
BEGIN
    IF length(app_password) < 24 THEN
        RAISE EXCEPTION 'The application database password must contain at least 24 characters';
    END IF;
    EXECUTE format('CREATE ROLE can LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', app_password);
END
$bootstrap$;
ALTER DATABASE can OWNER TO can;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO can;
