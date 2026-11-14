-- ParcelTrack database schema (PostgreSQL)
-- Run:  psql -U postgres -d parceltrack -f database/schema.sql

DROP VIEW IF EXISTS v_delayed_deliveries, v_branch_parcel_count, v_rider_deliveries, v_parcel_details;
DROP TABLE IF EXISTS payments, tracking_events, shipments, parcels,
                     service_types, riders, branches, customers, admins CASCADE;
DROP SEQUENCE IF EXISTS tracking_seq;

CREATE TABLE customers (
    customer_id   SERIAL PRIMARY KEY,
    full_name     VARCHAR(100) NOT NULL,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    email         VARCHAR(120) NOT NULL UNIQUE,
    phone         VARCHAR(15)  NOT NULL,
    address       TEXT         NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_customer_email CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT chk_customer_username CHECK (username ~ '^[a-z0-9_]{2,30}$')
);

CREATE TABLE branches (
    branch_id   SERIAL PRIMARY KEY,
    branch_name VARCHAR(100) NOT NULL UNIQUE,
    city        VARCHAR(60)  NOT NULL,
    address     TEXT         NOT NULL,
    phone       VARCHAR(15)  NOT NULL
);

CREATE TABLE riders (
    rider_id      SERIAL PRIMARY KEY,
    branch_id     INT          NOT NULL REFERENCES branches (branch_id),
    full_name     VARCHAR(100) NOT NULL,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    phone         VARCHAR(15)  NOT NULL UNIQUE,
    vehicle_type  VARCHAR(20)  NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'available',
    password_hash VARCHAR(255) NOT NULL,
    CONSTRAINT chk_rider_vehicle CHECK (vehicle_type IN ('bicycle', 'motorcycle', 'van', 'truck')),
    CONSTRAINT chk_rider_status  CHECK (status IN ('available', 'busy', 'inactive')),
    CONSTRAINT chk_rider_username CHECK (username ~ '^[a-z0-9_]{2,30}$')
);

CREATE TABLE service_types (
    service_id    SERIAL PRIMARY KEY,
    service_name  VARCHAR(50)   NOT NULL UNIQUE,
    base_rate     DECIMAL(10,2) NOT NULL CHECK (base_rate >= 0),
    rate_per_kg   DECIMAL(10,2) NOT NULL CHECK (rate_per_kg >= 0),
    delivery_days INT           NOT NULL CHECK (delivery_days > 0)
);

CREATE SEQUENCE tracking_seq START 1;

CREATE TABLE parcels (
    parcel_id             SERIAL PRIMARY KEY,
    -- e.g. PT26110900042 : PT + booking date (YYMMDD) + running number
    tracking_no           VARCHAR(20)   NOT NULL UNIQUE
                          DEFAULT 'PT' || to_char(CURRENT_TIMESTAMP, 'YYMMDD')
                                       || lpad(nextval('tracking_seq')::text, 5, '0'),
    sender_id             INT           NOT NULL REFERENCES customers (customer_id),
    receiver_name         VARCHAR(100)  NOT NULL,
    receiver_phone        VARCHAR(15)   NOT NULL,
    receiver_address      TEXT          NOT NULL,
    origin_branch_id      INT           NOT NULL REFERENCES branches (branch_id),
    destination_branch_id INT           NOT NULL REFERENCES branches (branch_id),
    service_id            INT           NOT NULL REFERENCES service_types (service_id),
    weight_kg             DECIMAL(6,2)  NOT NULL CHECK (weight_kg > 0),
    declared_value        DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (declared_value >= 0),
    charge                DECIMAL(10,2) NOT NULL CHECK (charge >= 0),
    current_status        VARCHAR(20)   NOT NULL DEFAULT 'booked',
    booking_date          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_parcel_status CHECK (current_status IN
        ('booked', 'picked_up', 'in_transit', 'at_hub', 'out_for_delivery', 'delivered', 'returned'))
);

-- Child rows of a parcel are removed with it, so cancelling a booking is one DELETE.
CREATE TABLE shipments (
    shipment_id  SERIAL PRIMARY KEY,
    parcel_id    INT         NOT NULL REFERENCES parcels (parcel_id) ON DELETE CASCADE,
    rider_id     INT         NOT NULL REFERENCES riders (rider_id),
    task_type    VARCHAR(10) NOT NULL CHECK (task_type IN ('pickup', 'delivery')),
    assigned_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    CONSTRAINT chk_shipment_time CHECK (completed_at IS NULL OR completed_at >= assigned_at)
);

