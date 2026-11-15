
"use strict";

// =====================================================
// SMARTBANK PRO - JAVASCRIPT
// =====================================================

const API = "";

// DOM ELEMENTS
const currentBalance = document.getElementById("currentBalance");
const savingsBalance = document.getElementById("savingsBalance");
const totalBalance = document.getElementById("totalBalance");

const transactionForm = document.getElementById("transactionForm");
const transferForm = document.getElementById("transferForm");

const transactionList = document.getElementById("transactionList");
const message = document.getElementById("message");
const transferMessage = document.getElementById("transferMessage");
const interestMessage = document.getElementById("interestMessage");

const filter = document.getElementById("filter");
const search = document.getElementById("search");

let transactions = [];
let balanceChart = null;


// =====================================================
// HELPERS
// =====================================================

function formatMoney(value) {
    return Number(value || 0).toLocaleString("fr-FR", {
        minimumFractionDigits: 3,
        maximumFractionDigits: 3
    }) + " DT";
}

function showMessage(element, text, error = false) {
    element.textContent = text;
    element.classList.toggle("error", error);
}

async function apiRequest(url, options = {}) {
    const response = await fetch(API + url, {
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        },
        ...options
    });

    let data;

    try {
        data = await response.json();
    } catch {
        throw new Error("Réponse invalide du serveur.");
    }

    if (!response.ok) {
        throw new Error(data.detail || "Une erreur est survenue.");
    }

    return data;
}


// =====================================================
// LOAD DASHBOARD
// =====================================================

async function loadDashboard() {
    try {
        const data = await apiRequest("/api/dashboard");

        const current = data.accounts.find(
            account => account.id === "current"
        );

        const savings = data.accounts.find(
            account => account.id === "savings"
        );

        currentBalance.textContent = formatMoney(
            current ? current.balance : 0
        );

        savingsBalance.textContent = formatMoney(
            savings ? savings.balance : 0
        );

        totalBalance.textContent = formatMoney(data.total);

        transactions = data.transactions || [];

        renderTransactions();
        updateChart(data.accounts);

    } catch (error) {
        console.error(error);

        showMessage(
            message,
            "Impossible de charger les données : " + error.message,
            true
        );
    }
}


// =====================================================
// TRANSACTION HISTORY
// =====================================================

function getTransactionCategory(type) {
    if (type === "deposit") return "Dépôt";
    if (type === "withdrawal") return "Retrait";
    if (type === "transfer_out") return "Transfert envoyé";
    if (type === "transfer_in") return "Transfert reçu";
    if (type === "interest") return "Intérêts";
    if (type === "tax") return "Impôt";

    return type;
}

function matchesFilter(transaction, selectedFilter) {
    if (selectedFilter === "all") return true;

    if (selectedFilter === "transfer") {
        return [
            "transfer_in",
            "transfer_out"
        ].includes(transaction.type);
    }

    return transaction.type === selectedFilter;
}

function renderTransactions() {
    const selectedFilter = filter.value;

    const searchText = search
        ? search.value.trim().toLowerCase()
        : "";

    const filtered = transactions.filter(transaction => {
        const matchesType = matchesFilter(
            transaction,
            selectedFilter
        );

        const text = (
            transaction.description + " " +
            transaction.account_name + " " +
            transaction.type
        ).toLowerCase();

        return matchesType && text.includes(searchText);
    });

    if (filtered.length === 0) {
        transactionList.innerHTML = `
            <tr>
                <td colspan="5">Aucune transaction trouvée.</td>
            </tr>
        `;
        return;
    }

    transactionList.innerHTML = filtered.map(transaction => {
        const positive = [
            "deposit",
            "transfer_in",
            "interest"
        ].includes(transaction.type);

        const sign = positive ? "+" : "-";

        const color = positive ? "#45d9ae" : "#ff7d89";

        return `
            <tr>
                <td>${escapeHTML(transaction.description)}</td>
                <td>${escapeHTML(transaction.date)}</td>
                <td>${escapeHTML(transaction.account_name)}</td>
                <td>${escapeHTML(getTransactionCategory(transaction.type))}</td>
                <td style="color:${color};font-weight:700">
                    ${sign}${formatMoney(transaction.amount)}
                </td>
            </tr>
        `;
    }).join("");
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[character]);
}


// =====================================================
// BALANCE CHART
// =====================================================

