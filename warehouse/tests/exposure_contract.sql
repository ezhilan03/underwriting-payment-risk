select e.snapshot_id from {{ ref('stg_exposures') }} e
left join {{ ref('stg_features') }} f using (application_id)
where f.application_id is null or e.currency <> f.currency
   or e.consumer_id <> f.consumer_id or e.age_days < 0
   or e.age_days is null or e.currency is null or e.consumer_id is null
   or e.attempted_minor is null or e.settled_minor is null
   or e.returned_minor is null or e.recovered_minor is null or e.outstanding_minor is null
   or e.as_of is null or e.snapshot_id is null or e.payment_id is null
   or e.outstanding_minor <> e.returned_minor - e.recovered_minor
   or e.outstanding_minor < 0 or e.recovered_minor < 0
   or e.returned_minor > e.settled_minor or e.settled_minor > e.attempted_minor
union all
select snapshot_id from {{ ref('stg_exposures') }}
group by snapshot_id having count(*) <> 1
