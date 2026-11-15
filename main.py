from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from calendar import monthrange
from typing import Optional
import sqlite3
import math
import os
import secrets
import tempfile

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "smartbank.db"

APP_NAME = "SmartBank Pro"
APP_VERSION = "2.0.0"

# Educational simulation settings.
# These values must be checked against the rates in your PDF.
ANNUAL_SAVINGS_RATE = 0.025
SAVINGS_TAX_RATE = 0.20
ANNUAL_OVERDRAFT_RATE = 0.05
OVERDRAFT_FREE_LIMIT = 200.0

# Accounting day-count convention.
DAYS_PER_YEAR = 365
ACCOUNTING_DIVISOR = 360

# Value-date offsets:
# Current account: deposit +1 day, withdrawal -1 day.
# Savings account: deposit +7 days, withdrawal -7 days.
VALUE_DATE_OFFSETS = {
    "current": {
        "deposit": 1,
        "withdrawal": -1,
    },
    "savings": {
        "deposit": 7,
        "withdrawal": -7,
    },
}

# Commission parameters.
# Keep at zero until the exact PDF rules and rates are confirmed.
OVERDRAFT_COMMISSION_RATE = 0.0
OVERDRAFT_FIXED_COMMISSION = 0.0
VAT_RATE = 0.0

ALLOWED_ACCOUNTS = ("current", "savings")
ALLOWED_TRANSACTION_TYPES = (
    "deposit",
    "withdrawal",
    "transfer_in",
    "transfer_out",
    "interest",
    "tax",
    "overdraft_interest",
    "commission",
    "vat",
)


# ============================================================
# 2. FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Bank account management and quarterly interest simulation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


# ============================================================
# 3. DATABASE CONNECTION
# ============================================================

@contextmanager
def connect_db():
    """
    Open a SQLite connection.
    Every successful operation is committed.
    Every failed operation is rolled back.
    """

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")

        yield connection

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def table_columns(db, table_name: str) -> set[str]:
    rows = db.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {row["name"] for row in rows}


def add_column_if_missing(
    db,
    table_name: str,
    column_name: str,
    column_definition: str,
):
    columns = table_columns(db, table_name)

    if column_name not in columns:
        db.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {column_definition}"
        )


# ============================================================
# 4. DATABASE INITIALIZATION AND MIGRATIONS
# ============================================================

def initialize_database():
    """
    Create required tables and migrate old transactions safely.
    Existing database records are not deleted.
    """

    with connect_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                created_at TEXT
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

        # Add missing columns to older databases.
        add_column_if_missing(
            db,
            "transactions",
            "date_operation",
            "TEXT",
        )

        add_column_if_missing(
            db,
            "transactions",
            "date_valeur",
            "TEXT",
        )

        add_column_if_missing(
            db,
            "transactions",
            "created_at",
            "TEXT",
        )

        add_column_if_missing(
            db,
            "transactions",
            "reference",
            "TEXT",
        )

        # Backfill old records without changing their amounts.
        db.execute("""
            UPDATE transactions
            SET date_operation = COALESCE(
                date_operation,
                date
            )
            WHERE date_operation IS NULL
        """)

        db.execute("""
            UPDATE transactions
            SET date_valeur = COALESCE(
                date_valeur,
                substr(date, 1, 10)
            )
            WHERE date_valeur IS NULL
        """)

        db.execute("""
            UPDATE transactions
            SET created_at = COALESCE(
                created_at,
                date
            )
            WHERE created_at IS NULL
        """)

        # Insert initial accounts only if they do not exist.
        db.execute("""
            INSERT OR IGNORE INTO accounts
                (id, name, balance, created_at)
            VALUES
                (
                    'current',
                    'Compte Courant',
                    3250.000,
                    ?
                ),
                (
                    'savings',
                    'Compte Épargne',
                    4800.000,
                    ?
                )
        """, (
            datetime.now().isoformat(timespec="seconds"),
            datetime.now().isoformat(timespec="seconds"),
        ))

        db.execute("""
            CREATE TABLE IF NOT EXISTS bank_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL UNIQUE,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                gross_interest REAL NOT NULL DEFAULT 0,
                tax REAL NOT NULL DEFAULT 0,
                net_interest REAL NOT NULL DEFAULT 0,
                overdraft_interest REAL NOT NULL DEFAULT 0,
                commissions REAL NOT NULL DEFAULT 0,
                vat REAL NOT NULL DEFAULT 0,
                total_agios REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS bank_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_transactions_account
            ON transactions(account_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_transactions_value_date
            ON transactions(date_valeur)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_transactions_operation_date
            ON transactions(date_operation)
        """)

        default_settings = {
            "annual_savings_rate": str(ANNUAL_SAVINGS_RATE),
            "savings_tax_rate": str(SAVINGS_TAX_RATE),
            "annual_overdraft_rate": str(ANNUAL_OVERDRAFT_RATE),
            "overdraft_free_limit": str(OVERDRAFT_FREE_LIMIT),
            "overdraft_commission_rate": str(
                OVERDRAFT_COMMISSION_RATE
            ),
            "overdraft_fixed_commission": str(
                OVERDRAFT_FIXED_COMMISSION
            ),
            "vat_rate": str(VAT_RATE),
        }

        for key, value in default_settings.items():
            db.execute("""
                INSERT OR IGNORE INTO bank_settings
                    (setting_key, setting_value)
                VALUES (?, ?)
            """, (key, value))


initialize_database()


# ============================================================
# 5. PYDANTIC REQUEST MODELS
# ============================================================

