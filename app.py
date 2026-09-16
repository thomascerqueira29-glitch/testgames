from __future__ import annotations

import html
import hashlib
import logging
import math
import os
import re
import sqlite3
import io
import itertools
import json
import random
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import networkx as nx
from scipy.stats import chisquare

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    Image = None
    pytesseract = None
    OCR_AVAILABLE = False



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
    base_price: float = 0.0
    info_url: str = ""

    @property
    def universe(self) -> tuple[int, ...]:
        return tuple(range(self.number_min, self.number_max + 1))

    @property
    def universe_size(self) -> int:
        return self.number_max - self.number_min + 1

    def format_number(self, number: int) -> str:
        return f"{number:0{self.display_width}d}"

    def bet_price(self, size: int) -> float:
        self.validate_bet_size(size)
        if self.name == "Lotomania":
            return self.base_price
        return self.base_price * math.comb(size, self.drawn_count)

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
        base_price=6.0,
        info_url="https://loterias.caixa.gov.br/Paginas/mega-sena.aspx",
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
        base_price=3.5,
        info_url="https://loterias.caixa.gov.br/paginas/lotofacil.aspx",
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
        base_price=3.0,
        info_url="https://loterias.caixa.gov.br/Paginas/quina.aspx",
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
        base_price=3.0,
        info_url="https://loterias.caixa.gov.br/paginas/lotomania.aspx",
    ),
}


# Modalidades exibidas no painel geral de resultados oficiais.
OFFICIAL_RESULT_GAMES: dict[str, dict[str, str]] = {
    "Mega-Sena": {"slug": "megasena", "accent": "#1f9d55"},
    "Lotofácil": {"slug": "lotofacil", "accent": "#7b2cbf"},
    "Quina": {"slug": "quina", "accent": "#2563eb"},
    "Lotomania": {"slug": "lotomania", "accent": "#f59e0b"},
    "Timemania": {"slug": "timemania", "accent": "#0f9f6e"},
    "Dia de Sorte": {"slug": "diadesorte", "accent": "#c58b12"},
    "Dupla Sena": {"slug": "duplasena", "accent": "#b91c1c"},
    "Super Sete": {"slug": "supersete", "accent": "#d97706"},
    "+Milionária": {"slug": "maismilionaria", "accent": "#0f766e"},
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
    metadata: dict[str, object] = field(default_factory=dict)

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


@dataclass(frozen=True, slots=True)
class PrizeTier:
    description: str
    winners: int
    prize_value: float
    tier: int | None = None


@dataclass(frozen=True, slots=True)
class OfficialContest:
    lottery_name: str
    contest: int
    draw: Draw
    draw_order: Draw
    draw_date: str
    next_draw_date: str
    accumulated: bool
    estimated_next_prize: float
    accumulated_next_prize: float
    revenue: float
    venue: str
    city_uf: str
    previous_contest: int | None
    next_contest: int | None
    prize_tiers: tuple[PrizeTier, ...]
    source_url: str

    @property
    def main_tier_winners(self) -> int:
        return self.prize_tiers[0].winners if self.prize_tiers else 0

    @property
    def main_tier_prize(self) -> float:
        return self.prize_tiers[0].prize_value if self.prize_tiers else 0.0


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


def _caixa_api_url(config: LotteryConfig, contest: int | None = None) -> str:
    base = f"{CAIXA_API_BASE}/{config.slug}"
    return f"{base}/{int(contest)}" if contest is not None else base


def _fetch_json(url: str, timeout: int = 12, attempts: int = 3) -> dict:
    """Consulta JSON da CAIXA com retries curtos e mensagens de erro legíveis."""
    last_error: Exception | None = None
    attempts = max(1, int(attempts))

    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (compatible; LoteriasLab/3.3; +https://caixa.gov.br)",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                payload = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(payload, dict):
                    raise ValueError("A CAIXA retornou um JSON em formato inesperado.")
                return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 4xx (exceto rate limit) tende a ser erro definitivo de concurso/URL.
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            last_error = exc

        if attempt < attempts - 1:
            time.sleep(0.6 * (2 ** attempt))

    raise ConnectionError(f"Falha ao consultar a API da CAIXA em {url}: {last_error}") from last_error


def fetch_generic_official_result(slug: str, timeout: int = 8, attempts: int = 2) -> dict:
    """Busca o resultado mais recente de qualquer modalidade do painel geral."""
    return _fetch_json(f"{CAIXA_API_BASE}/{slug}", timeout=timeout, attempts=attempts)


def _safe_money(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _display_number(value: object, width: int = 2) -> str:
    text = str(value).strip()
    try:
        return f"{int(text):0{width}d}"
    except (TypeError, ValueError):
        return text


def parse_official_contest(payload: dict, config: LotteryConfig, source_url: str = "") -> OfficialContest:
    number = payload.get("numero")
    if number is None:
        raise ValueError("A resposta da CAIXA não informou o número do concurso.")

    raw_sorted = payload.get("listaDezenas") or payload.get("dezenasSorteadasOrdemSorteio")
    if not raw_sorted:
        raise ValueError(f"Concurso {number} sem dezenas na resposta da CAIXA.")

    raw_order = payload.get("dezenasSorteadasOrdemSorteio") or raw_sorted
    draw = validate_draw([int(str(value)) for value in raw_sorted], config)
    order_values = tuple(int(str(value)) for value in raw_order)
    # A ordem do sorteio não deve ser ordenada; apenas validamos conjunto/quantidade.
    validate_draw(order_values, config)

    tiers: list[PrizeTier] = []
    for item in payload.get("listaRateioPremio") or []:
        tiers.append(
            PrizeTier(
                description=str(item.get("descricaoFaixa") or f"Faixa {item.get('faixa', '')}").strip(),
                winners=int(item.get("numeroDeGanhadores") or 0),
                prize_value=float(item.get("valorPremio") or 0.0),
                tier=int(item["faixa"]) if item.get("faixa") is not None else None,
            )
        )

    def optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return OfficialContest(
        lottery_name=config.name,
        contest=int(number),
        draw=draw,
        draw_order=order_values,
        draw_date=str(payload.get("dataApuracao") or ""),
        next_draw_date=str(payload.get("dataProximoConcurso") or ""),
        accumulated=bool(payload.get("acumulado", False)),
        estimated_next_prize=float(payload.get("valorEstimadoProximoConcurso") or 0.0),
        accumulated_next_prize=float(payload.get("valorAcumuladoProximoConcurso") or 0.0),
        revenue=float(payload.get("valorArrecadado") or 0.0),
        venue=str(payload.get("localSorteio") or ""),
        city_uf=str(payload.get("nomeMunicipioUFSorteio") or ""),
        previous_contest=optional_int(payload.get("numeroConcursoAnterior")),
        next_contest=optional_int(payload.get("numeroConcursoProximo")),
        prize_tiers=tuple(tiers),
        source_url=source_url or _caixa_api_url(config, int(number)),
    )


def fetch_official_contest(
    config: LotteryConfig, contest: int | None = None, timeout: int = 8, attempts: int = 2
) -> OfficialContest:
    """Busca o concurso mais recente ou um concurso específico no endpoint do Portal Loterias."""
    url = _caixa_api_url(config, contest)
    return parse_official_contest(_fetch_json(url, timeout=timeout, attempts=attempts), config, url)


def fetch_latest_contest_number(config: LotteryConfig) -> int:
    return fetch_official_contest(config).contest


def _fetch_contest(config: LotteryConfig, contest: int) -> tuple[int, tuple[int, ...]]:
    official = fetch_official_contest(config, contest)
    return official.contest, official.draw


def fetch_recent_official_draws(
    config: LotteryConfig,
    quantity: int,
    max_workers: int = 6,
) -> LoadReport:
    """Carrega concursos recentes da CAIXA preservando a ordem cronológica."""
    quantity = max(1, int(quantity))
    latest_data = fetch_official_contest(config)
    latest = latest_data.contest
    start = max(1, latest - quantity + 1)
    contests = list(range(start, latest + 1))
    report = LoadReport(
        total_rows=len(contests),
        source_name="CAIXA — API do Portal Loterias",
        source_kind="oficial",
        metadata={
            "latest_contest": latest_data.contest,
            "latest_date": latest_data.draw_date,
            "next_date": latest_data.next_draw_date,
            "estimated_next_prize": latest_data.estimated_next_prize,
            "accumulated": latest_data.accumulated,
            "source_url": latest_data.source_url,
        },
    )

    # Já temos o concurso mais recente; evita uma chamada duplicada.
    found: dict[int, tuple[int, ...]] = {latest_data.contest: latest_data.draw}
    pending = [n for n in contests if n != latest_data.contest]

    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 8))) as executor:
        futures = {executor.submit(_fetch_contest, config, n): n for n in pending}
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
            "Alguns concursos não puderam ser carregados. A API do Portal Loterias pode oscilar ou limitar requisições temporariamente."
        )
    return report


