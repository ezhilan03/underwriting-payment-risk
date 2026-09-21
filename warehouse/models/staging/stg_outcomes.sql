select application_id, consumer_id, currency,
       cast(decided_at as timestamp) as decided_at,
       mature, attempted_minor, settled_minor, returned_minor, recovered_minor,
       outstanding_minor, return_count, payment_count, observed
from {{ source('risk_raw', 'outcomes') }}