class TransactionRequest(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=100,
    )

    account: str

    type: str

    amount: float = Field(gt=0)

    operation_date: Optional[date] = None

    value_date: Optional[date] = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, value):
        value = value.strip()

        if not value:
            raise ValueError("Description obligatoire.")

        return value

    @field_validator("account")
    @classmethod
    def validate_account(cls, value):
        if value not in ALLOWED_ACCOUNTS:
            raise ValueError("Compte invalide.")

        return value

    @field_validator("type")
    @classmethod
    def validate_type(cls, value):
        if value not in ("deposit", "withdrawal"):
            raise ValueError("Type d'opération invalide.")

        return value


class TransferRequest(BaseModel):
    description: str = Field(
        default="Transfert entre comptes",
        max_length=100,
    )

    from_account: str

    to_account: str

    amount: float = Field(gt=0)

    operation_date: Optional[date] = None

    source_value_date: Optional[date] = None

    destination_value_date: Optional[date] = None


class SettingsRequest(BaseModel):
    annual_savings_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )

    savings_tax_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )

    annual_overdraft_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )

    overdraft_free_limit: Optional[float] = Field(
        default=None,
        ge=0,
    )

    overdraft_commission_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )

    overdraft_fixed_commission: Optional[float] = Field(
        default=None,
        ge=0,
    )

    vat_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )


# ============================================================
# 6. GENERAL VALIDATION AND FORMATTING
# ============================================================

def round_money(value: float) -> float:
    return round(float(value), 3)


def validate_amount(amount: float):
    if not math.isfinite(amount):
        raise HTTPException(
            status_code=400,
            detail="Montant invalide.",
        )

    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Le montant doit être supérieur à zéro.",
        )


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])

    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Format de date invalide. Utilisez YYYY-MM-DD.",
        )


def date_string(value: date) -> str:
    return value.isoformat()


def signed_amount(transaction_type: str, amount: float) -> float:
    """
    Positive effects increase the account balance.
    Negative effects decrease the account balance.
    """

    positive_types = {
        "deposit",
        "transfer_in",
        "interest",
    }

    negative_types = {
        "withdrawal",
        "transfer_out",
        "tax",
        "overdraft_interest",
        "commission",
        "vat",
    }

    if transaction_type in positive_types:
        return float(amount)

    if transaction_type in negative_types:
        return -float(amount)

    return 0.0


# ============================================================
# 7. SETTINGS MANAGEMENT
# ============================================================

def read_settings(db) -> dict:
    rows = db.execute("""
        SELECT setting_key, setting_value
        FROM bank_settings
    """).fetchall()

    values = {
        row["setting_key"]: float(row["setting_value"])
        for row in rows
    }

    return {
        "annual_savings_rate": values.get(
            "annual_savings_rate",
            ANNUAL_SAVINGS_RATE,
        ),
        "savings_tax_rate": values.get(
            "savings_tax_rate",
            SAVINGS_TAX_RATE,
        ),
        "annual_overdraft_rate": values.get(
            "annual_overdraft_rate",
            ANNUAL_OVERDRAFT_RATE,
        ),
        "overdraft_free_limit": values.get(
            "overdraft_free_limit",
            OVERDRAFT_FREE_LIMIT,
        ),
        "overdraft_commission_rate": values.get(
            "overdraft_commission_rate",
            OVERDRAFT_COMMISSION_RATE,
        ),
        "overdraft_fixed_commission": values.get(
            "overdraft_fixed_commission",
            OVERDRAFT_FIXED_COMMISSION,
        ),
        "vat_rate": values.get(
            "vat_rate",
            VAT_RATE,
        ),
    }


def save_settings(db, settings: dict):
    for key, value in settings.items():
        if value is None:
            continue

        db.execute("""
            INSERT INTO bank_settings
                (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value
        """, (
            key,
            str(float(value)),
        ))


# ============================================================
# 8. ACCOUNT HELPERS
# ============================================================

def get_account(db, account_id: str):
    account = db.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        raise HTTPException(
            status_code=404,
            detail="Compte introuvable.",
        )

    return account


def get_account_balances(db) -> dict:
    rows = db.execute("""
        SELECT id, name, balance
        FROM accounts
    """).fetchall()

    return {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "balance": round_money(row["balance"]),
        }
        for row in rows
    }


def update_account_balance(db, account_id: str, new_balance: float):
    db.execute("""
        UPDATE accounts
        SET balance = ?
        WHERE id = ?
    """, (
        round_money(new_balance),
        account_id,
    ))


# ============================================================
# 9. VALUE DATE RULES
# ============================================================

def calculate_value_date(
    account_id: str,
    transaction_type: str,
    operation_day: date,
) -> date:

    if account_id not in VALUE_DATE_OFFSETS:
        raise HTTPException(
            status_code=400,
            detail="Compte invalide.",
        )

    if transaction_type not in ("deposit", "withdrawal"):
        raise HTTPException(
            status_code=400,
            detail="Type d'opération invalide.",
        )

    offset = VALUE_DATE_OFFSETS[account_id][transaction_type]

    return operation_day + timedelta(days=offset)


def resolve_transaction_dates(
    account_id: str,
    transaction_type: str,
    operation_day: Optional[date],
    value_day: Optional[date],
):
    operation_day = operation_day or date.today()

    if value_day is None:
        value_day = calculate_value_date(
            account_id,
            transaction_type,
            operation_day,
        )

    if value_day < date(1900, 1, 1):
        raise HTTPException(
            status_code=400,
            detail="Date de valeur invalide.",
        )

    return operation_day, value_day


# ============================================================
# 10. TRANSACTION HISTORY
# ============================================================

