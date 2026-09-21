select application_id, consumer_id,
       cast(decided_at as timestamp) as decided_at,
       currency, report_id, report_version,
       cast(report_effective_at as timestamp) as report_effective_at,
       cast(report_received_at as timestamp) as report_received_at,
       history_missing, debt_ratio, history_months,
       prior_returns, prior_payments, original_approved, split
from {{ source('risk_raw', 'features') }}