function updateChart(accounts) {
    const canvas = document.getElementById("balanceChart");

    if (!canvas || typeof Chart === "undefined") {
        return;
    }

    const current = accounts.find(
        account => account.id === "current"
    );

    const savings = accounts.find(
        account => account.id === "savings"
    );

    const values = [
        current ? current.balance : 0,
        savings ? savings.balance : 0
    ];

    if (balanceChart) {
        balanceChart.data.datasets[0].data = values;
        balanceChart.update();
        return;
    }

    balanceChart = new Chart(canvas, {
        type: "doughnut",

        data: {
            labels: [
                "Compte courant",
                "Compte épargne"
            ],

            datasets: [{
                data: values,
                backgroundColor: [
                    "#438cff",
                    "#20c6ad"
                ],
                borderColor: "#111c30",
                borderWidth: 5,
                hoverOffset: 10
            }]
        },

        options: {
            responsive: true,
            maintainAspectRatio: false,

            plugins: {
                legend: {
                    position: "bottom",
                    labels: {
                        color: "#b5c3d7",
                        padding: 22,
                        usePointStyle: true
                    }
                }
            },

            cutout: "70%"
        }
    });
}


// =====================================================
// DEPOSIT AND WITHDRAWAL
// =====================================================

transactionForm.addEventListener("submit", async event => {
    event.preventDefault();

    const description = document.getElementById("description").value.trim();
    const account = document.getElementById("account").value;
    const type = document.getElementById("type").value;
    const amount = Number(document.getElementById("amount").value);

    if (!description || !Number.isFinite(amount) || amount <= 0) {
        showMessage(message, "Veuillez vérifier les informations.", true);
        return;
    }

    const button = transactionForm.querySelector(
        'button[type="submit"]'
    );

    button.disabled = true;

    try {
        const result = await apiRequest("/api/transactions", {
            method: "POST",
            body: JSON.stringify({
                description,
                account,
                type,
                amount
            })
        });

        showMessage(message, result.message);

        transactionForm.reset();

        await loadDashboard();

    } catch (error) {
        showMessage(message, error.message, true);

    } finally {
        button.disabled = false;
    }
});


// =====================================================
// TRANSFERS
// =====================================================

transferForm.addEventListener("submit", async event => {
    event.preventDefault();

    const fromAccount = document.getElementById("fromAccount").value;
    const toAccount = document.getElementById("toAccount").value;

    const amount = Number(
        document.getElementById("transferAmount").value
    );

    const description = document.getElementById(
        "transferDescription"
    ).value.trim() || "Transfert entre comptes";

    if (fromAccount === toAccount) {
        showMessage(
            transferMessage,
            "Les deux comptes doivent être différents.",
            true
        );
        return;
    }

    if (!Number.isFinite(amount) || amount <= 0) {
        showMessage(
            transferMessage,
            "Montant invalide.",
            true
        );
        return;
    }

    const button = transferForm.querySelector(
        'button[type="submit"]'
    );

    button.disabled = true;

    try {
        const result = await apiRequest("/api/transfers", {
            method: "POST",
            body: JSON.stringify({
                from_account: fromAccount,
                to_account: toAccount,
                amount,
                description
            })
        });

        showMessage(transferMessage, result.message);

        transferForm.reset();

        await loadDashboard();

    } catch (error) {
        showMessage(transferMessage, error.message, true);

    } finally {
        button.disabled = false;
    }
});


// =====================================================
// QUARTERLY INTEREST
// =====================================================

document.getElementById("applyInterest").addEventListener(
    "click",
    async event => {
        const button = event.currentTarget;

        button.disabled = true;

        try {
            const result = await apiRequest(
                "/api/interest/apply",
                { method: "POST" }
            );

            showMessage(
                interestMessage,
                `Intérêts bruts : ${formatMoney(result.gross_interest)} | ` +
                `Impôt : ${formatMoney(result.tax)} | ` +
                `Intérêts nets : ${formatMoney(result.net_interest)}`
            );

            await loadDashboard();

        } catch (error) {
            showMessage(
                interestMessage,
                error.message,
                true
            );

        } finally {
            button.disabled = false;
        }
    }
);


// =====================================================
// FILTERS AND SEARCH
// =====================================================

filter.addEventListener("change", renderTransactions);

if (search) {
    search.addEventListener("input", renderTransactions);
}


// =====================================================
// QUICK ACTION
// =====================================================

document.getElementById("focusForm").addEventListener(
    "click",
    () => {
        document.getElementById("description").focus();

        document.getElementById("transactions").scrollIntoView({
            behavior: "smooth"
        });
    }
);


// =====================================================
// INITIALIZATION
// =====================================================

document.addEventListener("DOMContentLoaded", () => {
    loadDashboard();
});
