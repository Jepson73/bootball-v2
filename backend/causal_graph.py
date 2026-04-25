import uuid
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from src.storage.db import get_session
from sqlalchemy import text


NODE_TYPES = [
    'prediction',
    'bet',
    'run',
    'layer_contribution',
    'calibration',
    'odds_snapshot',
    'outcome',
    'model_invocation',
    'feature_computation'
]

EDGE_TYPES = [
    'CAUSED_BY',
    'MODIFIED_BY',
    'INFLUENCED_BY',
    'AGGREGATES',
    'CONTRADICTS',
    'DERIVES_FROM',
    'LEADS_TO'
]


def generate_node_id() -> str:
    return str(uuid.uuid4())


def generate_edge_id() -> str:
    return str(uuid.uuid4())


@dataclass
class DecisionNode:
    id: int
    node_id: str
    timestamp: datetime
    node_type: str
    run_id: Optional[str]
    round_id: Optional[int]
    entity_ref: Optional[str]
    payload_json: Optional[str]
    value_delta: Optional[float]
    confidence_score: Optional[float]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'node_id': self.node_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'node_type': self.node_type,
            'run_id': self.run_id,
            'round_id': self.round_id,
            'entity_ref': self.entity_ref,
            'payload': json.loads(self.payload_json) if self.payload_json else {},
            'value_delta': self.value_delta,
            'confidence_score': self.confidence_score
        }


@dataclass
class DecisionEdge:
    id: int
    edge_id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    weight: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'edge_id': self.edge_id,
            'from_node_id': self.from_node_id,
            'to_node_id': self.to_node_id,
            'edge_type': self.edge_type,
            'weight': self.weight
        }


def create_decision_node(
    node_type: str,
    run_id: Optional[str] = None,
    round_id: Optional[int] = None,
    entity_ref: Optional[str] = None,
    payload: Optional[Dict] = None,
    value_delta: Optional[float] = None,
    confidence_score: Optional[float] = None
) -> str:
    """Create a new decision node. Returns node_id."""
    node_id = generate_node_id()
    payload_json = json.dumps(payload) if payload else None
    
    with get_session() as s:
        s.execute(text("""
            INSERT INTO decision_nodes 
            (node_id, timestamp, node_type, run_id, round_id, entity_ref, payload_json, value_delta, confidence_score)
            VALUES (:node_id, :timestamp, :node_type, :run_id, :round_id, :entity_ref, :payload_json, :value_delta, :confidence_score)
        """), {
            'node_id': node_id,
            'timestamp': datetime.utcnow().isoformat(),
            'node_type': node_type,
            'run_id': run_id,
            'round_id': round_id,
            'entity_ref': entity_ref,
            'payload_json': payload_json,
            'value_delta': value_delta,
            'confidence_score': confidence_score
        })
        s.commit()
    
    return node_id


def create_decision_edge(
    from_node_id: str,
    to_node_id: str,
    edge_type: str,
    weight: float = 1.0
) -> str:
    """Create a causal edge between nodes. Returns edge_id."""
    edge_id = generate_edge_id()
    
    with get_session() as s:
        s.execute(text("""
            INSERT INTO decision_edges 
            (edge_id, from_node_id, to_node_id, edge_type, weight)
            VALUES (:edge_id, :from_node_id, :to_node_id, :edge_type, :weight)
        """), {
            'edge_id': edge_id,
            'from_node_id': from_node_id,
            'to_node_id': to_node_id,
            'edge_type': edge_type,
            'weight': weight
        })
        s.commit()
    
    return edge_id


def get_node(node_id: str) -> Optional[DecisionNode]:
    """Get a decision node by ID."""
    with get_session() as s:
        row = s.execute(text("""
            SELECT id, node_id, timestamp, node_type, run_id, round_id, entity_ref, payload_json, value_delta, confidence_score
            FROM decision_nodes WHERE node_id = :node_id
        """), {'node_id': node_id}).fetchone()
        
        if row:
            return DecisionNode(*row)
    return None


def get_node_trace(node_id: str, max_depth: int = 10) -> Dict[str, Any]:
    """Get causal trace from a node (ancestors and descendants)."""
    node = get_node(node_id)
    if not node:
        return {'error': 'Node not found'}
    
    ancestors = _trace_upstream(node_id, max_depth)
    descendants = _trace_downstream(node_id, max_depth)
    
    return {
        'node': node.to_dict(),
        'ancestors': ancestors,
        'descendants': descendants,
        'depth': max_depth
    }


def _trace_upstream(node_id: str, max_depth: int) -> List[Dict]:
    """Trace all ancestor nodes (causes)."""
    result = []
    visited = set()
    
    def _recurse(nid: str, depth: int):
        if depth >= max_depth or nid in visited:
            return
        visited.add(nid)
        
        with get_session() as s:
            rows = s.execute(text("""
                SELECT dn.node_id, dn.timestamp, dn.node_type, dn.entity_ref, dn.value_delta, de.edge_type, de.weight
                FROM decision_edges de
                JOIN decision_nodes dn ON de.from_node_id = dn.node_id
                WHERE de.to_node_id = :nid
            """), {'nid': nid}).fetchall()
        
        for row in rows:
            result.append({
                'node_id': row[0],
                'timestamp': row[1].isoformat() if row[1] else None,
                'node_type': row[2],
                'entity_ref': row[3],
                'value_delta': row[4],
                'edge_type': row[5],
                'weight': row[6]
            })
            _recurse(row[0], depth + 1)
    
    _recurse(node_id, 0)
    return result


