-- Track lifecycle of tasks dispatched by the controller to agents
CREATE TABLE IF NOT EXISTS controller.task_history (
    id SERIAL PRIMARY KEY,
    task_id INTEGER,
    task TEXT NOT NULL,
    agent TEXT,
    status TEXT NOT NULL,            -- e.g., dispatched, completed, failed
    result TEXT,                     -- optional feedback/result text
    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS ix_task_history_agent_created ON controller.task_history (agent, created_at);
CREATE INDEX IF NOT EXISTS ix_task_history_task_id ON controller.task_history (task_id);
