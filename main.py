
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from math import isfinite
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field


# =====================================================
# CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parent
DB = BASE_DIR / "smartbank.db"

app = FastAPI(title="SmartBank Pro")

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static"
)

templates = Jinja2Templates(directory=BASE_DIR / "templates")


# Taux éducatifs et configurables (non officiels)
ANNUAL_INTEREST_RATE = 0.025
INTEREST_TAX_RATE = 0.20
QUARTERS_PER_YEAR = 4


# =====================================================
# DATABASE
# =====================================================

@contextmanager
def connect_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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

        # Ne remplace pas les comptes déjà existants
        db.execute("""
            INSERT OR IGNORE INTO accounts
            (id, name, balance)
            VALUES
            ('current', 'Compte courant', 3250),
            ('savings', 'Compte épargne', 4800)
        """)

        # Empêche le calcul deux fois pour le même trimestre
        db.execute("""
            CREATE TABLE IF NOT EXISTS bank_periods (
                period TEXT PRIMARY KEY,
                gross_interest REAL NOT NULL,
                tax REAL NOT NULL,
                net_interest REAL NOT NULL,
                date TEXT NOT NULL
            )
        """)


initialize_db()


# =====================================================
# DATA MODELS
# =====================================================

class Transaction(BaseModel):
    description: str = Field(min_length=1, max_length=100)
    account: str
    type: str
    amount: float = Field(gt=0)


class Transfer(BaseModel):
    description: str = Field(
        default="Transfert entre comptes",
        max_length=100
    )
    from_account: str
    to_account: str
    amount: float = Field(gt=0)


# =====================================================
# HELPERS
# =====================================================

def validate_amount(amount):
    if not isfinite(amount) or amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Montant invalide."
        )


def add_history(db, description, account_id, type_, amount):
    db.execute("""
        INSERT INTO transactions
        (description, account_id, type, amount, date)
        VALUES (?, ?, ?, ?, ?)
    """, (
        description,
        account_id,
        type_,
        round(amount, 3),
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))


# =====================================================
# PAGES
# =====================================================

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


# =====================================================
# DASHBOARD
# =====================================================

@app.get("/api/dashboard")
def get_dashboard():

    with connect_db() as db:

        accounts = db.execute("""
            SELECT * FROM accounts
        """).fetchall()

        transactions = db.execute("""
            SELECT
                transactions.*,
                accounts.name AS account_name
            FROM transactions
            JOIN accounts
                ON accounts.id = transactions.account_id
            ORDER BY transactions.id DESC
            LIMIT 100
        """).fetchall()

        last_interest = db.execute("""
            SELECT *
            FROM bank_periods
            ORDER BY date DESC
            LIMIT 1
        """).fetchone()

    account_list = [dict(a) for a in accounts]

    return {
        "accounts": account_list,
        "transactions": [dict(t) for t in transactions],
        "total": round(
            sum(a["balance"] for a in account_list),
            3
        ),
        "interest_rate": ANNUAL_INTEREST_RATE,
        "tax_rate": INTEREST_TAX_RATE,
        "last_interest": (
            dict(last_interest) if last_interest else None
        )
    }


# =====================================================
# DEPOSIT AND WITHDRAWAL
# =====================================================

@app.post("/api/transactions")
def add_transaction(data: Transaction):

    description = data.description.strip()

    if not description:
        raise HTTPException(
            status_code=400,
            detail="Description obligatoire."
        )

    if data.account not in ["current", "savings"]:
        raise HTTPException(
            status_code=400,
            detail="Compte invalide."
        )

    if data.type not in ["deposit", "withdrawal"]:
        raise HTTPException(
            status_code=400,
            detail="Type de transaction invalide."
        )

    validate_amount(data.amount)
    amount = round(data.amount, 3)

    with connect_db() as db:

        account = db.execute("""
            SELECT balance
            FROM accounts
            WHERE id = ?
        """, (data.account,)).fetchone()

        if account is None:
            raise HTTPException(
                status_code=404,
                detail="Compte introuvable."
            )

        balance = account["balance"]

        if data.type == "withdrawal":
            if amount > balance:
                raise HTTPException(
                    status_code=400,
                    detail="Solde insuffisant."
                )
            new_balance = balance - amount

        else:
            new_balance = balance + amount

        db.execute("""
            UPDATE accounts
            SET balance = ?
            WHERE id = ?
        """, (round(new_balance, 3), data.account))

        add_history(
            db,
            description,
            data.account,
            data.type,
            amount
        )

    return {
        "message": "Transaction effectuée avec succès."
    }