def insert_transaction(
    db,
    description: str,
    account_id: str,
    transaction_type: str,
    amount: float,
    operation_day: date,
    value_day: date,
    created_at: Optional[datetime] = None,
):
    created_at = created_at or datetime.now()

    reference = (
        f"SB-{created_at.strftime('%Y%m%d%H%M%S')}-"
        f"{account_id}"
    )

    db.execute("""
        INSERT INTO transactions (
            description,
            account_id,
            type,
            amount,
            date,
            date_operation,
            date_valeur,
            created_at,
            reference
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        description,
        account_id,
        transaction_type,
        round_money(amount),
        created_at.isoformat(timespec="seconds"),
        operation_day.isoformat(),
        value_day.isoformat(),
        created_at.isoformat(timespec="seconds"),
        reference,
    ))


def fetch_transactions(
    db,
    account_id: Optional[str] = None,
    limit: int = 500,
):
    if account_id:
        rows = db.execute("""
            SELECT
                t.*,
                a.name AS account_name
            FROM transactions t
            JOIN accounts a
                ON a.id = t.account_id
            WHERE t.account_id = ?
            ORDER BY
                t.date_valeur ASC,
                t.id ASC
            LIMIT ?
        """, (
            account_id,
            limit,
        )).fetchall()

    else:
        rows = db.execute("""
            SELECT
                t.*,
                a.name AS account_name
            FROM transactions t
            JOIN accounts a
                ON a.id = t.account_id
            ORDER BY t.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [dict(row) for row in rows]


# ============================================================
# 11. BALANCE CALCULATION BY VALUE DATE
# ============================================================

def get_transactions_until(
    db,
    account_id: str,
    end_day: date,
):
    return db.execute("""
        SELECT
            type,
            amount,
            date_valeur,
            date_operation
        FROM transactions
        WHERE account_id = ?
          AND date_valeur <= ?
        ORDER BY date_valeur ASC, id ASC
    """, (
        account_id,
        end_day.isoformat(),
    )).fetchall()


def balance_at_value_date(
    db,
    account_id: str,
    day: date,
) -> float:
    """
    Reconstruct the account balance at the end of a value date.

    The current account balance is adjusted by transaction effects
    whose value dates are later than the requested date.
    """

    account = get_account(db, account_id)

    current_balance = float(account["balance"])

    future_rows = db.execute("""
        SELECT type, amount
        FROM transactions
        WHERE account_id = ?
          AND date_valeur > ?
    """, (
        account_id,
        day.isoformat(),
    )).fetchall()

    future_effect = sum(
        signed_amount(row["type"], row["amount"])
        for row in future_rows
    )

    return round_money(current_balance - future_effect)


def daily_balances(
    db,
    account_id: str,
    start_day: date,
    end_day: date,
):
    """
    Yield one daily balance for each calendar day.
    """

    current_day = start_day

    while current_day <= end_day:
        yield (
            current_day,
            balance_at_value_date(
                db,
                account_id,
                current_day,
            ),
        )

        current_day += timedelta(days=1)


# ============================================================
# 12. QUARTER HELPERS
# ============================================================

def get_quarter(day: date):
    quarter_number = ((day.month - 1) // 3) + 1

    first_month = (quarter_number - 1) * 3 + 1
    last_month = first_month + 2

    start_day = date(
        day.year,
        first_month,
        1,
    )

    last_day = monthrange(
        day.year,
        last_month,
    )[1]

    end_day = date(
        day.year,
        last_month,
        last_day,
    )

    period = f"{day.year}-T{quarter_number}"

    return period, start_day, end_day


def quarter_for_date(day: date):
    return get_quarter(day)


def previous_day(day: date) -> date:
    return day - timedelta(days=1)


# ============================================================
# 13. SAVINGS ACCOUNT INTEREST
# ============================================================

def calculate_savings_interest(
    db,
    start_day: date,
    end_day: date,
    annual_rate: float,
):
    """
    Calculate interest on positive daily value-date balances.

    Interest = daily credit balance * annual rate / 365.
    """

    total_interest = 0.0
    daily_details = []

    for day, balance in daily_balances(
        db,
        "savings",
        start_day,
        end_day,
    ):
        credit_balance = max(balance, 0.0)

        daily_interest = (
            credit_balance
            * annual_rate
            / DAYS_PER_YEAR
        )

        total_interest += daily_interest

        daily_details.append({
            "date": day.isoformat(),
            "balance": round_money(credit_balance),
            "daily_interest": round_money(daily_interest),
        })

    return {
        "gross_interest": round_money(total_interest),
        "days": len(daily_details),
        "details": daily_details,
    }


def calculate_savings_tax(
    gross_interest: float,
    tax_rate: float,
) -> float:

    return round_money(
        gross_interest * tax_rate
    )


# ============================================================
# 14. OVERDRAFT INTEREST
# ============================================================

def calculate_overdraft_interest(
    db,
    start_day: date,
    end_day: date,
    annual_rate: float,
    free_limit: float,
):
    """
    Calculate debit interest only on the overdraft portion
    exceeding the configured free limit.
    """

    total_interest = 0.0
    daily_details = []

    for day, balance in daily_balances(
        db,
        "current",
        start_day,
        end_day,
    ):
        overdraft = max(0.0, -balance)

        taxable_overdraft = max(
            0.0,
            overdraft - free_limit,
        )

        daily_interest = (
            taxable_overdraft
            * annual_rate
            / DAYS_PER_YEAR
        )

        total_interest += daily_interest

        daily_details.append({
            "date": day.isoformat(),
            "balance": round_money(balance),
            "overdraft": round_money(overdraft),
            "debit_base": round_money(taxable_overdraft),
            "daily_interest": round_money(daily_interest),
        })

    return {
        "overdraft_interest": round_money(total_interest),
        "days": len(daily_details),
        "details": daily_details,
    }


# ============================================================
# 15. COMMISSION AND VAT CALCULATION
# ============================================================

def calculate_commissions(
    debit_interest: float,
    settings: dict,
):
    """
    Configurable commission model.

    The exact CPFD/CD rules must be set from the course PDF.
    No unverified commission amount is imposed by default.
    """

    rate = settings["overdraft_commission_rate"]
    fixed = settings["overdraft_fixed_commission"]

    commission = (
        debit_interest * rate
    ) + fixed

    commission = round_money(commission)

    vat = round_money(
        commission * settings["vat_rate"]
    )

    return {
        "commission": commission,
        "vat": vat,
        "total_commissions_and_vat": round_money(
            commission + vat
        ),
    }


# ============================================================
# 16. ACCOUNTING TABLE: COMMON MOVEMENT ROWS
# ============================================================

def build_movement_rows(
    db,
    account_id: str,
    start_day: date,
    end_day: date,
):
    """
    Create a chronological movement list.
    Rows are sorted by value date, then transaction ID.
    """

    rows = db.execute("""
        SELECT *
        FROM transactions
        WHERE account_id = ?
          AND date_valeur >= ?
          AND date_valeur <= ?
        ORDER BY date_valeur ASC, id ASC
    """, (
        account_id,
        start_day.isoformat(),
        end_day.isoformat(),
    )).fetchall()

    movements = []

    for row in rows:
        movements.append({
            "id": row["id"],
            "description": row["description"],
            "type": row["type"],
            "amount": round_money(row["amount"]),
            "date_operation": row["date_operation"],
            "date_valeur": row["date_valeur"],
        })

    return movements


def calculate_movement_balances(
    db,
    account_id: str,
    movements: list,
    start_day: date,
):
    """
    Build debit and credit balances after every movement.

    The opening balance is reconstructed from the account's
    current balance and all transaction effects after the
    opening value date.
    """

    opening_balance = balance_at_value_date(
        db,
        account_id,
        start_day - timedelta(days=1),
    )

    running_balance = opening_balance
    result = []

    for movement in movements:
        effect = signed_amount(
            movement["type"],
            movement["amount"],
        )

        running_balance += effect

        debit_balance = max(
            0.0,
            -running_balance,
        )

        credit_balance = max(
            0.0,
            running_balance,
        )

        result.append({
            **movement,
            "balance": round_money(running_balance),
            "debit_balance": round_money(debit_balance),
            "credit_balance": round_money(credit_balance),
        })

    return opening_balance, result


# ============================================================
# 17. SAVINGS ACCOUNT TABLE
# ============================================================

def build_savings_table(
    db,
    start_day: date,
    end_day: date,
    settings: dict,
):
    movements = build_movement_rows(
        db,
        "savings",
        start_day,
        end_day,
    )

    opening_balance, rows = calculate_movement_balances(
        db,
        "savings",
        movements,
        start_day,
    )

    table_rows = []

    previous_day_for_balance = start_day - timedelta(days=1)

    for row in rows:
        value_day = parse_date(row["date_valeur"])

        days_count = max(
            0,
            (value_day - previous_day_for_balance).days,
        )

        credit_balance = row["credit_balance"]

        number_credit = (
            credit_balance * days_count
        )

        interest = (
            number_credit
            * settings["annual_savings_rate"]
            / ACCOUNTING_DIVISOR
        )

        # Keep the balance movement and interest columns separate.
        table_rows.append({
            "Dop": row["date_operation"],
            "Dv": row["date_valeur"],
            "Débit": (
                row["amount"]
                if row["type"] in (
                    "withdrawal",
                    "transfer_out",
                )
                else 0.0
            ),
            "Crédit": (
                row["amount"]
                if row["type"] in (
                    "deposit",
                    "transfer_in",
                )
                else 0.0
            ),
            "SD": row["debit_balance"],
            "SC": row["credit_balance"],
            "NjC": days_count,
            "TxC": settings["annual_savings_rate"] * 100,
            "NC": round_money(number_credit),
            "IC": round_money(interest),
            "TxImpC": settings["savings_tax_rate"] * 100,
            "ImpC": round_money(
                interest * settings["savings_tax_rate"]
            ),
            "IC net": round_money(
                interest * (
                    1 - settings["savings_tax_rate"]
                )
            ),
            "Cap_IC": round_money(
                row["balance"]
            ),
        })

        previous_day_for_balance = value_day

    return {
        "account": "savings",
        "opening_balance": round_money(opening_balance),
        "closing_balance": round_money(
            balance_at_value_date(
                db,
                "savings",
                end_day,
            )
        ),
        "rows": table_rows,
        "columns": [
            "Dop",
            "Dv",
            "Débit",
            "Crédit",
            "SD",
            "SC",
            "NjC",
            "TxC",
            "NC",
            "IC",
            "TxImpC",
            "ImpC",
            "IC net",
            "Cap_IC",
        ],
    }


# ============================================================
# 18. CURRENT ACCOUNT TABLE
# ============================================================

def build_current_table(
    db,
    start_day: date,
    end_day: date,
    settings: dict,
):
    movements = build_movement_rows(
        db,
        "current",
        start_day,
        end_day,
    )

    opening_balance, rows = calculate_movement_balances(
        db,
        "current",
        movements,
        start_day,
    )

    table_rows = []

    previous_day_for_balance = start_day - timedelta(days=1)

    for row in rows:
        value_day = parse_date(row["date_valeur"])

        days_count = max(
            0,
            (value_day - previous_day_for_balance).days,
        )

        debit_balance = row["debit_balance"]

        number_debit = (
            debit_balance * days_count
        )

        debit_interest = (
            number_debit
            * settings["annual_overdraft_rate"]
            / ACCOUNTING_DIVISOR
        )

        commission = (
            debit_interest
            * settings["overdraft_commission_rate"]
        ) + settings["overdraft_fixed_commission"]

        commission = round_money(commission)

        vat = round_money(
            commission * settings["vat_rate"]
        )

        total_agios = round_money(
            debit_interest + commission + vat
        )

        table_rows.append({
            "Dop": row["date_operation"],
            "Dv": row["date_valeur"],
            "Débit": (
                row["amount"]
                if row["type"] in (
                    "withdrawal",
                    "transfer_out",
                )
                else 0.0
            ),
            "Crédit": (
                row["amount"]
                if row["type"] in (
                    "deposit",
                    "transfer_in",
                )
                else 0.0
            ),
            "SD": row["debit_balance"],
            "SC": row["credit_balance"],
            "NjD": days_count,
            "TxD": settings["annual_overdraft_rate"] * 100,
            "ND": round_money(number_debit),
            "ID": round_money(debit_interest),
            "CPFD": commission,
            "CD": vat,
            "Total Agios": total_agios,
        })

        previous_day_for_balance = value_day

    return {
        "account": "current",
        "opening_balance": round_money(opening_balance),
        "closing_balance": round_money(
            balance_at_value_date(
                db,
                "current",
                end_day,
            )
        ),
        "rows": table_rows,
        "columns": [
            "Dop",
            "Dv",
            "Débit",
            "Crédit",
            "SD",
            "SC",
            "NjD",
            "TxD",
            "ND",
            "ID",
            "CPFD",
            "CD",
            "Total Agios",
        ],
    }


# ============================================================
# 19. QUARTERLY ACCOUNTING REPORT
# ============================================================

def build_quarterly_report(
    db,
    start_day: date,
    end_day: date,
):
    settings = read_settings(db)

    savings = build_savings_table(
        db,
        start_day,
        end_day,
        settings,
    )

    current = build_current_table(
        db,
        start_day,
        end_day,
        settings,
    )

    savings_interest = calculate_savings_interest(
        db,
        start_day,
        end_day,
        settings["annual_savings_rate"],
    )

    savings_tax = calculate_savings_tax(
        savings_interest["gross_interest"],
        settings["savings_tax_rate"],
    )

    overdraft_interest = calculate_overdraft_interest(
        db,
        start_day,
        end_day,
        settings["annual_overdraft_rate"],
        settings["overdraft_free_limit"],
    )

    commissions = calculate_commissions(
        overdraft_interest["overdraft_interest"],
        settings,
    )

    total_agios = round_money(
        overdraft_interest["overdraft_interest"]
        + commissions["commission"]
        + commissions["vat"]
    )

    return {
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "savings": savings,
        "current": current,
        "summary": {
            "gross_interest": savings_interest["gross_interest"],
            "tax": savings_tax,
            "net_interest": round_money(
                savings_interest["gross_interest"]
                - savings_tax
            ),
            "overdraft_interest": (
                overdraft_interest["overdraft_interest"]
            ),
            "commissions": commissions["commission"],
            "vat": commissions["vat"],
            "total_agios": total_agios,
        },
        "settings": settings,
    }


# ============================================================
# 20. PAGE ROUTE
# ============================================================

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
        },
    )


