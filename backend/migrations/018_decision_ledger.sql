-- Migration 018: Unified Decision Ledger with Causal Graph
-- All system decisions are tracked as nodes in a causal graph

-- Decision Nodes: Individual decision points in the system
CREATE TABLE IF NOT EXISTS decision_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id VARCHAR(36) NOT NULL UNIQUE,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    node_type VARCHAR(30) NOT NULL,
    run_id VARCHAR(36),
    round_id INTEGER,
    entity_ref VARCHAR(100),
    payload_json TEXT,
    value_delta REAL,
    confidence_score REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (round_id) REFERENCES bankroll_rounds(id)
);

CREATE INDEX idx_node_run_id ON decision_nodes(run_id);
CREATE INDEX idx_node_round_id ON decision_nodes(round_id);
CREATE INDEX idx_node_type ON decision_nodes(node_type);
CREATE INDEX idx_node_entity_ref ON decision_nodes(entity_ref);

-- Decision Edges: Causal relationships between nodes
CREATE TABLE IF NOT EXISTS decision_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id VARCHAR(36) NOT NULL UNIQUE,
    from_node_id VARCHAR(36) NOT NULL,
    to_node_id VARCHAR(36) NOT NULL,
    edge_type VARCHAR(30) NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_node_id) REFERENCES decision_nodes(node_id),
    FOREIGN KEY (to_node_id) REFERENCES decision_nodes(node_id)
);

CREATE INDEX idx_edge_from ON decision_edges(from_node_id);
CREATE INDEX idx_edge_to ON decision_edges(to_node_id);
CREATE INDEX idx_edge_type ON decision_edges(edge_type);