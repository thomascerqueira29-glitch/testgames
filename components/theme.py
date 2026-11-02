CUSTOM_CSS = """
<style>
:root { --card-radius: 18px; }
.block-container {padding-top: 1.25rem; padding-bottom: 4rem; max-width: 1500px;}
[data-testid="stSidebar"] {border-right:1px solid rgba(128,128,128,.16);}
[data-testid="stMetric"] {border:1px solid rgba(128,128,128,.18);border-radius:var(--card-radius);padding:14px 16px;background:rgba(128,128,128,.025);}
.lottery-balls {display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin:8px 0 16px 0;}
.lottery-ball {border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:800;border:2px solid rgba(41,121,255,.42);background:rgba(41,121,255,.10);}
.number-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(55px,1fr));gap:7px;margin:10px 0 18px;}
.number-cell {border-radius:12px;padding:9px 5px;text-align:center;border:1px solid rgba(128,128,128,.16);font-weight:750;}
.hero-card {border:1px solid rgba(128,128,128,.18);border-radius:24px;padding:22px 24px;margin:10px 0 18px;background:linear-gradient(135deg,rgba(41,121,255,.08),rgba(0,200,160,.04));}
.soft-card {border:1px solid rgba(128,128,128,.16);border-radius:16px;padding:14px 16px;margin-bottom:10px;}
.small-note {opacity:.76;font-size:.88rem;}
.kpi-label {font-size:.8rem;opacity:.65;text-transform:uppercase;letter-spacing:.04em;}
@media (max-width: 700px) {
  .block-container {padding-left:.75rem;padding-right:.75rem;}
  .lottery-ball {width:38px!important;height:38px!important;font-size:.82rem;}
  .number-grid {grid-template-columns:repeat(5,1fr);}
  [data-testid="stMetric"] {padding:10px;}
}

/* V3.4 - cards, modo cego e criacao auditavel */
[data-testid="stMetric"] {position:relative;overflow:hidden;box-shadow:0 8px 24px rgba(15,23,42,.045);transition:transform .18s ease, box-shadow .18s ease;}
[data-testid="stMetric"]:hover {transform:translateY(-2px);box-shadow:0 12px 30px rgba(15,23,42,.09);}
.info-card {position:relative;min-height:132px;padding:17px 18px;border:1px solid rgba(128,128,128,.16);border-radius:20px;background:linear-gradient(145deg,color-mix(in srgb,var(--accent) 9%, transparent),rgba(255,255,255,.02));box-shadow:0 9px 26px rgba(15,23,42,.055);overflow:hidden;}
.info-card:before {content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--accent);}
.info-card-top {display:flex;gap:8px;align-items:center;margin-bottom:12px}.info-card-icon{font-size:1.1rem}.info-card-label{font-size:.76rem;font-weight:750;letter-spacing:.055em;text-transform:uppercase;opacity:.65}.info-card-value{font-size:1.68rem;line-height:1.05;font-weight:850;letter-spacing:-.035em}.info-card-subtitle{font-size:.8rem;opacity:.62;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.official-card {position:relative;min-height:365px;padding:20px 20px 18px;border:1px solid rgba(128,128,128,.16);border-radius:22px;background:linear-gradient(160deg,color-mix(in srgb,var(--accent) 8%, transparent),rgba(255,255,255,.02) 48%,rgba(255,255,255,.01));box-shadow:0 10px 28px rgba(15,23,42,.07);margin-bottom:12px;overflow:hidden;display:flex;flex-direction:column;transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}
.official-card:hover{transform:translateY(-2px);box-shadow:0 16px 38px rgba(15,23,42,.10);border-color:color-mix(in srgb,var(--accent) 28%,rgba(128,128,128,.16))}.official-card-topline{position:absolute;left:0;right:0;top:0;height:4px;background:linear-gradient(90deg,var(--accent),color-mix(in srgb,var(--accent) 38%,white));}.official-card:after{content:"";position:absolute;width:145px;height:145px;border-radius:50%;right:-70px;top:-75px;background:var(--accent);opacity:.07;pointer-events:none}.official-card.unavailable{min-height:180px;opacity:.72}.official-card-error{margin-top:18px;opacity:.65}.official-card-header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;position:relative;z-index:1}.official-heading{min-width:0}.official-card-title{font-size:1.18rem;font-weight:850;letter-spacing:-.025em;line-height:1.12}.official-card-contest{display:flex;flex-wrap:wrap;gap:5px 9px;font-size:.72rem;opacity:.62;margin-top:6px}.official-card-contest span:first-child{font-weight:700}.status-pill{font-size:.62rem;font-weight:850;padding:6px 8px;border-radius:999px;letter-spacing:.04em;white-space:nowrap;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}.status-pill.accumulated{background:rgba(239,68,68,.10);color:#dc2626;border:1px solid rgba(239,68,68,.20)}.status-pill.winner{background:rgba(16,185,129,.10);color:#059669;border:1px solid rgba(16,185,129,.20)}
.result-special-label{font-size:.66rem;text-transform:uppercase;letter-spacing:.085em;font-weight:800;opacity:.52;margin:17px 0 8px}.result-balls{display:flex;flex-wrap:wrap;gap:7px;align-items:center}.result-ball{width:35px;height:35px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:.78rem;font-weight:850;background:var(--accent);color:white;box-shadow:0 5px 12px color-mix(in srgb,var(--accent) 24%,transparent);border:2px solid rgba(255,255,255,.42)}.result-ball.compact{width:30px;height:30px;font-size:.69rem}.official-card-spacer{flex:1;min-height:12px}
.official-extra{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:12px;padding:10px 12px;border:1px solid rgba(128,128,128,.08);border-radius:12px;background:rgba(128,128,128,.055);font-size:.76rem}.official-extra span{opacity:.60}.official-extra strong{font-size:.78rem;text-align:right}.official-card-footer{border-top:1px solid rgba(128,128,128,.13);margin-top:14px;padding-top:13px}.prize-row{display:flex;justify-content:space-between;align-items:flex-end;gap:10px}.prize-copy{display:flex;width:100%;justify-content:space-between;align-items:baseline;gap:10px}.prize-copy span{font-size:.69rem;opacity:.58}.prize-copy strong{font-size:.98rem;letter-spacing:-.02em;color:var(--accent)}.next-draw{display:flex;align-items:center;gap:6px;font-size:.70rem;opacity:.60;margin-top:8px}.next-draw-dot{width:6px;height:6px;border-radius:50%;background:var(--accent);flex:0 0 auto}.super-sete-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}.super-sete-cell{display:flex;flex-direction:column;align-items:center;gap:4px}.super-sete-col{font-size:.55rem;opacity:.48;font-weight:800}.super-sete-number{width:30px;height:30px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:var(--accent);color:#fff;font-size:.78rem;font-weight:900;box-shadow:0 5px 12px color-mix(in srgb,var(--accent) 22%,transparent)}
@media(max-width:900px){.official-card{min-height:auto}.info-card{min-height:112px}.result-ball{width:34px;height:34px}.result-ball.compact{width:29px;height:29px}.super-sete-number{width:29px;height:29px}}
</style>
"""

