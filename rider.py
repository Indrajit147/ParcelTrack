"""Rider pages: assigned tasks and status updates."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from db import query, query_one
from ops import change_status, require

bp = Blueprint('rider', __name__, url_prefix='/rider')
bp.before_request(require('rider'))

TASK_SQL = """
    SELECT s.shipment_id, s.task_type, s.assigned_at, s.completed_at,
           d.parcel_id, d.tracking_no, d.current_status, d.weight_kg, d.origin_branch, d.destination_branch,
           d.sender_name, d.sender_phone, c.address AS sender_address,
           d.receiver_name, d.receiver_phone, d.receiver_address,
           (SELECT COALESCE(SUM(amount), 0) FROM payments p
            WHERE p.parcel_id = d.parcel_id AND p.payment_status = 'pending'
              AND p.payment_method = 'cash_on_delivery') AS cod_due
    FROM shipments s
    JOIN v_parcel_details d ON d.parcel_id = s.parcel_id
    JOIN customers c        ON c.customer_id = d.sender_id
    WHERE s.rider_id = %s AND s.completed_at IS {} NULL
    ORDER BY {} LIMIT 50"""


@bp.route('/')
def home():
    rider = query_one("""SELECT r.*, b.branch_name FROM riders r JOIN branches b ON b.branch_id = r.branch_id
                         WHERE r.rider_id = %s""", (session['uid'],))
    open_tasks = query(TASK_SQL.format('', 's.assigned_at'), (session['uid'],))
    done_tasks = query(TASK_SQL.format('NOT', 's.completed_at DESC'), (session['uid'],))
    return render_template('rider/home.html', rider=rider, open_tasks=open_tasks, done_tasks=done_tasks)


@bp.route('/update/<int:parcel_id>', methods=['POST'])
def update(parcel_id):
    change_status(parcel_id, request.form.get('status', ''), request.form.get('remarks', ''),
                  rider_id=session['uid'])
    flash('Status updated.', 'success')
    return redirect(url_for('rider.home'))
