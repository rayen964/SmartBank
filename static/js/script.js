const currentBalance = document.getElementById("currentBalance");
const savingsBalance = document.getElementById("savingsBalance");
const totalBalance = document.getElementById("totalBalance");

const transactionForm = document.getElementById("transactionForm");
const transactionList = document.getElementById("transactionList");
const message = document.getElementById("message");

const search = document.getElementById("search");
const filter = document.getElementById("filter");

let dashboard = {
    accounts: [],
    transactions: [],
    total: 0
};

let chart = null;


// FORMAT MONEY

function money(value) {
    return Number(value).toLocaleString("fr-TN", {
        minimumFractionDigits: 3,
        maximumFractionDigits: 3
    }) + " DT";
}


// LOAD DATA FROM PYTHON

async function loadDashboard() {
    try {
        const response = await fetch("/api/dashboard");

        if (!response.ok) {
            throw new Error("Cannot load dashboard.");
        }

        dashboard = await response.json();

        updateBalances();
        displayTransactions();
        updateChart();

    } catch (error) {
        showMessage(error.message, true);
    }
}


// UPDATE BALANCES

function updateBalances() {
    const current = dashboard.accounts.find(
        account => account.id === "current"
    );

    const savings = dashboard.accounts.find(
        account => account.id === "savings"
    );

    currentBalance.textContent = money(current?.balance ?? 0);
    savingsBalance.textContent = money(savings?.balance ?? 0);
    totalBalance.textContent = money(dashboard.total);
}


// SHOW MESSAGE

function showMessage(text, error = false) {
    message.textContent = text;
    message.classList.toggle("error", error);
}


// DISPLAY TRANSACTIONS

function displayTransactions() {

    const keyword = search.value.toLowerCase();
    const selectedType = filter.value;

    const filtered = dashboard.transactions.filter(transaction => {

        const matchesSearch =
            transaction.description.toLowerCase().includes(keyword) ||
            transaction.account_name.toLowerCase().includes(keyword);

        const matchesType =
            selectedType === "all" ||
            transaction.type === selectedType;

        return matchesSearch && matchesType;
    });

    if (filtered.length === 0) {
        transactionList.innerHTML =
            '<tr><td colspan="5">No transactions found.</td></tr>';
        return;
    }

    transactionList.innerHTML = filtered.map(transaction => {

        const deposit = transaction.type === "deposit";
        const sign = deposit ? "+" : "-";
        const label = deposit ? "Dépôt" : "Retrait";

        return `
            <tr>
                <td>${escapeHTML(transaction.description)}</td>
                <td>${transaction.date}</td>
                <td>${escapeHTML(transaction.account_name)}</td>
                <td>${label}</td>
                <td class="${transaction.type}">
                    ${sign}${money(transaction.amount)}
                </td>
            </tr>
        `;

    }).join("");
}


// PROTECT HTML FROM USER INPUT

function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[character]);
}


// CREATE CHART

function updateChart() {

    const canvas = document.getElementById("balanceChart");

    if (typeof Chart === "undefined") {
        canvas.parentElement.textContent =
            "Chart unavailable. Check your internet connection.";
        return;
    }

    if (chart) {
        chart.destroy();
    }

    chart = new Chart(canvas, {
        type: "bar",

        data: {
            labels: dashboard.accounts.map(a => a.name),

            datasets: [{
                label: "Balance",
                data: dashboard.accounts.map(a => a.balance),
                backgroundColor: ["#1265c5", "#08a4b8"],
                borderRadius: 9
            }]
        },

        options: {
            responsive: true,
            maintainAspectRatio: false,

            plugins: {
                legend: {
                    display: false
                }
            },

            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}


// ADD TRANSACTION

transactionForm.addEventListener("submit", async function(event) {

    event.preventDefault();

    const data = {
        description: document.getElementById("description").value.trim(),
        account: document.getElementById("account").value,
        type: document.getElementById("type").value,
        amount: Number(document.getElementById("amount").value)
    };

    if (!data.description || !Number.isFinite(data.amount) ||
        data.amount <= 0) {

        showMessage("Vérifiez les informations.", true);
        return;
    }

    const button = transactionForm.querySelector("button");
    button.disabled = true;
    button.textContent = "Adding...";

    try {

        const response = await fetch("/api/transactions", {
            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.detail || "Transaction failed.");
        }

        transactionForm.reset();

        showMessage(result.message);

        await loadDashboard();

    } catch (error) {

        showMessage(error.message, true);

    } finally {

        button.disabled = false;
        button.textContent = "＋ Add Transaction";
    }
});


// SEARCH AND FILTER

search.addEventListener("input", displayTransactions);
filter.addEventListener("change", displayTransactions);


// QUICK ACTION

document.getElementById("focusForm").addEventListener("click", () => {
    document.getElementById("description").focus();

    document.getElementById("transactions").scrollIntoView({
        behavior: "smooth"
    });
});


// START APPLICATION

loadDashboard();