# V4: shared navigation styling
CUSTOM_CSS += """
<style>
.stApp {background: #f5f8fb;color:#18324b;}
[data-testid="stHeader"] {background:rgba(245,248,251,.96);}
.block-container {max-width:1640px;padding-top:1rem;}
[data-testid="stSidebar"] {background:#edf3f8;border-right:1px solid #dbe5ed;}
[data-testid="stMetric"] {background:#fff;border:1px solid #dce7ee;border-bottom:3px solid #0d8895;border-radius:13px;}
.hero-card {background:linear-gradient(135deg,#fff,#f0f7fa);border-color:#d9e7ec;border-radius:16px;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px;}
.stButton>button[kind="primary"] {background:#0a8394;border:0;border-radius:9px;}
.stButton>button[kind="primary"]:hover {background:#086d7a;}
/* horizontal navigation rendered by native Streamlit radio controls */
div[role="radiogroup"]:has(input[aria-label="Área principal"]) {flex-wrap:wrap;}
[data-testid="stRadio"]:has(input[aria-label="Área principal"]) label {border-radius:10px;}
[data-testid="stDataFrame"] {border-radius:12px;overflow:hidden;}
.lotlab-topbar {display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 24px;background:#12344f;color:white;border-radius:15px 15px 0 0;}
.lotlab-topbar strong {font-size:1.25rem;letter-spacing:-.025em;}
.lotlab-topbar small {font-size:.78rem;color:#b9d2e3;}
.lotlab-nav-description {color:#60778a;font-size:.8rem;margin:0 0 10px;}
@media(max-width:800px){.lotlab-topbar{padding:15px;flex-wrap:wrap;}.block-container{padding-left:.7rem;padding-right:.7rem;}}
</style>
"""
