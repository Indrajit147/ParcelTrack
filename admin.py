"""Admin pages: record management, parcels, rider assignment, payments."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from db import query, query_one
from ops import (NEXT_STATUS, PAYMENT_METHODS, STATUSES, OpError, assign_rider, book_parcel,
                 change_status, clean, delete_parcel, require)

bp = Blueprint('admin', __name__, url_prefix='/admin')
bp.before_request(require('admin'))

BRANCH_OPTIONS = "SELECT branch_id, city || ' - ' || branch_name FROM branches ORDER BY 2"

# The simple tables share one list page and one form page.
# fields: (column, label, input type, options for a select)
TABLES = {
    'customers': {
        'title': 'Customers', 'pk': 'customer_id',
        'list_sql': """SELECT c.*, (SELECT COUNT(*) FROM parcels p WHERE p.sender_id = c.customer_id) AS parcels
                       FROM customers c""",
        'search': ['full_name', 'email', 'phone', 'address'],
        'columns': [('customer_id', 'ID'), ('full_name', 'Name'), ('email', 'Email'), ('phone', 'Phone'),
                    ('address', 'Address'), ('parcels', 'Parcels'), ('created_at', 'Registered')],
        'fields': [('full_name', 'Full name', 'text', None), ('email', 'Email', 'email', None),
                   ('phone', 'Phone', 'text', None), ('address', 'Address', 'text', None),
                   ('password', 'Password', 'password', None)],
    },
    'riders': {
        'title': 'Riders', 'pk': 'rider_id',
        'list_sql': "SELECT r.*, b.branch_name FROM riders r JOIN branches b ON b.branch_id = r.branch_id",
        'search': ['full_name', 'phone', 'branch_name', 'vehicle_type', 'status'],
        'columns': [('rider_id', 'ID'), ('full_name', 'Name'), ('phone', 'Phone'), ('branch_name', 'Branch'),
                    ('vehicle_type', 'Vehicle'), ('status', 'Status')],
        'fields': [('full_name', 'Full name', 'text', None), ('phone', 'Phone', 'text', None),
                   ('branch_id', 'Branch', 'select', BRANCH_OPTIONS),
                   ('vehicle_type', 'Vehicle type', 'select', ['motorcycle', 'bicycle', 'van', 'truck']),
                   ('status', 'Status', 'select', ['available', 'busy', 'inactive']),
                   ('password', 'Password', 'password', None)],
    },
    'branches': {
        'title': 'Branches', 'pk': 'branch_id',
        'list_sql': "SELECT * FROM branches",
        'search': ['branch_name', 'city', 'address', 'phone'],
        'columns': [('branch_id', 'ID'), ('branch_name', 'Branch'), ('city', 'City'), ('address', 'Address'),
                    ('phone', 'Phone')],
        'fields': [('branch_name', 'Branch name', 'text', None), ('city', 'City', 'text', None),
                   ('address', 'Address', 'text', None), ('phone', 'Phone', 'text', None)],
    },
    'service_types': {
        'title': 'Service types', 'pk': 'service_id',
        'list_sql': "SELECT * FROM service_types",
        'search': ['service_name'],
        'columns': [('service_id', 'ID'), ('service_name', 'Service'), ('base_rate', 'Base rate (৳)'),
                    ('rate_per_kg', 'Rate per kg (৳)'), ('delivery_days', 'Delivery days')],
        'fields': [('service_name', 'Service name', 'text', None), ('base_rate', 'Base rate (৳)', 'number', None),
                   ('rate_per_kg', 'Rate per kg (৳)', 'number', None),
                   ('delivery_days', 'Delivery days', 'number', None)],
    },
}


def table_config(table):
    if table not in TABLES:
        abort(404)
    return TABLES[table]


def form_values(cfg, creating):
    """Read and check the submitted fields; returns {column: value} for the SQL statement."""
    values = {}
    for name, label, kind, _ in cfg['fields']:
        raw = request.form.get(name, '').strip()
        if kind == 'password':
            if raw:
                if len(raw) < 6:
                    raise OpError('Password must be at least 6 characters.')
                values['password_hash'] = generate_password_hash(raw)
            elif creating:
                raise OpError('Password is required.')
        elif not raw:
            raise OpError(f'{label} is required.')
        else:
            values[name] = raw.lower() if kind == 'email' else raw
    return values


# Table and column names below come only from TABLES, never from the request.

@bp.route('/manage/<table>')
def manage(table):
    cfg = table_config(table)
    q = request.args.get('q', '').strip()
    where = ' OR '.join(f"{col}::text ILIKE %(q)s" for col in cfg['search'])
    rows = query(f"SELECT * FROM ({cfg['list_sql']}) t WHERE {where} ORDER BY {cfg['pk']}", {'q': f'%{q}%'})
    return render_template('admin/list.html', table=table, cfg=cfg, rows=rows, q=q)


@bp.route('/manage/<table>/new', methods=['GET', 'POST'])
@bp.route('/manage/<table>/<int:row_id>/edit', methods=['GET', 'POST'])
def edit(table, row_id=None):
    cfg = table_config(table)
    if request.method == 'POST':
        values = form_values(cfg, creating=row_id is None)
        if row_id is None:
            query(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                  list(values.values()))
            flash('Record added.', 'success')
        else:
            query(f"UPDATE {table} SET {', '.join(c + ' = %s' for c in values)} WHERE {cfg['pk']} = %s",
                  list(values.values()) + [row_id])
            flash('Record updated.', 'success')
        return redirect(url_for('admin.manage', table=table))
    row = None
    if row_id is not None:
        row = query_one(f"SELECT * FROM {table} WHERE {cfg['pk']} = %s", (row_id,)) or abort(404)
    options = {name: [(o, o.capitalize()) for o in opts] if isinstance(opts, list)
               else [tuple(r.values()) for r in query(opts)]
               for name, _, kind, opts in cfg['fields'] if kind == 'select'}
    return render_template('admin/form.html', table=table, cfg=cfg, row=row, options=options)


@bp.route('/manage/<table>/<int:row_id>/delete', methods=['POST'])
def delete(table, row_id):
    cfg = table_config(table)
    query(f"DELETE FROM {table} WHERE {cfg['pk']} = %s", (row_id,))
    flash('Record deleted.', 'success')
    return redirect(url_for('admin.manage', table=table))


# ---------- parcels ----------

@bp.route('/parcels')
def parcels():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    rows = query(
        """SELECT * FROM v_parcel_details
           WHERE (tracking_no ILIKE %(q)s OR sender_name ILIKE %(q)s OR sender_email ILIKE %(q)s
                  OR receiver_name ILIKE %(q)s OR receiver_phone ILIKE %(q)s)
             AND (%(status)s = '' OR current_status = %(status)s)
           ORDER BY booking_date DESC LIMIT 300""", {'q': f'%{q}%', 'status': status})
    return render_template('admin/parcels.html', parcels=rows, q=q, status=status, statuses=STATUSES)


@bp.route('/parcels/new', methods=['GET', 'POST'])
def parcel_new():
    if request.method == 'POST':
        sender = clean(request.form, 'sender_id')['sender_id']
        parcel = book_parcel(sender, request.form)
        flash(f"Parcel booked. Tracking number: {parcel['tracking_no']}.", 'success')
        return redirect(url_for('admin.parcel', parcel_id=parcel['parcel_id']))
    return render_template(
        'admin/book.html',
        customers=query("SELECT customer_id, full_name, phone FROM customers ORDER BY full_name"),
        branches=query("SELECT branch_id, branch_name, city FROM branches ORDER BY city, branch_name"),
        services=query("SELECT * FROM service_types ORDER BY base_rate"),
        methods=PAYMENT_METHODS)


@bp.route('/parcels/<int:parcel_id>')
def parcel(parcel_id):
    p = query_one("SELECT * FROM v_parcel_details WHERE parcel_id = %s", (parcel_id,)) or abort(404)
    events = query(
        """SELECT e.*, b.branch_name FROM tracking_events e JOIN branches b ON b.branch_id = e.branch_id
           WHERE e.parcel_id = %s ORDER BY e.event_time DESC, e.event_id DESC""", (parcel_id,))
    shipments = query(
        """SELECT s.*, r.full_name AS rider_name, r.phone AS rider_phone
           FROM shipments s JOIN riders r ON r.rider_id = s.rider_id
           WHERE s.parcel_id = %s ORDER BY s.assigned_at""", (parcel_id,))
    payments = query("SELECT * FROM payments WHERE parcel_id = %s ORDER BY payment_date", (parcel_id,))
    # riders who can take the current task: pickup from the origin branch, delivery from the destination branch
    branch = {'booked': p['origin_branch_id'], 'at_hub': p['destination_branch_id']}.get(p['current_status'])
    riders = query(
        """SELECT r.rider_id, r.full_name, r.vehicle_type, r.status,
                  (SELECT COUNT(*) FROM shipments s WHERE s.rider_id = r.rider_id AND s.completed_at IS NULL) AS open_tasks
           FROM riders r WHERE r.branch_id = %s AND r.status <> 'inactive' ORDER BY open_tasks, r.full_name""",
        (branch,)) if branch else []
    return render_template('admin/parcel.html', p=p, events=events, shipments=shipments, payments=payments,
                           riders=riders, next_statuses=NEXT_STATUS.get(p['current_status'], []),
                           methods=PAYMENT_METHODS)


@bp.route('/parcels/<int:parcel_id>/assign', methods=['POST'])
def parcel_assign(parcel_id):
    task = assign_rider(parcel_id, clean(request.form, 'rider_id')['rider_id'])
    flash(f'Rider assigned for {task}.', 'success')
    return redirect(url_for('admin.parcel', parcel_id=parcel_id))


@bp.route('/parcels/<int:parcel_id>/status', methods=['POST'])
def parcel_status(parcel_id):
    change_status(parcel_id, request.form.get('status', ''), request.form.get('remarks', ''))
    flash('Status updated.', 'success')
    return redirect(url_for('admin.parcel', parcel_id=parcel_id))


@bp.route('/parcels/<int:parcel_id>/receiver', methods=['POST'])
def parcel_receiver(parcel_id):
    f = clean(request.form, 'receiver_name', 'receiver_phone', 'receiver_address')
    query("UPDATE parcels SET receiver_name = %s, receiver_phone = %s, receiver_address = %s WHERE parcel_id = %s",
          (f['receiver_name'], f['receiver_phone'], f['receiver_address'], parcel_id))
    flash('Receiver details updated.', 'success')
    return redirect(url_for('admin.parcel', parcel_id=parcel_id))


@bp.route('/parcels/<int:parcel_id>/payment', methods=['POST'])
def payment_add(parcel_id):
    f = clean(request.form, 'amount', 'payment_method')
    query("INSERT INTO payments (parcel_id, amount, payment_method, payment_status) VALUES (%s, %s, %s, 'paid')",
          (parcel_id, f['amount'], f['payment_method']))
    flash('Payment recorded.', 'success')
    return redirect(url_for('admin.parcel', parcel_id=parcel_id))


@bp.route('/payments/<int:payment_id>/paid', methods=['POST'])
def payment_paid(payment_id):
    row = query_one("""UPDATE payments SET payment_status = 'paid', payment_date = CURRENT_TIMESTAMP
                       WHERE payment_id = %s RETURNING parcel_id""", (payment_id,)) or abort(404)
    flash('Payment marked as paid.', 'success')
    return redirect(url_for('admin.parcel', parcel_id=row['parcel_id']))


@bp.route('/parcels/<int:parcel_id>/delete', methods=['POST'])
def parcel_delete(parcel_id):
    p = delete_parcel(parcel_id)
    flash(f"Parcel {p['tracking_no']} deleted.", 'success')
    return redirect(url_for('admin.parcels'))


# ---------- dashboard and reports ----------

@bp.route('/')
def dashboard():
    summary = query_one(
        """SELECT (SELECT COUNT(*) FROM parcels) AS total,
                  (SELECT COUNT(*) FROM parcels WHERE current_status IN
                          ('picked_up', 'in_transit', 'at_hub', 'out_for_delivery')) AS in_transit,
                  (SELECT COUNT(*) FROM parcels WHERE current_status = 'delivered') AS delivered,
                  (SELECT COUNT(*) FROM parcels WHERE current_status = 'returned') AS returned,
                  (SELECT COUNT(*) FROM riders WHERE status <> 'inactive') AS riders_on_duty,
                  (SELECT COUNT(*) FROM customers) AS customers,
                  (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_status = 'paid') AS revenue,
                  (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_status = 'pending') AS pending,
                  (SELECT COUNT(*) FROM v_delayed_deliveries WHERE delivered_at IS NULL) AS delayed_open""")
    by_status = query("SELECT current_status, COUNT(*) AS count FROM parcels GROUP BY current_status ORDER BY count DESC")
    recent = query("SELECT * FROM v_parcel_details ORDER BY booking_date DESC LIMIT 8")
    return render_template('admin/dashboard.html', s=summary, by_status=by_status, recent=recent)


@bp.route('/reports')
def reports():
    return render_template(
        'admin/reports.html',
        revenue_month=query(
            """SELECT to_char(payment_date, 'YYYY-MM') AS month, COUNT(*) AS payments, SUM(amount) AS revenue
               FROM payments WHERE payment_status = 'paid' GROUP BY month ORDER BY month DESC"""),
        revenue_service=query(
            """SELECT s.service_name, COUNT(DISTINCT p.parcel_id) AS parcels, SUM(pay.amount) AS revenue,
                      AVG(pay.amount) AS average
               FROM payments pay
               JOIN parcels p       ON p.parcel_id  = pay.parcel_id
               JOIN service_types s ON s.service_id = p.service_id
               WHERE pay.payment_status = 'paid'
               GROUP BY s.service_name ORDER BY revenue DESC"""),
        revenue_method=query(
            """SELECT payment_method, payment_status, COUNT(*) AS payments, SUM(amount) AS amount
               FROM payments GROUP BY payment_method, payment_status ORDER BY payment_method, payment_status"""),
        riders=query("SELECT * FROM v_rider_deliveries ORDER BY deliveries_done DESC, pickups_done DESC, full_name"),
        branches=query("SELECT * FROM v_branch_parcel_count ORDER BY parcels_sent + parcels_received DESC"),
        delayed=query("SELECT * FROM v_delayed_deliveries ORDER BY delivered_at IS NULL DESC, late_by DESC"),
        customers=query(
            """SELECT c.customer_id, c.full_name, c.email, COUNT(p.parcel_id) AS parcels,
                      COUNT(p.parcel_id) FILTER (WHERE p.current_status = 'delivered') AS delivered,
                      COALESCE(SUM(p.charge), 0) AS total_charge, MAX(p.booking_date) AS last_booking
               FROM customers c LEFT JOIN parcels p ON p.sender_id = c.customer_id
               GROUP BY c.customer_id ORDER BY parcels DESC, c.full_name"""))
