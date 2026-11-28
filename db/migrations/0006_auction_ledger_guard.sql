-- Spec 02 backstop on the live ledger (depends on 0003 for wb_ledger).
--
-- A prospect's auction win can be debited once per week. ref_id is the week's
-- Monday (ISO date). A second resolution of the same week cannot double-charge,
-- even if the service's own checks were bypassed.
create unique index wb_ledger_auction_win_once on wb_ledger (upid, ref_id)
  where ref_type = 'auction' and kind = 'auction_win';
