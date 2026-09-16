from __future__ import annotations

import html
import io
import itertools
import json
import random
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st



# ===== config.py =====
@dataclass(frozen=True, slots=True)
class LotteryConfig:
    name: str
    slug: str
    number_min: int
    number_max: int
    drawn_count: int
    bet_min: int
    bet_max: int
    bet_default: int
    display_width: int = 2

    @property
    def universe(self) -> tuple[int, ...]:
        return tuple(range(self.number_min, self.number_max + 1))

    @property
    def universe_size(self) -> int:
        return self.number_max - self.number_min + 1

    def format_number(self, number: int) -> str:
        return f"{number:0{self.display_width}d}"

    def validate_bet_size(self, size: int) -> None:
        if not self.bet_min <= size <= self.bet_max:
            raise ValueError(
                f"Aposta inválida para {self.name}: use de {self.bet_min} a {self.bet_max} números."
            )


LOTTERIES: dict[str, LotteryConfig] = {
    "Mega-Sena": LotteryConfig(
        name="Mega-Sena",
        slug="megasena",
        number_min=1,
        number_max=60,
        drawn_count=6,
        bet_min=6,
        bet_max=20,
        bet_default=6,
    ),
    "Lotofácil": LotteryConfig(
        name="Lotofácil",
        slug="lotofacil",
        number_min=1,
        number_max=25,
        drawn_count=15,
        bet_min=15,
        bet_max=20,
        bet_default=15,
    ),
    "Quina": LotteryConfig(
        name="Quina",
        slug="quina",
        number_min=1,
        number_max=80,
        drawn_count=5,
        bet_min=5,
        bet_max=15,
        bet_default=5,
    ),
    "Lotomania": LotteryConfig(
        name="Lotomania",
        slug="lotomania",
        number_min=0,
        number_max=99,
        drawn_count=20,
        bet_min=50,
        bet_max=50,
        bet_default=50,
    ),
}


# ===== models.py =====
Draw = tuple[int, ...]


@dataclass(slots=True)
class LoadReport:
    draws: list[Draw] = field(default_factory=list)
    total_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    rejected_wrong_count: int = 0
    rejected_duplicates: int = 0
    rejected_out_of_range: int = 0
    source_name: str = ""
    source_kind: str = "arquivo"
    loaded_at: datetime = field(default_factory=datetime.now)
    warnings: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return self.valid_rows / self.total_rows


@dataclass(frozen=True, slots=True)
class NumberGroups:
    hot: tuple[int, ...]
    neutral: tuple[int, ...]
    cold: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GeneratedBet:
    numbers: Draw
    hot_count: int
    neutral_count: int
    cold_count: int
    seed: int | None = None


@dataclass(slots=True)
class BacktestResult:
    strategy_hits: list[int]
    random_hits: list[int]
    tested_draws: int
    simulations_per_draw: int

    @staticmethod
    def average(values: Iterable[int]) -> float:
        values = list(values)
        return sum(values) / len(values) if values else 0.0

    @property
    def strategy_average(self) -> float:
        return self.average(self.strategy_hits)

    @property
    def random_average(self) -> float:
        return self.average(self.random_hits)

    @property
    def difference(self) -> float:
        return self.strategy_average - self.random_average


# ===== validators.py =====
class DrawValidationError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def validate_draw(numbers: list[int] | tuple[int, ...], config: LotteryConfig) -> Draw:
    values = tuple(int(n) for n in numbers)

    if len(values) != config.drawn_count:
        raise DrawValidationError(
            "wrong_count",
            f"Esperados {config.drawn_count} números, recebidos {len(values)}.",
        )

    if len(set(values)) != len(values):
        raise DrawValidationError("duplicates", "O sorteio contém números repetidos.")

    if any(n < config.number_min or n > config.number_max for n in values):
        raise DrawValidationError(
            "out_of_range",
            f"Há números fora do intervalo {config.number_min}–{config.number_max}.",
        )

    return tuple(sorted(values))


