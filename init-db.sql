-- Schema für den Controller
CREATE SCHEMA IF NOT EXISTS controller;

-- Schema für den AI-Agent
CREATE SCHEMA IF NOT EXISTS agent;

-- Controller Tabellen
CREATE TABLE IF NOT EXISTS controller.config (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.blacklist (
    id SERIAL PRIMARY KEY,
    cmd TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.control_log (
    id SERIAL PRIMARY KEY,
    received TEXT NOT NULL,
    summary TEXT,
    approved TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS controller.tasks (
    id SERIAL PRIMARY KEY,
    task TEXT NOT NULL,
    agent TEXT,
    template TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Agent Tabellen
CREATE TABLE IF NOT EXISTS agent.logs (
    id SERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    level INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent.config (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent.flags (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
