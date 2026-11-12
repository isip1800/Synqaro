# Synqaro

Synqaro is an ERP for an **import & wholesale distribution** business. It covers buying from overseas suppliers, landing the goods and selling them to wholesale customers. Every operational step posts to a real **double-entry general ledger**.

Built with Django 5. It uses server-rendered pages with no front-end build step. It runs on SQLite for local development and on PostgreSQL in production. See [Deploying to Render](#deploying-to-render) for the hosted web version.

## Quick start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # optional: full demo company with real transactions
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in:

| Role | Username | Password | What they see |
|---|---|---|---|
| Staff / admin | `admin` | `admin12345` | Entire ERP + Django admin |
| Sales person | `maria` | `demo12345` | ERP; non-staff reps see only their own customers & orders |
| Vendor portal | `sunrise` | `demo12345` | Only Sunrise Manufacturing's POs, shipments and invoices |

For a clean start without demo data, run `python manage.py setup_erp --admin <username> --password <password>`. That creates the chart of accounts, currencies and a superuser. Then go to **Settings → Company** to set your name, base currency and default warehouse.

Run the tests with `python manage.py test`.

## Modules

### Sales
- Quotations become sales orders, with expiry dates and customer PO numbers.
- **Price lists with quantity tiers.** A line price defaults to the customer's price list, otherwise the product's list price.
- **Credit control.** Confirming an order checks AR balance + uninvoiced open orders + the new order against the credit limit. An explicit, recorded override is available.
- Partial shipments (deliveries / packing slips) relieve stock at average landed cost and post COGS.
- One click invoices everything that has shipped but isn't invoiced yet.
- Customers have AR balance, available credit and a printable statement of account.

### Purchasing & Import Management
- Purchase orders in **any currency**, with exchange rate, Incoterms, payment terms and destination warehouse.
- PO lifecycle: draft → sent → **confirmed by vendor** → partially received / received → closed.
- **Import shipments** cover containers, air freight, LCL and so on:
  - mode, carrier, vessel/voyage, ports, B/L or AWB, container numbers
  - ETD/ETA and actual arrival, customs entry number and clearance date
  - a visual status timeline (planned → booked → in transit → at port → customs → cleared → received)
- **Landed costs:** freight, insurance, duty, tariffs, brokerage, port, drayage, demurrage and more.
  - They are allocated across the goods **by value, quantity, weight or volume**, exact to the cent.
  - **Duty estimate** from each product's HS code duty rate.
  - On receipt, landed costs are **capitalized into inventory** so each SKU carries its true landed unit cost.
  - A landed cost billed *after* receipt can still be capitalized. The share for stock still on hand raises its average cost, and the share for goods already sold goes to COGS.
- Shipment documents (commercial invoice, packing list, B/L, certificate of origin, and so on) can be uploaded.
- Direct PO receipts handle domestic purchases that don't need shipment tracking.
- Draft vendor bills can be created straight from a PO (received but not yet billed).

### Full accounting integration
Every document posts automatically to the general ledger:

| Event | Journal |
|---|---|
| Goods received (shipment / PO) | Dr Inventory · Cr Goods Received Not Invoiced · Cr Accrued Landed Costs |
| Supplier bill (PO lines) | Dr GRNI · Dr/Cr Purchase Price Variance (price & FX) · Cr Accounts Payable |
| Forwarder / broker / duty bill | Dr Accrued Landed Costs · Cr Accounts Payable |
| Vendor payment (foreign currency) | Dr AP (at bill rate) · Cr Bank (at payment rate) · Dr/Cr Realized FX |
| Delivery | Dr COGS · Cr Inventory (moving-average landed cost) |
| Customer invoice | Dr Accounts Receivable · Cr Sales · Cr Sales Tax |
| Customer payment | Dr Bank · Cr Accounts Receivable |
| Stock adjustment | Dr/Cr Inventory · Cr/Dr Inventory Adjustments (or Opening Balance Equity) |
| Commission accrual / payout | Dr Commission Expense · Cr Commissions Payable → Dr Payable · Cr Bank |

- **Receivables:** invoices (from orders or free-form), payments with allocation (manual or auto to oldest), partial payments, unapplied credits, and AR aging (current / 1–30 / 31–60 / 61–90 / 90+).
- **Payables:** bills in vendor currency, duplicate vendor-invoice protection, over-billing protection, multi-currency payments, and AP aging in base currency.
- **General ledger:** chart of accounts (system accounts are auto-created), manual journal entries with balance validation, reversing entries, and per-account ledger with running balance.
- **Reports:** Profit & Loss (with COGS and gross profit), Balance Sheet (with current earnings), Trial Balance, AR/AP aging, and inventory valuation.

### Product Management
- **Stock items**, each with these tabs:
  - General, Item details, Packaging, Warehouses, Vendors, Cross-reference, Images, Activity.
  - "Copy as new" duplicates an item.
- **Three-tier item classes:** Tier 1 Class → Tier 2 Subclass → Tier 3 Product Type, e.g. `INT -UNDERWEAR -10PKPANTIE` = Basics / Underwear / 10 Pack Panties.
- **Item details:**
  - Fields: Brand, Package Size, Season, Seasonal Cutoff, Gender, Size Range, Size Breakdown, Item Spec, Material, Customer Code, Label 1/2, Multi PC Set, plus monthly carrying cost.
  - Each list has its own menu under **Products → Product Setup**.
- **HTS code table:**
  - Each row holds an HTS code, duty rate and tariff rate, keyed by item class + material + gender. A row with a blank gender applies to any gender.
  - Items pick up the matching row automatically, and updates to the table flow to items. A per-item override is available.
  - Includes a "Waiting on info" tab and Excel import/export.
- **Packaging & containers:**
  - Prepack qty, carton qty, carton dimensions (inches or cm) → carton CBM and CUFT.
  - Qty per 20' / 40' / 40'HQ / 45' container, full cartons only. Defaults are 27 / 58 / 67 / 76 CBM, editable in **Settings → Company**.
  - There's a live calculator on the item form, and per-unit volume and weight are derived for landed-cost allocation.
- **UPC:**
  - A GS1 company prefix and next item reference are set in **Settings → Company**.
  - **Generate UPC** assigns the next number with its check digit and skips numbers already in use.
  - Typed UPCs are validated, and the check digit is added when you enter 11 digits.
- **Cross-references:** barcodes, vendor part numbers and customer part numbers.
- **Images:**
  - Multiple photos per item, with a main image shown as the thumbnail on the item list.
  - Stored in the database, so they survive redeploys.
  - **Export to Excel** puts each item's picture *inside* its cell (Excel 365 / 2021+ "Place in Cell" pictures).
- **Per-warehouse settings:** bin location, reorder point, reorder qty and max qty per warehouse.
- **Pricing:** list price, standard cost and moving-average landed cost. Vendor prices (vendor SKU, cost, currency, MOQ, lead time) and quantity-tier price lists.

### Inventory Management
- Multi-warehouse perpetual inventory, with a full stock-move ledger.
- Stock on hand & valuation by warehouse and category.
- **Reorder report:** projected stock (on hand + on order − reserved) vs reorder point, with suggested quantities and a "Create PO" shortcut.
  - Items with warehouse settings are checked per warehouse; other items are checked company-wide.
- Stock adjustments (cycle count, damage, shrinkage, opening balances) and inter-warehouse transfers.
- Overselling is prevented: deliveries cannot exceed stock in the shipping warehouse.

### Sales People Management
- Sales reps with territory, manager, monthly target and optional login.
- **Commission plans:** % of revenue or % of gross margin, earned on invoice or on customer payment (prorated for partial payments).
- Commissions are approved and then paid in batches, and payouts post to the ledger.
- Performance dashboard: revenue, margin, margin %, target attainment and commission by rep and month.
- Rep detail: 6-month history, open quotes and orders, customers' open invoices.

### Vendor Portal
Suppliers sign in at the same login page and are confined to `/portal/`, where they see only their own data. They can:
- Review purchase orders (with their own SKUs and HS codes) and **confirm them** with a ready/ship date and their PI number.
- File **advance shipping notices**: vessel, B/L, containers, ETD/ETA, which PO lines and how many cartons.
- Update shipment status while it is in transit, and **upload shipping documents**.
- **Submit invoices** against POs. These arrive as draft bills for AP approval.
- See invoice status and payments received.

Staff create portal users from the vendor page or **Settings → Vendor Portal Users**.

## Project layout

```
synqaro/      settings & root URLs
core/         company, currencies/FX, numbering, CRUD framework, dashboard, demo seed
products/     products, categories, UoM, vendor prices, price lists
inventory/    warehouses, stock quants & moves, adjustments, transfers, costing service
purchasing/   vendors, POs, shipments, landed costs, documents, receipts
sales/        customers, sales orders/quotes, deliveries
accounting/   chart of accounts, journal, AR invoices/payments, AP bills/payments, reports
salesteam/    sales reps, commissions, payouts, performance
portal/       vendor portal views and portal user management
```

Business rules live in each app's `services.py`, as transactional functions that validate and post. Views stay thin, and the tests drive the services directly.

## Deploying to Render

The repository contains a Render Blueprint (`render.yaml`) that creates a web service and a PostgreSQL database.

1. Sign in at https://dashboard.render.com and connect your GitHub account if you haven't already. Give Render access to this repository.
2. Click **New → Blueprint**. Pick this repository and the branch to deploy (whichever branch has `render.yaml`).
3. When asked, enter a value for **ADMIN_PASSWORD**. This is the password for the `admin` login, so use a strong one.
4. Click **Apply**. The first deploy takes a few minutes. Then open the URL shown on the `synqaro` service (`https://synqaro-xxxx.onrender.com`).

On first start the app runs migrations, creates the admin user and (because `SEED_DEMO=1`) loads the demo company.
- The demo also creates `maria` / `demo12345` and `sunrise` / `demo12345`.
- **Before real use**, either set `SEED_DEMO=0` on a fresh database, or delete the demo users in **Admin → Users**.

What the scripts do:
- `build.sh` installs dependencies and collects static files.
- `start.sh` runs migrations and setup, then starts gunicorn.
- WhiteNoise serves static files.
- Uploaded shipment documents are served only to signed-in users.

Free-plan limits (check Render's current pricing page):
- The free web service sleeps when idle, so the first request after a quiet period is slow.
- The free PostgreSQL database expires after a limited period. Upgrade the database before you rely on the data.
- Free web services have no persistent disk, so **uploaded documents are lost on each redeploy or restart**.
- For real use, move to a paid instance and add a persistent disk mounted at, for example, `/var/data`, then set `MEDIA_ROOT=/var/data/media`.

## Production settings (any host)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (or `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_HOST`/`POSTGRES_PORT`) |
| `DJANGO_SECRET_KEY` | Long random secret |
| `DJANGO_DEBUG=0` | Production mode (HTTPS redirect, secure cookies, HSTS) |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames, e.g. `erp.example.com` (Render's hostname is added automatically) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | e.g. `https://erp.example.com` when using a custom domain |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Initial superuser created by `start.sh` |
| `SEED_DEMO` | `1` loads demo data into an empty database |
| `MEDIA_ROOT` | Where uploaded documents are stored |

## Roadmap ideas
- Role-based permissions per module (Django groups are already available)
- Lot / batch and expiry tracking; bin locations
- Sales returns and credit notes; vendor returns and debit notes
- Bank reconciliation and period close
- Email sending of POs, invoices and statements; PDF generation
- Multi-currency sales invoicing
- CSV import/export for products, customers and opening balances