# ============================================================
# 21. DASHBOARD API
# ============================================================

@app.get("/api/dashboard")
def dashboard():
    with connect_db() as db:
        accounts = get_account_balances(db)

        transactions = fetch_transactions(
            db,
            limit=100,
        )

        settings = read_settings(db)

        last_period = db.execute("""
            SELECT *
            FROM bank_periods
            ORDER BY end_date DESC
            LIMIT 1
        """).fetchone()

        total_balance = sum(
            item["balance"]
            for item in accounts.values()
        )

        return {
            "application": APP_NAME,
            "version": APP_VERSION,
            "accounts": list(accounts.values()),
            "transactions": transactions,
            "total": round_money(total_balance),
            "interest_rate": settings[
                "annual_savings_rate"
            ],
            "tax_rate": settings[
                "savings_tax_rate"
            ],
            "overdraft_rate": settings[
                "annual_overdraft_rate"
            ],
            "overdraft_free_limit": settings[
                "overdraft_free_limit"
            ],
            "settings": settings,
            "last_interest": (
                dict(last_period)
                if last_period
                else None
            ),
        }


# ============================================================
# 22. ACCOUNT DETAILS API
# ============================================================

@app.get("/api/accounts")
def accounts_list():
    with connect_db() as db:
        return {
            "accounts": list(
                get_account_balances(db).values()
            )
        }


