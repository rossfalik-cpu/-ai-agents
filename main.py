from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json
import os
import stripe
import uuid
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Database setup - Use /app/agent.db for writable path in Railway
DB_PATH = os.getenv("DATABASE_URL", "/app/agent.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH.replace("sqlite:///", "")

# Initialize Stripe (placeholder)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

# Pydantic models
class AgentCreate(BaseModel):
    name: str
    title: str
    description: str
    model: str = "gpt-4"

class AgentResponse(BaseModel):
    id: str
    name: str
    title: str
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
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
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
        # If title column doesn't exist (legacy table), add it
        cursor.execute("PRAGMA table_info(agents)")
        columns = [col[1] for col in cursor.fetchall()]
        if "title" not in columns:
            cursor.execute("ALTER TABLE agents ADD COLUMN title TEXT DEFAULT ''")
            logger.info("Added title column to agents table")
        conn.commit()
        return conn
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        return None

@app.on_event("startup")
def startup():
    logger.info("Starting up AI Agents Platform...")
    conn = init_db()
    if conn:
        conn.close()
        logger.info("Database initialized successfully.")
    else:
        logger.warning("Database initialization failed. Platform will run with limited functionality.")

# Routes
@app.get("/health")
def health():
    return {"status": "ok", "service": "AI Agents Platform"}

@app.get("/agents", response_model=List[AgentResponse])
def list_agents():
    conn = init_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not initialized")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, title, description, model, created_at, status FROM agents")
    rows = cursor.fetchall()
    conn.close()
    return [
        AgentResponse(
            id=row[0],
            name=row[1],
            title=row[2],
            description=row[3],
            model=row[4],
            created_at=row[5],
            status=row[6]
        ) for row in rows
    ]

@app.post("/agents", response_model=AgentResponse)
def create_agent(agent: AgentCreate):
    conn = init_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not initialized")
    cursor = conn.cursor()
    agent_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO agents (id, name, title, description, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, agent.name, agent.title, agent.description, agent.model, created_at)
    )
    conn.commit()
    conn.close()
    return AgentResponse(
        id=agent_id,
        name=agent.name,
        title=agent.title,
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
        if conn:
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
            if conn:
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