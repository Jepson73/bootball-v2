-- Migration: Add execution logs for centralized dispatch tracking

CREATE TABLE IF NOT EXISTS execution_logs (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    job_name                 VARCHAR(50) NOT NULL,
    run_id                   VARCHAR(36) NOT NULL,
    context_mode             VARCHAR(20) NOT NULL,
    start_time               DATETIME NOT NULL,
    end_time                 DATETIME,
    status                   VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message            TEXT,
    result_summary           TEXT
);

CREATE INDEX IF NOT EXISTS idx_exec_log_run ON execution_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_exec_log_job ON execution_logs(job_name);
CREATE INDEX IF NOT EXISTS idx_exec_log_status ON execution_logs(status);
CREATE INDEX IF NOT EXISTS idx_exec_log_time ON execution_logs(start_time);