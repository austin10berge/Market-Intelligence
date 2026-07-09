import math


def _norm_cdf(value: float) -> float:
    """Normal CDF used for Black-Scholes pricing."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

def black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
) -> float:
    """Approximate European option value."""
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return 0.0

    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiry_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    if option_type.lower() == "call":
        return (spot * _norm_cdf(d1)) - (
            strike * math.exp(-risk_free_rate * time_to_expiry_years) * _norm_cdf(d2)
        )

    return (strike * math.exp(-risk_free_rate * time_to_expiry_years) * _norm_cdf(-d2)) - (
        spot * _norm_cdf(-d1)
    )

def _norm_ppf(p: float) -> float:
    """Approximate Inverse Normal CDF (quantile function)."""
    if p <= 0.0: return -10.0
    if p >= 1.0: return 10.0

    a1 = -3.969683028665376e+01
    a2 =  2.209460984245205e+02
    a3 = -2.759285104469687e+02
    a4 =  1.383577518672690e+02
    a5 = -3.066479806614716e+01
    a6 =  2.506628277459239e+00
    b1 = -5.447609879822406e+01
    b2 =  1.615858368580409e+02
    b3 = -1.556989798598866e+02
    b4 =  6.680131188771972e+01
    b5 = -1.328068155288572e+01
    c1 = -7.784894002430293e-03
    c2 = -3.223964580411365e-01
    c3 = -2.400758277161838e+00
    c4 = -2.549732539343734e+00
    c5 =  4.374664141464968e+00
    c6 =  2.938163982698783e+00
    d1 =  7.784695709041462e-03
    d2 =  3.224671290700398e-01
    d3 =  2.445134137142996e+00
    d4 =  3.754408661907416e+00
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2*math.log(p))
        return (((((c1*q+c2)*q+c3)*q+c4)*q+c5)*q+c6) / ((((d1*q+d2)*q+d3)*q+d4)*q+1)
    elif p <= p_high:
        q = p - 0.5
        r = q*q
        return (((((a1*r+a2)*r+a3)*r+a4)*r+a5)*r+a6)*q / (((((b1*r+b2)*r+b3)*r+b4)*r+b5)*r+1)
    else:
        q = math.sqrt(-2*math.log(1-p))
        return -(((((c1*q+c2)*q+c3)*q+c4)*q+c5)*q+c6) / ((((d1*q+d2)*q+d3)*q+d4)*q+1)

def get_strike_for_delta(
    spot: float,
    target_delta: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
) -> float:
    """Calculate strike price that produces the target delta under Black-Scholes."""
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0:
        return spot

    if option_type.lower() == "call":
        if target_delta <= 0.0 or target_delta >= 1.0: return spot
        z = _norm_ppf(target_delta)
    else: # put
        if target_delta >= 0.0 or target_delta <= -1.0: return spot
        z = _norm_ppf(target_delta + 1.0)

    sqrt_t = math.sqrt(time_to_expiry_years)
    k = spot * math.exp(-z * volatility * sqrt_t + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry_years)
    return round(k, 2)
