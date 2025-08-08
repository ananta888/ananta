-- Schema für den Controller
CREATE SCHEMA IF NOT EXISTS controller;

-- Schema für den AI-Agent
CREATE SCHEMA IF NOT EXISTS agent;

-- Controller Tabellen
CREATE TABLE IF NOT EXISTS controller.config (
    id SERIAL PRIMARY KEY,
    data JSONB
);

CREATE TABLE IF NOT EXISTS controller.blacklist (
    cmd TEXT PRIMARY KEY
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
    data JSONB
);

CREATE TABLE IF NOT EXISTS agent.flags (
    name TEXT PRIMARY KEY,
    value TEXT
);
