import pytest

from v2.plugins.strategies.regime_carry.core import carry_targets, sleeve_trades

UNIVERSE = ("BTC-USD", "ETH-USD", "SOL-USD")
VOLS = {"BTC-USD": 0.02, "ETH-USD": 0.03, "SOL-USD": 0.05}


class TestCarryTargets:
    def test_equal_all_eligible(self):
        w = carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "equal")
        for s in UNIVERSE:
            assert w[s] == pytest.approx(1 / 3)

    def test_equal_no_redistribution_when_sleeve_gated_out(self):
        # SOL gated out: its 1/3 goes to cash, NOT to BTC/ETH
        w = carry_targets(VOLS, {"BTC-USD", "ETH-USD"}, UNIVERSE, "equal")
        assert w["SOL-USD"] == 0.0
        assert w["BTC-USD"] == pytest.approx(1 / 3)
        assert w["ETH-USD"] == pytest.approx(1 / 3)
        assert sum(w.values()) == pytest.approx(2 / 3)

    def test_inverse_vol_base_over_full_universe(self):
        # Base inverse-vol weights over ALL THREE (1/.02 : 1/.03 : 1/.05
        # = 50:33.3:20 -> 0.4839/0.3226/0.1935, none capped), then zero SOL.
        w = carry_targets(VOLS, {"BTC-USD", "ETH-USD"}, UNIVERSE, "inverse_vol")
        assert w["SOL-USD"] == 0.0
        assert w["BTC-USD"] == pytest.approx(0.48387, abs=1e-4)
        assert w["ETH-USD"] == pytest.approx(0.32258, abs=1e-4)

    def test_cap_binds(self):
        vols = {"BTC-USD": 0.001, "ETH-USD": 0.05, "SOL-USD": 0.05}
        w = carry_targets(vols, set(UNIVERSE), UNIVERSE, "inverse_vol", cap=0.50)
        assert w["BTC-USD"] == pytest.approx(0.50)

    def test_exposure_scales_everything(self):
        w = carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "equal", exposure=0.4)
        for s in UNIVERSE:
            assert w[s] == pytest.approx(0.4 / 3)

    def test_none_vol_is_ineligible_even_if_gated_in(self):
        vols = {"BTC-USD": 0.02, "ETH-USD": None, "SOL-USD": 0.05}
        w = carry_targets(vols, set(UNIVERSE), UNIVERSE, "inverse_vol")
        assert w["ETH-USD"] == 0.0
        w2 = carry_targets(vols, set(UNIVERSE), UNIVERSE, "equal")
        assert w2["ETH-USD"] == 0.0

    def test_empty_eligible_all_cash(self):
        w = carry_targets(VOLS, set(), UNIVERSE, "equal")
        assert all(v == 0.0 for v in w.values())

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError):
            carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "momentum")


class TestSleeveTrades:
    def test_full_exit_ignores_band(self):
        t = sleeve_trades({"BTC-USD": 0.05}, {"BTC-USD": 0.0}, band=0.20)
        assert t["BTC-USD"] == pytest.approx(-0.05)

    def test_within_band_suppressed(self):
        # target 0.30, current 0.25: |delta| 0.05 < 0.20*0.30=0.06 -> no trade
        t = sleeve_trades({"BTC-USD": 0.25}, {"BTC-USD": 0.30}, band=0.20)
        assert t["BTC-USD"] == 0.0

    def test_outside_band_trades_delta(self):
        # target 0.30, current 0.22: |delta| 0.08 > 0.06 -> trade +0.08
        t = sleeve_trades({"BTC-USD": 0.22}, {"BTC-USD": 0.30}, band=0.20)
        assert t["BTC-USD"] == pytest.approx(0.08)

    def test_new_position_from_zero(self):
        # current 0 vs target 0.30: |0.30| > 0.06 -> buy full target
        t = sleeve_trades({}, {"ETH-USD": 0.30}, band=0.20)
        assert t["ETH-USD"] == pytest.approx(0.30)

    def test_zero_current_zero_target_no_trade(self):
        t = sleeve_trades({"SOL-USD": 0.0}, {"SOL-USD": 0.0}, band=0.20)
        assert t["SOL-USD"] == 0.0
