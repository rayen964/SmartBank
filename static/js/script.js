
"use strict";

/* =========================================================
   SMARTBANK — JAVASCRIPT APPLICATION
   Version 2.0 — Banking Management System
   ========================================================= */

/* ===================== CONFIGURATION ===================== */

const API = {
    dashboard: "/api/dashboard",
    settings: "/api/settings",
    transactions: "/api/transactions",
    savings: "/api/accounting/savings",
    current: "/api/accounting/current",
    accounting: "/api/accounting",
    applyInterest: "/api/interest/apply"
};

const CONFIG = {
    currency: "DT",
    decimals: 3,
    defaultSavingsRate: 2.5,
    defaultTaxRate: 20,
    defaultOverdraftRate: 5,
    defaultOverdraftLimit: 200
};

const state = {
    dashboard: null,
    settings: null,
    savings: null,
    current: null,
    loading: false
};

/* ===================== DOM HELPERS ===================== */

const $ = (id) => document.getElementById(id);

function setText(id, value) {
    const element = $(id);
    if (element) element.textContent = value;
}

function showMessage(id, message, type = "success") {
    const element = $(id);
    if (!element) return;

    element.textContent = message;
    element.className = type === "error"
        ? "form-message error"
        : "form-message success";
    element.style.display = "block";
}

function hideMessage(id) {
    const element = $(id);
    if (!element) return;

    element.textContent = "";
    element.className = "form-message";
    element.style.display = "none";
}

function showCalculationMessage(message, type = "success") {
    const element = $("calculationMessage");
    if (!element) return;

    element.textContent = message;
    element.className = type === "error"
        ? "calculation-message error"
        : "calculation-message success";
    element.style.display = "block";
}

/* ===================== FORMATTING ===================== */

function numberValue(value) {
    const result = Number(value);
    return Number.isFinite(result) ? result : 0;
}

function formatMoney(value) {
    return new Intl.NumberFormat("fr-TN", {
        minimumFractionDigits: CONFIG.decimals,
        maximumFractionDigits: CONFIG.decimals
    }).format(numberValue(value)) + " " + CONFIG.currency;
}

function formatNumber(value, decimals = 3) {
    return new Intl.NumberFormat("fr-TN", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    }).format(numberValue(value));
}

function formatPercent(value) {
    return formatNumber(value, 2) + " %";
}

function formatDate(value) {
    if (!value) return "—";

    const date = new Date(value + (String(value).length === 10 ? "T00:00:00" : ""));

    if (Number.isNaN(date.getTime())) return String(value);

    return new Intl.DateTimeFormat("fr-TN").format(date);
}

function todayISO() {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
}

function addDays(dateString, days) {
    const date = new Date(dateString + "T12:00:00");
    date.setDate(date.getDate() + days);

    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");

    return `${year}-${month}-${day}`;
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => {
        const entities = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;"
        };
        return entities[character];
    });
}

/* ===================== API HELPERS ===================== */

async function apiRequest(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    let data = {};

    try {
        data = await response.json();
    } catch {
        data = {};
    }

    if (!response.ok) {
        const message =
            data.detail ||
            data.message ||
            `Erreur serveur (${response.status})`;

        throw new Error(
            Array.isArray(message)
                ? message.map(item => item.msg || String(item)).join(" — ")
                : String(message)
        );
    }

    return data;
}

async function apiGet(url) {
    return apiRequest(url, { method: "GET" });
}

async function apiPost(url, body = {}) {
    return apiRequest(url, {
        method: "POST",
        body: JSON.stringify(body)
    });
}

/* ===================== DATE INITIALIZATION ===================== */

function initializeDates() {
    const today = todayISO();

    setText("todayDate", formatDate(today));

    const operationDate = $("operationDate");
    const valueDate = $("valueDate");

    if (operationDate && !operationDate.value) {
        operationDate.value = today;
    }

    if (valueDate && !valueDate.value) {
        valueDate.value = addDays(today, 1);
    }

    const start = addDays(today, -90);

    ["savingsStartDate", "currentStartDate"].forEach(id => {
        const element = $(id);
        if (element && !element.value) element.value = start;
    });

    ["savingsEndDate", "currentEndDate"].forEach(id => {
        const element = $(id);
        if (element && !element.value) element.value = today;
    });
}

