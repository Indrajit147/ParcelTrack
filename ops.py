"""Parcel operations shared by the customer, admin and rider pages.

Each function runs several statements inside the request's transaction,
so a failure in any step rolls the whole operation back.
"""
from flask import flash, redirect, session, url_for

from db import query, query_one

STATUSES = ['booked', 'picked_up', 'in_transit', 'at_hub', 'out_for_delivery', 'delivered', 'returned']
NEXT_STATUS = {
    'booked': ['picked_up'],
    'picked_up': ['in_transit'],
    'in_transit': ['at_hub'],
    'at_hub': ['out_for_delivery'],
    'out_for_delivery': ['delivered', 'returned'],
}
PAYMENT_METHODS = ['cash_on_delivery', 'mobile_banking', 'card', 'cash']


class OpError(Exception):
    """A request that breaks a business rule; the message is shown to the user."""


def require(*roles):
    """before_request guard for a blueprint: only the given roles may enter."""
    def check():
        if session.get('role') not in roles:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('login'))
    return check


def clean(form, *names):
    """Return the named form fields stripped; all of them are required."""
    values = {n: form.get(n, '').strip() for n in names}
    if not all(values.values()):
        raise OpError('Please fill in all required fields.')
    return values


def book_parcel(sender_id, form):
    f = clean(form, 'receiver_name', 'receiver_phone', 'receiver_address', 'origin_branch_id',
              'destination_branch_id', 'service_id', 'weight_kg', 'payment_method')
    if f['payment_method'] not in PAYMENT_METHODS:
        raise OpError('Unknown payment method.')
    # The charge is calculated by the database from the service rate table.
    parcel = query_one(
        """INSERT INTO parcels (sender_id, receiver_name, receiver_phone, receiver_address,
                                origin_branch_id, destination_branch_id, service_id,
                                weight_kg, declared_value, charge)
           SELECT %s, %s, %s, %s, %s, %s, service_id, %s, %s, base_rate + rate_per_kg * %s
           FROM service_types WHERE service_id = %s
           RETURNING parcel_id, tracking_no, charge""",
        (sender_id, f['receiver_name'], f['receiver_phone'], f['receiver_address'],
         f['origin_branch_id'], f['destination_branch_id'], f['weight_kg'],
         form.get('declared_value') or 0, f['weight_kg'], f['service_id']))
    if parcel is None:
        raise OpError('Unknown service type.')
    query("INSERT INTO tracking_events (parcel_id, branch_id, status, remarks) VALUES (%s, %s, 'booked', 'Parcel booked')",
          (parcel['parcel_id'], f['origin_branch_id']))
    # Online methods are paid at booking; cash and cash on delivery are collected later.
    paid = f['payment_method'] in ('mobile_banking', 'card')
    query("INSERT INTO payments (parcel_id, amount, payment_method, payment_status) VALUES (%s, %s, %s, %s)",
          (parcel['parcel_id'], parcel['charge'], f['payment_method'], 'paid' if paid else 'pending'))
    return parcel


def refresh_rider(rider_id):
    """A rider is busy while they have an open task (inactive riders stay inactive)."""
    query("""UPDATE riders
             SET status = CASE WHEN EXISTS (SELECT 1 FROM shipments
                                            WHERE rider_id = %s AND completed_at IS NULL)
                               THEN 'busy' ELSE 'available' END
             WHERE rider_id = %s AND status <> 'inactive'""", (rider_id, rider_id))


def lock_parcel(parcel_id):
    parcel = query_one("SELECT * FROM parcels WHERE parcel_id = %s FOR UPDATE", (parcel_id,))
    if parcel is None:
        raise OpError('Parcel not found.')
    return parcel


def open_shipment(parcel_id, task_type):
    return query_one("""SELECT shipment_id, rider_id FROM shipments
                        WHERE parcel_id = %s AND task_type = %s AND completed_at IS NULL""",
                     (parcel_id, task_type))


