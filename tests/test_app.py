"""End-to-end tests of the main flows.

They run on a separate database (parceltrack_test) that is rebuilt from
schema.sql and seed.sql, so the real data is never touched.

    createdb -U postgres parceltrack_test        (once)
    python -m unittest discover tests -v
"""
import os
import re
import sys
import unittest
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / '.env')
TEST_URL = os.environ.get('TEST_DATABASE_URL') or os.environ['DATABASE_URL'].rsplit('/', 1)[0] + '/parceltrack_test'
assert TEST_URL.endswith('_test'), 'refusing to rebuild a database whose name does not end with _test'
os.environ['DATABASE_URL'] = TEST_URL

from app import app  # noqa: E402  (must be imported after DATABASE_URL is switched)


def sql(statement, params=()):
    """Run a statement directly on the test database and return the first row."""
    conn = psycopg2.connect(TEST_URL)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(statement, params or None)  # None: keep the % signs of the SQL files
            return cur.fetchone() if cur.description else None
    finally:
        conn.close()


def setUpModule():
    for name in ('schema.sql', 'seed.sql'):
        sql((ROOT / 'database' / name).read_text(encoding='utf-8'))


class Browser:
    """A logged-in test client that adds the CSRF token to every form."""

    def __init__(self, role=None, identifier=None, password=None):
        self.client = app.test_client()
        self.token = None
        self.page = self.get('/login')
        if role:
            self.page = self.post('/login', role=role, identifier=identifier, password=password)

    def _read(self, response):
        page = response.get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="(\w+)"', page)
        if token:  # the token changes when the session is renewed at login
            self.token = token.group(1)
        return page

    def get(self, url):
        return self._read(self.client.get(url, follow_redirects=True))

    def post(self, url, **form):
        return self._read(self.client.post(url, data={'csrf_token': self.token, **form}, follow_redirects=True,
                                           headers={'Referer': '/'}))


PARCEL = dict(receiver_name='Test Receiver', receiver_phone='01800000000', receiver_address='Banani, Dhaka',
              origin_branch_id=7, destination_branch_id=1, service_id=2, weight_kg='2.5', declared_value='500',
              payment_method='cash_on_delivery')


def new_customer(email):
    browser = Browser()
    browser.page = browser.post('/register', full_name='Test Customer', email=email, phone='01700000000',
                 address='KUET, Khulna', password='secret1')
    return browser


class PublicPages(unittest.TestCase):
    def test_home_and_tracking(self):
        b = Browser()
        self.assertIn('Delivery charges', b.get('/'))
        tracking_no = sql("SELECT tracking_no FROM parcels WHERE current_status = 'delivered' LIMIT 1")[0]
        page = b.get('/track?tracking_no=' + tracking_no.lower())
        self.assertIn('Tracking history', page)
        self.assertIn('Parcel booked at', page)
        self.assertIn('No parcel found', b.get('/track?tracking_no=NOPE'))

    def test_post_without_csrf_token_is_rejected(self):
        response = app.test_client().post('/login', data={'role': 'admin', 'identifier': 'admin', 'password': 'admin123'})
        self.assertEqual(response.status_code, 400)

    def test_wrong_password(self):
        self.assertIn('Wrong login details', Browser('admin', 'admin', 'nope').page)

    def test_pages_are_closed_to_other_roles(self):
        customer = Browser('customer', 'sajib.saha@outlook.com', 'customer123')
        for url in ('/admin/', '/admin/reports', '/admin/manage/customers', '/rider/'):
            self.assertIn('Please log in to continue', customer.get(url), url)


