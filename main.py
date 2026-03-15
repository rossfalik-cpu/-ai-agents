from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json
import os
import stripe
import uuid
from datetime import datetime

# Initialize FastAPI app
app = FastAPI(title="AI Agents Platform", version="1.0.0")

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/agent.db")
if DATABASE_URL.startswith("sqlite"):
    # Use SQLite directly for simplicity
    DB_PATH = DATABASE_URL.replace("sqlite:///", "")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
else:
    # PostgreSQL or other
    DB_PATH = None

# Initialize Stripe (placeholder)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

# Pydantic models
class AgentCreate(BaseModel):
    name: str
    description: str
    model: str = "gpt-4"

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    model: str
    created_at: str
    status: str = "active"

class PaymentCreate(BaseModel):
    agent_id: str
    amount: int  # cents
    currency: str = "usd"

# Database helper
def init_db():
    conn = sqlite3.connect(DB_PATH if DB_PATH else ":memory:")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            model TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            amount INTEGER,
            currency TEXT,
            status TEXT,
            stripe_session_id TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn

@app.on_event("startup")
def startup():
    init_db()

# Routes
@app.get("/health")
def health():
    return {"status": "ok", "service": "AI Agents Platform"}

@app.get("/agents", response_model=List[AgentResponse])
def list_agents():
    conn = init_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, model, created_at, status FROM agents")
    rows = cursor.fetchall()
    conn.close()
    return [
        AgentResponse(
            id=row[0],
            name=row[1],
            description=row[2],
            model=row[3],
            created_at=row[4],
            status=row[5]
        ) for row in rows
    ]

@app.post("/agents", response_model=AgentResponse)
def create_agent(agent: AgentCreate):
    conn = init_db()
    cursor = conn.cursor()
    agent_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO agents (id, name, description, model, created_at) VALUES (?, ?, ?, ?, ?)",
        (agent_id, agent.name, agent.description, agent.model, created_at)
    )
    conn.commit()
    conn.close()
    return AgentResponse(
        id=agent_id,
        name=agent.name,
        description=agent.description,
        model=agent.model,
        created_at=created_at,
        status="active"
    )

@app.post("/payment/create-checkout-session")
def create_checkout_session(payment: PaymentCreate):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': payment.currency,
                    'product_data': {
                        'name': f'AI Agent: {payment.agent_id}',
                    },
                    'unit_amount': payment.amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='https://your-netlify-site.netlify.app/success',
            cancel_url='https://your-netlify-site.netlify.app/cancel',
        )
        # Store payment in DB
        conn = init_db()
        cursor = conn.cursor()
        payment_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO payments (id, agent_id, amount, currency, status, stripe_session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payment_id, payment.agent_id, payment.amount, payment.currency, 'pending', session.id, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
        return {"session_id": session.id, "url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
        )
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            # Update payment status
            conn = init_db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE payments SET status = 'completed' WHERE stripe_session_id = ?",
                (session['id'],)
            )
            conn.commit()
            conn.close()
        return {"received": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)