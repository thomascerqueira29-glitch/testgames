"""Smoke tests for the non-UI core of Loterias Lab V3."""
from pathlib import Path
import sys
import types

st = types.ModuleType("streamlit")
def cache_data(*args, **kwargs):
    def decorator(fn):
        return fn
    return decorator
st.cache_data = cache_data
sys.modules["streamlit"] = st

full = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
core = full.split("# ===== Streamlit UI =====")[0]
ns = {}
exec(core, ns)

LOTTERIES = ns["LOTTERIES"]
AdvancedAnalytics = ns["AdvancedAnalytics"]
simulate_draws = ns["simulate_draws"]
generate_custom_bets = ns["generate_custom_bets"]
coverage_metrics = ns["coverage_metrics"]
build_closure = ns["build_closure"]
monte_carlo_hits = ns["monte_carlo_hits"]
load_tabular_bytes = ns["load_tabular_bytes"]
top_prize_probability = ns["top_prize_probability"]

assert LOTTERIES["Lotomania"].universe[0] == 0
assert LOTTERIES["Lotomania"].universe[-1] == 99
assert LOTTERIES["Mega-Sena"].bet_price(7) == 42.0
assert LOTTERIES["Lotofácil"].bet_price(16) == 56.0
assert LOTTERIES["Quina"].bet_price(6) == 18.0
assert LOTTERIES["Lotomania"].bet_price(50) == 3.0

for name, cfg in LOTTERIES.items():
    report = simulate_draws(cfg, 250, 42)
    assert len(report.draws) == 250
    analytics = AdvancedAnalytics(report.draws, cfg)
    assert len(analytics.frequency_table()) == cfg.universe_size
    stat, pvalue = analytics.chi_square_uniformity()
    assert stat >= 0 and 0 <= pvalue <= 1
    games = generate_custom_bets(
        cfg, 5, cfg.bet_default, "Aleatório", analytics,
        diversity="Média", even_min=0, even_max=cfg.bet_default,
        min_bands=1, seed=7,
    )
    assert len(games) == 5
    assert all(len(set(game)) == cfg.bet_default for game in games)
    coverage = coverage_metrics(games)
    assert 0 <= coverage["pair_coverage"] <= 1
    prob, one_in = top_prize_probability(cfg, cfg.bet_default)
    assert 0 < prob <= 1 and one_in >= 1

lotomania = LOTTERIES["Lotomania"]
csv = (";".join(f"{n:02d}" for n in range(20)) + "\n").encode()
report = load_tabular_bytes(csv, "lotomania.csv", lotomania)
assert report.valid_rows == 1
assert 0 in report.draws[0]

mega = LOTTERIES["Mega-Sena"]
closure = build_closure(list(range(1, 11)), mega, 6, 12, "Pares", 42)
assert 1 <= len(closure) <= 12
assert all(len(game) == 6 for game in closure)

mc = monte_carlo_hits(mega, 6, 10_000, 42)
assert int(mc["Simulações"].sum()) == 10_000

print("SMOKE_TESTS_OK")