class ParcelLifeCycle(unittest.TestCase):
    def test_book_deliver_and_collect_cash(self):
        customer = new_customer('lifecycle@example.com')
        page = customer.post('/customer/book', **PARCEL)
        tracking_no = re.search(r'Tracking number: (PT\d+)', page).group(1)
        parcel_id, charge = sql("SELECT parcel_id, charge FROM parcels WHERE tracking_no = %s", (tracking_no,))
        self.assertEqual(float(charge), 100 + 30 * 2.5)  # Express: base 100 + 30 per kg

        admin = Browser('admin', 'admin', 'admin123')
        url = f'/admin/parcels/{parcel_id}'
        self.assertIn('Assign a pickup rider first', admin.post(url + '/status', status='picked_up'))
        self.assertIn('cannot be marked', admin.post(url + '/status', status='delivered'))

        pickup_id, pickup_phone = sql("SELECT rider_id, phone FROM riders WHERE branch_id = 7 AND status <> 'inactive' LIMIT 1")
        delivery_id, delivery_phone = sql("SELECT rider_id, phone FROM riders WHERE branch_id = 1 AND status <> 'inactive' LIMIT 1")
        self.assertIn('Choose an active rider', admin.post(url + '/assign', rider_id=delivery_id))
        self.assertIn('Rider assigned for pickup', admin.post(url + '/assign', rider_id=pickup_id))
        self.assertEqual(sql("SELECT status FROM riders WHERE rider_id = %s", (pickup_id,))[0], 'busy')

        other_rider = Browser('rider', delivery_phone, 'rider123')
        self.assertIn('not assigned to you', other_rider.post(f'/rider/update/{parcel_id}', status='picked_up'))
        pickup_rider = Browser('rider', pickup_phone, 'rider123')
        self.assertIn(tracking_no, pickup_rider.get('/rider/'))
        self.assertIn('Status updated', pickup_rider.post(f'/rider/update/{parcel_id}', status='picked_up'))

        for status in ('in_transit', 'at_hub'):
            self.assertIn('Status updated', admin.post(url + '/status', status=status))
        self.assertIn('Rider assigned for delivery', admin.post(url + '/assign', rider_id=delivery_id))
        for status in ('out_for_delivery', 'delivered'):
            self.assertIn('Status updated', other_rider.post(f'/rider/update/{parcel_id}', status=status))

        # trigger kept the parcel in sync, the tasks are closed and the cash on delivery is collected
        self.assertEqual(sql("SELECT current_status FROM parcels WHERE parcel_id = %s", (parcel_id,))[0], 'delivered')
        self.assertEqual(sql("SELECT COUNT(*) FROM tracking_events WHERE parcel_id = %s", (parcel_id,))[0], 6)
        self.assertEqual(sql("SELECT COUNT(*) FROM shipments WHERE parcel_id = %s AND completed_at IS NULL", (parcel_id,))[0], 0)
        self.assertEqual(sql("SELECT payment_status FROM payments WHERE parcel_id = %s", (parcel_id,))[0], 'paid')
        self.assertIn('Delivered', customer.get('/track?tracking_no=' + tracking_no))

    def test_cancel_booking(self):
        customer = new_customer('cancel@example.com')
        tracking_no = re.search(r'Tracking number: (PT\d+)', customer.post('/customer/book', **PARCEL)).group(1)
        parcel_id = sql("SELECT parcel_id FROM parcels WHERE tracking_no = %s", (tracking_no,))[0]
        someone_else = sql("SELECT parcel_id FROM parcels WHERE current_status = 'booked' AND parcel_id <> %s LIMIT 1", (parcel_id,))[0]
        self.assertIn('Only your own parcels', customer.post(f'/customer/cancel/{someone_else}'))
        self.assertIn('cancelled', customer.post(f'/customer/cancel/{parcel_id}'))
        self.assertEqual(sql("SELECT COUNT(*) FROM parcels WHERE parcel_id = %s", (parcel_id,))[0], 0)
        self.assertEqual(sql("SELECT COUNT(*) FROM payments WHERE parcel_id = %s", (parcel_id,))[0], 0)

    def test_database_constraints_reach_the_user(self):
        customer = new_customer('rules@example.com')
        self.assertIn('Weight must be greater than 0', customer.post('/customer/book', **{**PARCEL, 'weight_kg': '-1'}))
        self.assertIn('already registered', new_customer('rules@example.com').page)
        self.assertIn('Profile updated', customer.post('/customer/profile', full_name='New Name', email='rules@example.com',
                                                       phone='01711111111', address='Khulna'))


class AdminPages(unittest.TestCase):
    def setUp(self):
        self.admin = Browser('admin', 'admin', 'admin123')

    def test_branch_add_edit_delete(self):
        fields = dict(city='Bogura', address='Satmatha, Bogura', phone='01711000199')
        self.assertIn('Record added', self.admin.post('/admin/manage/branches/new', branch_name='Satmatha Branch', **fields))
        self.assertIn('already exists', self.admin.post('/admin/manage/branches/new', branch_name='Satmatha Branch', **fields))
        branch_id = sql("SELECT branch_id FROM branches WHERE branch_name = 'Satmatha Branch'")[0]
        self.assertIn('Bogura Hub', self.admin.post(f'/admin/manage/branches/{branch_id}/edit', branch_name='Bogura Hub', **fields))
        self.assertIn('Record deleted', self.admin.post(f'/admin/manage/branches/{branch_id}/delete'))
        # a branch that still has riders and parcels is protected by its foreign keys
        self.assertIn('linked to other records', self.admin.post('/admin/manage/branches/1/delete'))

    def test_lists_search_and_reports(self):
        for table in ('customers', 'riders', 'branches', 'service_types'):
            self.assertIn('Add new', self.admin.get(f'/admin/manage/{table}'))
            self.assertIn('Save', self.admin.get(f'/admin/manage/{table}/1/edit'))
        self.assertIn('No records found', self.admin.get('/admin/manage/riders?q=zzzz'))
        self.assertIn('No parcels found', self.admin.get('/admin/parcels?q=zzzz'))
        self.assertIn('Riders on duty', self.admin.get('/admin/'))
        reports = self.admin.get('/admin/reports')
        for title in ('Revenue by month', 'Rider-wise deliveries', 'Branch-wise parcel count',
                      'Delayed deliveries', 'Customer parcel history'):
            self.assertIn(title, reports)

    def test_record_payment(self):
        parcel_id, payment_id = sql("SELECT parcel_id, payment_id FROM payments WHERE payment_status = 'pending' LIMIT 1")
        self.assertIn('Payment marked as paid', self.admin.post(f'/admin/payments/{payment_id}/paid'))
        self.assertIn('Payment recorded', self.admin.post(f'/admin/parcels/{parcel_id}/payment', amount='50', payment_method='cash'))
        self.assertIn('Amount must be greater than 0', self.admin.post(f'/admin/parcels/{parcel_id}/payment', amount='0', payment_method='cash'))


if __name__ == '__main__':
    unittest.main()
