# Social Communication Research: Multi-User Chat & Messaging

---

## Architecture Options

### 1. Database Store (Simplest)
- Store messages in PostgreSQL
- Poll via REST API
- Good for: Low traffic, MVP

### 2. WebSocket (Real-Time)
- Persistent connections
- Bidirectional updates
- Good for: Live chat, notifications

### 3. Message Queue (Scale)
- Redis/RabbitMQ for message queue
- Decouple send/receive
- Good for: High volume

---

## Implementation Approaches

### Option A: Simple REST (MVP)
```python
# POST /api/messages
@app.route('/api/messages', methods=['POST'])
def send_message():
    data = request.json
    # Store in database
    save_message(user_id, content)
    return {"status": "sent"}, 201

# GET /api/messages?since=<timestamp>
@app.route('/api/messages')
def get_messages():
    messages = fetch_messages(since=request.args.get('since'))
    return jsonify(messages)
```

### Option B: WebSocket (Real-Time)
```python
from flask_socketio import SocketIO, emit

socketio = SocketIO(app)

@socketio.on('send_message')
def handle_message(data):
    # Save to DB
    save_message(data['user_id'], data['content'])
    # Broadcast
    emit('new_message', data, broadcast=True)

@socketio.on('connect')
def handle_connect():
    emit('connected', {'status': 'ok'})
```

---

## Database Schema

### Messages Table
```sql
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER REFERENCES users(id),
    to_user_id INTEGER REFERENCES users(id),  -- NULL for public/global
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    is_read BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_messages_user ON messages(from_user_id, to_user_id);
CREATE INDEX idx_messages_created ON messages(created_at DESC);
```

### Rooms/Channels
```sql
CREATE TABLE chat_rooms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    is_public BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE room_messages (
    id SERIAL PRIMARY KEY,
    room_id INTEGER REFERENCES chat_rooms(id),
    user_id INTEGER REFERENCES users(id),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## Features

### Direct Messages
- [x] User to user
- [x] Read receipts
- [x] Typing indicators
- [x] Message history

### Chat Rooms
- [x] Public channels (e.g., #general, #prematch)
- [x] Private rooms
- [x] Real-time updates

### Moderation
- [x] Report message
- [x] Block user
- [x] Profanity filter
- [x] Rate limit messages

---

## Chat UI Components

### Message Display
```html
<div class="message">
    <div class="user-avatar">{{ user.avatar }}</div>
    <div class="message-content">
        <div class="username">{{ user.username }}</div>
        <div class="text">{{ message.content }}</div>
        <div class="timestamp">{{ message.created_at }}</div>
    </div>
</div>
```

### Send Form
```html
<form id="message-form">
    <input type="text" name="content" placeholder="Type a message...">
    <button type="submit">Send</button>
</form>
```

---

## Rate Limiting

### Anti-Spam
| Action | Limit |
|--------|-------|
| Messages/minute | 10 |
| Messages/hour | 100 |
| Same message | Block repeats |

### Moderation
- Auto-flag: 3 reports
- Auto-ban: 10 reports
- Profanity: Replace with *****

---

## Real-Time Technology

### WebSocket Libraries
| Library | Pros | Cons |
|---------|------|------|
| Flask-SocketIO | Easy integration | Python focused |
| Socket.IO | Real-time | Extra protocol |
| websockets | Async native | More complex |

### Fallback (Long Polling)
```javascript
// Client
async function poll() {
    while(connected) {
        const messages = await fetch(`/api/messages?since=${lastTimestamp}`);
        render(messages);
        await sleep(5000);
    }
}
```

---

## Security Considerations

### Message Security
- [x] Input sanitization
- [x] Max length (1000 chars)
- [x] No HTML in messages (XSS)
- [x] Rate limiting
- [x] Audit logging

### Privacy
- [x] Blocked users can't message
- [x] Private rooms only visible to members
- [x] Message deletion (GDPR)

---

## Integration with Betting

### Chat Triggers
- "New match starting in 5 min!"
- "Odds moved significantly"
- "Breaking news: Player injured"

### Bot Commands
```text
!match 456    # Link to fixture
!odds btts    # Show odds
!flag 456    # Flag to watch
!stats home   # Team stats
```

---

## Checklist

### MVP
- [ ] Direct messages
- [ ] Message history
- [ ] Real-time updates

### Extended
- [ ] Chat rooms
- [ ] Typing indicators
- [ ] Read receipts
- [ ] Link detection (!match)

### Advanced
- [ ] Moderation tools
- [ ] Bot commands
- [ ] File/images (secure)

---

## References

### WebSocket
- https://flask-socketio.readthedocs.io/
- https://socket.io/

### Real-Time Patterns
- https://ably.com/topic/websockets-vs-sse

### Moderation
- https://www.owasp.org/index.php/Input_Validation_Cheat_Sheet

---

*Last Updated: 2026-04-12*  
*Category: Social Communication*