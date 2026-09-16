import streamlit as st
import pandas as pd
import random
from collections import Counter

# ==========================================
# Classe de Análise
# ==========================================
class AnalisadorLoteriaCaixa:
    def __init__(self):
        self.loterias = {
            'Mega-Sena': {'total_bolas': 60, 'bolas_sorteadas': 6},
            'Lotofácil': {'total_bolas': 25, 'bolas_sorteadas': 15},
            'Quina': {'total_bolas': 80, 'bolas_sorteadas': 5},
            'Lotomania': {'total_bolas': 100, 'bolas_sorteadas': 20}
        }
        self.historico = []
        self.loteria_atual = None

    def configurar_loteria(self, nome_loteria):
        self.loteria_atual = self.loterias[nome_loteria]

    def carregar_simulacao(self, qtd_sorteios):
        bolas_possiveis = list(range(1, self.loteria_atual['total_bolas'] + 1))
        self.historico = [
            random.sample(bolas_possiveis, self.loteria_atual['bolas_sorteadas']) 
            for _ in range(qtd_sorteios)
        ]

    def carregar_dados_reais(self, df):
        # Tenta inferir colunas que contêm os números (ignora data, concurso, etc)
        # Assumimos que o usuário limpou o CSV ou as colunas se chamam Bola1, Bola2...
        self.historico = []
        for index, row in df.iterrows():
            # Pega apenas valores numéricos da linha
            sorteio = pd.to_numeric(row, errors='coerce').dropna().astype(int).tolist()
            # Filtra apenas os números que fazem sentido para a loteria
            sorteio = [num for num in sorteio if 1 <= num <= self.loteria_atual['total_bolas']]
            if len(sorteio) >= self.loteria_atual['bolas_sorteadas']:
                # Pega as primeiras X bolas equivalentes ao sorteio
                self.historico.append(sorteio[:self.loteria_atual['bolas_sorteadas']])

    def obter_estatisticas(self):
        if not self.historico:
            return [], []

        todos_numeros = [numero for sorteio in self.historico for numero in sorteio]
        contagem = Counter(todos_numeros)

        # Garante que números não sorteados apareçam com valor 0
        for i in range(1, self.loteria_atual['total_bolas'] + 1):
            if i not in contagem:
                contagem[i] = 0

        numeros_ordenados = contagem.most_common()
        quentes = numeros_ordenados[:10]
        frios = numeros_ordenados[-10:]
        frios.reverse()

        return quentes, frios

    def gerar_palpite_inteligente(self):
        if not self.historico:
            return []

        quentes, frios = self.obter_estatisticas()
        lista_quentes = [num[0] for num in quentes]
        lista_frios = [num[0] for num in frios]
        
        qtd_sorteio = self.loteria_atual['bolas_sorteadas']
        qtd_quentes = max(1, int(qtd_sorteio * 0.4))
        qtd_frios = max(1, int(qtd_sorteio * 0.2))

        palpite = set()
        
        while len(palpite) < qtd_quentes:
            palpite.add(random.choice(lista_quentes))
            
        while len(palpite) < (qtd_quentes + qtd_frios):
            palpite.add(random.choice(lista_frios))
            
        bolas_possiveis = list(range(1, self.loteria_atual['total_bolas'] + 1))
        while len(palpite) < qtd_sorteio:
            palpite.add(random.choice(bolas_possiveis))

        return sorted(list(palpite))

# ==========================================
# Interface Streamlit
# ==========================================
st.set_page_config(page_title="Analisador de Loterias", page_icon="🍀", layout="wide")

st.title("🍀 Analisador Inteligente de Loterias")
st.markdown("*Use estatísticas de números quentes e frios para gerar palpites.*")
st.warning("⚠️ **Aviso:** Loterias são jogos de azar. Esta ferramenta utiliza estatística baseada em histórico para fins de estudo e entretenimento, não havendo garantias de acerto.")

# Instancia o analisador
analisador = AnalisadorLoteriaCaixa()

# --- BARRA LATERAL (MENU) ---
st.sidebar.header("⚙️ Configurações")
loteria_selecionada = st.sidebar.selectbox(
    "Escolha a Loteria:", 
    list(analisador.loterias.keys())
)
analisador.configurar_loteria(loteria_selecionada)

fonte_dados = st.sidebar.radio(
    "Fonte de Dados dos Sorteios:",
    ("Gerar Simulação Aleatória", "Fazer Upload de CSV (Dados Reais)")
)

dados_carregados = False

if fonte_dados == "Gerar Simulação Aleatória":
    qtd = st.sidebar.slider("Quantidade de sorteios passados:", 10, 5000, 1000)
    if st.sidebar.button("Carregar Simulação"):
        analisador.carregar_simulacao(qtd)
        st.session_state['historico'] = analisador.historico
        st.sidebar.success(f"{qtd} sorteios simulados!")

elif fonte_dados == "Fazer Upload de CSV (Dados Reais)":
    arquivo = st.sidebar.file_uploader("Envie o CSV com histórico (Apenas números nas colunas)", type=['csv'])
    if arquivo is not None:
        try:
            df = pd.read_csv(arquivo)
            analisador.carregar_dados_reais(df)
            st.session_state['historico'] = analisador.historico
            st.sidebar.success(f"{len(analisador.historico)} sorteios carregados do arquivo!")
        except Exception as e:
            st.sidebar.error(f"Erro ao ler arquivo: {e}")

# Recupera o histórico da sessão (para não perder ao recarregar a tela)
if 'historico' in st.session_state and st.session_state['historico']:
    analisador.historico = st.session_state['historico']
    dados_carregados = True

# --- CORPO PRINCIPAL ---
if dados_carregados:
    st.divider()
    st.subheader(f"📊 Estatísticas da {loteria_selecionada}")
    
    quentes, frios = analisador.obter_estatisticas()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🔥 Top 10 Mais Sorteados (Quentes)")
        df_quentes = pd.DataFrame(quentes, columns=['Número', 'Frequência'])
        st.dataframe(df_quentes.set_index('Número'), use_container_width=True)

    with col2:
        st.markdown("### ❄️ Top 10 Menos Sorteados (Frios)")
        df_frios = pd.DataFrame(frios, columns=['Número', 'Frequência'])
        st.dataframe(df_frios.set_index('Número'), use_container_width=True)
        
    st.divider()
    
    st.subheader("🔮 Gerador de Palpite Inteligente")
    st.markdown(f"O palpite é gerado pegando ~40% de números quentes, ~20% de frios e completando com aleatórios.")
    
    if st.button("GERAR MEU JOGO AGORA", type="primary", use_container_width=True):
        palpite = analisador.gerar_palpite_inteligente()
        
        # Formata o palpite para ficar visualmente bonito
        palpite_formatado = "  -  ".join([f"{num:02d}" for num in palpite])
        
        st.success("🎯 **Seu jogo recomendado:**")
        st.markdown(f"<h2 style='text-align: center; color: #1f77b4;'>{palpite_formatado}</h2>", unsafe_allow_html=True)
        st.balloons()

else:
    st.info("👈 Por favor, configure a fonte de dados na barra lateral para começar a análise.")
