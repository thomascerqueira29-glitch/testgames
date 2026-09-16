from __future__ import annotations

import html
from datetime import datetime

import pandas as pd
import streamlit as st

from loteria_app import (
    LOTTERIES,
    LotteryAnalytics,
    fetch_recent_official_draws,
    generate_profiled_bet,
    load_tabular_bytes,
    run_backtest,
    simulate_draws,
)
from loteria_app.models import LoadReport


st.set_page_config(
    page_title="Loterias — Laboratório Estatístico",
    page_icon="🍀",
    layout="wide",
    initial_sidebar_state="expanded",
)


CUSTOM_CSS = """
<style>
.block-container {padding-top: 1.8rem; padding-bottom: 3rem;}
[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.22);
    border-radius: 14px;
    padding: 14px 16px;
}
.lottery-balls {display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:8px 0 16px 0;}
.lottery-ball {
    width:46px; height:46px; border-radius:50%; display:inline-flex;
    align-items:center; justify-content:center; font-weight:800;
    border:2px solid rgba(31,119,180,.42); background:rgba(31,119,180,.10);
}
.number-grid {display:grid; grid-template-columns:repeat(auto-fit,minmax(52px,1fr)); gap:7px; margin:10px 0 18px;}
.number-cell {border-radius:10px; padding:9px 5px; text-align:center; border:1px solid rgba(128,128,128,.18); font-weight:700;}
.muted-card {border:1px solid rgba(128,128,128,.18); border-radius:14px; padding:14px 16px; margin-bottom:10px;}
.small-note {opacity:.78; font-size:.9rem;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_load_file(data: bytes, filename: str, lottery_name: str) -> LoadReport:
    return load_tabular_bytes(data, filename, LOTTERIES[lottery_name])


@st.cache_data(ttl=3600, show_spinner=False)
def cached_official(lottery_name: str, quantity: int) -> LoadReport:
    return fetch_recent_official_draws(LOTTERIES[lottery_name], quantity)


def reset_for_lottery(lottery_name: str) -> None:
    st.session_state.current_lottery = lottery_name
    for key in (
        "load_report", "bet_history", "backtest_result", "file_fingerprint",
        "bt_bet_size", "bt_hot", "bt_cold", "gen_bet_size", "gen_hot", "gen_cold",
    ):
        st.session_state.pop(key, None)


def format_draw(draw: tuple[int, ...], config) -> str:
    return " - ".join(config.format_number(n) for n in draw)


def balls_html(draw: tuple[int, ...], config) -> str:
    balls = "".join(
        f'<span class="lottery-ball">{html.escape(config.format_number(n))}</span>'
        for n in draw
    )
    return f'<div class="lottery-balls">{balls}</div>'


def frequency_grid_html(table: pd.DataFrame, config) -> str:
    if table.empty:
        return ""
    min_f = float(table["Frequência"].min())
    max_f = float(table["Frequência"].max())
    span = max(max_f - min_f, 1.0)
    cells: list[str] = []
    for _, row in table.iterrows():
        intensity = (float(row["Frequência"]) - min_f) / span
        opacity = 0.06 + 0.26 * intensity
        number = config.format_number(int(row["Número"]))
        freq = int(row["Frequência"])
        cells.append(
            f'<div class="number-cell" style="background:rgba(31,119,180,{opacity:.3f})" '
            f'title="{freq} aparições">{html.escape(number)}<br><span style="font-size:.72rem;opacity:.7">{freq}x</span></div>'
        )
    return '<div class="number-grid">' + "".join(cells) + "</div>"


def get_active_draws(report: LoadReport, sample_size: int | None) -> list[tuple[int, ...]]:
    if sample_size is None or sample_size >= len(report.draws):
        return report.draws
    return report.draws[-sample_size:]


def default_strategy_counts(config, groups, bet_size: int) -> tuple[int, int]:
    hot = min(len(groups.hot), max(0, round(bet_size * 0.40)))
    cold = min(len(groups.cold), max(0, round(bet_size * 0.20)))
    neutral_needed = bet_size - hot - cold
    if neutral_needed > len(groups.neutral):
        shortage = neutral_needed - len(groups.neutral)
        add_hot = min(shortage, len(groups.hot) - hot)
        hot += add_hot
        shortage -= add_hot
        if shortage:
            cold += min(shortage, len(groups.cold) - cold)
    return hot, cold


# ---------- Sidebar ----------
st.sidebar.title("⚙️ Configurações")
lottery_name = st.sidebar.selectbox("Modalidade", list(LOTTERIES.keys()))
config = LOTTERIES[lottery_name]

if st.session_state.get("current_lottery") != lottery_name:
    reset_for_lottery(lottery_name)

st.sidebar.caption(
    f"Universo: {config.format_number(config.number_min)}–{config.format_number(config.number_max)} · "
    f"Sorteio: {config.drawn_count} números · Aposta: {config.bet_min}–{config.bet_max}"
)

st.sidebar.divider()
source = st.sidebar.radio(
    "Fonte dos dados",
    ["Arquivo CSV/Excel", "Dados oficiais CAIXA", "Simulação"],
)

if source == "Arquivo CSV/Excel":
    with st.sidebar.expander("Como preparar o arquivo?"):
        st.write(
            "CSV, TXT, XLSX ou XLS. O sistema tenta identificar automaticamente as colunas das dezenas, "
            "aceita CSV com vírgula ou ponto e vírgula e valida cada concurso antes de incluí-lo."
        )
    uploaded = st.sidebar.file_uploader(
        "Histórico",
        type=["csv", "txt", "xlsx", "xls"],
    )
    if uploaded is not None:
        try:
            file_bytes = uploaded.getvalue()
            fingerprint = (lottery_name, uploaded.name, len(file_bytes), hash(file_bytes))
            if st.session_state.get("file_fingerprint") != fingerprint:
                report = cached_load_file(file_bytes, uploaded.name, lottery_name)
                st.session_state.load_report = report
                st.session_state.file_fingerprint = fingerprint
                st.session_state.bet_history = []
        except Exception:
            st.sidebar.error(
                "Não foi possível interpretar o arquivo. Verifique o formato, o separador e se as dezenas estão em colunas numéricas."
            )

elif source == "Dados oficiais CAIXA":
    st.sidebar.caption(
        "Consulta o endpoint público usado pelo Portal Loterias. Como não há documentação pública estável do endpoint, "
        "essa integração é tratada como experimental."
    )
    official_qty = st.sidebar.slider("Concursos recentes", 20, 500, 100, step=20)
    if st.sidebar.button("Atualizar dados oficiais", type="primary", use_container_width=True):
        try:
            with st.spinner("Consultando resultados..."):
                st.session_state.load_report = cached_official(lottery_name, official_qty)
                st.session_state.bet_history = []
        except Exception:
            st.sidebar.error(
                "Não foi possível consultar os resultados agora. Tente novamente ou utilize um arquivo CSV/Excel."
            )

else:
    sim_qty = st.sidebar.slider("Quantidade de concursos", 100, 5000, 1000, step=100)
    use_seed = st.sidebar.checkbox("Usar seed reproduzível", value=True)
    sim_seed = st.sidebar.number_input("Seed", value=42, step=1, disabled=not use_seed)
    if st.sidebar.button("Gerar simulação", type="primary", use_container_width=True):
        st.session_state.load_report = simulate_draws(
            config,
            sim_qty,
            seed=int(sim_seed) if use_seed else None,
        )
        st.session_state.bet_history = []

report: LoadReport | None = st.session_state.get("load_report")

# ---------- Header ----------
st.title("🍀 Loterias — Laboratório Estatístico")
st.caption(
    "Análise histórica, simulação, backtesting e geração de combinações por perfil estatístico. "
    "Frequências passadas descrevem o histórico e não aumentam a probabilidade matemática de uma dezena no próximo sorteio."
)

if report is None or not report.draws:
    st.info("Configure uma fonte de dados na barra lateral para iniciar a análise.")
    st.stop()

# ---------- Sample filter ----------
max_draws = len(report.draws)
choices: list[tuple[str, int | None]] = []
for n in (20, 50, 100, 250, 500, 1000):
    if n < max_draws:
        choices.append((f"Últimos {n}", n))
choices.append((f"Histórico completo ({max_draws})", None))
choice_labels = [label for label, _ in choices]
selected_label = st.sidebar.selectbox("Amostra para análise", choice_labels, index=len(choice_labels) - 1)
sample_size = dict(choices)[selected_label]
active_draws = get_active_draws(report, sample_size)
analytics = LotteryAnalytics(active_draws, config)
summary = analytics.summary()

st.sidebar.divider()
st.sidebar.caption(f"Fonte ativa: {report.source_name}")
st.sidebar.caption(f"Carregado em: {report.loaded_at:%d/%m/%Y %H:%M}")

# ---------- Main tabs ----------
tab_overview, tab_freq, tab_patterns, tab_backtest, tab_generator, tab_data = st.tabs(
    ["Visão geral", "Frequências", "Padrões", "Backtest", "Gerador", "Dados"]
)

with tab_overview:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Concursos analisados", f"{len(active_draws):,}".replace(",", "."))
    c2.metric("Universo", f"{config.universe_size} números")
    c3.metric("Soma média", f"{summary['average_sum']:.1f}")
    c4.metric("Pares por sorteio", f"{summary['average_even']:.2f}")

    st.subheader("Qualidade e origem dos dados")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Linhas recebidas", report.total_rows)
    q2.metric("Concursos válidos", report.valid_rows)
    q3.metric("Linhas rejeitadas", report.rejected_rows)
    q4.metric("Taxa válida", f"{report.success_rate * 100:.1f}%")

    if report.source_kind == "simulacao":
        st.warning("🧪 A base ativa é uma simulação. Os concursos exibidos não são resultados oficiais.")
    elif report.source_kind == "oficial":
        st.success("Base carregada a partir do Portal Loterias/CAIXA.")

    if report.warnings:
        with st.expander("Detalhes da validação"):
            for warning in report.warnings[:30]:
                st.write(f"• {warning}")
            if len(report.warnings) > 30:
                st.caption(f"Mais {len(report.warnings) - 30} aviso(s) foram omitidos nesta visualização.")

    freq_table = analytics.frequency_table()
    groups = analytics.groups()

    st.subheader("Mapa de frequência")
    st.markdown(frequency_grid_html(freq_table, config), unsafe_allow_html=True)
    st.caption("A intensidade representa somente a frequência dentro da amostra selecionada.")

    hot_df = freq_table[freq_table["Número"].isin(groups.hot)].sort_values(
        ["Frequência", "Número"], ascending=[False, True]
    )
    cold_df = freq_table[freq_table["Número"].isin(groups.cold)].sort_values(
        ["Frequência", "Número"], ascending=[True, True]
    )

    col_hot, col_cold = st.columns(2)
    with col_hot:
        st.markdown("#### 🔥 Grupo mais frequente")
        show = hot_df[["Número", "Frequência", "Esperado", "Desvio %"]].head(12).copy()
        show["Número"] = show["Número"].map(config.format_number)
        st.dataframe(show, hide_index=True, use_container_width=True)
    with col_cold:
        st.markdown("#### ❄️ Grupo menos frequente")
        show = cold_df[["Número", "Frequência", "Esperado", "Desvio %"]].head(12).copy()
        show["Número"] = show["Número"].map(config.format_number)
        st.dataframe(show, hide_index=True, use_container_width=True)

with tab_freq:
    st.subheader("Frequência por dezena")
    freq_table = analytics.frequency_table()
    formatted = freq_table.copy()
    formatted["Número"] = formatted["Número"].map(config.format_number)
    formatted["Frequência %"] = formatted["Frequência %"].map(lambda x: f"{x:.2f}%")
    formatted["Esperado"] = formatted["Esperado"].map(lambda x: f"{x:.1f}")
    formatted["Desvio %"] = formatted["Desvio %"].map(lambda x: f"{x:+.1f}%")
    formatted["Intervalo médio"] = formatted["Intervalo médio"].map(
        lambda x: "—" if pd.isna(x) else f"{x:.1f}"
    )
    st.dataframe(formatted, hide_index=True, use_container_width=True, height=520)

    chart_df = freq_table.set_index("Número")[["Frequência"]]
    st.bar_chart(chart_df, height=300)

    st.subheader("Recente × histórico")
    recent_n = st.slider(
        "Janela recente",
        min_value=min(10, len(active_draws)),
        max_value=len(active_draws),
        value=min(50, len(active_draws)),
        step=1 if len(active_draws) <= 100 else 10,
    )
    comparison = analytics.recent_comparison(recent_n)
    comparison["Número"] = comparison["Número"].map(config.format_number)
    st.dataframe(comparison, hide_index=True, use_container_width=True)

with tab_patterns:
    st.subheader("Distribuições históricas")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### Pares e ímpares")
        parity = analytics.parity_distribution().set_index("Composição")
        st.bar_chart(parity)
    with col_b:
        st.markdown("#### Repetições do concurso anterior")
        repeats = analytics.repeats_from_previous().set_index("Repetidos")
        st.bar_chart(repeats)

    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown("#### Números consecutivos")
        consecutive = analytics.consecutive_distribution().set_index("Pares consecutivos")
        st.bar_chart(consecutive)
    with col_d:
        st.markdown("#### Faixas de soma")
        sums = analytics.sums()
        if not sums.empty:
            bins = min(12, max(4, int(len(sums) ** 0.5)))
            distribution = pd.cut(sums, bins=bins).value_counts().sort_index()
            sum_df = pd.DataFrame({"Concursos": distribution.values}, index=distribution.index.astype(str))
            st.bar_chart(sum_df)

    st.subheader("Combinações recorrentes")
    pairs_col, triples_col = st.columns(2)
    with pairs_col:
        st.markdown("#### Pares mais frequentes")
        st.dataframe(analytics.top_combinations(2, 20), hide_index=True, use_container_width=True)
    with triples_col:
        st.markdown("#### Trios mais frequentes")
        st.dataframe(analytics.top_combinations(3, 20), hide_index=True, use_container_width=True)

with tab_backtest:
    st.subheader("Backtest da estratégia")
    st.write(
        "Cada concurso é testado usando apenas concursos anteriores como treinamento. "
        "A estratégia por perfil é comparada a apostas totalmente aleatórias do mesmo tamanho."
    )

    bt_bet_size = st.slider(
        "Tamanho da aposta",
        config.bet_min,
        config.bet_max,
        config.bet_default,
        key="bt_bet_size",
    )
    bt_groups = LotteryAnalytics(active_draws, config).groups()
    bt_default_hot, bt_default_cold = default_strategy_counts(config, bt_groups, bt_bet_size)
    bt_hot = st.slider(
        "Números do grupo mais frequente",
        0,
        min(len(bt_groups.hot), bt_bet_size),
        min(bt_default_hot, min(len(bt_groups.hot), bt_bet_size)),
        key="bt_hot",
    )
    bt_max_cold = min(len(bt_groups.cold), bt_bet_size - bt_hot)
    bt_cold = st.slider(
        "Números do grupo menos frequente",
        0,
        bt_max_cold,
        min(bt_default_cold, bt_max_cold),
        key="bt_cold",
    )
    bt_neutral = bt_bet_size - bt_hot - bt_cold
    st.caption(f"Composição: {bt_hot} mais frequentes + {bt_cold} menos frequentes + {bt_neutral} neutros.")

    max_window = max(20, len(active_draws) - 1)
    training_window = st.slider(
        "Janela de treinamento",
        20,
        max_window,
        min(100, max_window),
        disabled=len(active_draws) <= 20,
    )
    simulations = st.slider("Simulações por concurso", 1, 50, 10)
    max_test_draws = st.slider("Máximo de concursos testados", 20, min(500, max(20, len(active_draws))), min(200, max(20, len(active_draws))))

    if st.button("Executar backtest", type="primary"):
        try:
            with st.spinner("Executando backtest..."):
                st.session_state.backtest_result = run_backtest(
                    active_draws,
                    config,
                    bt_bet_size,
                    bt_hot,
                    bt_cold,
                    training_window=training_window,
                    simulations_per_draw=simulations,
                    max_test_draws=max_test_draws,
                    seed=42,
                )
        except ValueError as exc:
            st.error(str(exc))

    result = st.session_state.get("backtest_result")
    if result is not None:
        r1, r2, r3 = st.columns(3)
        r1.metric("Média — perfil", f"{result.strategy_average:.3f} acertos")
        r2.metric("Média — aleatório", f"{result.random_average:.3f} acertos")
        r3.metric("Diferença observada", f"{result.difference:+.3f}")
        st.caption(
            f"{result.tested_draws} concursos × {result.simulations_per_draw} simulações. "
            "Diferenças pequenas podem decorrer apenas da variabilidade aleatória; este painel não demonstra capacidade preditiva."
        )
        dist = pd.DataFrame(
            {
                "Perfil": pd.Series(result.strategy_hits).value_counts(),
                "Aleatório": pd.Series(result.random_hits).value_counts(),
            }
        ).fillna(0).sort_index()
        st.bar_chart(dist)

with tab_generator:
    st.subheader("Gerador por perfil estatístico")
    st.write(
        "A composição é explícita e os grupos são disjuntos: uma dezena não pode ser simultaneamente classificada como quente e fria."
    )

    bet_size = st.slider(
        "Quantidade de números na aposta",
        config.bet_min,
        config.bet_max,
        config.bet_default,
        key="gen_bet_size",
    )
    scope_options = [("Amostra ativa", None)]
    for n in (20, 50, 100, 250):
        if n < len(active_draws):
            scope_options.append((f"Últimos {n}", n))
    scope_label = st.selectbox("Base para classificar os grupos", [x[0] for x in scope_options])
    group_last_n = dict(scope_options)[scope_label]
    groups = LotteryAnalytics(active_draws, config).groups(last_n=group_last_n)

    default_hot, default_cold = default_strategy_counts(config, groups, bet_size)
    hot_count = st.slider(
        "Mais frequentes",
        0,
        min(len(groups.hot), bet_size),
        min(default_hot, min(len(groups.hot), bet_size)),
        key="gen_hot",
    )
    max_cold = min(len(groups.cold), bet_size - hot_count)
    cold_count = st.slider(
        "Menos frequentes",
        0,
        max_cold,
        min(default_cold, max_cold),
        key="gen_cold",
    )
    neutral_count = bet_size - hot_count - cold_count
    st.info(
        f"Composição final: **{hot_count}** mais frequentes + **{cold_count}** menos frequentes + "
        f"**{neutral_count}** neutros = **{bet_size} números**."
    )

    g1, g2, g3 = st.columns(3)
    g1.metric("Grupo frequente", len(groups.hot))
    g2.metric("Grupo neutro", len(groups.neutral))
    g3.metric("Grupo menos frequente", len(groups.cold))

    with st.expander("Ver composição dos grupos"):
        st.write("**Mais frequentes:**", ", ".join(config.format_number(n) for n in groups.hot))
        st.write("**Neutros:**", ", ".join(config.format_number(n) for n in groups.neutral))
        st.write("**Menos frequentes:**", ", ".join(config.format_number(n) for n in groups.cold))

    use_gen_seed = st.checkbox("Usar seed na geração", value=False)
    gen_seed = st.number_input("Seed da geração", value=42, step=1, disabled=not use_gen_seed)
    games_qty = st.slider("Quantidade de combinações", 1, 20, 1)

    if st.button("Gerar combinações", type="primary", use_container_width=True):
        if "bet_history" not in st.session_state:
            st.session_state.bet_history = []
        base_seed = int(gen_seed) if use_gen_seed else None
        generated = []
        for i in range(games_qty):
            seed = (base_seed + i) if base_seed is not None else None
            generated.append(
                generate_profiled_bet(
                    config,
                    groups,
                    bet_size,
                    hot_count,
                    cold_count,
                    seed=seed,
                )
            )
        timestamp = datetime.now()
        for bet in generated:
            st.session_state.bet_history.insert(
                0,
                {
                    "data": timestamp,
                    "loteria": lottery_name,
                    "numeros": bet.numbers,
                    "quentes": bet.hot_count,
                    "neutros": bet.neutral_count,
                    "frios": bet.cold_count,
                    "seed": bet.seed,
                },
            )

    history = st.session_state.get("bet_history", [])
    if history:
        st.markdown("#### Combinações geradas")
        for item in history[:20]:
            st.markdown(balls_html(item["numeros"], config), unsafe_allow_html=True)
            st.caption(
                f"{item['data']:%d/%m/%Y %H:%M:%S} · "
                f"{item['quentes']} frequentes / {item['neutros']} neutros / {item['frios']} menos frequentes"
            )

        export_rows = [
            {
                "data": item["data"].isoformat(timespec="seconds"),
                "loteria": item["loteria"],
                "numeros": format_draw(item["numeros"], config),
                "mais_frequentes": item["quentes"],
                "neutros": item["neutros"],
                "menos_frequentes": item["frios"],
                "seed": item["seed"],
            }
            for item in history
        ]
        csv = pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Baixar histórico de combinações (CSV)",
            csv,
            file_name=f"combinacoes_{config.slug}.csv",
            mime="text/csv",
            use_container_width=True,
        )

with tab_data:
    st.subheader("Base carregada")
    d1, d2, d3 = st.columns(3)
    d1.metric("Válidos", report.valid_rows)
    d2.metric("Rejeitados", report.rejected_rows)
    d3.metric("Fonte", report.source_kind.capitalize())

    if report.rejected_rows:
        rejects = pd.DataFrame(
            {
                "Motivo": ["Quantidade incorreta", "Duplicados", "Fora da faixa"],
                "Linhas": [
                    report.rejected_wrong_count,
                    report.rejected_duplicates,
                    report.rejected_out_of_range,
                ],
            }
        )
        st.dataframe(rejects, hide_index=True, use_container_width=True)

    preview = pd.DataFrame(
        [list(draw) for draw in active_draws],
        columns=[f"D{i + 1}" for i in range(config.drawn_count)],
    )
    for column in preview.columns:
        preview[column] = preview[column].map(config.format_number)
    st.dataframe(preview.tail(200), hide_index=True, use_container_width=True, height=520)

    export = preview.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar amostra validada (CSV)",
        export,
        file_name=f"base_validada_{config.slug}.csv",
        mime="text/csv",
        use_container_width=True,
    )
