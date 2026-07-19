"""Tests for src/algo_detective/schwab_options.py — parses schwab-mcp's
compact get_option_chain text output and selects the put contract closest
to a target delta, for nightly narrow-universe delta collection (Step 8 of
the pipeline). HTML/text snippets below are trimmed excerpts of a real
schwab-mcp response captured 2026-07-19, not a synthetic mockup.
See docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md.
"""

from __future__ import annotations

from src.algo_detective.schwab_options import (
    _parse_put_chain,
    _select_target_delta_contract,
)

_HOOD_CHAIN_FIXTURE = """symbol: HOOD
status: SUCCESS
strategy: SINGLE
interval: 0
isDelayed: false
isIndex: false
interestRate: 3.707
underlyingPrice: 99.96
volatility: 29.0
daysToExpiration: 5.0
dividendYield: 0
numberOfContracts: 30
assetMainType: EQUITY
assetSubType: COE
isChainTruncated: false
ethOptionEligible: true
hasBinaryOptions: false
putExpDateMap:
  "2026-07-24:5":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.33,1.41,1.37,1.37,77,19,-0.223,0.029,-0.222,0.041,-0.004,370,"2026-07-24T20:00:00.000+00:00",5,W,false
    "94.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.59,1.67,1.62,1.63,21,34,-0.255,0.032,-0.24,0.044,-0.005,155,"2026-07-24T20:00:00.000+00:00",5,W,false
    "95.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.85,1.95,1.9,1.9,92,24,-0.288,0.034,-0.253,0.047,-0.006,8325,"2026-07-24T20:00:00.000+00:00",5,W,false
  "2026-07-31:12":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      3.3,4.0,3.59,3.65,419,14,-0.306,0.021,-0.218,0.068,-0.013,2012,"2026-07-31T20:00:00.000+00:00",12,W,false
    "94.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      4.05,4.45,4.25,4.25,46,154,-0.332,0.021,-0.234,0.071,-0.014,275,"2026-07-31T20:00:00.000+00:00",12,W,false
"""

_EMPTY_CHAIN_FIXTURE = """symbol: XXXX
status: SUCCESS
numberOfContracts: 0
putExpDateMap: {}
"""


class TestParsePutChain:
    def test_parses_all_contracts_across_expirations(self):
        contracts = _parse_put_chain(_HOOD_CHAIN_FIXTURE)
        assert len(contracts) == 5

        first = contracts[0]
        assert first["strike"] == 93.0
        assert first["delta"] == -0.223
        assert first["bid"] == 1.33
        assert first["ask"] == 1.41
        assert first["open_interest"] == 370

    def test_returns_empty_list_for_chain_with_no_contracts(self):
        assert _parse_put_chain(_EMPTY_CHAIN_FIXTURE) == []


class TestSelectTargetDeltaContract:
    def test_picks_contract_closest_to_target_delta(self):
        contracts = _parse_put_chain(_HOOD_CHAIN_FIXTURE)
        selected = _select_target_delta_contract(contracts, target_delta=0.20)

        assert selected["strike"] == 93.0
        assert selected["delta"] == -0.223
        assert selected["bid"] == 1.33
        assert selected["ask"] == 1.41
        assert selected["open_interest"] == 370

    def test_returns_none_for_empty_contract_list(self):
        assert _select_target_delta_contract([], target_delta=0.20) is None
