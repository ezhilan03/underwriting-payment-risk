select application_id from {{ ref('stg_features') }}
where report_effective_at > decided_at
   or report_received_at > decided_at
   or (history_missing = 0 and (report_id is null or report_effective_at is null or report_received_at is null))
   or history_missing not in (0, 1)
