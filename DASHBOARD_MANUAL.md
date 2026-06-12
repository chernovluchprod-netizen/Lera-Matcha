# Taal Cafe Dashboard — User Manual

> **Who this is for:** Anyone who needs to update the data or use the dashboard.  
> No coding experience required.

---

## 1. What is this system?

The Taal Cafe Dashboard is a live financial reporting tool that reads your exported files (Excel, CSV, PDF) from several folders, processes them automatically, and displays charts and tables in a browser — just like a website, but running on your own computer.

It shows:
- Monthly P&L (Profit & Loss) with expandable cost detail
- Sales performance and daily trends
- Product category breakdown
- Labor costs, freelancer hours, and payroll weight
- Gross margin, EBITDA, and other KPIs

---

## 2. The folder structure

```
Taal Data/
│
├── Sales/              ← Drop your POS / sales Excel files here
├── Costs/              ← Drop your GL (grootboek) Excel files here
├── Hours/              ← Drop your freelancer hours Excel files here
├── Labor/              ← Drop Journaalposten PDFs here (payroll journals)
├── Product sales/      ← Drop ProductReport CSV files here
│
├── Dashboard Data/     ← Auto-generated. DO NOT edit these files manually.
│   ├── monthly_pl.csv          (processed P&L per month)
│   ├── monthly_kpis.csv        (KPIs like margin %, avg basket)
│   ├── transactions.csv        (all POS transactions)
│   ├── labor_hours.csv         (freelancer shifts)
│   ├── costs_ledger.csv        (all GL cost entries)
│   ├── product_sales.csv       (product category revenue)
│   └── payroll_journal.csv     (parsed payroll from PDFs)
│
├── pipeline.py         ← The "engine" — reads source files, builds Dashboard Data
├── dashboard.py        ← The "screen" — displays the dashboard in the browser
├── watcher.py          ← Auto-runs pipeline when files change (optional)
└── start_watcher.sh    ← Double-click to start auto-watching (Mac)
```

**Rule of thumb:**
- You only interact with the 5 source folders (Sales, Costs, Hours, Labor, Product sales)
- Everything in `Dashboard Data/` is generated automatically — never edit it

---

## 3. How to update the data

### Step 1 — Add your new files

Drop the new Excel/CSV/PDF files into the correct folder:

| New data you have | Drop it into |
|---|---|
| Sales report / POS export | `Sales/` |
| Grootboekmutatiekaarten (GL costs) | `Costs/` |
| Freelancer hours sheet | `Hours/` |
| Journaalposten PDF (payroll) | `Labor/` |
| ProductReport CSV | `Product sales/` |

You can keep old files in the folder — the pipeline only re-processes files that changed.

### Step 2 — Run the pipeline

Open **Terminal** and type:

```
cd "/Users/alexanderchernov/Calude code VS/Taal Data"
python3 pipeline.py
```

Wait for it to finish. You'll see a line like:
```
Done. Manifest updated with 40 source files.
```

If you want to force a full reprocess (e.g. after changing something):
```
python3 pipeline.py --force
```

### Step 3 — Restart the dashboard