@app.get("/api/accounts/{account_id}")
def account_details(account_id: str):
    if account_id not in ALLOWED_ACCOUNTS:
        raise HTTPException(
            status_code=404,
            detail="Compte introuvable.",
        )

    with connect_db() as db:
        account = get_account(db, account_id)

        transactions = fetch_transactions(
            db,
            account_id=account_id,
            limit=500,
        )

        return {
            "account": dict(account),
            "transactions": transactions,
        }


# ============================================================
# 23. CREATE DEPOSIT OR WITHDRAWAL
# ============================================================

@app.post("/api/transactions")
def create_transaction(data: TransactionRequest):
    validate_amount(data.amount)

    amount = round_money(data.amount)

    operation_day, value_day = resolve_transaction_dates(
        data.account,
        data.type,
        data.operation_date,
        data.value_date,
    )

    with connect_db() as db:
        account = get_account(
            db,
            data.account,
        )

        old_balance = float(account["balance"])

        if data.type == "deposit":
            new_balance = old_balance + amount

        else:
            new_balance = old_balance - amount

            if (
                data.account == "savings"
                and new_balance < 0
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Solde insuffisant sur le compte épargne."
                    ),
                )

        update_account_balance(
            db,
            data.account,
            new_balance,
        )

        insert_transaction(
            db,
            data.description,
            data.account,
            data.type,
            amount,
            operation_day,
            value_day,
        )

    return {
        "message": "Opération effectuée avec succès.",
        "account": data.account,
        "type": data.type,
        "amount": amount,
        "date_operation": operation_day.isoformat(),
        "date_valeur": value_day.isoformat(),
        "old_balance": round_money(old_balance),
        "new_balance": round_money(new_balance),
    }