def assign_rider(parcel_id, rider_id):
    """Assign (or re-assign) the pickup rider of a booked parcel or the delivery rider of a parcel at its hub."""
    parcel = lock_parcel(parcel_id)
    if parcel['current_status'] == 'booked':
        task, branch = 'pickup', parcel['origin_branch_id']
    elif parcel['current_status'] == 'at_hub':
        task, branch = 'delivery', parcel['destination_branch_id']
    else:
        raise OpError('A rider can be assigned only when the parcel is booked (pickup) or at the hub (delivery).')
    rider = query_one("SELECT rider_id FROM riders WHERE rider_id = %s AND branch_id = %s AND status <> 'inactive'",
                      (rider_id, branch))
    if rider is None:
        raise OpError('Choose an active rider of the branch that handles this task.')
    current = open_shipment(parcel_id, task)
    if current:
        query("UPDATE shipments SET rider_id = %s, assigned_at = CURRENT_TIMESTAMP WHERE shipment_id = %s",
              (rider_id, current['shipment_id']))
        refresh_rider(current['rider_id'])
    else:
        query("INSERT INTO shipments (parcel_id, rider_id, task_type) VALUES (%s, %s, %s)",
              (parcel_id, rider_id, task))
    refresh_rider(rider_id)
    return task


def change_status(parcel_id, new_status, remarks='', rider_id=None):
    """Move a parcel to its next status. With rider_id, only that rider's own task may be updated."""
    parcel = lock_parcel(parcel_id)
    if new_status not in NEXT_STATUS.get(parcel['current_status'], []):
        raise OpError(f"A parcel that is {parcel['current_status'].replace('_', ' ')} cannot be marked "
                      f"{new_status.replace('_', ' ')}.")
    task = {'picked_up': 'pickup', 'out_for_delivery': 'delivery',
            'delivered': 'delivery', 'returned': 'delivery'}.get(new_status)
    shipment = open_shipment(parcel_id, task) if task else None
    if task and shipment is None:
        raise OpError(f'Assign a {task} rider first.')
    if rider_id is not None and (shipment is None or shipment['rider_id'] != rider_id):
        raise OpError('This parcel is not assigned to you.')

    at_origin = new_status in ('picked_up', 'in_transit')
    # The trigger on tracking_events updates parcels.current_status.
    query("INSERT INTO tracking_events (parcel_id, branch_id, status, remarks) VALUES (%s, %s, %s, %s)",
          (parcel_id, parcel['origin_branch_id' if at_origin else 'destination_branch_id'],
           new_status, remarks.strip() or None))
    if new_status in ('picked_up', 'delivered', 'returned'):
        query("UPDATE shipments SET completed_at = CURRENT_TIMESTAMP WHERE shipment_id = %s",
              (shipment['shipment_id'],))
        refresh_rider(shipment['rider_id'])
    if new_status == 'delivered':
        query("""UPDATE payments SET payment_status = 'paid', payment_date = CURRENT_TIMESTAMP
                 WHERE parcel_id = %s AND payment_method = 'cash_on_delivery' AND payment_status = 'pending'""",
              (parcel_id,))


def delete_parcel(parcel_id, sender_id=None):
    """Delete a parcel with its shipments, events and payments. With sender_id: cancel an own, still-booked parcel."""
    parcel = lock_parcel(parcel_id)
    if sender_id is not None and (parcel['sender_id'] != sender_id or parcel['current_status'] != 'booked'):
        raise OpError('Only your own parcels that are not picked up yet can be cancelled.')
    riders = query("SELECT DISTINCT rider_id FROM shipments WHERE parcel_id = %s AND completed_at IS NULL", (parcel_id,))
    query("DELETE FROM parcels WHERE parcel_id = %s", (parcel_id,))
    for r in riders:
        refresh_rider(r['rider_id'])
    return parcel
