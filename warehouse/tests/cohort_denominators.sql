select split, currency from {{ ref('mart_risk_cohorts') }}
where application_count <> mature_application_count + immature_application_count
   or mature_application_count <> observed_mature_application_count + unobserved_mature_application_count
   or returned_application_count > observed_mature_application_count
   or (observed_mature_application_count = 0 and observed_mature_return_rate is not null)
   or (attempted_minor = 0 and observed_mature_net_loss_rate is not null)
