"""V5 regression tests. No network or interactive browser required."""
import sys
import types
import json
import sqlite3
import time
from pathlib import Path
from tempfile import TemporaryDirectory

st = types.ModuleType('streamlit')
st.cache_data = lambda *a, **kw: lambda f: f
sys.modules['streamlit'] = st
app = Path(__file__).resolve().parents[1] / 'app.py'
namespace = {'__name__': 'loterias_test_v5'}
sys.modules['loterias_test_v5'] = types.ModuleType('loterias_test_v5')
namespace = sys.modules['loterias_test_v5'].__dict__
exec(app.read_text(encoding='utf-8').split('# ===== Streamlit UI =====')[0], namespace)
cfg = namespace['LOTTERIES']['Mega-Sena']
repo_type = namespace['LocalRepository']
with TemporaryDirectory() as folder:
    repo = repo_type(Path(folder) / 'test.sqlite')
    assert repo.enabled and repo.integrity_status() == 'ok'
    draw = (1, 2, 3, 4, 5, 6)
    assert repo.get_official_draw(cfg, 100) is None
    repo.save_official_draw(cfg, 100, draw)
    assert repo.get_official_draw(cfg, 100) == draw
    assert repo.get_official_draw(cfg, 100, max_age_hours=0) is None
    repo.conn.execute('UPDATE official_draw_cache SET numbers=? WHERE contest=?', ('1,2,3,4,5,7',100))
    repo.conn.commit()
    assert repo.get_official_draw(cfg, 100) is None, 'Corrupted checksum must invalidate cache'
    repo.save_official_draw(cfg, 100, draw)
    repo.conn.execute('UPDATE official_draw_cache SET fetched_at=? WHERE contest=?', (time.time()-90000,100))
    repo.conn.commit()
    assert repo.get_official_draw(cfg, 100) is None, 'Stale cache must expire'
    namespace['REPO'] = repo
    original_fetch_latest = namespace['fetch_official_contest']
    original_fetch_old = namespace['_fetch_contest']
    official = namespace['OfficialContest']
    # Retrieve an official fixture via the real object signature (without a network call).
    calls = []
    def mock_latest(config, *args, **kwargs):
        payload={'numero':102,'listaDezenas':['01','02','03','04','05','06'],
                 'dezenasSorteadasOrdemSorteio':['06','05','04','03','02','01']}
        return namespace['parse_official_contest'](payload, config)
    def mock_old(config, contest):
        calls.append(contest)
        return contest, draw
    namespace['fetch_official_contest'] = mock_latest
    namespace['_fetch_contest'] = mock_old
    # Re-executed function resolves globals at runtime.
    namespace['fetch_recent_official_draws'].__globals__.update({'REPO':repo,'fetch_official_contest':mock_latest,'_fetch_contest':mock_old})
    first = namespace['fetch_recent_official_draws'](cfg, 3, max_workers=1)
    assert first.valid_rows == 3 and first.metadata['historical_api_requests'] == 3
    assert sorted(calls) == [100,101]
    calls.clear()
    second = namespace['fetch_recent_official_draws'](cfg, 3, max_workers=1)
    assert second.valid_rows == 3 and second.metadata['historical_api_requests'] == 1
    assert second.metadata['historical_cache_hits'] == 2 and not calls

payload={'numero':55,'listaDezenas':[1,2,3,4,5,6], 'dezenasSorteadasOrdemSorteio':[1,2,3,4,5,7]}
try:
    namespace['parse_official_contest'](payload, cfg)
    raise AssertionError('Inconsistent drawn order accepted')
except ValueError:
    pass

backtest = namespace['BacktestResult'](strategy_hits=[2,3,4,5], random_hits=[1,2,3,4],tested_draws=2,simulations_per_draw=2)
stat = namespace['backtest_uncertainty'](backtest)
assert stat['difference'] == 1.0 and stat['ci_lower'] == stat['ci_upper'] == 1.0
assert stat['includes_zero'] is False
print('V5_RELIABILITY_TESTS_OK')