# ===== V3 advanced services =====
APP_VERSION = "3.3.0"
DB_PATH = Path(os.getenv("LOTTERY_LAB_DB", ".lottery_lab.db"))
LOG_PATH = Path(os.getenv("LOTTERY_LAB_LOG", "lottery_lab.log"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("lottery_lab")


class AdvancedAnalytics(LotteryAnalytics):
    def band_distribution(self, bands: int = 6) -> pd.DataFrame:
        bands = max(2, min(int(bands), self.config.universe_size))
        edges = np.linspace(self.config.number_min, self.config.number_max + 1, bands + 1, dtype=int)
        rows = []
        for i in range(bands):
            lo = int(edges[i])
            hi = int(edges[i + 1] - 1)
            if i == bands - 1:
                hi = self.config.number_max
            count = sum(sum(lo <= n <= hi for n in draw) for draw in self.draws)
            rows.append({"Faixa": f"{self.config.format_number(lo)}–{self.config.format_number(hi)}", "Ocorrências": count})
        return pd.DataFrame(rows)

    def delay_table(self) -> pd.DataFrame:
        rows = []
        for n in self.config.universe:
            indices = [i for i, draw in enumerate(self.draws) if n in draw]
            current = self.delay(n, self.draws)
            if len(indices) >= 2:
                gaps = [b - a for a, b in zip(indices, indices[1:])]
                avg = float(np.mean(gaps))
                max_gap = int(max(gaps))
            else:
                avg = float("nan")
                max_gap = len(self.draws)
            last_idx = indices[-1] if indices else None
            rows.append({
                "Número": n,
                "Atraso atual": current,
                "Intervalo médio": avg,
                "Maior intervalo": max_gap,
                "Última posição": last_idx,
            })
        return pd.DataFrame(rows)

    def trend_table(self, recent_n: int = 50) -> pd.DataFrame:
        comp = self.recent_comparison(recent_n)
        col = [c for c in comp.columns if c.startswith("Últimos ")][0]
        delta = comp["Variação p.p."]
        threshold = max(0.5, float(delta.abs().median()) * 0.6)
        comp["Tendência"] = np.where(delta > threshold, "↑ acima da frequência histórica", np.where(delta < -threshold, "↓ abaixo da frequência histórica", "→ próxima da frequência histórica"))
        comp = comp.rename(columns={col: "Recente %"})
        return comp

    def historical_feature_profile(self) -> dict[str, Counter]:
        parity = Counter()
        consecutive = Counter()
        repeats = Counter()
        occupied_bands = Counter()
        for i, draw in enumerate(self.draws):
            evens = sum(n % 2 == 0 for n in draw)
            parity[evens] += 1
            consecutive[consecutive_pairs(draw)] += 1
            if i > 0:
                repeats[len(set(draw) & set(self.draws[i - 1]))] += 1
            occupied_bands[occupied_band_count(draw, self.config)] += 1
        return {"parity": parity, "consecutive": consecutive, "repeats": repeats, "bands": occupied_bands}

    def cooccurrence_edges(self, limit: int = 35, min_frequency: int = 2) -> pd.DataFrame:
        counter: Counter[tuple[int, int]] = Counter()
        for draw in self.draws:
            counter.update(itertools.combinations(sorted(draw), 2))
        rows = []
        for (a, b), freq in counter.most_common(limit):
            if freq < min_frequency:
                continue
            rows.append({"A": a, "B": b, "Frequência": freq})
        return pd.DataFrame(rows)

    def chi_square_uniformity(self) -> tuple[float, float]:
        counter = self._counter()
        observed = np.array([counter[n] for n in self.config.universe], dtype=float)
        if observed.sum() == 0:
            return 0.0, 1.0
        expected = np.full_like(observed, observed.mean())
        stat, p = chisquare(observed, expected)
        return float(stat), float(p)

    def rarity_score(self, draw: Draw, previous: Draw | None = None) -> tuple[float, list[str]]:
        if not self.draws:
            return 0.0, []
        profile = self.historical_feature_profile()
        total = max(1, len(self.draws))
        features: list[tuple[str, float, str]] = []

        evens = sum(n % 2 == 0 for n in draw)
        p_parity = profile["parity"].get(evens, 0) / total
        features.append(("paridade", p_parity, f"{evens} pares e {len(draw)-evens} ímpares"))

        cons = consecutive_pairs(draw)
        p_cons = profile["consecutive"].get(cons, 0) / total
        features.append(("consecutivos", p_cons, f"{cons} par(es) consecutivo(s)"))

        bands = occupied_band_count(draw, self.config)
        p_bands = profile["bands"].get(bands, 0) / total
        features.append(("faixas", p_bands, f"{bands} faixas ocupadas"))

        sums = self.sums().astype(float)
        if len(sums) > 2 and sums.std(ddof=0) > 0:
            z = abs((sum(draw) - sums.mean()) / sums.std(ddof=0))
            p_sum = max(0.01, math.erfc(z / math.sqrt(2)))
        else:
            p_sum = 1.0
        features.append(("soma", p_sum, f"soma {sum(draw)}"))

        if previous is not None and profile["repeats"]:
            rep = len(set(draw) & set(previous))
            p_rep = profile["repeats"].get(rep, 0) / max(1, sum(profile["repeats"].values()))
            features.append(("repetição", p_rep, f"{rep} repetido(s) do concurso anterior"))

        surprisals = [-math.log(max(p, 0.005)) for _, p, _ in features]
        raw = float(np.mean(surprisals)) if surprisals else 0.0
        score = min(100.0, 100.0 * raw / 5.3)
        details = [desc for _, p, desc in sorted(features, key=lambda x: x[1])[:3] if p < 0.25]
        return score, details

    def balance_score(self, bet: Draw, previous: Draw | None = None) -> tuple[float, dict[str, float]]:
        if not self.draws:
            return 50.0, {}
        profile = self.historical_feature_profile()
        total = max(1, len(self.draws))

        def normalized(counter: Counter, value: int) -> float:
            if not counter:
                return 0.5
            max_count = max(counter.values())
            return counter.get(value, 0) / max_count if max_count else 0.5

        evens = sum(n % 2 == 0 for n in bet)
        parity_s = normalized(profile["parity"], evens)
        cons_s = normalized(profile["consecutive"], consecutive_pairs(bet))
        band_s = normalized(profile["bands"], occupied_band_count(bet, self.config))

        sums = self.sums().astype(float)
        if len(sums) > 2 and sums.std(ddof=0) > 0:
            z = abs((sum(bet) - sums.mean()) / sums.std(ddof=0))
            sum_s = math.exp(-0.5 * z * z)
        else:
            sum_s = 0.5

        repeat_s = 0.5
        if previous is not None and profile["repeats"]:
            repeat_s = normalized(profile["repeats"], len(set(bet) & set(previous)))

        scores = {
            "Paridade": parity_s,
            "Soma": sum_s,
            "Consecutivos": cons_s,
            "Distribuição por faixas": band_s,
            "Repetição": repeat_s,
        }
        return 100.0 * float(np.mean(list(scores.values()))), scores


def consecutive_pairs(numbers: Iterable[int]) -> int:
    ordered = sorted(set(int(n) for n in numbers))
    return sum(b == a + 1 for a, b in zip(ordered, ordered[1:]))


def occupied_band_count(numbers: Iterable[int], config: LotteryConfig, bands: int = 6) -> int:
    edges = np.linspace(config.number_min, config.number_max + 1, bands + 1)
    occupied = set()
    for n in numbers:
        idx = min(bands - 1, max(0, int(np.searchsorted(edges, n, side="right") - 1)))
        occupied.add(idx)
    return len(occupied)


def parse_numbers(text: str, config: LotteryConfig, unique: bool = True) -> list[int]:
    tokens = re.findall(r"(?<!\d)\d{1,3}(?!\d)", text or "")
    values = [int(t) for t in tokens]
    values = [v for v in values if config.number_min <= v <= config.number_max]
    if unique:
        seen = set()
        values = [v for v in values if not (v in seen or seen.add(v))]
    return values


def split_game_lines(text: str, config: LotteryConfig, bet_size: int | None = None) -> list[Draw]:
    games: list[Draw] = []
    for line in (text or "").splitlines():
        nums = parse_numbers(line, config, unique=True)
        if not nums:
            continue
        if bet_size is None:
            if config.bet_min <= len(nums) <= config.bet_max:
                games.append(tuple(sorted(nums)))
        elif len(nums) == bet_size:
            games.append(tuple(sorted(nums)))
    return games


def dataset_hash(draws: list[Draw]) -> str:
    payload = "|".join(",".join(str(n) for n in d) for d in draws).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def bet_constraints_ok(
    bet: Draw,
    config: LotteryConfig,
    even_min: int,
    even_max: int,
    sum_min: int,
    sum_max: int,
    max_consecutive: int,
    previous: Draw | None,
    max_repeats: int,
    min_bands: int,
) -> bool:
    evens = sum(n % 2 == 0 for n in bet)
    if not even_min <= evens <= even_max:
        return False
    total = sum(bet)
    if not sum_min <= total <= sum_max:
        return False
    if consecutive_pairs(bet) > max_consecutive:
        return False
    if previous is not None and len(set(bet) & set(previous)) > max_repeats:
        return False
    if occupied_band_count(bet, config) < min_bands:
        return False
    return True


def generate_custom_bets(
    config: LotteryConfig,
    count: int,
    bet_size: int,
    mode: str,
    analytics: AdvancedAnalytics,
    hot_count: int = 0,
    cold_count: int = 0,
    required: Iterable[int] = (),
    excluded: Iterable[int] = (),
    diversity: str = "Média",
    even_min: int = 0,
    even_max: int | None = None,
    sum_min: int | None = None,
    sum_max: int | None = None,
    max_consecutive: int | None = None,
    max_repeats: int | None = None,
    min_bands: int = 1,
    seed: int | None = None,
    max_attempts: int = 25000,
) -> list[Draw]:
    config.validate_bet_size(bet_size)
    required_set = set(int(n) for n in required)
    excluded_set = set(int(n) for n in excluded)
    if required_set & excluded_set:
        raise ValueError("Um número não pode ser obrigatório e excluído ao mesmo tempo.")
    if len(required_set) > bet_size:
        raise ValueError("Há mais números obrigatórios do que posições na aposta.")
    if any(n not in config.universe for n in required_set | excluded_set):
        raise ValueError("Há números obrigatórios/excluídos fora do universo da modalidade.")

    even_max = bet_size if even_max is None else even_max
    min_possible_sum = sum(sorted(config.universe)[:bet_size])
    max_possible_sum = sum(sorted(config.universe, reverse=True)[:bet_size])
    sum_min = min_possible_sum if sum_min is None else sum_min
    sum_max = max_possible_sum if sum_max is None else sum_max
    max_consecutive = bet_size if max_consecutive is None else max_consecutive
    max_repeats = bet_size if max_repeats is None else max_repeats
    previous = analytics.draws[-1] if analytics.draws else None
    rng = random.Random(seed)
    groups = analytics.groups()

    overlap_limits = {
        "Baixa": bet_size,
        "Média": max(1, math.ceil(bet_size * 0.70)),
        "Alta": max(1, math.ceil(bet_size * 0.50)),
    }
    max_overlap = overlap_limits.get(diversity, bet_size)
    results: list[Draw] = []
    attempts = 0

    while len(results) < count and attempts < max_attempts:
        attempts += 1
        local_seed = rng.randrange(0, 2**31 - 1)
        if mode == "Aleatório":
            pool = [n for n in config.universe if n not in excluded_set and n not in required_set]
            need = bet_size - len(required_set)
            if len(pool) < need:
                raise ValueError("Números excluídos demais para montar a aposta.")
            candidate = tuple(sorted(required_set | set(random.Random(local_seed).sample(pool, need))))
        elif mode == "Perfil histórico":
            # Generate exact profile, then enforce required/excluded through retries.
            candidate = generate_profiled_bet(config, groups, bet_size, hot_count, cold_count, seed=local_seed).numbers
            if not required_set.issubset(candidate) or set(candidate) & excluded_set:
                continue
        else:
            # Personalizado: random pool plus all constraints.
            pool = [n for n in config.universe if n not in excluded_set and n not in required_set]
            need = bet_size - len(required_set)
            if len(pool) < need:
                raise ValueError("Números excluídos demais para montar a aposta.")
            candidate = tuple(sorted(required_set | set(random.Random(local_seed).sample(pool, need))))

        if not bet_constraints_ok(
            candidate, config, even_min, even_max, sum_min, sum_max,
            max_consecutive, previous, max_repeats, min_bands,
        ):
            continue
        if candidate in results:
            continue
        if diversity != "Baixa" and any(len(set(candidate) & set(old)) > max_overlap for old in results):
            continue
        results.append(candidate)

    return results


def coverage_metrics(games: list[Draw], base_numbers: Iterable[int] | None = None) -> dict[str, float | int]:
    if not games:
        return {"pair_coverage": 0.0, "triple_coverage": 0.0, "pairs": 0, "triples": 0}
    base = set(base_numbers or itertools.chain.from_iterable(games))
    pair_total = math.comb(len(base), 2) if len(base) >= 2 else 0
    triple_total = math.comb(len(base), 3) if len(base) >= 3 else 0
    pairs = set(itertools.chain.from_iterable(itertools.combinations(sorted(g), 2) for g in games))
    triples = set(itertools.chain.from_iterable(itertools.combinations(sorted(g), 3) for g in games))
    return {
        "pair_coverage": len(pairs) / pair_total if pair_total else 0.0,
        "triple_coverage": len(triples) / triple_total if triple_total else 0.0,
        "pairs": len(pairs),
        "triples": len(triples),
    }


def build_closure(
    base_numbers: list[int],
    config: LotteryConfig,
    bet_size: int,
    max_games: int,
    optimize_for: str = "Pares",
    seed: int = 42,
) -> list[Draw]:
    base = sorted(set(base_numbers))
    if len(base) < bet_size:
        raise ValueError("A base precisa ter pelo menos a quantidade de números da aposta.")
    if len(base) == bet_size:
        return [tuple(base)]

    total = math.comb(len(base), bet_size)
    rng = random.Random(seed)
    candidate_limit = min(12000, total)
    if total <= candidate_limit:
        candidates = [tuple(c) for c in itertools.combinations(base, bet_size)]
    else:
        candidates_set: set[Draw] = set()
        while len(candidates_set) < candidate_limit:
            candidates_set.add(tuple(sorted(rng.sample(base, bet_size))))
        candidates = list(candidates_set)

    feature_size = 2 if optimize_for == "Pares" else 3
    covered: set[tuple[int, ...]] = set()
    chosen: list[Draw] = []
    remaining = candidates.copy()

    first = rng.choice(remaining)
    chosen.append(first)
    covered.update(itertools.combinations(first, feature_size))
    remaining.remove(first)

    while remaining and len(chosen) < max_games:
        sample = remaining if len(remaining) <= 2000 else rng.sample(remaining, 2000)
        best = max(
            sample,
            key=lambda c: len(set(itertools.combinations(c, feature_size)) - covered),
        )
        new_features = set(itertools.combinations(best, feature_size)) - covered
        if not new_features and len(chosen) > 1:
            break
        chosen.append(best)
        covered.update(itertools.combinations(best, feature_size))
        remaining.remove(best)
    return chosen


def monte_carlo_hits(config: LotteryConfig, bet_size: int, simulations: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hits = rng.hypergeometric(
        ngood=bet_size,
        nbad=config.universe_size - bet_size,
        nsample=config.drawn_count,
        size=simulations,
    )
    values, counts = np.unique(hits, return_counts=True)
    return pd.DataFrame({"Acertos": values.astype(int), "Simulações": counts.astype(int), "Probabilidade simulada %": counts / simulations * 100})


def interpret_draw(draw: Draw, analytics: AdvancedAnalytics, config: LotteryConfig, previous: Draw | None = None) -> str:
    evens = sum(n % 2 == 0 for n in draw)
    cons = consecutive_pairs(draw)
    bands = occupied_band_count(draw, config)
    repeats = len(set(draw) & set(previous)) if previous else 0
    rarity, details = analytics.rarity_score(draw, previous)
    sums = analytics.sums()
    sum_mean = float(sums.mean()) if not sums.empty else 0.0
    relation = "próxima" if abs(sum(draw) - sum_mean) <= max(1, float(sums.std(ddof=0)) if len(sums) > 1 else 1) else ("acima" if sum(draw) > sum_mean else "abaixo")
    extras = f" Os aspectos menos comuns na amostra foram: {', '.join(details)}." if details else ""
    return (
        f"A combinação tem {evens} número(s) par(es) e {len(draw)-evens} ímpar(es), soma {sum(draw)} "
        f"({relation} da média histórica de {sum_mean:.1f}), {cons} par(es) consecutivo(s) e ocupa {bands} faixas. "
        f"Ela repete {repeats} número(s) do último concurso. O índice de raridade histórica é {rarity:.0f}/100; "
        "esse índice mede apenas o quão incomum o perfil foi dentro da amostra e não representa chance futura."
        + extras
    )


class LocalRepository:
    """SQLite local. On Streamlit Cloud the filesystem can be ephemeral; export is provided as backup."""
    def __init__(self, path: Path):
        self.path = path
        self.enabled = True
        try:
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()
        except Exception as exc:
            logger.warning("SQLite unavailable: %s", exc)
            self.enabled = False
            self.conn = None

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL,
                created_at TEXT NOT NULL,
                lottery TEXT NOT NULL,
                numbers TEXT NOT NULL,
                contest TEXT,
                amount REAL DEFAULT 0,
                source TEXT DEFAULT 'manual',
                notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL,
                created_at TEXT NOT NULL,
                lottery TEXT NOT NULL,
                name TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budget_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                lottery TEXT,
                notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                profile TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(profile, key)
            );
            """
        )
        self.conn.commit()

    def add_bet(self, profile: str, lottery: str, numbers: Draw, contest: str = "", amount: float = 0.0, source: str = "manual", notes: str = "") -> None:
        if not self.enabled:
            return
        self.conn.execute(
            "INSERT INTO bets(profile,created_at,lottery,numbers,contest,amount,source,notes) VALUES(?,?,?,?,?,?,?,?)",
            (profile, datetime.now().isoformat(timespec="seconds"), lottery, ",".join(map(str, numbers)), contest, float(amount), source, notes),
        )
        self.conn.commit()

    def list_bets(self, profile: str, lottery: str | None = None) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        query = "SELECT id,created_at,lottery,numbers,contest,amount,source,notes FROM bets WHERE profile=?"
        params: list = [profile]
        if lottery:
            query += " AND lottery=?"
            params.append(lottery)
        query += " ORDER BY id DESC"
        return pd.read_sql_query(query, self.conn, params=params)

    def delete_bet(self, bet_id: int) -> None:
        if self.enabled:
            self.conn.execute("DELETE FROM bets WHERE id=?", (int(bet_id),))
            self.conn.commit()

    def save_strategy(self, profile: str, lottery: str, name: str, payload: dict) -> None:
        if not self.enabled:
            return
        self.conn.execute(
            "INSERT INTO strategies(profile,created_at,lottery,name,payload) VALUES(?,?,?,?,?)",
            (profile, datetime.now().isoformat(timespec="seconds"), lottery, name, json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def list_strategies(self, profile: str, lottery: str | None = None) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        query = "SELECT id,created_at,lottery,name,payload FROM strategies WHERE profile=?"
        params: list = [profile]
        if lottery:
            query += " AND lottery=?"
            params.append(lottery)
        query += " ORDER BY id DESC"
        return pd.read_sql_query(query, self.conn, params=params)

    def add_budget(self, profile: str, kind: str, amount: float, lottery: str = "", notes: str = "") -> None:
        if not self.enabled:
            return
        self.conn.execute(
            "INSERT INTO budget_entries(profile,created_at,kind,amount,lottery,notes) VALUES(?,?,?,?,?,?)",
            (profile, datetime.now().isoformat(timespec="seconds"), kind, float(amount), lottery, notes),
        )
        self.conn.commit()

    def list_budget(self, profile: str) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        return pd.read_sql_query(
            "SELECT id,created_at,kind,amount,lottery,notes FROM budget_entries WHERE profile=? ORDER BY id DESC",
            self.conn,
            params=[profile],
        )

    def set_setting(self, profile: str, key: str, value: str) -> None:
        if self.enabled:
            self.conn.execute(
                "INSERT INTO settings(profile,key,value) VALUES(?,?,?) ON CONFLICT(profile,key) DO UPDATE SET value=excluded.value",
                (profile, key, value),
            )
            self.conn.commit()

    def get_setting(self, profile: str, key: str, default: str = "") -> str:
        if not self.enabled:
            return default
        row = self.conn.execute("SELECT value FROM settings WHERE profile=? AND key=?", (profile, key)).fetchone()
        return row[0] if row else default

    def import_backup(self, profile: str, payload: dict) -> dict[str, int]:
        counts = {"bets": 0, "strategies": 0, "budget": 0}
        if not self.enabled:
            return counts
        for row in payload.get("bets", []):
            nums = tuple(int(x) for x in str(row.get("numbers", "")).split(",") if str(x).strip())
            if nums:
                self.add_bet(profile, str(row.get("lottery", "")), nums, str(row.get("contest", "")), float(row.get("amount", 0) or 0), str(row.get("source", "backup")), str(row.get("notes", "")))
                counts["bets"] += 1
        for row in payload.get("strategies", []):
            try:
                data = json.loads(row.get("payload", "{}")) if isinstance(row.get("payload"), str) else dict(row.get("payload", {}))
            except Exception:
                data = {}
            self.save_strategy(profile, str(row.get("lottery", "")), str(row.get("name", "Estratégia importada")), data)
            counts["strategies"] += 1
        for row in payload.get("budget", []):
            self.add_budget(profile, str(row.get("kind", "Aposta")), float(row.get("amount", 0) or 0), str(row.get("lottery", "")), str(row.get("notes", "")))
            counts["budget"] += 1
        return counts

    def export_backup(self, profile: str) -> bytes:
        payload = {
            "version": APP_VERSION,
            "profile": profile,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "bets": self.list_bets(profile).to_dict("records") if self.enabled else [],
            "strategies": self.list_strategies(profile).to_dict("records") if self.enabled else [],
            "budget": self.list_budget(profile).to_dict("records") if self.enabled else [],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


REPO = LocalRepository(DB_PATH)

def balls_html(draw: Iterable[int], config: LotteryConfig, size: int = 44) -> str:
    balls = "".join(
        f'<span class="lottery-ball" style="width:{size}px;height:{size}px">{html.escape(config.format_number(int(n)))}</span>'
        for n in draw
    )
    return f'<div class="lottery-balls">{balls}</div>'


def frequency_grid_html(table: pd.DataFrame, config: LotteryConfig, metric: str = "Frequência") -> str:
    if table.empty:
        return ""
    values = table[metric].astype(float)
    lo, hi = float(values.min()), float(values.max())
    span = max(hi - lo, 1e-9)
    cells = []
    for _, row in table.iterrows():
        value = float(row[metric])
        intensity = (value - lo) / span
        opacity = 0.05 + 0.34 * intensity
        n = int(row["Número"])
        label = config.format_number(n)
        detail = f"{value:.1f}" if not float(value).is_integer() else str(int(value))
        cells.append(
            f'<div class="number-cell" style="background:rgba(41,121,255,{opacity:.3f})" '
            f'title="{html.escape(metric)}: {html.escape(detail)}">{html.escape(label)}'
            f'<br><span style="font-size:.70rem;opacity:.72">{html.escape(detail)}</span></div>'
        )
    return '<div class="number-grid">' + "".join(cells) + "</div>"


def network_figure(edges: pd.DataFrame, config: LotteryConfig):
    if edges.empty:
        return go.Figure()
    graph = nx.Graph()
    for _, row in edges.iterrows():
        graph.add_edge(int(row["A"]), int(row["B"]), weight=int(row["Frequência"]))
    pos = nx.spring_layout(graph, seed=42, weight="weight")
    edge_x, edge_y = [], []
    widths = []
    for a, b, data in graph.edges(data=True):
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        widths.append(data["weight"])
    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(width=1), hoverinfo="none")
    node_x, node_y, labels, sizes = [], [], [], []
    degree = dict(graph.degree(weight="weight"))
    for node in graph.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        labels.append(config.format_number(node))
        sizes.append(18 + min(30, degree.get(node, 1) * 0.35))
    node_trace = go.Scatter(
        x=node_x, y=node_y, mode="markers+text", text=labels, textposition="middle center",
        hovertemplate="Número %{text}<extra></extra>", marker=dict(size=sizes, line=dict(width=1)),
    )
    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        showlegend=False, margin=dict(l=10, r=10, t=20, b=10), height=560,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig


def top_prize_probability(config: LotteryConfig, bet_size: int) -> tuple[float, float]:
    config.validate_bet_size(bet_size)
    total = math.comb(config.universe_size, config.drawn_count)
    favorable = math.comb(bet_size, config.drawn_count)
    probability = favorable / total
    one_in = total / favorable
    return probability, one_in


def currency_br(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def maybe_authenticate() -> None:
    try:
        configured = str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        configured = ""
    if not configured:
        return
    if st.session_state.get("authenticated"):
        return
    st.title("🔐 Acesso ao Loterias Lab")
    password = st.text_input("Senha do aplicativo", type="password")
    if st.button("Entrar", type="primary"):
        if password == configured:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


def data_source_controls(lottery_name: str, config: LotteryConfig) -> LoadReport | None:
    st.sidebar.markdown("### 📚 Base de dados")
    source = st.sidebar.radio("Fonte", ["Dados oficiais CAIXA", "Arquivo CSV/Excel", "Simulação"], key="source_mode")

    if source == "Dados oficiais CAIXA":
        st.sidebar.caption("O último resultado é consultado automaticamente com 1 chamada. O controle abaixo sincroniza o histórico usado nas análises.")
        official_qty = st.sidebar.slider("Concursos para sincronizar", 20, 1000, 200, step=20)
        auto = st.sidebar.checkbox("Sincronizar histórico ao abrir", value=False, help="Pode fazer várias chamadas à API. Para apenas ver o último resultado, não é necessário ativar.")
        should_load = st.sidebar.button("Sincronizar histórico CAIXA", use_container_width=True, type="primary")
        if auto and "load_report" not in st.session_state:
            should_load = True
        if should_load:
            try:
                with st.spinner("Sincronizando concursos com a API da CAIXA..."):
                    st.session_state.load_report = cached_official(lottery_name, official_qty)
                    st.session_state.bet_history = []
                logger.info("Official data loaded: %s %s", lottery_name, official_qty)
                st.sidebar.success(f"{st.session_state.load_report.valid_rows} concursos sincronizados.")
            except Exception as exc:
                logger.exception("Official load failed")
                st.sidebar.error("Não foi possível sincronizar o histórico da CAIXA agora. O resultado mais recente pode continuar disponível acima; use arquivo ou simulação como alternativa.")

    elif source == "Arquivo CSV/Excel":
        uploaded = st.sidebar.file_uploader("CSV, TXT, XLSX ou XLS", type=["csv", "txt", "xlsx", "xls"])
        if uploaded is not None:
            try:
                raw = uploaded.getvalue()
                fingerprint = (lottery_name, uploaded.name, len(raw), hashlib.sha256(raw).hexdigest())
                if st.session_state.get("file_fingerprint") != fingerprint:
                    st.session_state.load_report = cached_load_file(raw, uploaded.name, lottery_name)
                    st.session_state.file_fingerprint = fingerprint
                    st.session_state.bet_history = []
                    logger.info("File loaded: %s %s", lottery_name, uploaded.name)
            except Exception:
                logger.exception("File load failed")
                st.sidebar.error("Não foi possível interpretar o arquivo.")
    else:
        sim_qty = st.sidebar.slider("Concursos simulados", 100, 10000, 1000, step=100)
        seed = st.sidebar.number_input("Seed", value=42, step=1)
        if st.sidebar.button("Gerar simulação", type="primary", use_container_width=True):
            st.session_state.load_report = simulate_draws(config, sim_qty, int(seed))
            st.session_state.bet_history = []
            logger.info("Simulation created: %s %s", lottery_name, sim_qty)

    return st.session_state.get("load_report")


def validated_sample(report: LoadReport, size: int | None) -> list[Draw]:
    if size is None or size >= len(report.draws):
        return report.draws
    return report.draws[-size:]


def add_games_to_wallet(games: list[Draw], profile: str, lottery_name: str, config: LotteryConfig, source: str, register_budget: bool = False, notes: str = "") -> None:
    unit = config.bet_price(len(games[0])) if games else 0.0
    for game in games:
        REPO.add_bet(profile, lottery_name, game, amount=unit, source=source, notes=notes)
    if register_budget and games:
        REPO.add_budget(profile, "Aposta", unit * len(games), lottery_name, f"{len(games)} jogo(s) salvos — {source}")


def get_saved_bets(profile: str, lottery_name: str, config: LotteryConfig) -> list[tuple[int, Draw, dict]]:
    df = REPO.list_bets(profile, lottery_name)
    out = []
    if df.empty:
        return out
    for _, row in df.iterrows():
        nums = tuple(sorted(int(x) for x in str(row["numbers"]).split(",") if x != ""))
        out.append((int(row["id"]), nums, row.to_dict()))
    return out


def render_bet_profile(bet: Draw, analytics: AdvancedAnalytics, config: LotteryConfig) -> None:
    previous = analytics.draws[-1] if analytics.draws else None
    score, parts = analytics.balance_score(bet, previous)
    rarity, _ = analytics.rarity_score(bet, previous)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Índice de equilíbrio", f"{score:.0f}/100")
    c2.metric("Raridade histórica", f"{rarity:.0f}/100")
    c3.metric("Pares", sum(n % 2 == 0 for n in bet))
    c4.metric("Soma", sum(bet))
    c5.metric("Consecutivos", consecutive_pairs(bet))
    st.markdown(balls_html(bet, config), unsafe_allow_html=True)
    st.info(interpret_draw(bet, analytics, config, previous))
    detail = pd.DataFrame({"Componente": list(parts.keys()), "Aderência ao perfil histórico %": [v * 100 for v in parts.values()]})
    st.bar_chart(detail.set_index("Componente"))

@st.cache_data(show_spinner=False)
def cached_load_file(data: bytes, filename: str, lottery_name: str) -> LoadReport:
    return load_tabular_bytes(data, filename, LOTTERIES[lottery_name])


@st.cache_data(ttl=300, show_spinner=False)
def cached_latest_official(lottery_name: str) -> OfficialContest:
    return fetch_official_contest(LOTTERIES[lottery_name])


@st.cache_data(ttl=300, show_spinner=False)
def cached_recent_generic_results(slug: str, quantity: int) -> list[dict]:
    """Busca os concursos mais recentes de uma modalidade do painel geral."""
    quantity = max(1, min(int(quantity), 50))
    latest = fetch_generic_official_result(slug, timeout=8, attempts=2)
    latest_number = int(latest.get("numero") or 0)
    if latest_number <= 0:
        return []

    contest_numbers = list(range(max(1, latest_number - quantity + 1), latest_number + 1))
    results: dict[int, dict] = {latest_number: latest}

    def worker(contest: int) -> tuple[int, dict]:
        payload = _fetch_json(f"{CAIXA_API_BASE}/{slug}/{contest}", timeout=8, attempts=2)
        return contest, payload

    pending = [n for n in contest_numbers if n != latest_number]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(worker, n): n for n in pending}
        for future in as_completed(futures):
            n = futures[future]
            try:
                contest, payload = future.result()
                results[contest] = payload
            except Exception as exc:
                logger.warning("Falha ao buscar %s concurso %s: %s", slug, n, exc)

    return [results[n] for n in sorted(results, reverse=True) if n in results]


@st.cache_data(ttl=300, show_spinner=False)
def cached_all_official_results() -> dict[str, dict]:
    """Consulta todas as modalidades do painel em paralelo e guarda por 5 minutos."""
    results: dict[str, dict] = {}

    def worker(item: tuple[str, dict[str, str]]) -> tuple[str, dict]:
        name, meta = item
        return name, fetch_generic_official_result(meta["slug"], timeout=8, attempts=2)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(worker, item): item[0] for item in OFFICIAL_RESULT_GAMES.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result_name, payload = future.result()
                results[result_name] = payload
            except Exception as exc:
                results[name] = {"_error": str(exc)}
    return results


def info_card_html(label: str, value: str, subtitle: str = "", icon: str = "", accent: str = "#2563eb") -> str:
    return f'''<div class="info-card" style="--accent:{html.escape(accent)}">
      <div class="info-card-top"><span class="info-card-icon">{html.escape(icon)}</span><span class="info-card-label">{html.escape(label)}</span></div>
      <div class="info-card-value">{html.escape(value)}</div>
      <div class="info-card-subtitle">{html.escape(subtitle) if subtitle else "&nbsp;"}</div>
    </div>'''


def result_balls_html(values: Iterable[object], accent: str, width: int = 2, compact: bool = False) -> str:
    size_class = "result-ball compact" if compact else "result-ball"
    balls = "".join(
        f'<span class="{size_class}" style="--accent:{html.escape(accent)}">{html.escape(_display_number(v, width))}</span>'
        for v in values
    )
    return f'<div class="result-balls">{balls}</div>'


def super_sete_result_html(values: Iterable[object], accent: str) -> str:
    cells = []
    for idx, value in enumerate(values, start=1):
        cells.append(
            '<div class="super-sete-cell">'
            f'<span class="super-sete-col">C{idx}</span>'
            f'<span class="super-sete-number" style="--accent:{html.escape(accent)}">{html.escape(_display_number(value, 1))}</span>'
            '</div>'
        )
    return '<div class="super-sete-grid">' + ''.join(cells) + '</div>'


def _meaningful_extra(value: object) -> str:
    text = str(value or "").strip()
    if not text or text in {"-", "--", "—", "N/A", "NA"}:
        return ""
    # Alguns retornos da CAIXA usam caracteres de substituição quando o campo não se aplica.
    if text and all(ch in "�? \ufffd" for ch in text):
        return ""
    return text


def official_result_card_html(name: str, payload: dict, accent: str) -> str:
    if payload.get("_error"):
        return (
            f'<div class="official-card unavailable" style="--accent:{html.escape(accent)}">'
            f'<div class="official-card-topline"></div>'
            f'<div class="official-card-title">{html.escape(name)}</div>'
            '<div class="official-card-error">Resultado temporariamente indisponível</div>'
            '</div>'
        )

    contest = payload.get("numero", "—")
    draw_date = str(payload.get("dataApuracao") or "—")
    next_date = str(payload.get("dataProximoConcurso") or "")
    next_contest = payload.get("numeroConcursoProximo")
    prize = _safe_money(payload.get("valorEstimadoProximoConcurso"))
    accumulated = bool(payload.get("acumulado", False))
    status = "ACUMULOU" if accumulated else "TEVE GANHADOR"
    status_class = "accumulated" if accumulated else "winner"
    numbers = payload.get("listaDezenas") or []

    if name == "Super Sete":
        primary = super_sete_result_html(numbers, accent)
    else:
        primary = result_balls_html(numbers, accent, 2, compact=len(numbers) > 15)

    special_parts: list[str] = []
    second = payload.get("listaDezenasSegundoSorteio") or []
    if name == "Dupla Sena" and second:
        special_parts.append('<div class="result-special-label">2º sorteio</div>')
        special_parts.append(result_balls_html(second, accent, 2))

    trevos = payload.get("trevosSorteados") or []
    if name == "+Milionária" and trevos:
        special_parts.append('<div class="result-special-label">Trevos</div>')
        special_parts.append(result_balls_html(trevos, "#d4a72c", 1))

    # Este campo só é relevante para estas duas modalidades. Em outras, a API
    # pode retornar placeholders que não devem aparecer no card.
    if name in {"Dia de Sorte", "Timemania"}:
        extra = _meaningful_extra(payload.get("nomeTimeCoracaoMesSorte"))
        if extra:
            extra_label = "Mês da Sorte" if name == "Dia de Sorte" else "Time do Coração"
            special_parts.append(
                '<div class="official-extra">'
                f'<span>{html.escape(extra_label)}</span>'
                f'<strong>{html.escape(extra)}</strong>'
                '</div>'
            )

    special = ''.join(special_parts)
    prize_text = currency_br(prize) if prize else "—"
    next_bits = []
    if next_contest:
        next_bits.append(f"Concurso {html.escape(str(next_contest))}")
    if next_date:
        next_bits.append(html.escape(next_date))
    next_text = " · ".join(next_bits) if next_bits else "Data ainda não informada"

    # Mantido sem linhas HTML indentadas após conteúdo opcional: isso evita que
    # o Markdown do Streamlit transforme trechos em bloco de código (bug visto
    # no card do Super Sete quando 'special' estava vazio).
    return (
        f'<div class="official-card" style="--accent:{html.escape(accent)}">'
        '<div class="official-card-topline"></div>'
        '<div class="official-card-header">'
        '<div class="official-heading">'
        f'<div class="official-card-title">{html.escape(name)}</div>'
        f'<div class="official-card-contest"><span>Concurso {html.escape(str(contest))}</span><span>{html.escape(draw_date)}</span></div>'
        '</div>'
        f'<span class="status-pill {status_class}">{status}</span>'
        '</div>'
        '<div class="result-special-label">Resultado oficial</div>'
        f'{primary}'
        f'{special}'
        '<div class="official-card-spacer"></div>'
        '<div class="official-card-footer">'
        '<div class="prize-row">'
        '<div class="prize-copy"><span>Próximo prêmio estimado</span>'
        f'<strong>{html.escape(prize_text)}</strong></div>'
        '</div>'
        f'<div class="next-draw"><span class="next-draw-dot"></span><span>Próximo sorteio: {next_text}</span></div>'
        '</div>'
        '</div>'
    )


def render_all_official_results() -> None:
    st.markdown("## 🎰 Resultados oficiais das Loterias CAIXA")
    st.caption("Último concurso disponível de cada modalidade. Atualização automática com cache de 5 minutos.")
    _, header_right = st.columns([5, 1])
    with header_right:
        if st.button("↻ Atualizar todos", use_container_width=True, key="refresh_all_caixa"):
            cached_all_official_results.clear()
            cached_latest_official.clear()
            st.rerun()

    with st.spinner("Consultando os resultados oficiais da CAIXA..."):
        results = cached_all_official_results()

    names = list(OFFICIAL_RESULT_GAMES)
    for start in range(0, len(names), 3):
        cols = st.columns(3)
        for col, name in zip(cols, names[start:start+3]):
            meta = OFFICIAL_RESULT_GAMES[name]
            payload = results.get(name, {"_error": "sem resposta"})
            with col:
                st.markdown(official_result_card_html(name, payload, meta["accent"]), unsafe_allow_html=True)
                if not payload.get("_error"):
                    tiers = payload.get("listaRateioPremio") or []
                    with st.expander("Premiação e detalhes"):
                        if tiers:
                            table = pd.DataFrame([{
                                "Faixa": t.get("descricaoFaixa", ""),
                                "Ganhadores": int(t.get("numeroDeGanhadores") or 0),
                                "Prêmio": currency_br(_safe_money(t.get("valorPremio"))),
                            } for t in tiers])
                            st.dataframe(table, hide_index=True, use_container_width=True)
                        revenue = _safe_money(payload.get("valorArrecadado"))
                        if revenue:
                            st.caption(f"Arrecadação: {currency_br(revenue)}")

    st.caption("Fonte: Portal Loterias/CAIXA. Algumas modalidades possuem elementos próprios, como segundo sorteio, trevos, Mês da Sorte ou Time do Coração.")

    st.markdown("### 🗓️ Últimos concursos")
    st.caption("Consulte resultados anteriores diretamente na CAIXA sem carregar toda a base analítica.")
    h1, h2, h3 = st.columns([2.2, 1, 1.1])
    history_name = h1.selectbox("Modalidade", names, key="official_history_game")
    history_qty = h2.selectbox("Quantidade", [5, 10, 20, 30, 50], index=1, key="official_history_qty")
    show_history = h3.checkbox("Mostrar histórico", value=False, key="official_history_show")

    if show_history:
        meta = OFFICIAL_RESULT_GAMES[history_name]
        with st.spinner(f"Buscando os últimos {history_qty} concursos de {history_name}..."):
            history = cached_recent_generic_results(meta["slug"], int(history_qty))
        if not history:
            st.warning("Não foi possível carregar o histórico dessa modalidade agora.")
        else:
            rows = []
            for item in history:
                nums = item.get("listaDezenas") or []
                if history_name == "Super Sete":
                    result_text = "  ".join(_display_number(v, 1) for v in nums)
                else:
                    result_text = "  ".join(_display_number(v, 2) for v in nums)
                second = item.get("listaDezenasSegundoSorteio") or []
                if second:
                    result_text += "  |  2º: " + "  ".join(_display_number(v, 2) for v in second)
                trevos = item.get("trevosSorteados") or []
                if trevos:
                    result_text += "  |  Trevos: " + "  ".join(_display_number(v, 1) for v in trevos)
                rows.append({
                    "Concurso": int(item.get("numero") or 0),
                    "Data": str(item.get("dataApuracao") or ""),
                    "Resultado": result_text,
                    "Situação": "Acumulou" if bool(item.get("acumulado", False)) else "Teve ganhador",
                })
            history_df = pd.DataFrame(rows)
            st.dataframe(history_df, hide_index=True, use_container_width=True, height=min(520, 38 * len(history_df) + 40))


def render_latest_official_card(lottery_name: str, config: LotteryConfig) -> None:
    """Exibe automaticamente o resultado atual da CAIXA sem exigir histórico carregado."""
    st.markdown("### 🔴 Resultado oficial CAIXA — atualização automática")
    try:
        latest = cached_latest_official(lottery_name)
    except Exception as exc:
        st.warning("Não foi possível consultar o resultado oficial da CAIXA neste momento. As demais fontes do app continuam disponíveis.")
        logger.warning("Latest CAIXA result unavailable for %s: %s", lottery_name, exc)
        return

    accent = OFFICIAL_RESULT_GAMES.get(lottery_name, {}).get("accent", "#2563eb")
    top_left, top_mid, top_right, refresh_col = st.columns([1.1, 1, 1.25, .7])
    with top_left:
        st.markdown(info_card_html("Concurso", str(latest.contest), "último resultado oficial", "🏆", accent), unsafe_allow_html=True)
    with top_mid:
        st.markdown(info_card_html("Data", latest.draw_date or "—", "data da apuração", "📅", accent), unsafe_allow_html=True)
    with top_right:
        prize_text = currency_br(latest.estimated_next_prize) if latest.estimated_next_prize else "—"
        st.markdown(info_card_html("Próximo prêmio", prize_text, latest.next_draw_date or "estimativa CAIXA", "💰", accent), unsafe_allow_html=True)
    if refresh_col.button("↻ Atualizar", key=f"refresh_caixa_{lottery_name}", use_container_width=True):
        cached_latest_official.clear()
        cached_all_official_results.clear()
        st.rerun()

    st.markdown(result_balls_html(latest.draw, accent, 2, compact=len(latest.draw) > 15), unsafe_allow_html=True)
    status = "ACUMULOU" if latest.accumulated else f"{latest.main_tier_winners} ganhador(es) na faixa principal"
    details = []
    if latest.next_draw_date:
        details.append(f"próximo sorteio: **{latest.next_draw_date}**")
    if latest.venue or latest.city_uf:
        place = " · ".join(x for x in (latest.venue, latest.city_uf) if x)
        details.append(f"local: **{place}**")
    st.caption(f"{status}" + (" · " + " · ".join(details) if details else ""))

    with st.expander("Premiação e detalhes do concurso"):
        if latest.prize_tiers:
            prize_df = pd.DataFrame([
                {
                    "Faixa": tier.description,
                    "Ganhadores": tier.winners,
                    "Prêmio por aposta": currency_br(tier.prize_value),
                }
                for tier in latest.prize_tiers
            ])
            st.dataframe(prize_df, hide_index=True, use_container_width=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Acumulado próximo", currency_br(latest.accumulated_next_prize) if latest.accumulated_next_prize else "—")
        c2.metric("Arrecadação", currency_br(latest.revenue) if latest.revenue else "—")
        c3.metric("Próximo concurso", latest.next_contest or "—")
        st.caption("Fonte: endpoint JSON do Portal Loterias/CAIXA. A resposta é armazenada em cache por 5 minutos para reduzir chamadas.")


@st.cache_data(ttl=1800, show_spinner=False)
def cached_official(lottery_name: str, quantity: int) -> LoadReport:
    return fetch_recent_official_draws(LOTTERIES[lottery_name], quantity)


@st.cache_data(show_spinner=False)
def cached_backtest(
    draws: tuple[Draw, ...], lottery_name: str, bet_size: int, hot_count: int, cold_count: int,
    training_window: int, simulations_per_draw: int, max_test_draws: int, seed: int,
) -> BacktestResult:
    return run_backtest(
        list(draws), LOTTERIES[lottery_name], bet_size, hot_count, cold_count,
        training_window, simulations_per_draw, max_test_draws, seed,
    )


def reset_lottery_state(lottery_name: str) -> None:
    st.session_state.current_lottery = lottery_name
    for key in [
        "load_report", "file_fingerprint", "bet_history", "backtest_result", "generated_games",
        "closure_games", "checker_result", "ocr_text", "latest_official_result", "strategy_comparison", "mc_result", "api_health"
    ]:
        st.session_state.pop(key, None)


# ===== Streamlit UI =====
st.set_page_config(
    page_title="Loterias Lab V3.3",
    page_icon="🍀",
    layout="wide",
    initial_sidebar_state="expanded",
)

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

/* V3.3 - cards corrigidos e historico oficial */
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
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
maybe_authenticate()

st.sidebar.markdown("## 🍀 Loterias Lab")
st.sidebar.caption(f"Versão {APP_VERSION} · estatística, simulação e gestão")
profile = st.sidebar.text_input("Perfil local", value=st.session_state.get("profile", "Meu perfil"), help="Usado para separar jogos, estratégias e orçamento no armazenamento local.")
st.session_state.profile = profile.strip() or "Meu perfil"
profile = st.session_state.profile

theme_mode = st.sidebar.selectbox("Tema visual", ["Sistema/Streamlit", "Claro", "Escuro"], index=0)
if theme_mode == "Escuro":
    st.markdown("""<style>
    .stApp {background:#0e1117;color:#f4f6f8;}
    [data-testid="stSidebar"] {background:#111827;}
    [data-testid="stMetric"], .hero-card, .soft-card, .number-cell {background:rgba(255,255,255,.035)!important;border-color:rgba(255,255,255,.14)!important;}
    </style>""", unsafe_allow_html=True)
elif theme_mode == "Claro":
    st.markdown("""<style>
    .stApp {background:#ffffff;color:#18212f;}
    [data-testid="stSidebar"] {background:#f7f9fc;}
    </style>""", unsafe_allow_html=True)

lottery_name = st.sidebar.selectbox("Modalidade", list(LOTTERIES.keys()))
config = LOTTERIES[lottery_name]
if st.session_state.get("current_lottery") != lottery_name:
    reset_lottery_state(lottery_name)

st.sidebar.caption(
    f"Universo {config.format_number(config.number_min)}–{config.format_number(config.number_max)} · "
    f"sorteio {config.drawn_count} · aposta {config.bet_min}–{config.bet_max}"
)
report = data_source_controls(lottery_name, config)

st.sidebar.divider()
page = st.sidebar.radio(
    "Navegação",
    [
        "🎰 Resultados CAIXA", "🏠 Dashboard", "🔥 Frequências", "🧩 Padrões", "🕸️ Rede", "🎯 Gerador",
        "🧮 Fechamentos", "🧪 Laboratório", "💼 Meus Jogos", "✅ Conferidor & OCR",
        "💰 Orçamento", "🗂️ Dados", "❓ FAQ",
    ],
)

st.sidebar.divider()
st.sidebar.caption("Os recursos estatísticos descrevem padrões passados. Eles não transformam uma combinação em mais provável no próximo sorteio.")

st.markdown(
    f"""<div class="hero-card"><div class="kpi-label">{html.escape(lottery_name)}</div>
    <h1 style="margin:.15rem 0 .2rem">Loterias Lab V3.3</h1>
    <div style="opacity:.76">Análise histórica, geração por restrições, backtests, Monte Carlo, fechamentos, conferência e gestão de jogos.</div></div>""",
    unsafe_allow_html=True,
)

# Resultado da modalidade selecionada continua disponível nas páginas analíticas.
if page != "🎰 Resultados CAIXA":
    render_latest_official_card(lottery_name, config)

# Resultados gerais e FAQ funcionam mesmo sem histórico sincronizado.
if page not in {"❓ FAQ", "🎰 Resultados CAIXA"} and (report is None or not report.draws):
    st.info("Carregue dados oficiais, um arquivo ou uma simulação na barra lateral para liberar as análises.")
    st.stop()

if report is not None and report.draws:
    max_draws = len(report.draws)
    sample_candidates = [n for n in (20, 50, 100, 250, 500, 1000, 2500) if n < max_draws]
    sample_labels = [f"Últimos {n}" for n in sample_candidates] + [f"Histórico completo ({max_draws})"]
    sample_label = st.sidebar.selectbox("Amostra analítica", sample_labels, index=len(sample_labels)-1)
    sample_size = None if sample_label.startswith("Histórico") else int(sample_label.split()[-1])
    active_draws = validated_sample(report, sample_size)
    analytics = AdvancedAnalytics(active_draws, config)
else:
    active_draws = []
    analytics = AdvancedAnalytics([], config)
if page == "🎰 Resultados CAIXA":
    render_all_official_results()

elif page == "🏠 Dashboard":
    summary = analytics.summary()
    freq = analytics.frequency_table()
    delay = analytics.delay_table()
    hot_number = int(freq.sort_values(["Frequência", "Número"], ascending=[False, True]).iloc[0]["Número"])
    delayed_number = int(delay.sort_values(["Atraso atual", "Número"], ascending=[False, True]).iloc[0]["Número"])
    c1, c2, c3, c4, c5 = st.columns(5)
    cards = [
        (c1, "Concursos", f"{len(active_draws):,}".replace(",", "."), "na amostra ativa", "📚", "#2563eb"),
        (c2, "Mais frequente", config.format_number(hot_number), "maior frequência observada", "🔥", "#ef4444"),
        (c3, "Mais atrasado", config.format_number(delayed_number), "maior atraso atual", "⏳", "#f59e0b"),
        (c4, "Soma média", f"{summary['average_sum']:.1f}", "soma média das dezenas", "∑", "#7c3aed"),
        (c5, "Repetição média", f"{summary['average_repeat']:.2f}", "vs. concurso anterior", "↻", "#059669"),
    ]
    for col, label, value, subtitle, icon, accent in cards:
        with col:
            st.markdown(info_card_html(label, value, subtitle, icon, accent), unsafe_allow_html=True)

    st.markdown("### Mapa das dezenas")
    map_mode = st.segmented_control("Visualização", ["Frequência", "Atraso", "Desvio %"], default="Frequência")
    if map_mode == "Atraso":
        grid = freq[["Número"]].merge(delay[["Número", "Atraso atual"]], on="Número").rename(columns={"Atraso atual": "Atraso"})
        st.markdown(frequency_grid_html(grid, config, "Atraso"), unsafe_allow_html=True)
    else:
        st.markdown(frequency_grid_html(freq, config, map_mode), unsafe_allow_html=True)
    st.caption("A intensidade representa somente a métrica escolhida dentro da amostra ativa.")

    left, right = st.columns([1.15, .85])
    with left:
        st.markdown("### Tendência de frequência recente")
        recent_n = min(50, len(active_draws))
        trend = analytics.trend_table(recent_n).sort_values("Variação p.p.", ascending=False)
        show = trend[["Número", "Histórico %", "Recente %", "Variação p.p.", "Tendência"]].copy()
        show["Número"] = show["Número"].map(config.format_number)
        st.dataframe(show.head(15), hide_index=True, use_container_width=True)
    with right:
        st.markdown("### Qualidade da base")
        q1, q2 = st.columns(2)
        q1.metric("Válidos", report.valid_rows)
        q2.metric("Rejeitados", report.rejected_rows)
        st.metric("Hash da versão da base", dataset_hash(report.draws))
        st.caption(f"Fonte: {report.source_name} · carregada em {report.loaded_at:%d/%m/%Y %H:%M}")
        if report.source_kind == "simulacao":
            st.warning("A base ativa é simulada.")
        elif report.source_kind == "oficial":
            st.success("Base carregada a partir do Portal Loterias/CAIXA.")

    st.markdown("### Resumo de comportamento")
    a, b, c = st.columns(3)
    parity = analytics.parity_distribution()
    most_parity = parity.sort_values("Concursos", ascending=False).iloc[0]
    a.metric("Paridade mais recorrente", str(most_parity["Composição"]))
    sums = analytics.sums()
    b.metric("Faixa típica de soma", f"{sums.quantile(.25):.0f}–{sums.quantile(.75):.0f}" if len(sums) else "—")
    c.metric("Pares consecutivos médios", f"{np.mean([consecutive_pairs(d) for d in active_draws]):.2f}")

    if active_draws:
        st.markdown("### Assistente de leitura do último concurso")
        previous = active_draws[-2] if len(active_draws) >= 2 else None
        st.markdown(balls_html(active_draws[-1], config), unsafe_allow_html=True)
        st.info(interpret_draw(active_draws[-1], analytics, config, previous))

elif page == "🔥 Frequências":
    st.markdown("## Frequências, tendências e atrasos")
    freq = analytics.frequency_table()
    delay = analytics.delay_table()
    if len(active_draws) <= 10:
        recent_n = len(active_draws)
        st.caption(f"Janela recente: {recent_n} concurso(s) — a base é pequena demais para um controle deslizante maior.")
    else:
        recent_n = st.slider("Janela recente", 10, len(active_draws), min(50, len(active_draws)), step=1 if len(active_draws) < 100 else 10)
    trend = analytics.trend_table(recent_n)
    merged = freq.merge(delay[["Número", "Maior intervalo"]], on="Número").merge(trend[["Número", "Recente %", "Variação p.p.", "Tendência"]], on="Número")
    display = merged.copy()
    display["Número"] = display["Número"].map(config.format_number)
    for col in ["Frequência %", "Desvio %", "Recente %", "Variação p.p."]:
        display[col] = display[col].map(lambda x: f"{x:+.2f}%" if col in ["Desvio %", "Variação p.p."] else f"{x:.2f}%")
    display["Esperado"] = display["Esperado"].map(lambda x: f"{x:.1f}")
    display["Intervalo médio"] = display["Intervalo médio"].map(lambda x: "—" if pd.isna(x) else f"{x:.1f}")
    st.dataframe(display, hide_index=True, use_container_width=True, height=520)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Frequência absoluta")
        st.bar_chart(freq.set_index("Número")[["Frequência"]])
    with c2:
        st.markdown("### Variação recente × histórico")
        chart = trend.set_index("Número")[["Variação p.p."]]
        st.bar_chart(chart)

    st.markdown("### Ranking de atraso")
    ranking = delay.sort_values(["Atraso atual", "Número"], ascending=[False, True]).copy()
    ranking["Número"] = ranking["Número"].map(config.format_number)
    ranking["Intervalo médio"] = ranking["Intervalo médio"].map(lambda x: "—" if pd.isna(x) else f"{x:.1f}")
    st.dataframe(ranking.head(30), hide_index=True, use_container_width=True)

elif page == "🧩 Padrões":
    st.markdown("## Comportamento histórico")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Par × ímpar")
        parity = analytics.parity_distribution().set_index("Composição")
        st.bar_chart(parity)
    with c2:
        st.markdown("### Repetição do concurso anterior")
        repeats = analytics.repeats_from_previous().set_index("Repetidos")
        st.bar_chart(repeats)

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("### Sequências consecutivas")
        cons = analytics.consecutive_distribution().set_index("Pares consecutivos")
        st.bar_chart(cons)
    with c4:
        st.markdown("### Distribuição por faixas")
        st.bar_chart(analytics.band_distribution().set_index("Faixa"))

    st.markdown("### Distribuição das somas")
    sums = analytics.sums()
    fig = px.histogram(x=sums, nbins=min(30, max(8, int(math.sqrt(max(1, len(sums)))))), labels={"x": "Soma", "y": "Concursos"})
    fig.update_layout(showlegend=False, height=340, margin=dict(l=10,r=10,t=10,b=10))
    st.plotly_chart(fig, use_container_width=True)

    p1, p2 = st.columns(2)
    with p1:
        st.markdown("### Pares mais recorrentes")
        st.dataframe(analytics.top_combinations(2, 25), hide_index=True, use_container_width=True)
    with p2:
        st.markdown("### Trios mais recorrentes")
        st.dataframe(analytics.top_combinations(3, 25), hide_index=True, use_container_width=True)

    st.markdown("### Índice de raridade histórica por concurso")
    last_k = min(40, len(active_draws))
    rows = []
    start = len(active_draws) - last_k
    for i in range(start, len(active_draws)):
        previous = active_draws[i-1] if i > 0 else None
        score, _ = analytics.rarity_score(active_draws[i], previous)
        rows.append({"Posição": i+1, "Raridade": score, "Soma": sum(active_draws[i])})
    rarity_df = pd.DataFrame(rows).set_index("Posição")
    st.line_chart(rarity_df[["Raridade"]])
    st.caption("Raridade é similaridade/inusualidade dentro da amostra; não mede fraude nem probabilidade futura.")

elif page == "🕸️ Rede":
    st.markdown("## Rede de coocorrência")
    st.write("As conexões mostram pares de dezenas que apareceram juntos com maior frequência na amostra selecionada.")
    col1, col2 = st.columns([.3, .7])
    with col1:
        edge_limit = st.slider("Quantidade de conexões", 10, 120, 45, 5)
        min_freq = st.number_input("Frequência mínima", min_value=1, value=2, step=1)
        edges = analytics.cooccurrence_edges(edge_limit, int(min_freq))
        st.metric("Conexões exibidas", len(edges))
        if not edges.empty:
            node_counts = Counter(edges["A"].tolist() + edges["B"].tolist())
            top_nodes = pd.DataFrame(node_counts.most_common(12), columns=["Número", "Conexões"])
            top_nodes["Número"] = top_nodes["Número"].map(config.format_number)
            st.dataframe(top_nodes, hide_index=True, use_container_width=True)
    with col2:
        st.plotly_chart(network_figure(edges, config), use_container_width=True)
    st.caption("Uma conexão forte só descreve coocorrência passada. Não implica dependência causal ou vantagem de previsão.")
elif page == "🎯 Gerador":
    st.markdown("## Gerador avançado de combinações")
    mode = st.radio("Modo", ["Aleatório", "Perfil histórico", "Personalizado"], horizontal=True)
    bet_size = st.slider("Números por aposta", config.bet_min, config.bet_max, config.bet_default)
    games_qty = st.slider("Quantidade de jogos", 1, 60, 10)
    diversity = st.select_slider("Diversidade entre jogos", options=["Baixa", "Média", "Alta"], value="Média")

    groups = analytics.groups()
    hot_count = 0
    cold_count = 0
    if mode == "Perfil histórico":
        max_hot = min(len(groups.hot), bet_size)
        hot_count = st.slider("Mais frequentes", 0, max_hot, min(max_hot, round(bet_size * .4)))
        max_cold = min(len(groups.cold), bet_size - hot_count)
        cold_count = st.slider("Menos frequentes", 0, max_cold, min(max_cold, round(bet_size * .2)))
        st.caption(f"Neutros: {bet_size-hot_count-cold_count}")

    required_text = st.text_input("Números obrigatórios", placeholder="Ex.: 05 12 27")
    excluded_text = st.text_input("Números excluídos", placeholder="Ex.: 03 41")
    required = parse_numbers(required_text, config)
    excluded = parse_numbers(excluded_text, config)

    with st.expander("Filtros avançados", expanded=(mode == "Personalizado")):
        ev_default = bet_size // 2
        even_min, even_max = st.slider("Quantidade de pares", 0, bet_size, (max(0, ev_default-1), min(bet_size, ev_default+1)))
        historical_sums = analytics.sums()
        theoretical_min = sum(sorted(config.universe)[:bet_size])
        theoretical_max = sum(sorted(config.universe, reverse=True)[:bet_size])
        if len(historical_sums):
            q10 = int(max(theoretical_min, historical_sums.quantile(.10)))
            q90 = int(min(theoretical_max, historical_sums.quantile(.90)))
        else:
            q10, q90 = theoretical_min, theoretical_max
        sum_min, sum_max = st.slider("Faixa de soma", theoretical_min, theoretical_max, (q10, q90))
        max_consecutive = st.slider("Máximo de pares consecutivos", 0, max(1, bet_size-1), min(2, max(1, bet_size-1)))
        max_repeats = st.slider("Máximo de repetidos do último concurso", 0, min(bet_size, config.drawn_count), min(2, min(bet_size, config.drawn_count)))
        min_bands = st.slider("Mínimo de faixas ocupadas", 1, min(6, bet_size), min(4, min(6, bet_size)))

    seed_enabled = st.checkbox("Seed reproduzível", value=False)
    gen_seed = st.number_input("Seed", value=42, step=1, disabled=not seed_enabled)
    unit_price = config.bet_price(bet_size)
    prob_top, one_in_top = top_prize_probability(config, bet_size)
    st.caption(f"Preço oficial de referência para uma aposta deste tamanho: **{currency_br(unit_price)}**. Total estimado para {games_qty}: **{currency_br(unit_price*games_qty)}**.")
    st.caption(f"Probabilidade matemática do prêmio máximo com uma aposta desse tamanho: aproximadamente **1 em {one_in_top:,.0f}** ({prob_top*100:.8f}%).".replace(",", "."))

    cgen1, cgen2 = st.columns([.7, .3])
    with cgen1:
        generate_clicked = st.button("Gerar combinações", type="primary", use_container_width=True)
    with cgen2:
        if st.button("Limpar geração", use_container_width=True):
            st.session_state.generated_games = []

    if generate_clicked:
        try:
            with st.spinner("Gerando combinações e aplicando filtros..."):
                games = generate_custom_bets(
                    config=config,
                    count=games_qty,
                    bet_size=bet_size,
                    mode=mode,
                    analytics=analytics,
                    hot_count=hot_count,
                    cold_count=cold_count,
                    required=required,
                    excluded=excluded,
                    diversity=diversity,
                    even_min=even_min,
                    even_max=even_max,
                    sum_min=sum_min,
                    sum_max=sum_max,
                    max_consecutive=max_consecutive,
                    max_repeats=max_repeats,
                    min_bands=min_bands,
                    seed=int(gen_seed) if seed_enabled else None,
                )
                st.session_state.generated_games = games
            if len(games) < games_qty:
                st.warning(f"Foram encontradas {len(games)} combinações que atendem a todos os filtros. Afrouxe os filtros para gerar mais.")
        except Exception as exc:
            st.error(str(exc))

    games = st.session_state.get("generated_games", [])
    if games:
        coverage = coverage_metrics(games)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Jogos gerados", len(games))
        m2.metric("Cobertura de pares", f"{coverage['pair_coverage']*100:.1f}%")
        m3.metric("Cobertura de trios", f"{coverage['triple_coverage']*100:.1f}%")
        m4.metric("Custo estimado", currency_br(unit_price*len(games)))

        selected_index = st.selectbox("Analisar jogo", range(1, len(games)+1), format_func=lambda x: f"Jogo {x}")
        render_bet_profile(games[selected_index-1], analytics, config)

        with st.expander("Ver todos os jogos", expanded=True):
            rows = []
            for i, game in enumerate(games, 1):
                score, _ = analytics.balance_score(game, active_draws[-1] if active_draws else None)
                rows.append({"Jogo": i, "Números": " - ".join(config.format_number(n) for n in game), "Equilíbrio": round(score, 1), "Pares": sum(n%2==0 for n in game), "Soma": sum(game)})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        save_col1, save_col2 = st.columns(2)
        with save_col1:
            register_budget = st.checkbox("Registrar o valor no orçamento", value=False)
            if st.button("Salvar todos em Meus Jogos", use_container_width=True):
                add_games_to_wallet(games, profile, lottery_name, config, f"Gerador — {mode}", register_budget)
                st.success("Jogos salvos.")
        with save_col2:
            strategy_name = st.text_input("Nome para favoritar esta estratégia", placeholder="Ex.: Equilibrada 3/2")
            if st.button("Salvar estratégia favorita", use_container_width=True, disabled=not strategy_name.strip()):
                payload = {
                    "mode": mode, "bet_size": bet_size, "games_qty": games_qty, "diversity": diversity,
                    "hot_count": hot_count, "cold_count": cold_count, "required": required, "excluded": excluded,
                    "even_min": even_min, "even_max": even_max, "sum_min": sum_min, "sum_max": sum_max,
                    "max_consecutive": max_consecutive, "max_repeats": max_repeats, "min_bands": min_bands,
                }
                REPO.save_strategy(profile, lottery_name, strategy_name.strip(), payload)
                st.success("Estratégia salva nos favoritos.")

        csv_rows = pd.DataFrame({"jogo": range(1, len(games)+1), "numeros": [" - ".join(config.format_number(n) for n in g) for g in games]})
        st.download_button("Baixar jogos (CSV)", csv_rows.to_csv(index=False).encode("utf-8-sig"), f"jogos_{config.slug}.csv", "text/csv", use_container_width=True)

    favs = REPO.list_strategies(profile, lottery_name)
    if not favs.empty:
        st.markdown("### Estratégias favoritas")
        fav_view = favs[["id", "name", "created_at"]].rename(columns={"id":"ID", "name":"Nome", "created_at":"Criada em"})
        st.dataframe(fav_view, hide_index=True, use_container_width=True)

elif page == "🧮 Fechamentos":
    st.markdown("## Fechamentos e desdobramentos")
    st.write("Selecione um conjunto-base maior que a aposta e o algoritmo escolhe combinações buscando ampliar a cobertura de pares ou trios dentro do limite de jogos/orçamento.")
    bet_size = st.slider("Tamanho de cada jogo", config.bet_min, config.bet_max, config.bet_default, key="closure_bet_size")
    base_text = st.text_area("Números-base", placeholder=f"Informe mais de {bet_size} números separados por espaço, vírgula ou hífen.")
    base_numbers = parse_numbers(base_text, config)
    st.caption(f"Base reconhecida: {len(base_numbers)} número(s).")

    unit = config.bet_price(bet_size)
    limit_mode = st.radio("Limite", ["Quantidade de jogos", "Orçamento"], horizontal=True)
    if limit_mode == "Quantidade de jogos":
        max_games = st.slider("Máximo de jogos", 1, 200, 20)
        budget = max_games * unit
    else:
        budget = st.number_input("Orçamento máximo (R$)", min_value=float(unit), value=float(unit*20), step=float(max(0.5, unit)))
        max_games = max(1, int(budget // unit))
        st.caption(f"Com esse orçamento cabem até {max_games} jogos de {currency_br(unit)}.")
    optimize = st.radio("Otimizar cobertura de", ["Pares", "Trios"], horizontal=True)
    closure_seed = st.number_input("Seed do fechamento", value=42, step=1)

    if len(base_numbers) >= bet_size:
        total_combinations = math.comb(len(base_numbers), bet_size)
        st.info(f"A base permite {total_combinations:,} combinações possíveis. O fechamento selecionará no máximo {min(max_games,total_combinations)}.".replace(",", "."))

    if st.button("Montar fechamento", type="primary", use_container_width=True):
        try:
            with st.spinner("Otimizando cobertura..."):
                closure = build_closure(base_numbers, config, bet_size, max_games, optimize, int(closure_seed))
                st.session_state.closure_games = closure
        except Exception as exc:
            st.error(str(exc))

    closure = st.session_state.get("closure_games", [])
    if closure:
        cov = coverage_metrics(closure, base_numbers)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Jogos", len(closure))
        c2.metric("Cobertura pares", f"{cov['pair_coverage']*100:.1f}%")
        c3.metric("Cobertura trios", f"{cov['triple_coverage']*100:.1f}%")
        c4.metric("Custo estimado", currency_br(unit*len(closure)))
        rows = pd.DataFrame({"Jogo": range(1,len(closure)+1), "Números": [" - ".join(config.format_number(n) for n in g) for g in closure]})
        st.dataframe(rows, hide_index=True, use_container_width=True, height=460)
        reg = st.checkbox("Registrar custo do fechamento no orçamento", key="closure_budget")
        if st.button("Salvar fechamento em Meus Jogos", use_container_width=True):
            add_games_to_wallet(closure, profile, lottery_name, config, f"Fechamento {optimize}", reg)
            st.success("Fechamento salvo.")
        st.download_button("Baixar fechamento (CSV)", rows.to_csv(index=False).encode("utf-8-sig"), f"fechamento_{config.slug}.csv", "text/csv", use_container_width=True)
elif page == "🧪 Laboratório":
    st.markdown("## Laboratório estatístico")
    lab_tab1, lab_tab2, lab_tab3, lab_tab4 = st.tabs(["Backtest", "Comparador", "Monte Carlo", "Teste estatístico"])

    with lab_tab1:
        st.markdown("### Backtest temporal")
        st.write("Cada concurso-alvo usa somente concursos anteriores como treinamento, evitando olhar o futuro.")
        bet_size = st.slider("Tamanho da aposta", config.bet_min, config.bet_max, config.bet_default, key="lab_bt_size")
        groups = analytics.groups()
        hot = st.slider("Mais frequentes", 0, min(len(groups.hot), bet_size), min(round(bet_size*.4), len(groups.hot)), key="lab_bt_hot")
        cold = st.slider("Menos frequentes", 0, min(len(groups.cold), bet_size-hot), min(round(bet_size*.2), len(groups.cold), bet_size-hot), key="lab_bt_cold")
        max_window = max(20, len(active_draws)-1)
        training = st.slider("Janela de treinamento", 20, max_window, min(100, max_window), disabled=len(active_draws)<=20)
        sims = st.slider("Simulações por concurso", 1, 30, 10)
        test_draws = st.slider("Concursos testados", 20, max(20, min(500, len(active_draws))), min(200, max(20, len(active_draws))))
        if st.button("Executar backtest", type="primary", key="run_bt"):
            try:
                result = cached_backtest(tuple(active_draws), lottery_name, bet_size, hot, cold, training, sims, test_draws, 42)
                st.session_state.backtest_result = result
            except Exception as exc:
                st.error(str(exc))
        result = st.session_state.get("backtest_result")
        if result:
            a,b,c = st.columns(3)
            a.metric("Perfil", f"{result.strategy_average:.3f} acertos")
            b.metric("Aleatório", f"{result.random_average:.3f} acertos")
            c.metric("Diferença observada", f"{result.difference:+.3f}")
            dist = pd.DataFrame({
                "Perfil": pd.Series(result.strategy_hits).value_counts(),
                "Aleatório": pd.Series(result.random_hits).value_counts(),
            }).fillna(0).sort_index()
            st.bar_chart(dist)
            st.caption("Diferenças pequenas podem ser apenas variabilidade amostral; o backtest não comprova capacidade de previsão.")

    with lab_tab2:
        st.markdown("### Comparador de estratégias")
        compare_size = st.slider("Tamanho da aposta", config.bet_min, config.bet_max, config.bet_default, key="cmp_size")
        compare_training = st.slider("Treinamento", 20, max(20, len(active_draws)-1), min(100, max(20, len(active_draws)-1)), key="cmp_training", disabled=len(active_draws)<=20)
        compare_tests = st.slider("Concursos", 20, max(20, min(250,len(active_draws))), min(100,max(20,len(active_draws))), key="cmp_tests")
        strategies = {
            "Equilibrada 40/20": (.40,.20),
            "Simétrica 25/25": (.25,.25),
            "Mais frequentes 60/10": (.60,.10),
            "Menos frequentes 10/60": (.10,.60),
        }
        if st.button("Comparar estratégias", type="primary", key="cmp_run"):
            rows=[]
            grp=analytics.groups()
            for name,(hf,cf) in strategies.items():
                h=min(len(grp.hot), round(compare_size*hf))
                c=min(len(grp.cold), round(compare_size*cf), compare_size-h)
                try:
                    res=cached_backtest(tuple(active_draws), lottery_name, compare_size, h, c, compare_training, 5, compare_tests, 123)
                    rows.append({"Estratégia":name,"Média de acertos":res.strategy_average,"Referência aleatória":res.random_average,"Diferença":res.difference,"Quentes":h,"Frios":c})
                except Exception as exc:
                    rows.append({"Estratégia":name,"Erro":str(exc)})
            st.session_state.strategy_comparison=pd.DataFrame(rows)
        comp=st.session_state.get("strategy_comparison")
        if isinstance(comp,pd.DataFrame) and not comp.empty:
            st.dataframe(comp,hide_index=True,use_container_width=True)
            if "Média de acertos" in comp:
                st.bar_chart(comp.set_index("Estratégia")[["Média de acertos","Referência aleatória"]])
            st.caption("O comparador mostra resultados do período testado; não deve ser lido como ranking de probabilidade futura.")

    with lab_tab3:
        st.markdown("### Monte Carlo")
        mc_size=st.slider("Tamanho da aposta",config.bet_min,config.bet_max,config.bet_default,key="mc_size")
        mc_n=st.select_slider("Simulações",options=[1000,5000,10000,50000,100000,250000],value=50000)
        mc_seed=st.number_input("Seed",value=42,step=1,key="mc_seed")
        if st.button("Executar Monte Carlo",type="primary",key="mc_run"):
            st.session_state.mc_result=monte_carlo_hits(config,mc_size,int(mc_n),int(mc_seed))
        mc=st.session_state.get("mc_result")
        if isinstance(mc,pd.DataFrame) and not mc.empty:
            st.dataframe(mc,hide_index=True,use_container_width=True)
            st.bar_chart(mc.set_index("Acertos")[["Probabilidade simulada %"]])
            mean=float((mc["Acertos"]*mc["Simulações"]).sum()/mc["Simulações"].sum())
            st.metric("Média simulada de acertos",f"{mean:.3f}")
            st.caption("Em um sorteio aleatório, qualquer aposta fixa do mesmo tamanho possui a mesma distribuição teórica de acertos.")

    with lab_tab4:
        st.markdown("### Teste de uniformidade das frequências")
        stat,p=analytics.chi_square_uniformity()
        a,b=st.columns(2)
        a.metric("Qui-quadrado",f"{stat:.2f}")
        b.metric("p-value aproximado",f"{p:.4f}")
        if p < .05:
            st.warning("Nesta amostra, as frequências agregadas mostram um desvio estatístico em relação à igualdade perfeita. Isso não implica que o desvio persistirá nem que seja explorável para prever sorteios.")
        else:
            st.success("Nesta amostra, o teste não encontrou evidência forte de desvio global da frequência uniforme.")
        st.caption("É um diagnóstico aproximado das frequências agregadas. Sorteios sem reposição têm dependências internas, então o resultado deve ser interpretado com cautela.")

elif page == "💼 Meus Jogos":
    st.markdown("## Carteira de jogos")
    st.caption("Os dados são guardados em SQLite local quando possível. No Streamlit Cloud o armazenamento pode ser apagado em reinicializações ou novos deploys; use o backup para preservar informações importantes.")
    bets=REPO.list_bets(profile)
    if bets.empty:
        st.info("Nenhum jogo salvo neste perfil.")
    else:
        view=bets.copy()
        view["numbers"]=view.apply(lambda r: " - ".join(LOTTERIES[r["lottery"]].format_number(int(x)) for x in str(r["numbers"]).split(",")),axis=1)
        view=view.rename(columns={"id":"ID","created_at":"Data","lottery":"Loteria","numbers":"Números","contest":"Concurso","amount":"Valor","source":"Origem","notes":"Observações"})
        st.dataframe(view,hide_index=True,use_container_width=True,height=500)
        ids=bets["id"].astype(int).tolist()
        delete_id=st.selectbox("Excluir jogo",ids,format_func=lambda x:f"ID {x}")
        if st.button("Excluir selecionado"):
            REPO.delete_bet(delete_id)
            st.success("Jogo excluído.")
            st.rerun()

    st.markdown("### Adicionar jogo manualmente")
    manual=st.text_input("Números",placeholder="Ex.: 05 12 23 34 41 58")
    manual_nums=parse_numbers(manual,config)
    contest=st.text_input("Concurso (opcional)")
    notes=st.text_input("Observações",key="manual_notes")
    register=st.checkbox("Registrar o valor da aposta no orçamento",key="manual_budget")
    if st.button("Salvar jogo manual",type="primary"):
        try:
            config.validate_bet_size(len(manual_nums))
            game=tuple(sorted(manual_nums))
            REPO.add_bet(profile,lottery_name,game,contest,config.bet_price(len(game)),"manual",notes)
            if register:
                REPO.add_budget(profile,"Aposta",config.bet_price(len(game)),lottery_name,f"Jogo manual {contest}".strip())
            st.success("Jogo salvo.")
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### Backup do perfil")
    st.download_button("Baixar backup JSON",REPO.export_backup(profile),f"backup_loterias_lab_{re.sub(r'[^a-zA-Z0-9_-]+','_',profile)}.json","application/json",use_container_width=True)
    backup_file = st.file_uploader("Restaurar backup JSON", type=["json"], key="backup_restore")
    if backup_file is not None and st.button("Importar backup", key="backup_import"):
        try:
            payload = json.loads(backup_file.getvalue().decode("utf-8"))
            counts = REPO.import_backup(profile, payload)
            st.success(f"Backup importado: {counts['bets']} jogo(s), {counts['strategies']} estratégia(s), {counts['budget']} movimentação(ões).")
        except Exception as exc:
            st.error(f"Backup inválido: {exc}")

elif page == "✅ Conferidor & OCR":
    st.markdown("## Conferidor de jogos")
    result_source=st.radio("Resultado",["Digitar resultado","Último resultado oficial"],horizontal=True)
    result_nums=[]
    if result_source=="Digitar resultado":
        result_text=st.text_input("Dezenas sorteadas",placeholder=f"Informe {config.drawn_count} dezenas")
        result_nums=parse_numbers(result_text,config)
    else:
        if st.button("Buscar último resultado da CAIXA",type="primary"):
            try:
                official = fetch_official_contest(config)
                st.session_state.latest_official_result=(official.contest,official.draw)
            except Exception as exc:
                st.error(f"Falha ao consultar: {exc}")
        latest_data=st.session_state.get("latest_official_result")
        if latest_data:
            latest,draw=latest_data
            st.success(f"Concurso {latest}")
            st.markdown(balls_html(draw,config),unsafe_allow_html=True)
            result_nums=list(draw)

    input_mode=st.radio("Jogos para conferir",["Carteira salva","Colar jogos","Foto/OCR"],horizontal=True)
    games_to_check=[]
    if input_mode=="Carteira salva":
        games_to_check=[g for _,g,_ in get_saved_bets(profile,lottery_name,config)]
        st.caption(f"{len(games_to_check)} jogo(s) encontrados na carteira desta modalidade.")
    elif input_mode=="Colar jogos":
        pasted=st.text_area("Cole um jogo por linha")
        games_to_check=split_game_lines(pasted,config,None)
        st.caption(f"{len(games_to_check)} linha(s) reconhecida(s) como apostas válidas.")
    else:
        image_file=st.file_uploader("Foto do volante/comprovante",type=["png","jpg","jpeg","webp"])
        if image_file is not None:
            if Image is not None:
                img=Image.open(image_file)
                st.image(img,caption="Imagem enviada",use_container_width=True)
                if st.button("Executar OCR"):
                    if OCR_AVAILABLE:
                        try:
                            with st.spinner("Lendo a imagem..."):
                                txt=pytesseract.image_to_string(img,lang="por",config="--psm 6")
                            st.session_state.ocr_text=txt
                        except Exception as exc:
                            st.error(f"OCR não disponível neste ambiente: {exc}")
                    else:
                        st.error("As dependências de OCR não estão disponíveis.")
            else:
                st.error("Pillow não está disponível.")
        ocr_text=st.text_area("Texto reconhecido/editável",value=st.session_state.get("ocr_text",""),height=180)
        recognized=parse_numbers(ocr_text,config)
        st.caption("O OCR serve como assistência e pode capturar números que não pertencem à aposta. Revise antes de usar.")
        if recognized:
            st.write("Números identificados:"," ".join(config.format_number(n) for n in recognized))
        games_to_check=split_game_lines(ocr_text,config,None)

    if len(result_nums)==config.drawn_count and games_to_check:
        result_set=set(result_nums)
        rows=[]
        for i,g in enumerate(games_to_check,1):
            hits=sorted(set(g)&result_set)
            rows.append({"Jogo":i,"Números":" - ".join(config.format_number(n) for n in g),"Acertos":len(hits),"Acertos encontrados":" - ".join(config.format_number(n) for n in hits)})
        checked=pd.DataFrame(rows).sort_values("Acertos",ascending=False)
        st.dataframe(checked,hide_index=True,use_container_width=True)
        st.bar_chart(checked["Acertos"].value_counts().sort_index())
        prize=st.number_input("Prêmio recebido total (opcional)",min_value=0.0,value=0.0,step=1.0)
        if prize>0 and st.button("Registrar prêmio no orçamento"):
            REPO.add_budget(profile,"Prêmio",float(prize),lottery_name,"Prêmio informado no conferidor")
            st.success("Prêmio registrado.")
    elif result_nums and len(result_nums)!=config.drawn_count:
        st.warning(f"O resultado precisa ter exatamente {config.drawn_count} dezenas válidas.")
elif page == "💰 Orçamento":
    st.markdown("## Gestão de orçamento")
    st.write("Use esta área para acompanhar quanto foi registrado em apostas e quanto foi informado como prêmio. Defina um limite mensal para receber alertas de controle.")
    current_limit=float(REPO.get_setting(profile,"monthly_budget","100.0") or 100.0)
    monthly_limit=st.number_input("Limite mensal (R$)",min_value=0.0,value=current_limit,step=10.0)
    if st.button("Salvar limite"):
        REPO.set_setting(profile,"monthly_budget",str(monthly_limit))
        st.success("Limite salvo.")

    budget=REPO.list_budget(profile)
    if not budget.empty:
        budget["created_at_dt"]=pd.to_datetime(budget["created_at"],errors="coerce")
        now=pd.Timestamp.now()
        month_df=budget[(budget["created_at_dt"].dt.year==now.year)&(budget["created_at_dt"].dt.month==now.month)]
        spent=float(month_df.loc[month_df["kind"]=="Aposta","amount"].sum())
        prizes=float(month_df.loc[month_df["kind"]=="Prêmio","amount"].sum())
    else:
        month_df=pd.DataFrame()
        spent=prizes=0.0
    balance=prizes-spent
    remaining=max(0.0,monthly_limit-spent)
    a,b,c,d=st.columns(4)
    a.metric("Apostas no mês",currency_br(spent))
    b.metric("Prêmios informados",currency_br(prizes))
    c.metric("Resultado líquido",currency_br(balance))
    d.metric("Limite restante",currency_br(remaining))
    if monthly_limit>0:
        progress=min(1.0,spent/monthly_limit)
        st.progress(progress,text=f"{progress*100:.1f}% do limite mensal utilizado")
        if spent>monthly_limit:
            st.error("O total registrado ultrapassou o limite mensal definido.")
        elif spent>monthly_limit*.8:
            st.warning("Você já utilizou mais de 80% do limite mensal definido.")

    st.markdown("### Registrar movimentação")
    c1,c2,c3=st.columns(3)
    with c1:
        kind=st.selectbox("Tipo",["Aposta","Prêmio"])
    with c2:
        amount=st.number_input("Valor (R$)",min_value=0.0,value=0.0,step=1.0)
    with c3:
        movement_lottery=st.selectbox("Modalidade",[""]+list(LOTTERIES.keys()),index=0)
    movement_notes=st.text_input("Observação",key="budget_notes")
    if st.button("Registrar movimentação",type="primary",disabled=amount<=0):
        REPO.add_budget(profile,kind,float(amount),movement_lottery,movement_notes)
        st.success("Movimentação registrada.")
        st.rerun()

    st.markdown("### Histórico")
    budget=REPO.list_budget(profile)
    if budget.empty:
        st.info("Nenhuma movimentação registrada.")
    else:
        show=budget.rename(columns={"id":"ID","created_at":"Data","kind":"Tipo","amount":"Valor","lottery":"Loteria","notes":"Observação"})
        st.dataframe(show,hide_index=True,use_container_width=True,height=420)
        monthly=budget.copy()
        monthly["Mês"]=pd.to_datetime(monthly["created_at"]).dt.to_period("M").astype(str)
        pivot=monthly.pivot_table(index="Mês",columns="kind",values="amount",aggfunc="sum",fill_value=0)
        st.bar_chart(pivot)

    st.caption("O controle de orçamento é uma ferramenta de organização. Não há garantia de retorno e o valor de prêmios pode ser menor que o gasto acumulado.")

elif page == "🗂️ Dados":
    st.markdown("## Dados, validação e diagnóstico")
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Linhas recebidas",report.total_rows)
    c2.metric("Concursos válidos",report.valid_rows)
    c3.metric("Rejeitados",report.rejected_rows)
    c4.metric("Taxa válida",f"{report.success_rate*100:.1f}%")
    st.code(f"Fonte: {report.source_name}\nTipo: {report.source_kind}\nHash: {dataset_hash(report.draws)}\nCarregado: {report.loaded_at.isoformat(timespec='seconds')}\nVersão do app: {APP_VERSION}")

    if report.rejected_rows:
        rejects=pd.DataFrame({"Motivo":["Quantidade incorreta","Duplicados","Fora da faixa"],"Linhas":[report.rejected_wrong_count,report.rejected_duplicates,report.rejected_out_of_range]})
        st.dataframe(rejects,hide_index=True,use_container_width=True)
    if report.warnings:
        with st.expander("Avisos de importação"):
            for warning in report.warnings[:100]:
                st.write("•",warning)

    st.markdown("### Prévia da base validada")
    preview=pd.DataFrame([list(d) for d in active_draws],columns=[f"D{i+1}" for i in range(config.drawn_count)])
    for col in preview.columns:
        preview[col]=preview[col].map(config.format_number)
    st.dataframe(preview.tail(300),hide_index=True,use_container_width=True,height=500)
    st.download_button("Baixar amostra validada (CSV)",preview.to_csv(index=False).encode("utf-8-sig"),f"base_validada_{config.slug}.csv","text/csv",use_container_width=True)

    st.markdown("### Diagnóstico da API CAIXA")
    api_col1, api_col2 = st.columns([1, 2])
    with api_col1:
        if st.button("Testar API agora", use_container_width=True, key="api_health_test"):
            started = time.perf_counter()
            try:
                test_result = fetch_official_contest(config, timeout=8, attempts=2)
                elapsed = (time.perf_counter() - started) * 1000
                st.session_state.api_health = {
                    "ok": True,
                    "contest": test_result.contest,
                    "date": test_result.draw_date,
                    "elapsed_ms": elapsed,
                    "url": _caixa_api_url(config),
                }
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000
                st.session_state.api_health = {"ok": False, "error": str(exc), "elapsed_ms": elapsed, "url": _caixa_api_url(config)}
    with api_col2:
        st.code(_caixa_api_url(config), language=None)
    health = st.session_state.get("api_health")
    if health:
        if health.get("ok"):
            st.success(f"API respondendo · concurso {health['contest']} · {health['date']} · {health['elapsed_ms']:.0f} ms")
        else:
            st.error(f"API indisponível no teste ({health['elapsed_ms']:.0f} ms): {health.get('error','erro desconhecido')}")

    st.markdown("### Metadados e preços de referência")
    meta=[]
    for name,cfg in LOTTERIES.items():
        meta.append({"Modalidade":name,"Universo":f"{cfg.format_number(cfg.number_min)}–{cfg.format_number(cfg.number_max)}","Sorteados":cfg.drawn_count,"Aposta mínima":cfg.bet_min,"Aposta máxima":cfg.bet_max,"Preço mínimo de referência":currency_br(cfg.base_price),"Página CAIXA":cfg.info_url})
    st.dataframe(pd.DataFrame(meta),hide_index=True,use_container_width=True)

    with st.expander("Logs locais"):
        try:
            lines=LOG_PATH.read_text(encoding="utf-8",errors="ignore").splitlines()[-80:]
            st.code("\n".join(lines) if lines else "Sem logs.")
        except Exception:
            st.caption("Arquivo de log não disponível neste ambiente.")

elif page == "❓ FAQ":
    st.markdown("## FAQ — o que cada recurso faz")
    st.info("Esta página resume os recursos da V3. O objetivo é analisar e organizar dados de loteria de forma transparente, sem afirmar que padrões passados preveem o próximo sorteio.")

    faq_sections = [
        ("📚 Fontes de dados", "Você pode usar resultados recentes consultados no Portal Loterias/CAIXA, carregar CSV/TXT/Excel ou criar uma simulação reproduzível por seed. Todo arquivo passa por validação de quantidade, duplicidade e faixa das dezenas."),
        ("🏠 Dashboard", "Mostra indicadores rápidos da amostra: concursos analisados, dezenas mais frequentes e mais atrasadas, soma média, repetição média, mapa visual e uma leitura automática do último concurso."),
        ("🔥 Frequências e tendências", "Compara frequência histórica e recente, mostra desvio em relação ao valor esperado, intervalo médio, atraso atual, maior intervalo observado e uma classificação de frequência recente acima/próxima/abaixo do histórico."),
        ("🧩 Padrões", "Analisa paridade, repetição do concurso anterior, sequências consecutivas, faixas numéricas, distribuição da soma, pares e trios recorrentes e um índice de raridade histórica."),
        ("🕸️ Rede de coocorrência", "Cria um grafo em que dezenas são nós e conexões representam pares que apareceram juntos. Ele serve para explorar coocorrência histórica, não para inferir causalidade ou previsão."),
        ("🎯 Gerador aleatório", "Gera jogos sem usar frequência histórica. Dentro do mesmo tamanho de aposta, combinações válidas têm a mesma probabilidade matemática de serem sorteadas."),
        ("🎯 Gerador por perfil histórico", "Monta a quantidade definida de números dos grupos mais frequentes, neutros e menos frequentes. Os grupos são disjuntos para evitar a mesma dezena em duas categorias."),
        ("🎛️ Gerador personalizado", "Permite obrigar ou excluir dezenas e aplicar filtros de pares, soma, consecutivos, repetição do último concurso, faixas ocupadas e diversidade entre os jogos."),
        ("🔀 Diversidade entre jogos", "Reduz a sobreposição entre jogos de uma mesma geração. O modo alto tenta repetir menos dezenas entre combinações, sem prometer maior chance de prêmio."),
        ("📐 Cobertura de pares e trios", "Mede quantos pares e trios diferentes aparecem no conjunto de jogos comparados ao universo formado pelas dezenas utilizadas. É uma métrica combinatória de diversidade."),
        ("🧮 Fechamentos/desdobramentos", "Parte de um conjunto-base maior e seleciona jogos buscando maximizar a cobertura de pares ou trios dentro de um limite de quantidade ou orçamento. Para bases enormes, o algoritmo trabalha com uma amostra de candidatos para manter o app responsivo."),
        ("💵 Preço estimado", "O app usa os preços mínimos de referência da CAIXA e a relação combinatória para apostas com mais dezenas nas modalidades suportadas. Os valores podem mudar; confira sempre o Portal Loterias antes de apostar."),
        ("🧪 Backtest", "Reproduz uma estratégia em concursos passados usando apenas dados anteriores ao concurso testado. Compara a média de acertos do perfil com jogos aleatórios equivalentes. É avaliação histórica, não prova de poder preditivo."),
        ("⚖️ Comparador de estratégias", "Executa perfis diferentes no mesmo período de backtest e apresenta os resultados lado a lado. As diferenças podem ser ruído amostral e não devem ser lidas como ranking de chance futura."),
        ("🎲 Monte Carlo", "Simula muitos resultados para mostrar a distribuição de acertos de uma aposta de determinado tamanho. Ajuda a visualizar a variabilidade esperada em sorteios aleatórios."),
        ("📊 Teste qui-quadrado", "Compara as frequências agregadas das dezenas com uma distribuição uniforme. O p-value é diagnóstico estatístico aproximado e não transforma desvios históricos em estratégia de previsão."),
        ("🧭 Índice de equilíbrio histórico", "Resume o quanto um jogo se parece com padrões frequentes de paridade, soma, consecutivos, faixas e repetição. Ele mede similaridade histórica, não probabilidade de premiação."),
        ("✨ Índice de raridade", "Resume o quão incomum é o perfil de um concurso ou jogo dentro da amostra analisada. Um resultado raro não é impossível e não é evidência de fraude."),
        ("🤖 Assistente de leitura", "Gera uma explicação automática em português sobre paridade, soma, consecutivos, faixas, repetição e raridade. É um resumo determinístico dos cálculos do app, não uma previsão por IA."),
        ("💼 Meus Jogos", "Salva jogos manualmente ou vindos do gerador/fechamento, com data, origem, valor e observações. Também permite baixar um backup JSON do perfil."),
        ("✅ Conferidor", "Compara jogos salvos ou colados com um resultado digitado ou com o último resultado consultado na CAIXA e mostra quantos números coincidiram."),
        ("📷 OCR de foto", "Tenta extrair texto de uma foto de volante/comprovante usando Tesseract. OCR pode errar ou capturar números que não são apostas; o texto fica editável e precisa ser revisado."),
        ("💰 Orçamento", "Registra valores de apostas e prêmios, define limite mensal, mostra gasto, prêmio informado, resultado líquido e alerta de aproximação/ultrapassagem do limite."),
        ("⭐ Estratégias favoritas", "Salva os parâmetros de uma configuração de gerador para referência futura. Nesta versão o favorito é armazenado e listado; ele não altera a probabilidade matemática do sorteio."),
        ("🔐 Perfil e senha opcional", "O perfil local separa dados no SQLite. Se você definir APP_PASSWORD nos Secrets do Streamlit, o app também exige uma senha global antes de abrir. Isso não substitui autenticação corporativa multiusuário."),
        ("💾 Persistência", "O app usa SQLite local. Em computador próprio ele persiste normalmente; no Streamlit Cloud o disco pode ser efêmero em reinicializações/deploys. Por isso existe exportação de backup."),
        ("🔴 Resultado oficial automático", "Ao abrir o app, uma única chamada cacheada consulta o último concurso da modalidade diretamente no endpoint JSON do Portal Loterias/CAIXA. O card mostra concurso, data, dezenas, situação de acumulação, estimativa do próximo prêmio e premiação por faixa. O cache dura 5 minutos e há um botão para forçar atualização."),
        ("🔄 Sincronização do histórico CAIXA", "A sincronização do histórico é separada do resultado ao vivo: você escolhe quantos concursos deseja carregar para as análises. Cada concurso histórico exige consulta própria ao endpoint, então a opção automática vem desligada para evitar centenas de chamadas desnecessárias."),
        ("🧾 Versionamento da base", "A página Dados mostra um hash SHA-256 reduzido da sequência de concursos carregada, horário, origem e versão do app. Isso ajuda a identificar qual base estava ativa numa análise."),
        ("🛠️ Logs e observabilidade", "Erros e eventos relevantes são gravados em log local quando o ambiente permite. A página Dados mostra as últimas linhas para facilitar diagnóstico de deploy."),
        ("📱 Uso no celular", "A interface possui CSS responsivo, bolas e grades adaptáveis. O Streamlit Cloud não oferece, por si só, um modo PWA offline completo com service worker confiável; por isso a V3 prioriza uma experiência web responsiva em vez de prometer offline que pode não funcionar."),
        ("🎰 Isso prevê a loteria?", "Não. Frequência, atraso, pares, trios, tendências e índices são descrições do histórico. Em sorteios aleatórios regulares, o fato de uma dezena ter saído muito ou pouco não cria uma obrigação de ela sair no próximo concurso."),
    ]
    for title, body in faq_sections:
        with st.expander(title):
            st.write(body)

    st.markdown("### Modalidades e preços mínimos usados como referência")
    faq_price=pd.DataFrame([{"Modalidade":n,"Aposta mínima":c.bet_min,"Preço mínimo":currency_br(c.base_price),"Link oficial":c.info_url} for n,c in LOTTERIES.items()])
    st.dataframe(faq_price,hide_index=True,use_container_width=True)
    st.caption("Preços de referência configurados na V3 em setembro de 2026. Como regras e preços podem mudar, confira o Portal Loterias/CAIXA antes de qualquer aposta.")

    st.markdown("### Limitações técnicas conhecidas")
    st.write(
        "**Persistência em nuvem:** o SQLite local pode ser apagado pelo ciclo de vida do Streamlit Cloud; use backups.  "
        "\n**PWA/offline real:** não é habilitado nesta versão porque o Streamlit Cloud não garante o escopo de service worker necessário.  "
        "\n**OCR:** depende de Tesseract e da qualidade da foto; revise o texto.  "
        "\n**API CAIXA:** a integração usa o endpoint JSON público consumido pelo Portal Loterias (`servicebus2.caixa.gov.br/portaldeloterias/api`). Ele não é uma API contratualmente versionada para terceiros, portanto pode mudar ou oscilar; por isso arquivo e simulação permanecem como fallback."
    )
