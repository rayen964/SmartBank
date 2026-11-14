from pathlib import Path
import sqlite3
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB = BASE_DIR / "smartbank.db"

app = FastAPI(title="SmartBank Classic")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def connect_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def initialize_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                balance REAL NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                account_id TEXT NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                date TEXT NOT NULL
            )
        """)

        db.execute("""
            INSERT OR IGNORE INTO accounts VALUES
            ('current', 'Compte courant', 3250),
            ('savings', 'Compte épargne', 4800)
        """)


initialize_db()


class Transaction(BaseModel):
    description: str = Field(min_length=1, max_length=100)
    account: str
    type: str
    amount: float = Field(gt=0)


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/api/dashboard")
def get_dashboard():
    with connect_db() as db:
        accounts = db.execute(
            "SELECT * FROM accounts"
        ).fetchall()

        transactions = db.execute("""
            SELECT transactions.*, accounts.name AS account_name
            FROM transactions
            JOIN accounts ON accounts.id = transactions.account_id
            ORDER BY transactions.id DESC
            LIMIT 100
        """).fetchall()

    account_list = [dict(a) for a in accounts]

    return {
        "accounts": account_list,
        "transactions": [dict(t) for t in transactions],
        "total": sum(a["balance"] for a in account_list)
    }


@app.post("/api/transactions")
def add_transaction(data: Transaction):
    description = data.description.strip()

    if not description:
        raise HTTPException(400, "Description obligatoire.")

    if data.account not in ["current", "savings"]:
        raise HTTPException(400, "Compte invalide.")

    if data.type not in ["deposit", "withdrawal"]:
        raise HTTPException(400, "Type invalide.")

    with connect_db() as db:
        account = db.execute(
            "SELECT balance FROM accounts WHERE id = ?",
            (data.account,)
        ).fetchone()

        if account is None:
            raise HTTPException(404, "Compte introuvable.")

        balance = account["balance"]

        if data.type == "withdrawal" and data.amount > balance:
            raise HTTPException(400, "Solde insuffisant.")

        if data.type == "deposit":
            balance += data.amount
        else:
            balance -= data.amount

        db.execute(
            "UPDATE accounts SET balance = ? WHERE id = ?",
            (round(balance, 3), data.account)
        )

        db.execute("""
            INSERT INTO transactions
            (description, account_id, type, amount, date)
            VALUES (?, ?, ?, ?, ?)
        """, (
            description,
            data.account,
            data.type,
            round(data.amount, 3),
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ))

    return {"message": "Transaction ajoutée avec succès !"}


@app.get("/api/health")
def health():
    return {"status": "ok"}