If the dashboard is already open in your browser, stop it first (press **Ctrl+C** in the terminal window where it's running), then:

```
python3 dashboard.py
```

Open your browser and go to: **http://127.0.0.1:8050**

---

## 4. Auto-update mode (hands-free)

Instead of manually running the pipeline every time, you can start the **watcher** — it monitors the folders and runs the pipeline automatically whenever you drop in a new file.

**On Mac:** double-click `start_watcher.sh`

**Or in Terminal:**
```
python3 watcher.py
```

Leave the terminal window open. Every time you drop a new file into any of the 5 folders, it will automatically rebuild the data after a 5-second delay. You still need to **refresh the browser** to see the new data (press F5 or Cmd+R).

To stop the watcher: press **Ctrl+C** in the terminal.

---

## 5. What the special markers mean in the dashboard

| Symbol | Meaning |
|---|---|
| `*` (asterisk) next to a month | Revenue is from POS data — accountant hasn't booked it in GL yet |
| `†` (dagger) next to a month | Wages are from the payroll PDF — accountant hasn't entered them in GL yet |

Once the accountant books the figures and you export a new GL file into `Costs/`, re-run the pipeline and the markers will disappear automatically.

---

## 6. Troubleshooting

**Dashboard shows old data after I added files**
→ You need to run `python3 pipeline.py` and then restart `python3 dashboard.py`.

**"Port 8050 is already in use" error**
→ The dashboard is already running in another terminal window. Either use that window's browser tab, or close it (Ctrl+C there) and restart.

**"Module not found" error**
→ A Python package is missing. Run:
```
pip3 install dash dash-bootstrap-components plotly pandas pdfplumber watchdog openpyxl
```

**Pipeline says "No changes detected"**
→ The files didn't change since last run. Use `--force` to reprocess everything:
```
python3 pipeline.py --force
```

---

## 7. Running the dashboard on a remote server (web-based, with password)

Right now the dashboard runs only on your Mac and is only accessible at `http://127.0.0.1:8050` — meaning only you can see it while sitting at that machine.

If you want the dashboard available from anywhere (phone, other laptop, team members) under a password, here are the practical options:

---

### Option A — Render.com (easiest, free tier available)

Render is a hosting platform that can run Python apps with no server management.

**What you need:**
1. A free account at [render.com](https://render.com)
2. Your project pushed to a private GitHub repository
3. A small configuration file added to the project

**How it works:**
- Render runs `python3 dashboard.py` on their servers 24/7
- You get a public URL like `https://taal-dashboard.onrender.com`
- You add HTTP Basic Auth (username + password) using a Dash middleware or an nginx layer
- Data updates require re-running the pipeline and committing the `Dashboard Data/` CSVs to GitHub

**Cost:** Free tier sleeps after 15 min of inactivity (wakes in ~30s). Paid plan (~$7/month) stays always-on.

---

### Option B — Your own VPS (Digital Ocean / Hetzner)

A Virtual Private Server is a small cloud computer you rent (~€5–10/month). You install Python on it, copy your project there, and run the dashboard permanently.

**Rough steps:**
1. Rent a server (Ubuntu 22.04, 1GB RAM is enough)
2. Copy project files via SFTP (use FileZilla — drag and drop)
3. Install Python packages on the server
4. Run the dashboard with a process manager (e.g. `pm2` or `supervisor`) so it restarts automatically
5. Put **nginx** in front of it to add HTTPS and password protection:

```nginx
location / {
    auth_basic "Taal Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8050;
}
```

6. Generate a password file:
```
htpasswd -c /etc/nginx/.htpasswd yourname
```

**Result:** `https://your-domain.com` — password-protected, always available.

---

### Option C — Dash Enterprise (Plotly's official solution)

Plotly (the company behind Dash) offers **Dash Enterprise** — a platform specifically built for deploying Dash apps with authentication, multi-user access, and scheduled data refresh built in.

- Includes user login, role-based access, and app management
- More expensive (~$500+/month), aimed at teams
- Not necessary for a single-cafe use case

---

### Option D — Streamlit Cloud (alternative, requires rewriting)

If you ever wanted to rebuild the dashboard, Streamlit Cloud is even simpler to deploy — but it would require rewriting `dashboard.py` in a different framework (Streamlit instead of Dash).

---

### Recommendation for Taal Cafe

**Short term:** Keep running locally on your Mac — it's fast, free, and requires no setup.

**When you need remote access:** Go with **Option B (Hetzner VPS, ~€5/month)**. Hetzner is a reliable German provider (GDPR-compliant), and the setup takes about 2 hours. Your data stays in Europe and you have full control.

For the password: nginx Basic Auth is simple and sufficient. If multiple people need different access levels, Dash's built-in `dash-auth` package can handle that with a simple username/password dictionary in the code.

---

*Manual last updated: April 2026*
