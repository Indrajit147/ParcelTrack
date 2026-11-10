-- ParcelTrack: the main SQL queries used by the website, runnable on their own in psql / pgAdmin.

-- 1. Track a parcel by tracking number (SELECT with JOIN)
SELECT p.tracking_no, p.current_status, c.full_name AS sender, p.receiver_name,
       ob.branch_name AS origin, db.branch_name AS destination, s.service_name, p.charge
FROM parcels p
JOIN customers c     ON c.customer_id = p.sender_id
JOIN branches ob     ON ob.branch_id  = p.origin_branch_id
JOIN branches db     ON db.branch_id  = p.destination_branch_id
JOIN service_types s ON s.service_id  = p.service_id
WHERE p.tracking_no = 'PT26100200001';

-- 2. Full tracking history of that parcel
SELECT e.event_time, e.status, b.branch_name, e.remarks
FROM tracking_events e
JOIN parcels p  ON p.parcel_id = e.parcel_id
JOIN branches b ON b.branch_id = e.branch_id
WHERE p.tracking_no = 'PT26100200001'
ORDER BY e.event_time;

-- 3. Delivery charge = base rate + rate per kg x weight (2.5 kg, Express)
SELECT service_name, base_rate + rate_per_kg * 2.5 AS charge
FROM service_types WHERE service_name = 'Express';

-- 4. Total revenue and revenue per month (SUM, GROUP BY)
SELECT to_char(payment_date, 'YYYY-MM') AS month, COUNT(*) AS payments, SUM(amount) AS revenue
FROM payments WHERE payment_status = 'paid'
GROUP BY month ORDER BY month;

-- 5. Revenue per service type
SELECT s.service_name, COUNT(*) AS parcels, SUM(pay.amount) AS revenue
FROM payments pay
JOIN parcels p       ON p.parcel_id  = pay.parcel_id
JOIN service_types s ON s.service_id = p.service_id
WHERE pay.payment_status = 'paid'
GROUP BY s.service_name ORDER BY revenue DESC;

-- 6. Rider-wise deliveries (view)
SELECT * FROM v_rider_deliveries ORDER BY deliveries_done DESC;

-- 7. Branch-wise parcel count (view)
SELECT * FROM v_branch_parcel_count ORDER BY parcels_sent DESC;

-- 8. Delayed deliveries (view)
SELECT tracking_no, service_name, current_status, due_date, late_by
FROM v_delayed_deliveries ORDER BY late_by DESC;

-- 9. Customers with more than three parcels (HAVING)
SELECT c.full_name, COUNT(*) AS parcels, SUM(p.charge) AS total_charge
FROM customers c JOIN parcels p ON p.sender_id = c.customer_id
GROUP BY c.customer_id HAVING COUNT(*) > 3
ORDER BY parcels DESC;

-- 10. Customers who have never sent a parcel (subquery)
SELECT full_name, email FROM customers
WHERE customer_id NOT IN (SELECT sender_id FROM parcels);

-- 11. Cash-on-delivery money still to be collected
SELECT p.tracking_no, p.receiver_name, pay.amount
FROM payments pay JOIN parcels p ON p.parcel_id = pay.parcel_id
WHERE pay.payment_method = 'cash_on_delivery' AND pay.payment_status = 'pending';

-- 12. Parcel count per status
SELECT current_status, COUNT(*) FROM parcels GROUP BY current_status ORDER BY COUNT(*) DESC;
