"""
Transfer Service - PaySim Reliability Stack

Simulates an instant-transfer style debit/credit flow between two accounts.
Deliberately includes a togglable idempotency bug (see SIMULATE_DOUBLE_DEBIT_BUG)
so you can reproduce, observe, and write a postmortem on a classic fintech
failure mode: duplicate debits from retried requests.

Run locally:
    uvicorn main:app --reload --port 8001
"""

import os
import time
import uuid
import logging
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource

# ---------------------------------------------------------------------------
# Config / feature flags
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./transfers.db")
OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://localhost:4317")

# Flip this to True to reproduce the double-debit bug for your chaos exercise.
# When True, the idempotency key is accepted but NOT checked before debiting,
# so a client retry (e.g. after a timeout) causes a second debit.
SIMULATE_DOUBLE_DEBIT_BUG = os.getenv("SIMULATE_DOUBLE_DEBIT_BUG", "false").lower() == "true"

# Simulate the notification service being slow/unreliable, to create the
# "stuck transaction" failure mode as an alternative incident scenario.
NOTIFICATION_FAILURE_RATE = float(os.getenv("NOTIFICATION_FAILURE_RATE", "0.0"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("transfer-service")

# ---------------------------------------------------------------------------
# OpenTelemetry setup
# ---------------------------------------------------------------------------

resource = Resource(attributes={"service.name": "transfer-service"})
provider = TracerProvider(resource=resource)
try:
    exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
except Exception as e:
    logger.warning(f"OTLP exporter not configured: {e}")
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("transfer-service")

# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------

Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(String, primary_key=True)
    balance = Column(Float, nullable=False, default=0.0)


class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # NOTE: intentionally NOT unique at the DB level. Adding a unique
    # constraint here is one of the postmortem remediation items (defense in
    # depth) — see docs/postmortem-template.md. Without it, a bypassed
    # application-level idempotency check fully reproduces a double debit.
    idempotency_key = Column(String, nullable=True, index=True)
    from_account = Column(String, nullable=False)
    to_account = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, completed, failed
    created_at = Column(DateTime, server_default=func.now())


Base.metadata.create_all(bind=engine)


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_accounts():
    with get_db() as db:
        for acc_id, bal in [("acct_alice", 10000.0), ("acct_bob", 5000.0)]:
            if not db.get(Account, acc_id):
                db.add(Account(id=acc_id, balance=bal))
        db.commit()


seed_accounts()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Transfer Service")
FastAPIInstrumentor.instrument_app(app)


class TransferRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float
    idempotency_key: Optional[str] = None


class TransferResponse(BaseModel):
    transfer_id: str
    status: str
    from_account: str
    to_account: str
    amount: float


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/accounts/{account_id}")
def get_account(account_id: str):
    with get_db() as db:
        acct = db.get(Account, account_id)
        if not acct:
            raise HTTPException(status_code=404, detail="account not found")
        return {"id": acct.id, "balance": acct.balance}


@app.post("/transfers", response_model=TransferResponse)
def create_transfer(req: TransferRequest):
    with tracer.start_as_current_span("create_transfer") as span:
        span.set_attribute("transfer.from_account", req.from_account)
        span.set_attribute("transfer.to_account", req.to_account)
        span.set_attribute("transfer.amount", req.amount)
        span.set_attribute("transfer.idempotency_key", req.idempotency_key or "none")
        span.set_attribute("bug.simulate_double_debit", SIMULATE_DOUBLE_DEBIT_BUG)

        with get_db() as db:
            # --- Idempotency check ---
            # THIS is the check that gets skipped when the bug flag is on.
            if req.idempotency_key and not SIMULATE_DOUBLE_DEBIT_BUG:
                with tracer.start_as_current_span("check_idempotency"):
                    existing = (
                        db.query(Transfer)
                        .filter(Transfer.idempotency_key == req.idempotency_key)
                        .first()
                    )
                    if existing:
                        span.set_attribute("idempotency.hit", True)
                        logger.info(f"Idempotent replay for key={req.idempotency_key}, returning existing transfer")
                        return TransferResponse(
                            transfer_id=existing.id,
                            status=existing.status,
                            from_account=existing.from_account,
                            to_account=existing.to_account,
                            amount=existing.amount,
                        )

            # --- Validation ---
            with tracer.start_as_current_span("validate_accounts"):
                from_acct = db.get(Account, req.from_account)
                to_acct = db.get(Account, req.to_account)
                if not from_acct or not to_acct:
                    span.set_attribute("error", True)
                    raise HTTPException(status_code=404, detail="account not found")
                if from_acct.balance < req.amount:
                    span.set_attribute("error", True)
                    span.set_attribute("error.reason", "insufficient_funds")
                    raise HTTPException(status_code=400, detail="insufficient funds")

            # --- Debit ---
            with tracer.start_as_current_span("debit_account") as debit_span:
                from_acct.balance -= req.amount
                debit_span.set_attribute("account.new_balance", from_acct.balance)
                # Simulate realistic processing latency
                time.sleep(0.05)

            # --- Credit ---
            with tracer.start_as_current_span("credit_account") as credit_span:
                to_acct.balance += req.amount
                credit_span.set_attribute("account.new_balance", to_acct.balance)
                time.sleep(0.05)

            transfer = Transfer(
                idempotency_key=req.idempotency_key,
                from_account=req.from_account,
                to_account=req.to_account,
                amount=req.amount,
                status="completed",
            )
            db.add(transfer)
            db.commit()
            db.refresh(transfer)

            # --- Notification (simulated, can fail/timeout) ---
            with tracer.start_as_current_span("notify_customer") as notify_span:
                import random
                if random.random() < NOTIFICATION_FAILURE_RATE:
                    notify_span.set_attribute("notification.failed", True)
                    logger.warning(f"Notification failed for transfer {transfer.id}")
                else:
                    notify_span.set_attribute("notification.failed", False)

            logger.info(
                f"Transfer {transfer.id} completed: {req.from_account} -> {req.to_account} "
                f"amount={req.amount} idempotency_key={req.idempotency_key}"
            )

            return TransferResponse(
                transfer_id=transfer.id,
                status=transfer.status,
                from_account=transfer.from_account,
                to_account=transfer.to_account,
                amount=transfer.amount,
            )
