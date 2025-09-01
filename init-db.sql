-- Schema für den Controller
CREATE SCHEMA IF NOT EXISTS controller;

-- Schema für den AI-Agent
CREATE SCHEMA IF NOT EXISTS agent;

-- Controller Tabellen
CREATE TABLE IF NOT EXISTS controller.config (
    id SERIAL PRIMARY KEY,
    data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS controller.blacklist (
    cmd TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS controller.control_log (
    id SERIAL PRIMARY KEY,
    received TEXT,
    summary TEXT,
    approved TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.tasks (
    id SERIAL PRIMARY KEY,
    task TEXT,
    agent TEXT,
    template TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Enhanced task tracking columns and index
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'queued';
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS log JSONB DEFAULT '[]'::jsonb;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS created_by TEXT;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS picked_by TEXT;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS picked_at TIMESTAMP;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS fail_count INTEGER DEFAULT 0;
ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS ix_tasks_agent_status_created ON controller.tasks (agent, status, created_at);

-- Agent Tabellen
CREATE TABLE IF NOT EXISTS agent.logs (
    id SERIAL PRIMARY KEY,
    agent TEXT,
    level TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent.config (
    id SERIAL PRIMARY KEY,
    data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent.flags (
    name TEXT PRIMARY KEY,
    value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
