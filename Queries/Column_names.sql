INSERT INTO public.active_symbols
  (symbol, as_of, window_hours, n, wins, losses, win_rate,
   mean_pnl, gross_profit, gross_loss, profit_factor, score, eligible)
VALUES
  ('IP-USD',  now(), 24, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, NULL, 0.0, TRUE),
  ('KAITO-USD',  now(), 24, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, NULL, 0.0, TRUE),
  ('BTC-USD',  now(), 24, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, NULL, 0.0, TRUE)
ON CONFLICT (symbol) DO UPDATE
SET as_of = EXCLUDED.as_of,
    window_hours = EXCLUDED.window_hours,
    eligible = TRUE;