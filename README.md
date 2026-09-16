Loterias Lab V3.1 — integração automática CAIXA

Aplicação Streamlit para análise histórica, simulação, geração de combinações, fechamentos, backtesting, Monte Carlo, conferência e organização de jogos das modalidades Mega-Sena, Lotofácil, Quina e Lotomania.

O aplicativo descreve dados históricos e não afirma prever sorteios. Frequência, atraso, coocorrência e índices históricos não aumentam, por si só, a probabilidade matemática de uma combinação futura.

Principais recursos

Dashboard com métricas, mapa visual, tendência recente e leitura automática.

Frequência observada x esperada, atrasos, intervalos e janela móvel.

Paridade, soma, faixas, consecutivos, repetição, pares e trios.

Rede interativa de coocorrência.

Gerador aleatório, por perfil e personalizado com filtros.

Diversidade entre jogos e métricas de cobertura de pares/trios.

Fechamentos/desdobramentos com limite por quantidade ou orçamento.

Backtest temporal, comparação de estratégias e Monte Carlo.

Teste qui-quadrado de uniformidade agregada.

Índice de equilíbrio e índice de raridade histórica.

Carteira de jogos com SQLite local e backup/restauração JSON.

Conferidor manual ou com último resultado oficial.

OCR de foto de volante/comprovante via Tesseract.

Controle de orçamento, gastos e prêmios informados.

Estratégias favoritas.

Perfil local e senha global opcional via Streamlit Secrets.

Hash da base, logs locais e diagnóstico.

Página FAQ explicando cada recurso e suas limitações.

Resultado oficial mais recente consultado automaticamente na API JSON do Portal Loterias/CAIXA.

Card ao vivo com concurso, data, dezenas, acumulação, próximo prêmio estimado e premiação por faixa.

Sincronização separada do histórico oficial para evitar centenas de chamadas na abertura.

Diagnóstico da API na página Dados, com teste de disponibilidade e tempo de resposta.

Estrutura para o Streamlit Cloud

Coloque estes arquivos na raiz do repositório:

app.py
requirements.txt
packages.txt
.streamlit/
  config.toml

No Streamlit Community Cloud, configure o Main file path como:

app.py

Depois faça o deploy/reboot.

Instalação local

Python

Recomendado: Python 3.11 ou 3.12.

python -m venv .venv

Windows:

.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py

Linux/macOS:

source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py

OCR local

Para usar a página Foto/OCR, instale também o Tesseract OCR com o idioma português.

Em Debian/Ubuntu:

sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-por

No Streamlit Cloud, packages.txt já solicita esses pacotes.

Senha opcional

Para proteger o aplicativo com uma senha global, crie a secret:

APP_PASSWORD = "sua-senha"

Sem essa secret, o aplicativo abre normalmente.

Persistência

A carteira, orçamento e favoritos usam SQLite local (.lottery_lab.db). Em execução local o arquivo persiste normalmente. No Streamlit Cloud, o filesystem pode ser efêmero após reinicializações e novos deploys. Use o botão Baixar backup JSON em Meus Jogos e restaure o arquivo quando necessário.

Dados oficiais e API automática da CAIXA

A V3.1 consulta diretamente o endpoint JSON do Portal Loterias/CAIXA:

https://servicebus2.caixa.gov.br/portaldeloterias/api/{modalidade}

Modalidades configuradas:

megasena
lotofacil
quina
lotomania

Para um concurso específico, o formato utilizado é:

https://servicebus2.caixa.gov.br/portaldeloterias/api/{modalidade}/{concurso}

Como a atualização funciona

Ao abrir o aplicativo, é feita apenas uma chamada automática para a modalidade selecionada.

O retorno é armazenado em cache por 5 minutos.

O card Resultado oficial CAIXA mostra concurso, data, dezenas, acumulação, estimativa do próximo prêmio, local e premiação por faixa.

O botão ↻ Atualizar limpa o cache e força uma nova consulta.

A sincronização de histórico é separada: o usuário escolhe quantos concursos carregar para as análises.

O carregador possui timeout, tentativas automáticas e fallback para arquivo/simulação caso o serviço esteja temporariamente indisponível.

A página Dados possui um botão Testar API agora, exibindo concurso retornado e tempo de resposta.

O endpoint pertence ao domínio da CAIXA e é consumido pelo Portal Loterias, mas não é tratado aqui como uma API pública versionada/contratada para terceiros. Por isso ele pode mudar ou oscilar sem aviso. CSV/Excel e simulação continuam disponíveis como alternativas.

Preços configurados na V3

Referência verificada em setembro de 2026:

Mega-Sena: aposta mínima R$ 6,00.

Lotofácil: aposta mínima R$ 3,50.

Quina: aposta mínima R$ 3,00.

Lotomania: aposta única R$ 3,00.

O app calcula apostas maiores de Mega-Sena, Lotofácil e Quina pela quantidade equivalente de combinações simples. Confira sempre o Portal Loterias antes de apostar, pois preços podem mudar.

Limitações importantes

Não existe previsão garantida de sorteios.

O índice de equilíbrio mede semelhança com padrões históricos, não chance futura.

O índice de raridade mede quão incomum é um perfil dentro da amostra, não fraude.

O OCR pode errar; revise o texto antes de importar.

O SQLite do Streamlit Cloud não é armazenamento permanente.

Streamlit Cloud não oferece um PWA offline completo confiável com service worker; a V3 é responsiva para celular, mas permanece uma aplicação web online.

Arquivos de apoio

smoke_test.py: valida o núcleo sem iniciar o Streamlit, incluindo o parser do JSON da CAIXA e Lotomania com 00.

packages.txt: instala o Tesseract no deploy Linux.

.streamlit/config.toml: configura aparência e upload.

Novidades da V3.2

Nova página Resultados CAIXA com o último resultado oficial de Mega-Sena, Lotofácil, Quina, Lotomania, Timemania, Dia de Sorte, Dupla Sena, Super Sete e +Milionária.

Cards redesenhados com identidade visual por modalidade, status de acumulação, concurso, data, dezenas, prêmio estimado e próximo sorteio.

Tratamento visual específico para 2º sorteio da Dupla Sena, trevos da +Milionária, Mês da Sorte e Time do Coração.

Premiação e arrecadação disponíveis em expansores individuais.

Consulta paralela e cache de 5 minutos para evitar lentidão e excesso de chamadas à CAIXA.

Dashboard com cards de indicadores mais modernos e responsivos.
