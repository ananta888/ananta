-- Initialize required schemas and tables for Ananta

-- Schemas
CREATE SCHEMA IF NOT EXISTS controller;
CREATE SCHEMA IF NOT EXISTS agent;

-- Controller tables
CREATE TABLE IF NOT EXISTS controller.config (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.blacklist (
    id SERIAL PRIMARY KEY,
    cmd TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.control_log (
    id SERIAL PRIMARY KEY,
    received TEXT NOT NULL,
    summary TEXT,
    approved TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.tasks (
    id SERIAL PRIMARY KEY,
    task TEXT NOT NULL,
    agent TEXT,
    template TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent tables
CREATE TABLE IF NOT EXISTS agent.config (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent.logs (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent.flags (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS ix_tasks_agent_created ON controller.tasks (agent, created_at);
CREATE INDEX IF NOT EXISTS ix_agent_logs_agent_created ON agent.logs (agent, created_at);
