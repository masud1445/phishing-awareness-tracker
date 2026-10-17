CREATE TABLE IF NOT EXISTS campaigns (
 id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_name TEXT NOT NULL, purpose TEXT,
 campaign_template TEXT NOT NULL, start_date TEXT NOT NULL, authorized INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS recipients (
 id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
 full_name TEXT NOT NULL, email TEXT NOT NULL, department TEXT, approved INTEGER NOT NULL DEFAULT 0,
 approved_at TEXT, tracking_token TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
 sent_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tracking_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
 recipient_id INTEGER NOT NULL REFERENCES recipients(id), event_type TEXT NOT NULL,
 event_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_templates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 sender_name TEXT NOT NULL, sender_email TEXT NOT NULL, sender_account_id INTEGER, presentation TEXT, reference_link TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS template_assets (
 id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL REFERENCES email_templates(id),
 filename TEXT NOT NULL, mime_type TEXT NOT NULL, kind TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_email_settings (
 campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id), template_id INTEGER NOT NULL REFERENCES email_templates(id),
 snapshot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sender_accounts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 display_name VARCHAR(200) NOT NULL, email VARCHAR(254) NOT NULL,
 provider VARCHAR(20) NOT NULL, smtp_host VARCHAR(253) NOT NULL,
 smtp_port INTEGER NOT NULL, tls_mode VARCHAR(20) NOT NULL,
 smtp_username VARCHAR(254) NOT NULL, encrypted_password TEXT,
 source_key VARCHAR(270) UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
 verified INTEGER NOT NULL DEFAULT 0, verification_digest VARCHAR(64),
 revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sender_preferences (
 id INTEGER PRIMARY KEY, default_sender_id INTEGER,
 FOREIGN KEY (default_sender_id) REFERENCES sender_accounts(id)
);

INSERT OR IGNORE INTO sender_preferences (id,default_sender_id) VALUES (1,NULL);