def _trace_downstream(node_id: str, max_depth: int) -> List[Dict]:
    """Trace all descendant nodes (effects)."""
    result = []
    visited = set()
    
    def _recurse(nid: str, depth: int):
        if depth >= max_depth or nid in visited:
            return
        visited.add(nid)
        
        with get_session() as s:
            rows = s.execute(text("""
                SELECT dn.node_id, dn.timestamp, dn.node_type, dn.entity_ref, dn.value_delta, de.edge_type, de.weight
                FROM decision_edges de
                JOIN decision_nodes dn ON de.to_node_id = dn.node_id
                WHERE de.from_node_id = :nid
            """), {'nid': nid}).fetchall()
        
        for row in rows:
            result.append({
                'node_id': row[0],
                'timestamp': row[1].isoformat() if row[1] else None,
                'node_type': row[2],
                'entity_ref': row[3],
                'value_delta': row[4],
                'edge_type': row[5],
                'weight': row[6]
            })
            _recurse(row[0], depth + 1)
    
    _recurse(node_id, 0)
    return result


def get_subgraph_for_run(run_id: str) -> Dict[str, Any]:
    """Get entire causal subgraph for a run."""
    with get_session() as s:
        nodes = s.execute(text("""
            SELECT node_id, timestamp, node_type, run_id, round_id, entity_ref, payload_json, value_delta, confidence_score
            FROM decision_nodes WHERE run_id = :run_id ORDER BY timestamp
        """), {'run_id': run_id}).fetchall()
        
        node_ids = [n[0] for n in nodes]
        
        edges = s.execute(text("""
            SELECT edge_id, from_node_id, to_node_id, edge_type, weight
            FROM decision_edges 
            WHERE from_node_id IN :node_ids OR to_node_id IN :node_ids
        """), {'node_ids': tuple(node_ids)}).fetchall()
    
    return {
        'run_id': run_id,
        'nodes': [DecisionNode(n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], n[9]).to_dict() for n in nodes],
        'edges': [DecisionEdge(e[0], e[1], e[2], e[3], e[4], e[5]).to_dict() for e in edges],
        'node_count': len(nodes),
        'edge_count': len(edges)
    }


def explain_bet(bet_id: int) -> Dict[str, Any]:
    """Get full causal explanation for a bet."""
    with get_session() as s:
        bet_node = s.execute(text("""
            SELECT node_id FROM decision_nodes 
            WHERE node_type = 'bet' AND entity_ref = :entity_ref
        """), {'entity_ref': f'bet:{bet_id}'}).fetchone()
        
        if not bet_node:
            return {'error': 'Bet not found in decision ledger'}
        
        return get_node_trace(bet_node[0])


def record_prediction(
    run_id: str,
    fixture_id: int,
    market: str,
    predicted_outcome: str,
    our_prob: float,
    calibrated_prob: float,
    ev: float,
    confidence: float
) -> str:
    """Record a prediction as a decision node."""
    node_id = create_decision_node(
        node_type='prediction',
        run_id=run_id,
        entity_ref=f'prediction:{fixture_id}:{market}',
        payload={
            'fixture_id': fixture_id,
            'market': market,
            'predicted_outcome': predicted_outcome,
            'our_prob': our_prob,
            'calibrated_prob': calibrated_prob
        },
        value_delta=ev,
        confidence_score=confidence
    )
    return node_id


def record_bet(
    run_id: str,
    round_id: int,
    fixture_id: int,
    market: str,
    outcome: str,
    stake: float,
    odds: float,
    ev: float,
    prediction_node_id: str = None
) -> str:
    """Record a bet as a decision node."""
    node_id = create_decision_node(
        node_type='bet',
        run_id=run_id,
        round_id=round_id,
        entity_ref=f'bet:{fixture_id}',
        payload={
            'fixture_id': fixture_id,
            'market': market,
            'outcome': outcome,
            'stake': stake,
            'odds': odds
        },
        value_delta=ev
    )
    
    if prediction_node_id:
        create_decision_edge(
            from_node_id=prediction_node_id,
            to_node_id=node_id,
            edge_type='LEADS_TO'
        )
    
    return node_id


def record_run_start(run_id: str, mode: str, config_hash: str) -> str:
    """Record run start as a decision node."""
    node_id = create_decision_node(
        node_type='run',
        run_id=run_id,
        entity_ref=f'run:{run_id}',
        payload={
            'mode': mode,
            'config_hash': config_hash
        }
    )
    return node_id


def get_causal_stats() -> Dict[str, Any]:
    """Get overall causal graph statistics."""
    with get_session() as s:
        total_nodes = s.execute(text("SELECT COUNT(*) FROM decision_nodes")).scalar()
        total_edges = s.execute(text("SELECT COUNT(*) FROM decision_edges")).scalar()
        
        nodes_by_type = s.execute(text("""
            SELECT node_type, COUNT(*) FROM decision_nodes GROUP BY node_type
        """)).fetchall()
        
        edges_by_type = s.execute(text("""
            SELECT edge_type, COUNT(*) FROM decision_edges GROUP BY edge_type
        """)).fetchall()
        
        runs_tracked = s.execute(text("""
            SELECT COUNT(DISTINCT run_id) FROM decision_nodes WHERE run_id IS NOT NULL
        """)).scalar()
    
    return {
        'total_nodes': total_nodes,
        'total_edges': total_edges,
        'nodes_by_type': dict(nodes_by_type),
        'edges_by_type': dict(edges_by_type),
        'runs_tracked': runs_tracked
    }