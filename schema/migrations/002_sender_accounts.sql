CREATE TABLE IF NOT EXISTS sender_accounts (
 id INTEGER PRIMARY KEY AUTO_INCREMENT,
 display_name VARCHAR(200) NOT NULL, email VARCHAR(254) NOT NULL,
 provider VARCHAR(20) NOT NULL, smtp_host VARCHAR(253) NOT NULL,
 smtp_port INTEGER NOT NULL, tls_mode VARCHAR(20) NOT NULL,
 smtp_username VARCHAR(254) NOT NULL, encrypted_password TEXT,
 source_key VARCHAR(270) UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
 verified INTEGER NOT NULL DEFAULT 0, verification_digest VARCHAR(64),
 revision INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sender_preferences (
 id INTEGER PRIMARY KEY, default_sender_id INTEGER,
 FOREIGN KEY (default_sender_id) REFERENCES sender_accounts(id)
);