# ============================================================
# 24. TRANSFER BETWEEN ACCOUNTS
# ============================================================

@app.post("/api/transfers")
def create_transfer(data: TransferRequest):
    validate_amount(data.amount)

    if data.from_account not in ALLOWED_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail="Compte source invalide.",
        )

    if data.to_account not in ALLOWED_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail="Compte destinataire invalide.",
        )

    if data.from_account == data.to_account:
        raise HTTPException(
            status_code=400,
            detail="Les deux comptes doivent être différents.",
        )

    amount = round_money(data.amount)

    operation_day = (
        data.operation_date or date.today()
    )

    source_value_day = (
        data.source_value_date
        or calculate_value_date(
            data.from_account,
            "withdrawal",
            operation_day,
        )
    )

    destination_value_day = (
        data.destination_value_date
        or calculate_value_date(
            data.to_account,
            "deposit",
            operation_day,
        )
    )

    with connect_db() as db:
        source = get_account(
            db,
            data.from_account,
        )

        destination = get_account(
            db,
            data.to_account,
        )

        source_balance = float(source["balance"])
        destination_balance = float(destination["balance"])

        new_source_balance = source_balance - amount
        new_destination_balance = destination_balance + amount

        if (
            data.from_account == "savings"
            and new_source_balance < 0
        ):
            raise HTTPException(
                status_code=400,
                detail="Solde insuffisant sur le compte épargne.",
            )

        update_account_balance(
            db,
            data.from_account,
            new_source_balance,
        )

        update_account_balance(
            db,
            data.to_account,
            new_destination_balance,
        )

        description = (
            data.description.strip()
            or "Transfert entre comptes"
        )

        insert_transaction(
            db,
            description + " - Sortie",
            data.from_account,
            "transfer_out",
            amount,
            operation_day,
            source_value_day,
        )

        insert_transaction(
            db,
            description + " - Entrée",
            data.to_account,
            "transfer_in",
            amount,
            operation_day,
            destination_value_day,
        )

    return {
        "message": "Transfert effectué avec succès.",
        "amount": amount,
        "date_operation": operation_day.isoformat(),
        "source_date_valeur": source_value_day.isoformat(),
        "destination_date_valeur": destination_value_day.isoformat(),
        "new_source_balance": round_money(
            new_source_balance
        ),
        "new_destination_balance": round_money(
            new_destination_balance
        ),
    }


# ============================================================
# 25. HISTORY API
# ============================================================

@app.get("/api/transactions")
def transactions_list(
    account: Optional[str] = None,
    limit: int = 500,
):
    if account and account not in ALLOWED_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail="Compte invalide.",
        )

    limit = max(1, min(limit, 2000))

    with connect_db() as db:
        return {
            "transactions": fetch_transactions(
                db,
                account_id=account,
                limit=limit,
            )
        }


# ============================================================
# 26. ACCOUNTING TABLES API
# ============================================================

@app.get("/api/accounting")
def accounting_tables(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    today = date.today()

    if start_date is None:
        _, start_date, _ = get_quarter(today)

    if end_date is None:
        end_date = today

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="La date de fin doit suivre la date de début.",
        )

    if (end_date - start_date).days > 370:
        raise HTTPException(
            status_code=400,
            detail="La période demandée est trop longue.",
        )

    with connect_db() as db:
        report = build_quarterly_report(
            db,
            start_date,
            end_date,
        )

    return report


