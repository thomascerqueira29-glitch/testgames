# Loterias — Laboratório Estatístico v2

Aplicação Streamlit para análise histórica de Mega-Sena, Lotofácil, Quina e Lotomania.

## O que mudou

- regras de sorteio separadas das regras de aposta;
- Lotomania corrigida para universo `00–99`, 20 dezenas sorteadas e aposta de 50 números;
- importação de CSV/TXT/XLSX/XLS com detecção de separador e validação de linhas;
- consulta experimental de resultados recentes pelo endpoint público usado pelo Portal Loterias/CAIXA;
- simulações reproduzíveis por seed;
- frequência observada x esperada, desvio, atraso e intervalo médio;
- grupos mais frequentes, neutros e menos frequentes sem sobreposição;
- padrões de pares/ímpares, soma, consecutivos e repetição do concurso anterior;
- pares e trios mais recorrentes;
- comparação de janela recente com histórico;
- backtest temporal sem vazamento de dados futuros;
- gerador por quantidade exata de grupos, com tamanho válido de aposta por modalidade;
- histórico de combinações no `session_state` e exportação CSV;
- cache para arquivos e dados oficiais;
- testes automatizados do núcleo da aplicação.

## Instalação

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Executar

```bash
streamlit run app.py
```

## Fontes de dados

### Arquivo
Use CSV, TXT, XLSX ou XLS. O importador lê sem assumir cabeçalho, tenta detectar o separador e identifica as colunas de dezenas. Linhas que não tenham exatamente a quantidade exigida, que contenham duplicidades ou números inválidos são rejeitadas.

### Dados oficiais CAIXA
A integração usa o endpoint público consumido pelo Portal Loterias. Como esse endpoint não possui documentação pública estável, a integração é apresentada como experimental e possui cache de uma hora.

### Simulação
Gera concursos aleatórios válidos. Uma seed opcional permite reproduzir exatamente a mesma amostra.

## Interpretação

O aplicativo é um laboratório estatístico. Frequências, atrasos, pares, trios e demais padrões descrevem o histórico e não tornam uma dezena matematicamente mais provável no próximo sorteio.

## Testes

```bash
pytest
```
