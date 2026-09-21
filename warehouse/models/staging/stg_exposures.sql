select snapshot_id, payment_id, application_id, consumer_id,
       cast(as_of as timestamp) as as_of, currency, age_days,
       attempted_minor, settled_minor, returned_minor, recovered_minor, outstanding_minor
from {{ source('risk_raw', 'exposures') }}