@app.get("/api/accounting/savings")
def savings_accounting(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    today = date.today()

    if start_date is None:
        _, start_date, _ = get_quarter(today)

    if end_date is None:
        end_date = today

    with connect_db() as db:
        settings = read_settings(db)

        return build_savings_table(
            db,
            start_date,
            end_date,
            settings,
        )


@app.get("/api/accounting/current")
def current_accounting(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    today = date.today()

    if start_date is None:
        _, start_date, _ = get_quarter(today)

    if end_date is None:
        end_date = today

    with connect_db() as db:
        settings = read_settings(db)

        return build_current_table(
            db,
            start_date,
            end_date,
            settings,
        )


# ============================================================
# 27. QUARTERLY INTEREST APPLICATION
# ============================================================

@app.post("/api/interest/apply")
def apply_quarterly_interest():
    today = date.today()

    period, quarter_start, quarter_end = get_quarter(
        today
    )

    calculation_end = min(
        today,
        quarter_end,
    )

    with connect_db() as db:
        already_applied = db.execute("""
            SELECT *
            FROM bank_periods
            WHERE period = ?
        """, (period,)).fetchone()

        if already_applied:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Les intérêts de cette période "
                    "ont déjà été appliqués."
                ),
            )

        settings = read_settings(db)

        savings = get_account(
            db,
            "savings",
        )

        current = get_account(
            db,
            "current",
        )

        savings_result = calculate_savings_interest(
            db,
            quarter_start,
            calculation_end,
            settings["annual_savings_rate"],
        )

        gross_interest = savings_result[
            "gross_interest"
        ]

        tax = calculate_savings_tax(
            gross_interest,
            settings["savings_tax_rate"],
        )

        net_interest = round_money(
            gross_interest - tax
        )

        overdraft_result = calculate_overdraft_interest(
            db,
            quarter_start,
            calculation_end,
            settings["annual_overdraft_rate"],
            settings["overdraft_free_limit"],
        )

        debit_interest = overdraft_result[
            "overdraft_interest"
        ]

        commission_result = calculate_commissions(
            debit_interest,
            settings,
        )

        commission = commission_result[
            "commission"
        ]

        vat = commission_result["vat"]

        total_agios = round_money(
            debit_interest + commission + vat
        )

        new_savings_balance = round_money(
            float(savings["balance"]) + net_interest
        )

        new_current_balance = round_money(
            float(current["balance"]) - total_agios
        )

        update_account_balance(
            db,
            "savings",
            new_savings_balance,
        )

        update_account_balance(
            db,
            "current",
            new_current_balance,
        )

        now = datetime.now()

        if gross_interest > 0:
            insert_transaction(
                db,
                f"Intérêts créditeurs {period}",
                "savings",
                "interest",
                gross_interest,
                today,
                today,
                now,
            )

        if tax > 0:
            insert_transaction(
                db,
                f"Impôt sur intérêts {period}",
                "savings",
                "tax",
                tax,
                today,
                today,
                now,
            )

        if debit_interest > 0:
            insert_transaction(
                db,
                f"Intérêts débiteurs {period}",
                "current",
                "overdraft_interest",
                debit_interest,
                today,
                today,
                now,
            )

        if commission > 0:
            insert_transaction(
                db,
                f"Commissions bancaires {period}",
                "current",
                "commission",
                commission,
                today,
                today,
                now,
            )

        if vat > 0:
            insert_transaction(
                db,
                f"TVA sur commissions {period}",
                "current",
                "vat",
                vat,
                today,
                today,
                now,
            )

        db.execute("""
            INSERT INTO bank_periods (
                period,
                start_date,
                end_date,
                gross_interest,
                tax,
                net_interest,
                overdraft_interest,
                commissions,
                vat,
                total_agios,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            period,
            quarter_start.isoformat(),
            calculation_end.isoformat(),
            gross_interest,
            tax,
            net_interest,
            debit_interest,
            commission,
            vat,
            total_agios,
            now.isoformat(timespec="seconds"),
        ))

    return {
        "message": "Clôture trimestrielle effectuée.",
        "period": period,
        "start_date": quarter_start.isoformat(),
        "end_date": calculation_end.isoformat(),
        "gross_interest": gross_interest,
        "tax": tax,
        "net_interest": net_interest,
        "overdraft_interest": debit_interest,
        "commissions": commission,
        "vat": vat,
        "total_agios": total_agios,
        "new_savings_balance": new_savings_balance,
        "new_current_balance": new_current_balance,
    }


# ============================================================
# 28. QUARTERLY CLOSING HISTORY
# ============================================================

@app.get("/api/closings")
def closings_history():
    with connect_db() as db:
        rows = db.execute("""
            SELECT *
            FROM bank_periods
            ORDER BY end_date DESC
        """).fetchall()

    return {
        "closings": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# 29. SETTINGS API
# ============================================================

@app.get("/api/settings")
def settings_get():
    with connect_db() as db:
        settings = read_settings(db)

    return {
        "bank_name": APP_NAME,
        "currency": "DT",
        "settings": settings,
        "value_date_offsets": VALUE_DATE_OFFSETS,
        "accounting_divisor": ACCOUNTING_DIVISOR,
        "days_per_year": DAYS_PER_YEAR,
    }


@app.put("/api/settings")
def settings_update(data: SettingsRequest):
    updates = data.model_dump(
        exclude_none=True
    )

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="Aucun paramètre à modifier.",
        )

    with connect_db() as db:
        save_settings(
            db,
            updates,
        )

        settings = read_settings(db)

    return {
        "message": "Paramètres mis à jour.",
        "settings": settings,
    }


# ============================================================
# 30. QUARTER PREVIEW API
# ============================================================

@app.get("/api/interest/preview")
def interest_preview(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    today = date.today()

    if start_date is None:
        _, start_date, _ = get_quarter(today)

    if end_date is None:
        end_date = today

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="Période invalide.",
        )

    with connect_db() as db:
        report = build_quarterly_report(
            db,
            start_date,
            end_date,
        )

    return {
        "message": "Simulation uniquement : aucun montant appliqué.",
        "report": report,
    }


# ============================================================
# 31. HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health_check():
    with connect_db() as db:
        db.execute("SELECT 1").fetchone()

    return {
        "status": "ok",
        "application": APP_NAME,
        "version": APP_VERSION,
        "database": "connected",
    }


# ============================================================
# 32. ROOT INFORMATION API
# ============================================================

@app.get("/api")
def api_information():
    return {
        "application": APP_NAME,
        "version": APP_VERSION,
        "description": (
            "SmartBank Pro - Account management "
            "and accounting simulation"
        ),
        "routes": {
            "dashboard": "/api/dashboard",
            "accounts": "/api/accounts",
            "transactions": "/api/transactions",
            "transfers": "/api/transfers",
            "accounting": "/api/accounting",
            "savings_table": "/api/accounting/savings",
            "current_table": "/api/accounting/current",
            "interest_preview": "/api/interest/preview",
            "interest_apply": "/api/interest/apply",
            "settings": "/api/settings",
            "closings": "/api/closings",
            "health": "/api/health",
        },
    }
# ============================================================
# TEMPORARY SECURE DATABASE BACKUP
# ============================================================

@app.get("/admin/backup")
def download_database_backup(
    x_backup_token: Optional[str] = Header(default=None),
):
    expected_token = os.getenv("SMARTBANK_BACKUP_TOKEN")

    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Backup token is not configured.",
        )

    if (
        not x_backup_token
        or not secrets.compare_digest(
            x_backup_token,
            expected_token,
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied.",
        )

    backup_db = sqlite3.connect(":memory:")

    try:
        with connect_db() as source:
            source.backup(backup_db)

        backup_data = backup_db.serialize()

    finally:
        backup_db.close()

    return Response(
        content=backup_data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                'attachment; filename="smartbank_render_backup.db"'
            )
        },
    )

# ============================================================
# TEMPORARY SECURE LOCAL DATABASE RESTORE
# ============================================================

@app.post("/admin/restore-local")
async def restore_local_database(
    request: Request,
    x_backup_token: Optional[str] = Header(default=None),
):
    expected_token = os.getenv("SMARTBANK_BACKUP_TOKEN")

    if (
        not expected_token
        or not x_backup_token
        or not secrets.compare_digest(
            x_backup_token,
            expected_token,
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Access denied.",
        )

    payload = await request.body()

    if not payload or len(payload) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Invalid backup file.",
        )

    temporary_path = None
    source = None

    try:
        # SQLite PRAGMA integrity_check is more reliable against a real file
        # than deserializing the uploaded bytes into an in-memory connection.
        with tempfile.NamedTemporaryFile(
            prefix="smartbank_restore_",
            suffix=".db",
            delete=False,
        ) as temporary_file:
            temporary_file.write(payload)
            temporary_path = temporary_file.name

        source = sqlite3.connect(temporary_path)
        source.row_factory = sqlite3.Row

        integrity = source.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise HTTPException(
                status_code=400,
                detail="Backup integrity check failed.",
            )

        required_tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        required = {
            "accounts",
            "transactions",
            "bank_periods",
            "bank_settings",
        }
        if not required.issubset(required_tables):
            raise HTTPException(
                status_code=400,
                detail="Backup is missing required tables.",
            )

        accounts = [
            dict(row)
            for row in source.execute(
                "SELECT * FROM accounts"
            ).fetchall()
        ]
        transactions = [
            dict(row)
            for row in source.execute(
                "SELECT * FROM transactions ORDER BY id"
            ).fetchall()
        ]
        periods = [
            dict(row)
            for row in source.execute(
                "SELECT * FROM bank_periods"
            ).fetchall()
        ]
        settings = [
            dict(row)
            for row in source.execute(
                "SELECT * FROM bank_settings"
            ).fetchall()
        ]

        account_ids = {account.get("id") for account in accounts}
        if not {"current", "savings"}.issubset(account_ids):
            raise HTTPException(
                status_code=400,
                detail="Required accounts are missing.",
            )

        if any(
            transaction.get("account_id") not in account_ids
            for transaction in transactions
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid transaction account.",
            )

    except sqlite3.DatabaseError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SQLite backup: {exc}",
        ) from exc

    finally:
        if source is not None:
            source.close()
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass

    # The live database is modified only after the uploaded backup has
    # passed integrity and required-data checks. connect_db rolls back
    # the complete restore if any insert fails.
    with connect_db() as db:
        db.execute("DELETE FROM transactions")
        db.execute("DELETE FROM bank_periods")
        db.execute("DELETE FROM accounts")
        db.execute("DELETE FROM bank_settings")

        for account in accounts:
            db.execute("""
                INSERT INTO accounts
                    (id, name, balance, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                account["id"],
                account["name"],
                account["balance"],
                account.get("created_at"),
            ))

        for transaction in transactions:
            operation_date = (
                transaction.get("date_operation")
                or transaction.get("date")
            )
            value_date = (
                transaction.get("date_valeur")
                or str(transaction.get("date", ""))[:10]
            )
            created_at = (
                transaction.get("created_at")
                or transaction.get("date")
            )

            db.execute("""
                INSERT INTO transactions (
                    id, description, account_id, type,
                    amount, date, date_operation,
                    date_valeur, created_at, reference
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                transaction.get("id"),
                transaction["description"],
                transaction["account_id"],
                transaction["type"],
                transaction["amount"],
                transaction.get("date") or created_at,
                operation_date,
                value_date,
                created_at,
                transaction.get("reference"),
            ))

        for period in periods:
            period_name = str(period["period"])
            try:
                year_text, quarter_text = period_name.split("-T", 1)
                year = int(year_text)
                quarter = int(quarter_text)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid period name: {period_name}",
                )

            if quarter not in (1, 2, 3, 4):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid quarter.",
                )

            first_month = (quarter - 1) * 3 + 1
            quarter_start = date(year, first_month, 1)
            last_month = first_month + 2
            quarter_end = date(
                year,
                last_month,
                monthrange(year, last_month)[1],
            )

            old_date = period.get("date") or period.get("end_date")
            end_date = (
                str(old_date)[:10]
                if old_date
                else quarter_end.isoformat()
            )
            created_at = (
                period.get("created_at")
                or period.get("date")
                or datetime.now().isoformat(timespec="seconds")
            )

            db.execute("""
                INSERT INTO bank_periods (
                    period, start_date, end_date,
                    gross_interest, tax, net_interest,
                    overdraft_interest, commissions,
                    vat, total_agios, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                period_name,
                quarter_start.isoformat(),
                end_date,
                period.get("gross_interest", 0),
                period.get("tax", 0),
                period.get("net_interest", 0),
                period.get("overdraft_interest", 0),
                period.get("commissions", 0),
                period.get("vat", 0),
                period.get("total_agios", 0),
                created_at,
            ))

        for setting in settings:
            db.execute("""
                INSERT INTO bank_settings
                    (setting_key, setting_value)
                VALUES (?, ?)
            """, (
                setting["setting_key"],
                setting["setting_value"],
            ))

    return {
        "message": "Local database restored successfully.",
        "accounts": len(accounts),
        "transactions": len(transactions),
        "periods": len(periods),
        "settings": len(settings),
    }
