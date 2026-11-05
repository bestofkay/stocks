-- Create MySQL database and application user for portfolio aggregator.
-- Run as a MySQL admin, then adjust password before production use.

CREATE DATABASE IF NOT EXISTS portfolio
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'portfolio_user'@'localhost' IDENTIFIED BY 'change_me';
CREATE USER IF NOT EXISTS 'portfolio_user'@'127.0.0.1' IDENTIFIED BY 'change_me';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES, TRIGGER
  ON portfolio.* TO 'portfolio_user'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES, TRIGGER
  ON portfolio.* TO 'portfolio_user'@'127.0.0.1';

-- GET_LOCK / RELEASE_LOCK used by the scheduler (no DB-specific privilege needed)

FLUSH PRIVILEGES;
