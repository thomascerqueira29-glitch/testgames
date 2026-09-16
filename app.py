import streamlit as st
import pandas as pd
import random
import itertools
from collections import Counter
from typing import Tuple, List

# ==========================================
# Classe de Análise Refatorada
# ==========================================
class AnalisadorLoteriaCaixa:
    
    # Constante de classe (não muda por instância)
    LOTERIAS = {
        'Mega-Sena': {'total_bolas': 60, 'bolas_sorteadas': 6},
        'Lotofácil': {'total_bolas': 25, 'bolas_sorteadas': 15},
        'Quina': {'total_bolas': 80, 'bolas_sorteadas': 5},
        'Lotomania': {'total_bolas': 100, 'bolas_sorteadas': 20}
    }

    def __init__(self):
        self.historico: List[List[int]] = []
        self.loteria_atual: dict = {}
        self.nome_loteria_atual: str = ""

    def configurar_loteria(self, nome_loteria: str) -> None:
        self.nome_loteria_atual = nome_loteria
        self.loteria_atual = self.LOTERIAS[nome_loteria]

    def carregar_simulacao(self, qtd_sorteios: int) -> None:
        bolas_possiveis = list(range(1, self.loteria_atual['total_bolas'] + 1))
        self.historico = [
            random.sample(bolas_possiveis, self.loteria_atual['bolas_sorteadas']) 
            for _ in range(qtd_sorteios)
        ]

    def carregar_dados_reais(self, df: pd.DataFrame) -> None:
        """Lê os dados usando vetorização (mais rápido que iterrows)."""
        self.historico = []
        limite_bolas = self.loteria_atual['total_bolas']
        qtd_sorteio = self.loteria_atual['bolas_sorteadas']
        
        # Converte para numérico e remove nulos, lidando com os dados como matriz
        matriz = df.apply(pd.to_numeric, errors='coerce').values
        
        for linha in matriz:
            # Filtra NaN e converte para int
            sorteio = [int(num) for num in linha if pd.notna(num) and 1 <= num <= limite_bolas]
            # Valida se a linha tem o mínimo de bolas para ser um sorteio válido
            if len(sorteio) >= qtd_sorteio:
                self.historico.append(sorteio[:qtd_sorteio])

    def obter_estatisticas(self) -> Tuple[List[tuple], List[tuple]]:
        if not self.historico:
            return [], []

        # Uso de itertools economiza MUITA memória em históricos grandes
        todos_numeros = itertools.chain.from_iterable(self.historico)
        contagem = Counter(todos_numeros)

        # Garante que números não sorteados apareçam com valor 0
        for i in range(1, self.loteria_atual['total_bolas'] + 1):
            contagem.setdefault(i, 0)

        numeros_ordenados = contagem.most_common()
        quentes = numeros_ordenados[:15] # Expandido para top 15 para ter folga na geração
        frios = numeros_ordenados[-15:][::-1] # Syntax sugar para inverter a lista

        return quentes, frios

    def gerar_palpite_inteligente(self, perc_quentes: float, perc_frios: float) -> List[int]:
        if not self.historico:
            return []

        quentes, frios = self.obter_estatisticas()
        lista_quentes = [num[0] for num in quentes]
        lista_frios = [num[0] for num in frios]
        
        qtd_sorteio = self.loteria_atual['bolas_sorteadas']
        
        # Calcula proporções baseadas na escolha do usuário
        qtd_quentes = max(0, int(qtd_sorteio * perc_quentes))
        qtd_frios = max(0, int(qtd_sorteio * perc_frios))
        
        # Ajusta para não ultrapassar o total de bolas sorteadas
        while (qtd_quentes + qtd_frios) > qtd_sorteio:
            if qtd_frios > 0: qtd_frios -= 1
            elif qtd_quentes > 0: qtd_quentes -= 1

        palpite = set()
        
        # random.sample evita duplicatas de forma eficiente sem precisar de loop while
        palpite.update(random.sample(lista_quentes, min(qtd_quentes, len(lista_quentes))))
        
        # Adiciona frios, filtrando para não repetir os que já entraram
        frios_disponiveis = list(set(lista_frios) - palpite)
        palpite.update(random.sample(frios_disponiveis, min(qtd_frios, len(frios_disponiveis))))
            
        # Completa com aleatórios
        bolas_possiveis = set(range(1, self.loteria_atual['total_bolas'] + 1))
        aleatorios_disponiveis = list(bolas_possiveis - palpite)
        
        qtd_faltante = qtd_sorteio - len(palpite)
        palpite.update(random.sample(aleatorios_disponiveis, qtd_faltante))

        return sorted(list(palpite))