/* ===================== VALUE DATE MANAGEMENT ===================== */

function getValueDateOffset(account, type) {
    const offsets = state.settings?.value_date_offsets;

    if (offsets && offsets[account]) {
        const accountOffsets = offsets[account];

        if (typeof accountOffsets[type] === "number") {
            return accountOffsets[type];
        }
    }

    const defaults = {
        current: {
            deposit: 1,
            withdrawal: -1
        },
        savings: {
            deposit: 7,
            withdrawal: -7
        }
    };

    return defaults[account]?.[type] ?? 1;
}

function updateSuggestedValueDate() {
    const operationDate = $("operationDate");
    const valueDate = $("valueDate");
    const account = $("account");
    const type = $("type");

    if (!operationDate || !valueDate || !account || !type) return;

    if (!operationDate.value) return;

    const offset = getValueDateOffset(account.value, type.value);

    valueDate.value = addDays(operationDate.value, offset);

    valueDate.min = "";
}

function validateDates(operationDate, valueDate) {
    if (!operationDate || !valueDate) {
        return "Veuillez saisir les deux dates.";
    }

    if (operationDate === valueDate) {
        return "La date opération et la date valeur doivent être différentes.";
    }

    return null;
}

/* ===================== DASHBOARD ===================== */

async function loadDashboard() {
    const data = await apiGet(API.dashboard);

    state.dashboard = data;

    const accounts = Array.isArray(data.accounts) ? data.accounts : [];

    let currentBalance = 0;
    let savingsBalance = 0;

    accounts.forEach(account => {
        const name = String(account.id || account.name || "").toLowerCase();
        const balance = numberValue(account.balance);

        if (name.includes("current") || name.includes("courant")) {
            currentBalance = balance;
        }

        if (name.includes("savings") || name.includes("épargne") || name.includes("epargne")) {
            savingsBalance = balance;
        }
    });

    setText("totalBalance", formatMoney(
        data.total ?? currentBalance + savingsBalance
    ));

    setText("currentBalance", formatMoney(currentBalance));
    setText("savingsBalance", formatMoney(savingsBalance));

    setText("currentTableBalance", formatMoney(currentBalance));
    setText("savingsTableBalance", formatMoney(savingsBalance));

    renderHistory(data.transactions || []);
}

/* ===================== SETTINGS ===================== */

async function loadSettings() {
    try {
        const data = await apiGet(API.settings);
        state.settings = data;

        const savingsRate = numberValue(
            data.annual_interest_rate ?? CONFIG.defaultSavingsRate
        );

        const taxRate = numberValue(
            data.interest_tax_rate ?? CONFIG.defaultTaxRate
        );

        const overdraftRate = numberValue(
            data.overdraft_annual_rate ?? CONFIG.defaultOverdraftRate
        );

        const overdraftLimit = numberValue(
            data.overdraft_free_limit ?? CONFIG.defaultOverdraftLimit
        );

        setText("interestRate", formatPercent(savingsRate));
        setText("savingsRate", formatPercent(savingsRate));
        setText("savingsTaxRate", formatPercent(taxRate));
        setText("overdraftRate", formatPercent(overdraftRate));
        setText("overdraftLimit", formatMoney(overdraftLimit));

    } catch (error) {
        console.warn("Impossible de charger les paramètres :", error.message);
    }
}

/* ===================== ACCOUNTING TABLE HELPERS ===================== */

function getRowValue(row, keys, index) {
    if (Array.isArray(row)) {
        return row[index] ?? "";
    }

    if (!row || typeof row !== "object") return "";

    for (const key of keys) {
        if (row[key] !== undefined && row[key] !== null) {
            return row[key];
        }
    }

    return "";
}