CREATE TABLE tracking_events (
    event_id   SERIAL PRIMARY KEY,
    parcel_id  INT         NOT NULL REFERENCES parcels (parcel_id) ON DELETE CASCADE,
    branch_id  INT         NOT NULL REFERENCES branches (branch_id),
    status     VARCHAR(20) NOT NULL,
    remarks    TEXT,
    event_time TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_event_status CHECK (status IN
        ('booked', 'picked_up', 'in_transit', 'at_hub', 'out_for_delivery', 'delivered', 'returned'))
);

CREATE TABLE payments (
    payment_id     SERIAL PRIMARY KEY,
    parcel_id      INT           NOT NULL REFERENCES parcels (parcel_id) ON DELETE CASCADE,
    amount         DECIMAL(10,2) NOT NULL CHECK (amount > 0),
    payment_method VARCHAR(20)   NOT NULL,
    payment_status VARCHAR(10)   NOT NULL DEFAULT 'pending',
    payment_date   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_payment_method CHECK (payment_method IN ('cash', 'mobile_banking', 'card', 'cash_on_delivery')),
    CONSTRAINT chk_payment_status CHECK (payment_status IN ('pending', 'paid'))
);

CREATE TABLE admins (
    admin_id      SERIAL PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes on the foreign keys used by the joins and reports
CREATE INDEX idx_parcels_sender      ON parcels (sender_id);
CREATE INDEX idx_parcels_status      ON parcels (current_status);
CREATE INDEX idx_shipments_parcel    ON shipments (parcel_id);
CREATE INDEX idx_shipments_rider     ON shipments (rider_id);
CREATE INDEX idx_events_parcel       ON tracking_events (parcel_id);
CREATE INDEX idx_payments_parcel     ON payments (parcel_id);

-- Every new tracking event becomes the parcel's current status.
CREATE OR REPLACE FUNCTION sync_parcel_status() RETURNS trigger AS $$
BEGIN
    UPDATE parcels SET current_status = NEW.status WHERE parcel_id = NEW.parcel_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_parcel_status
AFTER INSERT ON tracking_events
FOR EACH ROW EXECUTE FUNCTION sync_parcel_status();

-- Views used by the tracking page and the admin reports

CREATE VIEW v_parcel_details AS
SELECT p.*,
       c.full_name    AS sender_name,
       c.phone        AS sender_phone,
       c.email        AS sender_email,
       ob.branch_name AS origin_branch,
       db.branch_name AS destination_branch,
       s.service_name,
       p.booking_date + s.delivery_days * INTERVAL '1 day' AS due_date
FROM parcels p
JOIN customers c      ON c.customer_id = p.sender_id
JOIN branches ob      ON ob.branch_id  = p.origin_branch_id
JOIN branches db      ON db.branch_id  = p.destination_branch_id
JOIN service_types s  ON s.service_id  = p.service_id;

CREATE VIEW v_rider_deliveries AS
SELECT r.rider_id, r.full_name, r.phone, r.vehicle_type, r.status, b.branch_name,
       COUNT(sh.shipment_id) FILTER (WHERE sh.task_type = 'pickup'   AND sh.completed_at IS NOT NULL) AS pickups_done,
       COUNT(sh.shipment_id) FILTER (WHERE sh.task_type = 'delivery' AND sh.completed_at IS NOT NULL) AS deliveries_done,
       COUNT(sh.shipment_id) FILTER (WHERE sh.completed_at IS NULL)                                   AS open_tasks
FROM riders r
JOIN branches b       ON b.branch_id = r.branch_id
LEFT JOIN shipments sh ON sh.rider_id = r.rider_id
GROUP BY r.rider_id, b.branch_name;

CREATE VIEW v_branch_parcel_count AS
SELECT b.branch_id, b.branch_name, b.city,
       (SELECT COUNT(*) FROM parcels p WHERE p.origin_branch_id = b.branch_id)      AS parcels_sent,
       (SELECT COUNT(*) FROM parcels p WHERE p.destination_branch_id = b.branch_id) AS parcels_received,
       (SELECT COALESCE(SUM(p.charge), 0) FROM parcels p WHERE p.origin_branch_id = b.branch_id) AS booked_charge
FROM branches b;

-- Late = delivered after the due date, or still not delivered when the due date has passed.
CREATE VIEW v_delayed_deliveries AS
SELECT d.parcel_id, d.tracking_no, d.sender_name, d.origin_branch, d.destination_branch,
       d.service_name, d.current_status, d.booking_date, d.due_date, e.delivered_at,
       COALESCE(e.delivered_at, CURRENT_TIMESTAMP) - d.due_date AS late_by
FROM v_parcel_details d
LEFT JOIN (SELECT parcel_id, MAX(event_time) AS delivered_at
           FROM tracking_events WHERE status = 'delivered' GROUP BY parcel_id) e
       ON e.parcel_id = d.parcel_id
WHERE d.current_status <> 'returned'
  AND COALESCE(e.delivered_at, CURRENT_TIMESTAMP) > d.due_date;
