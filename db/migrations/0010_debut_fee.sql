-- 0010_debut_fee.sql
-- The debut purchase is the $5 step from a Development Contract ($5) to a Purchased Contract ($10).
-- 0003 seeded $15 from the Constitution's text (Art. III s02.3). Zach, 2026-11-28: the Constitution
-- is wrong there; it is a $5 increase.
update wb_prices set amount = 5 where item = 'debut_fee';