function formatCell(value, columnName) {
    if (value === null || value === undefined || value === "") {
        return "—";
    }

    const dateColumns = ["Dop", "Dv"];

    if (dateColumns.includes(columnName)) {
        return formatDate(value);
    }

    if (typeof value === "number") {
        return formatNumber(value, 3);
    }

    return String(value);
}

function renderAccountingRows(tbodyId, footerId, data, columns) {
    const tbody = $(tbodyId);
    const footer = $(footerId);

    if (!tbody) return;

    const rows = Array.isArray(data?.rows) ? data.rows : [];

    if (rows.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="${columns.length}" class="empty-state">
                    <i class="fa-solid fa-inbox"></i>
                    Aucune opération pour cette période.
                </td>
            </tr>
        `;

        if (footer) {
            footer.innerHTML = `
                <tr>
                    <td colspan="${columns.length}">
                        Solde d'ouverture : ${formatMoney(data?.opening_balance)}
                        &nbsp; | &nbsp;
                        Solde de clôture : ${formatMoney(data?.closing_balance)}
                    </td>
                </tr>
            `;
        }

        return;
    }

    const aliases = {
        Dop: ["Dop", "dop", "date_operation", "operation_date"],
        Dv: ["Dv", "dv", "date_valeur", "value_date"],
        Débit: ["Débit", "debit", "debit_amount"],
        Crédit: ["Crédit", "credit", "credit_amount"],
        SD: ["SD", "sd", "solde_debiteur"],
        SC: ["SC", "sc", "solde_crediteur"],
        NjC: ["NjC", "njc", "nombre_jours_crediteurs"],
        TxC: ["TxC", "txc", "taux_crediteur"],
        NC: ["NC", "nc", "nombre_crediteur"],
        IC: ["IC", "ic", "interet_crediteur"],
        TxImpC: ["TxImpC", "tximpc", "taux_impot"],
        ImpC: ["ImpC", "impc", "impot_crediteur"],
        "IC net": ["IC net", "ic_net", "interet_net"],
        Cap_IC: ["Cap_IC", "cap_ic", "capital_interet"],
        NjD: ["NjD", "njd", "nombre_jours_debiteurs"],
        TxD: ["TxD", "txd", "taux_debiteur"],
        ND: ["ND", "nd", "nombre_debiteur"],
        ID: ["ID", "id_interet", "interet_debiteur"],
        CPFD: ["CPFD", "cpfd", "commission_plus_fort_decouvert"],
        CD: ["CD", "cd", "commission_decouvert"],
        "Total Agios": ["Total Agios", "total_agios", "agios"]
    };

    tbody.innerHTML = rows.map(row => {
        const cells = columns.map((column, index) => {
            const keys = aliases[column] || [column];
            const value = getRowValue(row, keys, index);
            return `<td>${escapeHTML(formatCell(value, column))}</td>`;
        }).join("");

        return `<tr>${cells}</tr>`;
    }).join("");

    if (footer) {
        const totals = data.totals || data.summary || {};

        const opening = formatMoney(data.opening_balance);
        const closing = formatMoney(data.closing_balance);

        const extra = Object.entries(totals)
            .map(([key, value]) => `${escapeHTML(key)} : ${escapeHTML(formatNumber(value))}`)
            .join(" | ");

        footer.innerHTML = `
            <tr>
                <td colspan="${columns.length}">
                    Solde initial : ${opening}
                    &nbsp; | &nbsp;
                    Solde final : ${closing}
                    ${extra ? `&nbsp; | &nbsp; ${extra}` : ""}
                </td>
            </tr>
        `;
    }
}

/* ===================== LOAD SAVINGS ACCOUNTING ===================== */

const SAVINGS_COLUMNS = [
    "Dop", "Dv", "Débit", "Crédit", "SD", "SC",
    "NjC", "TxC", "NC", "IC", "TxImpC", "ImpC",
    "IC net", "Cap_IC"
];

async function loadSavingsAccounting() {
    const start = $("savingsStartDate")?.value || "";
    const end = $("savingsEndDate")?.value || "";

    const params = new URLSearchParams();

    if (start) params.set("start_date", start);
    if (end) params.set("end_date", end);

    const url = `${API.savings}?${params.toString()}`;

    try {
        const data = await apiGet(url);

        state.savings = data;

        renderAccountingRows(
            "savingsTableBody",
            "savingsTableFooter",
            data,
            SAVINGS_COLUMNS
        );

        if (data.closing_balance !== undefined) {
            setText("savingsTableBalance", formatMoney(data.closing_balance));
        }

    } catch (error) {
        console.error("Savings accounting:", error);

        setTableError(
            "savingsTableBody",
            14,
            "Impossible de charger le tableau épargne."
        );
    }
}

/* ===================== LOAD CURRENT ACCOUNTING ===================== */

const CURRENT_COLUMNS = [
    "Dop", "Dv", "Débit", "Crédit", "SD", "SC",
    "NjD", "TxD", "ND", "ID", "CPFD", "CD",
    "Total Agios"
];

async function loadCurrentAccounting() {
    const start = $("currentStartDate")?.value || "";
    const end = $("currentEndDate")?.value || "";

    const params = new URLSearchParams();

    if (start) params.set("start_date", start);
    if (end) params.set("end_date", end);

    const url = `${API.current}?${params.toString()}`;

    try {
        const data = await apiGet(url);

        state.current = data;

        renderAccountingRows(
            "currentTableBody",
            "currentTableFooter",
            data,
            CURRENT_COLUMNS
        );

        if (data.closing_balance !== undefined) {
            setText("currentTableBalance", formatMoney(data.closing_balance));
        }

    } catch (error) {
        console.error("Current accounting:", error);

        setTableError(
            "currentTableBody",
            13,
            "Impossible de charger le tableau courant."
        );
    }
}

function setTableError(id, colspan, message) {
    const tbody = $(id);
    if (!tbody) return;

    tbody.innerHTML = `
        <tr>
            <td colspan="${colspan}" class="empty-state">
                <i class="fa-solid fa-triangle-exclamation"></i>
                ${escapeHTML(message)}
            </td>
        </tr>
    `;
}

/* ===================== LOAD BOTH TABLES ===================== */

async function loadAccounting() {
    await Promise.all([
        loadSavingsAccounting(),
        loadCurrentAccounting()
    ]);
}

/* ===================== TRANSACTION HISTORY ===================== */

function getTransactionType(type) {
    const normalized = String(type || "").toLowerCase();

    if (normalized.includes("deposit") || normalized.includes("dépôt") ||
        normalized.includes("depot") || normalized.includes("transfer_in")) {
        return "deposit";
    }

    return "withdrawal";
}

function renderHistory(transactions) {
    const tbody = $("historyTableBody");
    if (!tbody) return;

    if (!Array.isArray(transactions) || transactions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-state">
                    <i class="fa-solid fa-folder-open"></i>
                    Aucune transaction enregistrée.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = transactions.map(transaction => {
        const type = getTransactionType(transaction.type);
        const isDeposit = type === "deposit";

        const accountName =
            transaction.account_name ||
            transaction.account ||
            (transaction.account_id === "savings"
                ? "Compte Épargne"
                : "Compte Courant");

        const operationDate =
            transaction.operation_date ||
            transaction.date_operation ||
            transaction.date ||
            "";

        const valueDate =
            transaction.value_date ||
            transaction.date_valeur ||
            "";

        const amount = numberValue(transaction.amount);

        return `
            <tr>
                <td>${escapeHTML(formatDate(operationDate))}</td>
                <td>${escapeHTML(formatDate(valueDate))}</td>
                <td>${escapeHTML(accountName)}</td>
                <td>${escapeHTML(transaction.description || "—")}</td>
                <td>
                    <span class="transaction-type ${type}">
                        <i class="fa-solid ${isDeposit ? "fa-arrow-down" : "fa-arrow-up"}"></i>
                        ${isDeposit ? "Dépôt" : "Retrait"}
                    </span>
                </td>
                <td class="${isDeposit ? "amount-positive" : "amount-negative"}">
                    ${isDeposit ? "+" : "−"} ${escapeHTML(formatMoney(amount))}
                </td>
            </tr>
        `;
    }).join("");
}

/* ===================== CREATE TRANSACTION ===================== */

async function submitTransaction(event) {
    event.preventDefault();

    const form = $("transactionForm");
    const submitButton = $("submitTransactionBtn");

    if (!form || !submitButton) return;

    hideMessage("transactionMessage");

    const account = $("account").value;
    const type = $("type").value;
    const amount = Number($("amount").value);
    const description = $("description").value.trim();

    const operationDate = $("operationDate").value;
    const valueDate = $("valueDate").value;

    if (!description) {
        showMessage("transactionMessage", "Veuillez saisir une description.", "error");
        return;
    }

    if (!Number.isFinite(amount) || amount <= 0) {
        showMessage("transactionMessage", "Le montant doit être supérieur à zéro.", "error");
        return;
    }

    const dateError = validateDates(operationDate, valueDate);

    if (dateError) {
        showMessage("transactionMessage", dateError, "error");
        return;
    }

    const payload = {
        description,
        account,
        type,
        amount,
        operation_date: operationDate,
        value_date: valueDate
    };

    submitButton.disabled = true;
    submitButton.innerHTML = `
        <i class="fa-solid fa-spinner fa-spin"></i>
        Enregistrement...
    `;

    try {
        const result = await apiPost(API.transactions, payload);

        showMessage(
            "transactionMessage",
            result.message || "Transaction enregistrée avec succès.",
            "success"
        );

        form.reset();

        initializeDates();
        updateSuggestedValueDate();

        await refreshAll();

    } catch (error) {
        showMessage(
            "transactionMessage",
            error.message || "Erreur lors de l'enregistrement.",
            "error"
        );
    } finally {
        submitButton.disabled = false;
        submitButton.innerHTML = `
            <i class="fa-solid fa-check"></i>
            Enregistrer l'opération
        `;
    }
}

/* ===================== QUARTERLY INTEREST ===================== */

async function applyQuarterlyInterest() {
    const button = $("calculateInterestBtn");

    if (!button) return;

    button.disabled = true;
    button.innerHTML = `
        <i class="fa-solid fa-spinner fa-spin"></i>
        Calcul en cours...
    `;

    const message = $("calculationMessage");

    if (message) {
        message.style.display = "none";
    }

    try {
        const result = await apiPost(API.applyInterest, {});

        const details = [
            result.message || "Calcul terminé.",
            result.period ? `Période : ${result.period}` : "",
            result.gross_interest !== undefined
                ? `Intérêts bruts : ${formatMoney(result.gross_interest)}`
                : "",
            result.tax !== undefined
                ? `Impôt : ${formatMoney(result.tax)}`
                : "",
            result.net_interest !== undefined
                ? `Intérêts nets : ${formatMoney(result.net_interest)}`
                : "",
            result.overdraft_interest !== undefined
                ? `Intérêts débiteurs : ${formatMoney(result.overdraft_interest)}`
                : ""
        ].filter(Boolean);

        showCalculationMessage(details.join(" | "), "success");

        await refreshAll();

    } catch (error) {
        showCalculationMessage(
            error.message || "Erreur pendant le calcul.",
            "error"
        );
    } finally {
        button.disabled = false;
        button.innerHTML = `
            <i class="fa-solid fa-play"></i>
            Lancer le calcul
        `;
    }
}

/* ===================== REFRESH ===================== */

async function refreshAll() {
    const results = await Promise.allSettled([
        loadDashboard(),
        loadAccounting()
    ]);

    results.forEach(result => {
        if (result.status === "rejected") {
            console.error("Refresh error:", result.reason);
        }
    });
}

async function refreshHistory() {
    try {
        const data = await apiGet(API.dashboard);
        state.dashboard = data;
        renderHistory(data.transactions || []);
    } catch (error) {
        console.error("History refresh:", error);
    }
}

/* ===================== FILTER BUTTONS ===================== */

function setupFilters() {
    const savingsButton = $("filterSavingsBtn");
    const currentButton = $("filterCurrentBtn");

    if (savingsButton) {
        savingsButton.addEventListener("click", async () => {
            const start = $("savingsStartDate").value;
            const end = $("savingsEndDate").value;

            if (start && end && start > end) {
                alert("La date de début doit être antérieure à la date de fin.");
                return;
            }

            await loadSavingsAccounting();
        });
    }

    if (currentButton) {
        currentButton.addEventListener("click", async () => {
            const start = $("currentStartDate").value;
            const end = $("currentEndDate").value;

            if (start && end && start > end) {
                alert("La date de début doit être antérieure à la date de fin.");
                return;
            }

            await loadCurrentAccounting();
        });
    }
}

/* ===================== NAVIGATION ===================== */

function setupNavigation() {
    const sidebar = $("sidebar");
    const overlay = $("mobileOverlay");
    const menuToggle = $("menuToggle");

    function closeMenu() {
        sidebar?.classList.remove("open");
        overlay?.classList.remove("active");
    }

    if (menuToggle) {
        menuToggle.addEventListener("click", () => {
            sidebar?.classList.toggle("open");
            overlay?.classList.toggle("active");
        });
    }

    if (overlay) {
        overlay.addEventListener("click", closeMenu);
    }

    document.querySelectorAll(".nav-link").forEach(link => {
        link.addEventListener("click", () => {
            document.querySelectorAll(".nav-link").forEach(item => {
                item.classList.remove("active");
            });

            link.classList.add("active");
            closeMenu();
        });
    });

    const sections = document.querySelectorAll(
        "#dashboard, #savings, #current, #operations, #history"
    );

    if ("IntersectionObserver" in window) {
        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;

                const id = entry.target.id;

                document.querySelectorAll(".nav-link").forEach(link => {
                    link.classList.toggle(
                        "active",
                        link.getAttribute("href") === `#${id}`
                    );
                });
            });
        }, {
            rootMargin: "-20% 0px -65% 0px"
        });

        sections.forEach(section => observer.observe(section));
    }
}

