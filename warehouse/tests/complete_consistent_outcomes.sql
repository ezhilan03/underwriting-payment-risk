select f.application_id from {{ ref('stg_features') }} f
left join {{ ref('stg_outcomes') }} o using (application_id)
where o.application_id is null or f.currency <> o.currency
   or f.consumer_id <> o.consumer_id or f.decided_at <> o.decided_at
   or f.currency is null or length(f.currency) <> 3 or f.currency <> upper(f.currency)