# ===== analytics.py =====
class LotteryAnalytics:
    def __init__(self, draws: list[Draw], config: LotteryConfig):
        self.draws = draws
        self.config = config

    def _counter(self, draws: list[Draw] | None = None) -> Counter[int]:
        selected = self.draws if draws is None else draws
        counter: Counter[int] = Counter(itertools.chain.from_iterable(selected))
        for number in self.config.universe:
            counter.setdefault(number, 0)
        return counter

    def frequency_table(self, last_n: int | None = None) -> pd.DataFrame:
        selected = self.draws[-last_n:] if last_n else self.draws
        count = self._counter(selected)
        draw_count = len(selected)
        expected = draw_count * self.config.drawn_count / self.config.universe_size if draw_count else 0.0
        rows: list[dict] = []

        for number in self.config.universe:
            freq = count[number]
            rows.append(
                {
                    "Número": number,
                    "Frequência": freq,
                    "Frequência %": (freq / draw_count * 100) if draw_count else 0.0,
                    "Esperado": expected,
                    "Desvio %": ((freq - expected) / expected * 100) if expected else 0.0,
                    "Atraso": self.delay(number, selected),
                    "Intervalo médio": self.average_interval(number, selected),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def delay(number: int, draws: list[Draw]) -> int:
        for delay, draw in enumerate(reversed(draws)):
            if number in draw:
                return delay
        return len(draws)

    @staticmethod
    def average_interval(number: int, draws: list[Draw]) -> float:
        indices = [i for i, draw in enumerate(draws) if number in draw]
        if len(indices) < 2:
            return float("nan")
        gaps = [b - a for a, b in zip(indices, indices[1:])]
        return sum(gaps) / len(gaps)

    def groups(
        self,
        last_n: int | None = None,
        hot_fraction: float = 0.25,
        cold_fraction: float = 0.25,
    ) -> NumberGroups:
        table = self.frequency_table(last_n)
        hot_size = max(1, round(self.config.universe_size * hot_fraction))
        cold_size = max(1, round(self.config.universe_size * cold_fraction))

        if hot_size + cold_size >= self.config.universe_size:
            cold_size = max(1, self.config.universe_size - hot_size - 1)

        hot_df = table.sort_values(["Frequência", "Número"], ascending=[False, True]).head(hot_size)
        hot = tuple(int(x) for x in hot_df["Número"].tolist())
        hot_set = set(hot)

        cold_candidates = table[~table["Número"].isin(hot_set)]
        cold_df = cold_candidates.sort_values(["Frequência", "Número"], ascending=[True, True]).head(cold_size)
        cold = tuple(int(x) for x in cold_df["Número"].tolist())
        cold_set = set(cold)

        neutral = tuple(n for n in self.config.universe if n not in hot_set and n not in cold_set)
        return NumberGroups(hot=hot, neutral=neutral, cold=cold)

    def recent_comparison(self, recent_n: int = 50) -> pd.DataFrame:
        full = self.frequency_table().set_index("Número")
        recent = self.frequency_table(min(recent_n, len(self.draws))).set_index("Número")
        out = pd.DataFrame(index=full.index)
        out["Histórico %"] = full["Frequência %"]
        out[f"Últimos {min(recent_n, len(self.draws))} %"] = recent["Frequência %"]
        out["Variação p.p."] = out.iloc[:, 1] - out.iloc[:, 0]
        return out.reset_index()

    def parity_distribution(self) -> pd.DataFrame:
        counter: Counter[str] = Counter()
        for draw in self.draws:
            evens = sum(n % 2 == 0 for n in draw)
            odds = len(draw) - evens
            counter[f"{evens} pares / {odds} ímpares"] += 1
        return pd.DataFrame(counter.most_common(), columns=["Composição", "Concursos"])

    def sums(self) -> pd.Series:
        return pd.Series([sum(draw) for draw in self.draws], name="Soma")

    def consecutive_distribution(self) -> pd.DataFrame:
        counter: Counter[int] = Counter()
        for draw in self.draws:
            ordered = sorted(draw)
            count = sum(b == a + 1 for a, b in zip(ordered, ordered[1:]))
            counter[count] += 1
        return pd.DataFrame(sorted(counter.items()), columns=["Pares consecutivos", "Concursos"])

    def repeats_from_previous(self) -> pd.DataFrame:
        counter: Counter[int] = Counter()
        for previous, current in zip(self.draws, self.draws[1:]):
            counter[len(set(previous) & set(current))] += 1
        return pd.DataFrame(sorted(counter.items()), columns=["Repetidos", "Ocorrências"])

    def top_combinations(self, size: int = 2, limit: int = 20) -> pd.DataFrame:
        if size not in (2, 3):
            raise ValueError("Somente pares ou trios são suportados.")
        counter: Counter[tuple[int, ...]] = Counter()
        for draw in self.draws:
            counter.update(itertools.combinations(sorted(draw), size))
        rows = [(" - ".join(self.config.format_number(n) for n in combo), freq) for combo, freq in counter.most_common(limit)]
        label = "Par" if size == 2 else "Trio"
        return pd.DataFrame(rows, columns=[label, "Frequência"])

    def summary(self) -> dict[str, float | int]:
        if not self.draws:
            return {"draws": 0, "average_sum": 0.0, "average_even": 0.0, "average_repeat": 0.0}
        sums = [sum(draw) for draw in self.draws]
        evens = [sum(n % 2 == 0 for n in draw) for draw in self.draws]
        repeats = [len(set(a) & set(b)) for a, b in zip(self.draws, self.draws[1:])]
        return {
            "draws": len(self.draws),
            "average_sum": sum(sums) / len(sums),
            "average_even": sum(evens) / len(evens),
            "average_repeat": sum(repeats) / len(repeats) if repeats else 0.0,
        }


# ===== generator.py =====
def _sample_exact(rng: random.Random, pool: tuple[int, ...], count: int, label: str) -> list[int]:
    if count < 0:
        raise ValueError(f"Quantidade de números {label} não pode ser negativa.")
    if count > len(pool):
        raise ValueError(
            f"Não há números {label} suficientes no grupo atual: solicitado {count}, disponível {len(pool)}."
        )
    return rng.sample(list(pool), count)


def generate_profiled_bet(
    config: LotteryConfig,
    groups: NumberGroups,
    bet_size: int,
    hot_count: int,
    cold_count: int,
    seed: int | None = None,
) -> GeneratedBet:
    config.validate_bet_size(bet_size)
    neutral_count = bet_size - hot_count - cold_count
    if neutral_count < 0:
        raise ValueError("Quentes + frios não pode ultrapassar o tamanho da aposta.")

    rng = random.Random(seed)
    selected: list[int] = []
    selected.extend(_sample_exact(rng, groups.hot, hot_count, "quentes"))
    selected.extend(_sample_exact(rng, groups.cold, cold_count, "frios"))
    selected.extend(_sample_exact(rng, groups.neutral, neutral_count, "neutros"))

    if len(selected) != len(set(selected)):
        raise RuntimeError("Erro interno: grupos estatísticos deveriam ser disjuntos.")

    return GeneratedBet(
        numbers=tuple(sorted(selected)),
        hot_count=hot_count,
        neutral_count=neutral_count,
        cold_count=cold_count,
        seed=seed,
    )


def generate_random_bet(config: LotteryConfig, bet_size: int, seed: int | None = None) -> Draw:
    config.validate_bet_size(bet_size)
    rng = random.Random(seed)
    return tuple(sorted(rng.sample(list(config.universe), bet_size)))


def generate_from_history(
    draws: list[Draw],
    config: LotteryConfig,
    bet_size: int,
    hot_count: int,
    cold_count: int,
    seed: int | None = None,
    last_n: int | None = None,
) -> GeneratedBet:
    if not draws:
        raise ValueError("Carregue um histórico antes de gerar uma combinação.")
    groups = LotteryAnalytics(draws, config).groups(last_n=last_n)
    return generate_profiled_bet(config, groups, bet_size, hot_count, cold_count, seed)


# ===== simulation.py =====
def simulate_draws(config: LotteryConfig, quantity: int, seed: int | None = None) -> LoadReport:
    rng = random.Random(seed)
    quantity = max(1, int(quantity))
    draws: list[Draw] = [
        tuple(sorted(rng.sample(list(config.universe), config.drawn_count)))
        for _ in range(quantity)
    ]
    return LoadReport(
        draws=draws,
        total_rows=quantity,
        valid_rows=quantity,
        source_name=f"Simulação (seed={seed})" if seed is not None else "Simulação aleatória",
        source_kind="simulacao",
    )


# ===== backtest.py =====
def run_backtest(
    draws: list[Draw],
    config: LotteryConfig,
    bet_size: int,
    hot_count: int,
    cold_count: int,
    training_window: int = 100,
    simulations_per_draw: int = 10,
    max_test_draws: int = 250,
    seed: int = 42,
) -> BacktestResult:
    config.validate_bet_size(bet_size)
    training_window = max(20, int(training_window))
    simulations_per_draw = max(1, int(simulations_per_draw))

    if len(draws) <= training_window:
        raise ValueError(
            f"Histórico insuficiente: são necessários mais de {training_window} concursos para o backtest."
        )

    rng = random.Random(seed)
    start = max(training_window, len(draws) - max_test_draws)
    strategy_hits: list[int] = []
    random_hits: list[int] = []

    for target_idx in range(start, len(draws)):
        train = draws[max(0, target_idx - training_window) : target_idx]
        target = set(draws[target_idx])
        groups = LotteryAnalytics(train, config).groups()

        for _ in range(simulations_per_draw):
            s1 = rng.randrange(0, 2**31 - 1)
            s2 = rng.randrange(0, 2**31 - 1)
            profiled = generate_profiled_bet(
                config,
                groups,
                bet_size,
                hot_count,
                cold_count,
                seed=s1,
            ).numbers
            random_bet = generate_random_bet(config, bet_size, seed=s2)
            strategy_hits.append(len(set(profiled) & target))
            random_hits.append(len(set(random_bet) & target))

    return BacktestResult(
        strategy_hits=strategy_hits,
        random_hits=random_hits,
        tested_draws=len(draws) - start,
        simulations_per_draw=simulations_per_draw,
    )


# ===== data_loader.py =====
CAIXA_API_BASE = "https://servicebus2.caixa.gov.br/portaldeloterias/api"


def _numeric_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(pd.to_numeric, errors="coerce")


def _score_candidate_columns(df: pd.DataFrame, config: LotteryConfig) -> list[int]:
    numeric = _numeric_matrix(df)
    scores: list[tuple[float, int]] = []

    for idx in range(numeric.shape[1]):
        series = numeric.iloc[:, idx].dropna()
        if series.empty:
            continue

        integer_like = series.map(lambda x: float(x).is_integer())
        in_range = series.between(config.number_min, config.number_max)
        valid_ratio = float((integer_like & in_range).mean())
        coverage = min(1.0, len(series) / max(1, len(numeric)))
        score = valid_ratio * 0.85 + coverage * 0.15
        if valid_ratio >= 0.80:
            scores.append((score, idx))

    scores.sort(key=lambda item: (-item[0], item[1]))
    return [idx for _, idx in scores[: config.drawn_count]]


def dataframe_to_report(
    df: pd.DataFrame,
    config: LotteryConfig,
    source_name: str,
    source_kind: str = "arquivo",
) -> LoadReport:
    report = LoadReport(
        total_rows=len(df),
        source_name=source_name,
        source_kind=source_kind,
    )
    if df.empty:
        report.warnings.append("O arquivo não contém linhas de dados.")
        return report

    numeric = _numeric_matrix(df)
    candidate_cols = _score_candidate_columns(df, config)
    use_candidates = len(candidate_cols) == config.drawn_count

    if use_candidates:
        report.warnings.append(
            f"Foram identificadas automaticamente {config.drawn_count} colunas de dezenas."
        )

    for _, row in numeric.iterrows():
        if use_candidates:
            raw_values = [row.iloc[i] for i in candidate_cols]
        else:
            raw_values = list(row.values)

        values: list[int] = []
        had_out_of_range = False
        for value in raw_values:
            if pd.isna(value):
                continue
            try:
                as_float = float(value)
            except (TypeError, ValueError):
                continue
            if not as_float.is_integer():
                continue
            as_int = int(as_float)
            if config.number_min <= as_int <= config.number_max:
                values.append(as_int)
            else:
                had_out_of_range = True

        if not use_candidates and len(values) > config.drawn_count:
            # Em layout desconhecido, não truncamos silenciosamente: a linha é rejeitada.
            report.rejected_rows += 1
            report.rejected_wrong_count += 1
            continue

        try:
            draw = validate_draw(values, config)
        except DrawValidationError as exc:
            report.rejected_rows += 1
            if had_out_of_range or exc.reason == "out_of_range":
                report.rejected_out_of_range += 1
            elif exc.reason == "duplicates":
                report.rejected_duplicates += 1
            elif exc.reason == "wrong_count":
                report.rejected_wrong_count += 1
            continue

        report.draws.append(draw)
        report.valid_rows += 1

    if report.rejected_rows:
        report.warnings.append(
            f"{report.rejected_rows} linha(s) foram ignoradas por não passarem pela validação."
        )
    return report


def load_tabular_bytes(
    data: bytes,
    filename: str,
    config: LotteryConfig,
) -> LoadReport:
    suffix = Path(filename).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(io.BytesIO(data), header=None)
        return dataframe_to_report(df, config, filename)

    if suffix not in {".csv", ".txt"}:
        raise ValueError("Formato não suportado. Use CSV, TXT, XLSX ou XLS.")

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
            df = pd.read_csv(
                io.StringIO(text),
                sep=None,
                engine="python",
                header=None,
                dtype=str,
            )
            return dataframe_to_report(df, config, filename)
        except Exception as exc:  # tentativa de fallback de encoding/separador
            last_error = exc

    raise ValueError(f"Não foi possível interpretar o arquivo: {last_error}")


def _fetch_json(url: str, timeout: int = 12) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 LotteryStatisticsDashboard/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"Falha ao consultar a CAIXA: {exc}") from exc


def fetch_latest_contest_number(config: LotteryConfig) -> int:
    payload = _fetch_json(f"{CAIXA_API_BASE}/{config.slug}")
    number = payload.get("numero")
    if number is None:
        raise ValueError("A resposta da CAIXA não informou o número do concurso.")
    return int(number)


def _fetch_contest(config: LotteryConfig, contest: int) -> tuple[int, tuple[int, ...]]:
    payload = _fetch_json(f"{CAIXA_API_BASE}/{config.slug}/{contest}")
    raw = payload.get("listaDezenas") or payload.get("dezenasSorteadasOrdemSorteio")
    if not raw:
        raise ValueError(f"Concurso {contest} sem dezenas na resposta.")
    numbers = [int(str(value)) for value in raw]
    return contest, validate_draw(numbers, config)


def fetch_recent_official_draws(
    config: LotteryConfig,
    quantity: int,
    max_workers: int = 10,
) -> LoadReport:
    quantity = max(1, int(quantity))
    latest = fetch_latest_contest_number(config)
    start = max(1, latest - quantity + 1)
    contests = list(range(start, latest + 1))
    report = LoadReport(
        total_rows=len(contests),
        source_name="CAIXA — endpoint público do Portal Loterias",
        source_kind="oficial",
    )

    found: dict[int, tuple[int, ...]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_contest, config, n): n for n in contests}
        for future in as_completed(futures):
            contest = futures[future]
            try:
                number, draw = future.result()
            except Exception as exc:
                report.rejected_rows += 1
                report.warnings.append(f"Concurso {contest}: {exc}")
                continue
            found[number] = draw

    for contest in contests:
        if contest in found:
            report.draws.append(found[contest])
            report.valid_rows += 1

    if report.rejected_rows:
        report.warnings.append(
            "Alguns concursos não puderam ser carregados. O endpoint do Portal Loterias pode oscilar ou mudar sem aviso."
        )
    return report


# ===== Streamlit UI =====
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