# =====================================================
# TRANSFER BETWEEN ACCOUNTS
# =====================================================

@app.post("/api/transfers")
def transfer_money(data: Transfer):

    validate_amount(data.amount)
    amount = round(data.amount, 3)

    if data.from_account not in ["current", "savings"]:
        raise HTTPException(
            status_code=400,
            detail="Compte source invalide."
        )

    if data.to_account not in ["current", "savings"]:
        raise HTTPException(
            status_code=400,
            detail="Compte destinataire invalide."
        )

    if data.from_account == data.to_account:
        raise HTTPException(
            status_code=400,
            detail="Choisissez deux comptes différents."
        )

    with connect_db() as db:

        source = db.execute("""
            SELECT balance
            FROM accounts
            WHERE id = ?
        """, (data.from_account,)).fetchone()

        destination = db.execute("""
            SELECT balance
            FROM accounts
            WHERE id = ?
        """, (data.to_account,)).fetchone()

        if source is None or destination is None:
            raise HTTPException(
                status_code=404,
                detail="Compte introuvable."
            )

        if amount > source["balance"]:
            raise HTTPException(
                status_code=400,
                detail="Solde insuffisant pour le transfert."
            )

        db.execute("""
            UPDATE accounts
            SET balance = ?
            WHERE id = ?
        """, (
            round(source["balance"] - amount, 3),
            data.from_account
        ))

        db.execute("""
            UPDATE accounts
            SET balance = ?
            WHERE id = ?
        """, (
            round(destination["balance"] + amount, 3),
            data.to_account
        ))

        description = data.description.strip() or "Transfert"

        add_history(
            db,
            description + " (sortie)",
            data.from_account,
            "transfer_out",
            amount
        )

        add_history(
            db,
            description + " (entrée)",
            data.to_account,
            "transfer_in",
            amount
        )

    return {
        "message": "Transfert effectué avec succès."
    }


# =====================================================
# QUARTERLY INTEREST AND TAX
# =====================================================

@app.post("/api/interest/apply")
def apply_quarterly_interest():

    now = datetime.now()

    quarter = ((now.month - 1) // 3) + 1
    period = f"{now.year}-T{quarter}"

    with connect_db() as db:

        already_applied = db.execute("""
            SELECT period
            FROM bank_periods
            WHERE period = ?
        """, (period,)).fetchone()

        if already_applied:
            raise HTTPException(
                status_code=400,
                detail="Les intérêts de ce trimestre sont déjà calculés."
            )

        savings = db.execute("""
            SELECT balance
            FROM accounts
            WHERE id = 'savings'
        """).fetchone()

        if savings is None:
            raise HTTPException(
                status_code=404,
                detail="Compte épargne introuvable."
            )

        balance = savings["balance"]

        gross_interest = round(
            balance * ANNUAL_INTEREST_RATE / QUARTERS_PER_YEAR,
            3
        )

        tax = round(
            gross_interest * INTEREST_TAX_RATE,
            3
        )

        net_interest = round(
            gross_interest - tax,
            3
        )

        new_balance = round(
            balance + net_interest,
            3
        )

        db.execute("""
            UPDATE accounts
            SET balance = ?
            WHERE id = 'savings'
        """, (new_balance,))

        if gross_interest > 0:
            add_history(
                db,
                f"Intérêts trimestriels {period}",
                "savings",
                "interest",
                gross_interest
            )

        if tax > 0:
            add_history(
                db,
                f"Impôt sur intérêts {period}",
                "savings",
                "tax",
                tax
            )

        db.execute("""
            INSERT INTO bank_periods
            (period, gross_interest, tax, net_interest, date)
            VALUES (?, ?, ?, ?, ?)
        """, (
            period,
            gross_interest,
            tax,
            net_interest,
            now.strftime("%Y-%m-%d %H:%M")
        ))

    return {
        "message": "Intérêts trimestriels calculés.",
        "period": period,
        "gross_interest": gross_interest,
        "tax": tax,
        "net_interest": net_interest,
        "new_balance": new_balance
    }


# =====================================================
# BANK SETTINGS
# =====================================================

@app.get("/api/settings")
def get_settings():
    return {
        "bank_name": "SmartBank Pro",
        "currency": "DT",
        "annual_interest_rate": ANNUAL_INTEREST_RATE,
        "interest_tax_rate": INTEREST_TAX_RATE,
        "calculation": "Quarterly"
    }


# =====================================================
# HEALTH CHECK
# =====================================================

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "application": "SmartBank Pro"
    }