# ==========================================
# Interface Streamlit
# ==========================================
st.set_page_config(page_title="Analisador de Loterias", page_icon="🍀", layout="wide")

st.title("🍀 Analisador Inteligente de Loterias")
st.warning("⚠️ **Aviso:** Loterias são jogos de azar. Esta ferramenta utiliza estatística baseada em histórico para fins de estudo e entretenimento, não havendo garantias de acerto.")

# Inicializa o analisador no Session State para persistência completa
if 'analisador' not in st.session_state:
    st.session_state.analisador = AnalisadorLoteriaCaixa()

analisador = st.session_state.analisador

# --- BARRA LATERAL (MENU) ---
st.sidebar.header("⚙️ Configurações")

# Dropdown de loterias
nova_loteria = st.sidebar.selectbox("Escolha a Loteria:", list(analisador.LOTERIAS.keys()))

# Limpa o histórico se a loteria mudar
if nova_loteria != analisador.nome_loteria_atual:
    analisador.configurar_loteria(nova_loteria)
    analisador.historico = [] # Reseta o histórico

st.sidebar.divider()
fonte_dados = st.sidebar.radio("Fonte de Dados dos Sorteios:", ("Gerar Simulação Aleatória", "Fazer Upload de CSV"))

if fonte_dados == "Gerar Simulação Aleatória":
    qtd = st.sidebar.slider("Quantidade de sorteios passados:", 100, 5000, 1000, step=100)
    if st.sidebar.button("Carregar Simulação", use_container_width=True):
        analisador.carregar_simulacao(qtd)
        st.sidebar.success(f"{qtd} sorteios simulados!")

elif fonte_dados == "Fazer Upload de CSV":
    with st.sidebar.expander("ℹ️ Como formatar o CSV?"):
        st.write("Deixe apenas os números dos sorteios nas colunas. Remova colunas de datas, nomes de ganhadores e prêmios. Apenas linhas com números.")
    
    arquivo = st.sidebar.file_uploader("Envie o CSV com histórico", type=['csv'])
    if arquivo is not None:
        try:
            df = pd.read_csv(arquivo)
            analisador.carregar_dados_reais(df)
            st.sidebar.success(f"{len(analisador.historico)} sorteios carregados!")
        except Exception as e:
            st.sidebar.error(f"Erro ao ler arquivo: {e}")

st.sidebar.divider()
st.sidebar.subheader("🎯 Estratégia do Palpite")
perc_quentes = st.sidebar.slider("% de Números Quentes", 0.0, 1.0, 0.4, 0.1)
perc_frios = st.sidebar.slider("% de Números Frios", 0.0, 1.0, 0.2, 0.1)
st.sidebar.caption(f"*O restante ({int((1 - perc_quentes - perc_frios)*100)}%) será preenchido com números aleatórios.*")

# --- CORPO PRINCIPAL ---
if analisador.historico:
    st.divider()
    st.subheader(f"📊 Estatísticas da {analisador.nome_loteria_atual}")
    
    quentes, frios = analisador.obter_estatisticas()
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 🔥 Top 10 Mais Sorteados (Quentes)")
        df_quentes = pd.DataFrame(quentes[:10], columns=['Número', 'Frequência'])
        st.dataframe(df_quentes.set_index('Número'), use_container_width=True)

    with col2:
        st.markdown("### ❄️ Top 10 Menos Sorteados (Frios)")
        df_frios = pd.DataFrame(frios[:10], columns=['Número', 'Frequência'])
        st.dataframe(df_frios.set_index('Número'), use_container_width=True)
        
    st.divider()
    st.subheader("🔮 Gerador de Palpite Inteligente")
    
    if st.button("GERAR MEU JOGO AGORA", type="primary", use_container_width=True):
        palpite = analisador.gerar_palpite_inteligente(perc_quentes, perc_frios)
        palpite_formatado = "  -  ".join([f"{num:02d}" for num in palpite])
        
        st.success("🎯 **Seu jogo recomendado:**")
        st.markdown(f"<h2 style='text-align: center; color: #1f77b4;'>{palpite_formatado}</h2>", unsafe_allow_html=True)
        
        # Botão para baixar o palpite (Funcionalidade nova UX)
        txt_download = f"Loteria: {analisador.nome_loteria_atual}\nPalpite gerado: {palpite_formatado}"
        st.download_button("📥 Baixar Palpite (TXT)", txt_download, file_name="meu_palpite.txt", use_container_width=True)
        
        st.balloons()
else:
    st.info("👈 Por favor, configure a fonte de dados na barra lateral e carregue o histórico para começar a análise.")
