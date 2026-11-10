# ParcelTrack — Milestones

Courier and Parcel Tracking System, Database System Laboratory (ECE 3210), Group B8.

Stack: PostgreSQL, Python Flask, psycopg2 (raw SQL), HTML/CSS/JavaScript with Bootstrap.

## M1 — Project setup
- [x] Git repository, `.gitignore`, `requirements.txt`, `.env.example`
- [x] README with setup steps and this milestone plan

## M2 — Database schema
- [x] Nine tables with primary keys, foreign keys, UNIQUE and CHECK constraints
- [x] Sequence-based tracking number
- [x] Trigger that keeps `parcels.current_status` in sync with the tracking log
- [x] Report views (parcel details, rider deliveries, branch volume, delayed deliveries)

## M3 — Sample data
- [x] Branches, service types, riders, customers and an admin account
- [x] Parcels in every status with matching shipments, tracking events and payments
- [x] Reference SQL queries (`db/queries.sql`)

## M4 — Backend foundation
- [ ] Flask app, database connection, one transaction per request
- [ ] Login / logout for admin, customer and rider with hashed passwords
- [ ] CSRF protection and friendly database error messages
- [ ] Base layout and home page

## M5 — Customer features
- [ ] Register and update profile (INSERT, UPDATE)
- [ ] Track a parcel by tracking number (SELECT with JOIN)
- [ ] Book a parcel with automatic charge calculation (INSERT)
- [ ] Cancel a booked parcel (DELETE)
- [ ] Parcel history

## M6 — Admin management
- [ ] Add, edit, remove and search branches, riders, service types and customers
- [ ] Parcel list with search and status filter, parcel detail page
- [ ] Assign a rider for pickup or delivery
- [ ] Update parcel status and record payments

## M7 — Rider features
- [ ] Task list for the logged-in rider
- [ ] Update status of assigned parcels, cash-on-delivery collection

## M8 — Dashboard and reports
- [ ] Dashboard counters (parcels, in transit, delivered, returned, riders on duty, revenue)
- [ ] Revenue reports (SUM, GROUP BY)
- [ ] Rider-wise deliveries, branch-wise parcel count, delayed deliveries, customer history

## M9 — Testing and documentation
- [ ] Automated end-to-end test of the main flows
- [ ] ER diagram and final README
