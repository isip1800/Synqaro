# Synqaro

Synqaro is an ERP for an **import & wholesale distribution** business. It covers buying from overseas suppliers, landing the goods and selling them to wholesale customers. Every operational step posts to a real **double-entry general ledger**.

Built with Django 5. It uses server-rendered pages with no front-end build step. It runs on SQLite out of the box and on PostgreSQL in production.

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
- SKU, barcode, category tree, unit of measure.
- **Customs data:** HS code, country of origin, duty rate, weight, volume (CBM), units per carton.
- List price, standard cost and **moving-average landed cost**, plus a margin indicator.
- Vendor price catalogue (vendor SKU, cost, currency, MOQ, lead time) and preferred vendor.

### Inventory Management
- Multi-warehouse perpetual inventory, with a full stock-move ledger.
- Stock on hand & valuation by warehouse and category.
- **Reorder report:** projected stock (on hand + on order − reserved) vs reorder point, with suggested quantities and a "Create PO" shortcut.
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

## Production notes

Set these environment variables:
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- for PostgreSQL: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
- optionally `DJANGO_TIME_ZONE`

Then run `python manage.py collectstatic` and serve with gunicorn or uWSGI behind a web server that also serves `media/` (uploaded documents).

## Roadmap ideas
- Role-based permissions per module (Django groups are already available)
- Lot / batch and expiry tracking; bin locations
- Sales returns and credit notes; vendor returns and debit notes
- Bank reconciliation and period close
- Email sending of POs, invoices and statements; PDF generation
- Multi-currency sales invoicing
- CSV import/export for products, customers and opening balances
