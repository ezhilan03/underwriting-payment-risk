-- No cross-currency aggregation. Unobserved/censored outcomes never become negatives.
with cohort as (
    select f.split, f.currency, f.original_approved,
           o.mature, o.observed, o.return_count,
           o.attempted_minor, o.settled_minor, o.returned_minor,
           o.recovered_minor, o.outstanding_minor
    from {{ ref('stg_features') }} f
    join {{ ref('stg_outcomes') }} o using (application_id)
), counts as (
    select split, currency, original_approved,
           count(*) as application_count,
           sum(case when mature then 1 else 0 end) as mature_application_count,
           sum(case when not mature then 1 else 0 end) as immature_application_count,
           sum(case when mature and observed then 1 else 0 end) as observed_mature_application_count,
           sum(case when mature and not observed then 1 else 0 end) as unobserved_mature_application_count,
           sum(case when mature and observed and return_count > 0 then 1 else 0 end) as returned_application_count,
           sum(case when mature and observed then attempted_minor else 0 end) as attempted_minor,
           sum(case when mature and observed then settled_minor else 0 end) as settled_minor,
           sum(case when mature and observed then returned_minor else 0 end) as returned_minor,
           sum(case when mature and observed then recovered_minor else 0 end) as recovered_minor,
           sum(case when mature and observed then outstanding_minor else 0 end) as outstanding_minor
    from cohort
    group by split, currency, original_approved
)
select *,
       1.0 * observed_mature_application_count / nullif(mature_application_count, 0) as mature_observation_rate,
       1.0 * returned_application_count / nullif(observed_mature_application_count, 0) as observed_mature_return_rate,
       1.0 * outstanding_minor / nullif(settled_minor, 0) as observed_mature_net_loss_rate
from counts
