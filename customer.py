"""Customer pages: parcel history, booking, cancelling and profile."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from db import query, query_one
from ops import PAYMENT_METHODS, OpError, book_parcel, clean, delete_parcel, require

bp = Blueprint('customer', __name__, url_prefix='/customer')
bp.before_request(require('customer'))


@bp.route('/')
def home():
    parcels = query("SELECT * FROM v_parcel_details WHERE sender_id = %s ORDER BY booking_date DESC",
                    (session['uid'],))
    summary = query_one(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE current_status = 'delivered') AS delivered,
                  COUNT(*) FILTER (WHERE current_status NOT IN ('delivered', 'returned')) AS active,
                  COALESCE(SUM(charge) FILTER (WHERE current_status NOT IN ('delivered', 'returned')), 0) AS spent
           FROM parcels WHERE sender_id = %s""", (session['uid'],))
    return render_template('customer/home.html', parcels=parcels, summary=summary)


@bp.route('/book', methods=['GET', 'POST'])
def book():
    if request.method == 'POST':
        parcel = book_parcel(session['uid'], request.form)
        flash(f"Parcel booked. Tracking number: {parcel['tracking_no']}, charge: ৳{parcel['charge']}.", 'success')
        return redirect(url_for('customer.home'))
    return render_template(
        'customer/book.html',
        branches=query("SELECT branch_id, branch_name, city FROM branches ORDER BY city, branch_name"),
        services=query("SELECT * FROM service_types ORDER BY base_rate"),
        methods=PAYMENT_METHODS)


@bp.route('/cancel/<int:parcel_id>', methods=['POST'])
def cancel(parcel_id):
    parcel = delete_parcel(parcel_id, sender_id=session['uid'])
    flash(f"Booking {parcel['tracking_no']} cancelled.", 'success')
    return redirect(url_for('customer.home'))


@bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if request.method == 'POST':
        f = clean(request.form, 'full_name', 'email', 'phone', 'address')
        query("UPDATE customers SET full_name = %s, email = %s, phone = %s, address = %s WHERE customer_id = %s",
              (f['full_name'], f['email'].lower(), f['phone'], f['address'], session['uid']))
        password = request.form.get('password', '')
        if password:
            if len(password) < 6:
                raise OpError('Password must be at least 6 characters.')
            query("UPDATE customers SET password_hash = %s WHERE customer_id = %s",
                  (generate_password_hash(password), session['uid']))
        session['name'] = f['full_name']
        flash('Profile updated.', 'success')
        return redirect(url_for('customer.profile'))
    customer = query_one("SELECT * FROM customers WHERE customer_id = %s", (session['uid'],))
    return render_template('customer/profile.html', customer=customer)
