"""ParcelTrack - Courier and Parcel Tracking System (Flask + PostgreSQL)."""
import os
import secrets

import psycopg2
from psycopg2 import errors
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import admin
import customer
import db
import rider
from ops import NEXT_STATUS, OpError, clean

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.teardown_request(db.close_db)
app.register_blueprint(customer.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(rider.bp)

HOME = {'admin': '/admin/', 'customer': '/customer/', 'rider': '/rider/'}

# who logs in with what
LOGIN_SQL = {
    'customer': "SELECT customer_id AS id, full_name AS name, password_hash FROM customers WHERE email = %s",
    'rider': "SELECT rider_id AS id, full_name AS name, password_hash FROM riders WHERE phone = %s AND status <> 'inactive'",
    'admin': "SELECT admin_id AS id, username AS name, password_hash FROM admins WHERE username = %s",
}

# friendly text for constraint violations reported by PostgreSQL
DB_MESSAGES = {
    'customers_email_key': 'This email address is already registered.',
    'chk_customer_email': 'Please enter a valid email address.',
    'riders_phone_key': 'Another rider already uses this phone number.',
    'branches_branch_name_key': 'A branch with this name already exists.',
    'service_types_service_name_key': 'A service with this name already exists.',
    'parcels_weight_kg_check': 'Weight must be greater than 0 kg.',
    'parcels_declared_value_check': 'Declared value cannot be negative.',
    'payments_amount_check': 'Amount must be greater than 0.',
}


# ---------- security ----------

def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']


@app.before_request
def csrf_protect():
    if request.method == 'POST':
        sent = request.form.get('csrf_token', '')
        if not sent or not secrets.compare_digest(sent, session.get('csrf_token', '')):
            abort(400, 'Form expired. Go back, reload the page and try again.')


@app.errorhandler(psycopg2.IntegrityError)
@app.errorhandler(psycopg2.DataError)
@app.errorhandler(OpError)
def bad_input(e):
    """Undo the transaction and send the user back with a readable message."""
    db.get_db().rollback()
    if isinstance(e, OpError):
        message = str(e)
    elif e.diag.constraint_name in DB_MESSAGES:
        message = DB_MESSAGES[e.diag.constraint_name]
    elif isinstance(e, errors.ForeignKeyViolation):
        message = 'This record is linked to other records, so the action is not allowed.'
    else:
        message = 'Invalid input: ' + e.diag.message_primary
    flash(message, 'danger')
    return redirect(request.referrer or url_for('index'))


# ---------- template helpers ----------

app.jinja_env.globals.update(csrf_token=csrf_token, NEXT_STATUS=NEXT_STATUS)
app.jinja_env.filters['label'] = lambda s: str(s).replace('_', ' ').capitalize()
app.jinja_env.filters['money'] = lambda v: f"৳{(v or 0):,.2f}"
app.jinja_env.filters['dt'] = lambda t: t.strftime('%d %b %Y, %I:%M %p') if t else '-'
app.jinja_env.filters['badge'] = lambda s: {
    'booked': 'secondary', 'picked_up': 'info', 'in_transit': 'primary', 'at_hub': 'primary',
    'out_for_delivery': 'warning', 'delivered': 'success', 'returned': 'danger',
    'paid': 'success', 'pending': 'warning', 'available': 'success', 'busy': 'warning', 'inactive': 'secondary',
}.get(s, 'secondary')


# ---------- public pages ----------

@app.route('/')
def index():
    services = db.query("SELECT * FROM service_types ORDER BY base_rate")
    branches = db.query("SELECT branch_name, city, address, phone FROM branches ORDER BY city, branch_name")
    return render_template('index.html', services=services, branches=branches)


@app.route('/track')
def track():
    tracking_no = request.args.get('tracking_no', '').strip().upper()
    parcel = db.query_one("SELECT * FROM v_parcel_details WHERE tracking_no = %s", (tracking_no,))
    events = db.query(
        """SELECT e.event_time, e.status, e.remarks, b.branch_name, b.city
           FROM tracking_events e JOIN branches b ON b.branch_id = e.branch_id
           WHERE e.parcel_id = %s ORDER BY e.event_time DESC, e.event_id DESC""",
        (parcel['parcel_id'],)) if parcel else []
    return render_template('track.html', tracking_no=tracking_no, parcel=parcel, events=events)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form.get('role')
        identifier = request.form.get('identifier', '').strip()
        if role == 'customer':
            identifier = identifier.lower()
        user = db.query_one(LOGIN_SQL[role], (identifier,)) if role in LOGIN_SQL else None
        if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
            session.clear()
            session.update(role=role, uid=user['id'], name=user['name'])
            return redirect(HOME[role])
        flash('Wrong login details.', 'danger')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        f = clean(request.form, 'full_name', 'email', 'phone', 'address', 'password')
        if len(f['password']) < 6:
            raise OpError('Password must be at least 6 characters.')
        user = db.query_one(
            """INSERT INTO customers (full_name, email, phone, address, password_hash)
               VALUES (%s, %s, %s, %s, %s) RETURNING customer_id""",
            (f['full_name'], f['email'].lower(), f['phone'], f['address'], generate_password_hash(f['password'])))
        session.clear()
        session.update(role='customer', uid=user['customer_id'], name=f['full_name'])
        flash('Account created. You can book your first parcel now.', 'success')
        return redirect(HOME['customer'])
    return render_template('register.html')


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
