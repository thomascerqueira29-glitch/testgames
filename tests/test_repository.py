"""Additional repository regression checks; offline and independent of Streamlit UI."""
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory

st = types.ModuleType('streamlit')
st.cache_data = lambda *a, **kw: lambda f: f
sys.modules['streamlit'] = st
code = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8').split('# ===== Streamlit UI =====')[0]
mod = types.ModuleType('loterias_core_test')
sys.modules[mod.__name__] = mod
ns = mod.__dict__
exec(code, ns)
with TemporaryDirectory() as d:
    repo = ns['LocalRepository'](Path(d) / 'wallet.sqlite3')
    assert repo.enabled
    repo.add_bet('alice','Mega-Sena',(1,2,3,4,5,6))
    repo.add_bet('bob','Mega-Sena',(7,8,9,10,11,12))
    a = repo.list_bets('alice')
    assert len(a) == 1
    repo.delete_bet(int(repo.list_bets('bob').iloc[0]['id']), 'alice')
    assert len(repo.list_bets('bob')) == 1, 'Cross-profile deletion must not succeed'
    payload={'bets':[{'lottery':'Mega-Sena','numbers':'13,14,15,16,17,18','amount':6}], 'strategies':[], 'budget':[{'kind':'Aposta','amount':6}]}
    assert repo.import_backup('alice', payload) == {'bets':1,'strategies':0,'budget':1}
    assert repo.import_backup('alice', payload) == {'bets':0,'strategies':0,'budget':0}
    before = len(repo.list_bets('alice'))
    try:
        repo.import_backup('alice', {'bets':[{'lottery':'Mega-Sena','numbers':'100,101,102,103,104,105'}]})
        raise AssertionError('Invalid games accepted')
    except ValueError:
        pass
    assert len(repo.list_bets('alice')) == before
print('REPOSITORY_TESTS_OK')
