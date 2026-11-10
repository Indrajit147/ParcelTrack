# ParcelTrack

Courier and Parcel Tracking System — Database System Laboratory (ECE 3210), KUET, Group B8.

A web application for a multi-branch courier company. Every button on the site runs a real
SQL statement on a PostgreSQL database.

**Stack:** PostgreSQL · Python Flask · psycopg2 (raw SQL) · Bootstrap

The work plan is in [MILESTONES.md](MILESTONES.md).

## Setup

1. Install Python 3 and PostgreSQL.
2. Install the packages:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and put your PostgreSQL password in it.