/* ===================== EVENT LISTENERS ===================== */

function setupEvents() {
    const form = $("transactionForm");

    if (form) {
        form.addEventListener("submit", submitTransaction);
    }

    const account = $("account");
    const type = $("type");
    const operationDate = $("operationDate");

    account?.addEventListener("change", updateSuggestedValueDate);
    type?.addEventListener("change", updateSuggestedValueDate);
    operationDate?.addEventListener("change", updateSuggestedValueDate);

    $("calculateInterestBtn")?.addEventListener(
        "click",
        applyQuarterlyInterest
    );

    $("refreshHistoryBtn")?.addEventListener(
        "click",
        refreshHistory
    );

    $("resetTransactionBtn")?.addEventListener("click", () => {
        setTimeout(() => {
            initializeDates();
            updateSuggestedValueDate();
            hideMessage("transactionMessage");
        }, 0);
    });

    setupFilters();
    setupNavigation();
}

/* ===================== APPLICATION INITIALIZATION ===================== */

async function initializeApplication() {
    initializeDates();
    setupEvents();

    try {
        await loadSettings();
        updateSuggestedValueDate();

        await refreshAll();

    } catch (error) {
        console.error("SmartBank initialization error:", error);

        setTableError(
            "savingsTableBody",
            14,
            "Erreur de connexion au serveur."
        );

        setTableError(
            "currentTableBody",
            13,
            "Erreur de connexion au serveur."
        );
    }
}

/* ===================== START APPLICATION ===================== */

document.addEventListener("DOMContentLoaded", () => {
    initializeApplication();
});
