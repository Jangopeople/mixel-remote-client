-- Mixel Remote live Stripe catalogue (account acct_1MHij8FRPXnJX22h, shared with Mixel Shop; created
-- 2026-11-13 by next/services/billing/scripts/catalogue.js). CHF, Swiss VAT included
-- (tax_behavior inclusive, no Stripe Tax), metadata app=mixel-remote.
begin;
insert into remote_saas.billing_prices(price_id, product_id, kind, plan, billing_interval, amount_rappen) values
    ('price_1UbE0dFRPXnJX22h7xUrgLgT', 'mixel_remote_solo', 'plan', 'solo', 'year', 14400),
    ('price_1UbE0dFRPXnJX22hRaSqXqnc', 'mixel_remote_solo', 'plan', 'solo', 'month', 1440),
    ('price_1UbE0eFRPXnJX22hQAxS2zVp', 'mixel_remote_team', 'plan', 'team', 'year', 46800),
    ('price_1UbE0eFRPXnJX22hk0HgevKd', 'mixel_remote_team', 'plan', 'team', 'month', 4680),
    ('price_1UbE0fFRPXnJX22hqPk8NMcg', 'mixel_remote_business', 'plan', 'business', 'year', 106800),
    ('price_1UbE0fFRPXnJX22hVejxq8TV', 'mixel_remote_business', 'plan', 'business', 'month', 10680),
    ('price_1UbE0gFRPXnJX22haP7ThGds', 'mixel_remote_technician', 'seat', null, 'year', 9600),
    ('price_1UbE0gFRPXnJX22hs8h5qgOS', 'mixel_remote_technician', 'seat', null, 'month', 960)
on conflict (price_id) do nothing;
commit;
