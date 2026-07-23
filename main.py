import pdfplumber
import pandas as pd
import requests
import re
import time
from datetime import datetime
from functools import reduce
from operator import mul
from io import BytesIO
import os
import sqlite3
import socket

import streamlit as st

# =====================================================
# MAPA DE MESES + VARIACOES DE COLUNAS
# =====================================================
MESES = {
    "jan": "01", "fev": "02", "mar": "03", "abr": "04", "mai": "05", "jun": "06",
    "jul": "07", "ago": "08", "set": "09", "out": "10", "nov": "11", "dez": "12"
}

COLUMN_VARIANTS = {
    "Mês referência/Ano cobrança": ["Referência", "Dta.Ref"],
    "Pagamento": ["Pagamento", "Data.Pagto"],
    "Pontos": ["Pontos", "Ptos./Qtd."],
    "Preço Pto.": ["Preço Pto.", "Prç.Pto./Vlr."],
    "Vencimento": ["Vencimento", "Data Vencimento", "Data.Venc."]
}

# =====================================================
# CORES DA MARCA
# =====================================================
PRIMARY_GOLD = "#9C7A33"
LIGHT_GOLD = "#B08A3C"
DARK_GOLD = "#7A5F28"
DARK_TEXT = "#2C2C2C"
LIGHT_BG = "#F8F5F0"
WHITE_BG = "#FFFFFF"

# =====================================================
# UTILITÁRIOS (mantidos exatamente iguais)
# =====================================================
def yyyymm_para_data_bacen(yyyymm):
    if not yyyymm or len(yyyymm) != 6 or not yyyymm.isdigit():
        return None
    ano = yyyymm[:4]
    mes = yyyymm[4:]
    return f"01/{mes}/{ano}"

def converter_referencia_yyyymm(valor):
    try:
        valor = str(valor).strip().lower()
        mes, ano = re.split(r"[/\s]+", valor)
        return f"{ano}{MESES[mes[:3]]}"
    except Exception:
        return ""

def converter_referencia_yyyymm_pagamento(valor):
    try:
        if pd.isna(valor): return ""
        data = pd.to_datetime(valor, dayfirst=True, errors="coerce")
        return data.strftime("%Y%m") if pd.notna(data) else ""
    except Exception:
        return ""

def eh_mes_ano(valor):
    if pd.isna(valor): return False
    return bool(re.match(r"^(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/\d{4}$", str(valor).lower().strip()))

def tratar_postes(valor):
    if pd.isna(valor): return 0
    texto = str(valor).replace(" ", "").replace(".", "").replace(",", "")
    return int(texto) if texto.isdigit() else 0

def tratar_numero(valor):
    if pd.isna(valor): return 0.0
    texto = str(valor).replace("R$", "").replace(".", "").replace(",", ".").strip()
    try: return float(texto)
    except: return 0.0

def add_meses_yyyymm(yyyymm, meses):
    if not yyyymm or len(yyyymm) != 6: return ""
    y = int(yyyymm[:4])
    m = int(yyyymm[4:])
    new_m = m + meses
    add_y = (new_m - 1) // 12
    new_m = (new_m - 1) % 12 + 1
    return f"{y + add_y:04d}{new_m:02d}"

def calcular_meses_diff(yyyymm1, yyyymm2):
    if not yyyymm1 or not yyyymm2: return 0
    y1, m1 = int(yyyymm1[:4]), int(yyyymm1[4:])
    y2, m2 = int(yyyymm2[:4]), int(yyyymm2[4:])
    return (y2 - y1) * 12 + (m2 - m1)

def carregar_icgj():
    try:
        base_dir = os.path.dirname(__file__)
        caminho = os.path.join(base_dir, "ICGJ.csv")
        df = pd.read_csv(caminho)
        df["Referencia_yyyymm"] = df["Referencia_yyyymm"].astype(str).str.strip()
        df["Indice"] = df["Indice"].astype(str).str.replace(",", ".").astype(float)
        return df[["Referencia_yyyymm", "Indice"]]
    except Exception as e:
        st.error(f"Erro ao carregar ICGJ.csv: {e}")
        return pd.DataFrame(columns=["Referencia_yyyymm", "Indice"])

def recalcular_icgj(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    icgj_df = carregar_icgj()
    if icgj_df.empty:
        df["ICGJ"] = 1.0
        return df

    icgj_dict = dict(zip(icgj_df["Referencia_yyyymm"], icgj_df["Indice"]))

    def obter_icgj(row):
        ref_pgto = row.get("referencia_pgto")
        if pd.notna(ref_pgto):
            ref_pgto_str = str(ref_pgto).strip()
            if ref_pgto_str and ref_pgto_str in icgj_dict:
                return icgj_dict[ref_pgto_str]

        ref = row.get("referencia")
        if pd.notna(ref):
            ref_str = str(ref).strip()
            if ref_str and ref_str in icgj_dict:
                return icgj_dict[ref_str]

        return 1.0

    df["ICGJ"] = df.apply(obter_icgj, axis=1)
    return df

def consultar_bacen_fator(codigo, data_ref, data_final=None, tentativas=3, delay=3, considerar_negativo=False):
    if data_final is None:
        data_final = datetime.today().strftime("%d/%m/%Y")
    if not data_ref:
        return 1.0

    for _ in range(tentativas):
        try:
            url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json&dataInicial={data_ref}&dataFinal={data_final}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            dados = r.json()
            if not dados:
                return 1.0
            fatores = [1 + float(d["valor"].replace(",", ".")) / 100 for d in dados]
            resultado = round(reduce(mul, fatores, 1), 8)
            if considerar_negativo and resultado < 1:
                return 1.0
            return resultado
        except Exception:
            time.sleep(delay)
    return 1.0

def obter_ipca(d, considerar_negativo=False):   
    return consultar_bacen_fator(433, converter_data_para_bacen(d), considerar_negativo=considerar_negativo)

def obter_igpm(d, considerar_negativo=False):   
    return consultar_bacen_fator(189, converter_data_para_bacen(d), considerar_negativo=considerar_negativo)

def obter_igpdi(d, considerar_negativo=False):  
    return consultar_bacen_fator(190, converter_data_para_bacen(d), considerar_negativo=considerar_negativo)

def converter_data_para_bacen(valor):
    try:
        if pd.isna(valor) or valor == "" or valor is None:
            return None
        data = pd.to_datetime(valor, dayfirst=True, errors="coerce")
        if pd.isna(data):
            return None
        return data.strftime("%d/%m/%Y")
    except Exception:
        return None

def extrair_tabela_pdf(uploaded_file):
    tabelas = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            tabela = page.extract_table({"vertical_strategy": "lines", "horizontal_strategy": "lines"})
            if not tabela or len(tabela) < 2: continue
            df = pd.DataFrame(tabela)
            df.dropna(how="all", inplace=True)
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)
            tabelas.append(df)
    if not tabelas:
        raise Exception("Nenhuma tabela encontrada")
    return pd.concat(tabelas, ignore_index=True)

def corrigir_tipos(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    if "Número de Postes" in df.columns:
        df["Número de Postes"] = pd.to_numeric(df["Número de Postes"], errors="coerce").fillna(0).astype(int)
    for col in ["Preço que estava sendo cobrado pela CEMIG", "Preço conquistado na AÇÃO",
                "Valor conquistado na AÇÃO", "Valor CEMIG", "Benefício Econômico"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in ["IPCA", "IGPM", "IGPDI", "ICGJ", "Corrigido IPCA", "Corrigido IGPM", "Corrigido IGPDI", "Corrigido ICGJ",
                "Honorários IPCA", "Honorários IGPM", "Honorários IGPDI", "Honorários ICGJ"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df

def inicializar_banco():
    base_dir = os.path.dirname(__file__)
    caminho_db = os.path.join(base_dir, "logs_bacen.db")

    conn = sqlite3.connect(caminho_db)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs_bacen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT,
            ip TEXT,
            maquina TEXT,
            data_hora TEXT,
            tipo_evento TEXT DEFAULT 'bacen'
        )
    """)

    # Migração: adicionar coluna tipo_evento se não existir
    try:
        cursor.execute("ALTER TABLE logs_bacen ADD COLUMN tipo_evento TEXT DEFAULT 'bacen'")
    except Exception:
        pass

    conn.commit()
    conn.close()

def registrar_log(usuario, tipo_evento="bacen"):
    base_dir = os.path.dirname(__file__)
    caminho_db = os.path.join(base_dir, "logs_bacen.db")

    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
    except:
        ip = "IP não identificado"

    maquina = socket.gethostname()
    data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(caminho_db)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO logs_bacen (usuario, ip, maquina, data_hora, tipo_evento)
        VALUES (?, ?, ?, ?, ?)
    """, (usuario, ip, maquina, data_hora, tipo_evento))

    conn.commit()
    conn.close()

def registrar_log_bacen(usuario):
    registrar_log(usuario, tipo_evento="bacen")

def carregar_dados_dashboard():
    base_dir = os.path.dirname(__file__)
    caminho_db = os.path.join(base_dir, "logs_bacen.db")
    try:
        conn = sqlite3.connect(caminho_db)
        df = pd.read_sql_query("SELECT * FROM logs_bacen ORDER BY data_hora DESC", conn)
        conn.close()
        if df.empty:
            return df
        df["data_hora"] = pd.to_datetime(df["data_hora"], errors="coerce")
        df["ano_mes"] = df["data_hora"].dt.to_period("M").astype(str)
        df["mes_label"] = df["data_hora"].dt.strftime("%b/%Y")
        if "tipo_evento" not in df.columns:
            df["tipo_evento"] = "bacen"
        df["tipo_evento"] = df["tipo_evento"].fillna("bacen")
        return df
    except Exception:
        return pd.DataFrame()

def mostrar_dashboard():
    st.markdown("## 📊 Dashboard de Uso")
    st.caption("Monitoramento mensal das execuções do sistema")

    df = carregar_dados_dashboard()

    hoje = datetime.now()
    mes_atual = hoje.strftime("%Y-%m")
    ano_atual = str(hoje.year)

    if df.empty:
        st.info("Nenhum dado de uso registrado ainda. Execute o sistema para começar a acumular métricas.")
        return

    df_bacen = df[df["tipo_evento"] == "bacen"]
    df_pdf = df[df["tipo_evento"] == "pdf"]
    df_excel = df[df["tipo_evento"] == "excel"]

    total_bacen = len(df_bacen)
    bacen_mes = len(df_bacen[df_bacen["ano_mes"] == mes_atual])
    bacen_ano = len(df_bacen[df_bacen["data_hora"].dt.year == hoje.year])
    total_pdf = len(df_pdf)
    total_excel = len(df_excel)
    usuarios_unicos = df["usuario"].nunique()
    ultimo_uso = df["data_hora"].max().strftime("%d/%m/%Y %H:%M") if not df.empty else "—"

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("🔍 Consultas BACEN (total)", total_bacen)
    with c2:
        st.metric("📅 Consultas BACEN (este mês)", bacen_mes)
    with c3:
        st.metric("📆 Consultas BACEN (este ano)", bacen_ano)
    with c4:
        st.metric("👤 Usuários únicos", usuarios_unicos)

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        st.metric("📄 PDFs extraídos (total)", total_pdf)
    with c6:
        st.metric("📥 Exportações Excel (total)", total_excel)
    with c7:
        st.metric("🕐 Último uso", ultimo_uso)
    with c8:
        total_eventos = len(df)
        st.metric("⚡ Total de eventos", total_eventos)

    st.divider()

    # Gráfico mensal de consultas BACEN (últimos 12 meses)
    col_graf1, col_graf2 = st.columns([2, 1])

    with col_graf1:
        st.markdown("#### Consultas BACEN por mês (últimos 12 meses)")
        if not df_bacen.empty:
            periodos = pd.period_range(end=pd.Timestamp.now(), periods=12, freq="M")
            meses_str = [str(p) for p in periodos]
            contagem = df_bacen.groupby("ano_mes").size().reindex(meses_str, fill_value=0)
            contagem.index = [pd.Period(m).strftime("%b/%Y") for m in contagem.index]
            st.bar_chart(contagem, use_container_width=True, color="#9C7A33")
        else:
            st.info("Sem dados de consultas BACEN ainda.")

    with col_graf2:
        st.markdown("#### Eventos por tipo")
        if not df.empty:
            tipos = df["tipo_evento"].value_counts()
            labels = {"bacen": "Consulta BACEN", "pdf": "Extração PDF", "excel": "Exportação Excel"}
            tipos.index = [labels.get(i, i) for i in tipos.index]
            st.bar_chart(tipos, use_container_width=True)

    st.divider()

    # Tabela mensal detalhada
    st.markdown("#### Resumo mensal detalhado")
    if not df.empty:
        resumo = df.groupby(["ano_mes", "tipo_evento"]).size().unstack(fill_value=0)
        resumo = resumo.rename(columns={"bacen": "Consultas BACEN", "pdf": "PDFs Extraídos", "excel": "Exportações Excel"})
        resumo = resumo.sort_index(ascending=False).reset_index()
        resumo = resumo.rename(columns={"ano_mes": "Mês"})
        resumo["Mês"] = resumo["Mês"].apply(lambda x: pd.Period(x).strftime("%B/%Y") if x else x)
        st.dataframe(resumo, hide_index=True, use_container_width=True)

    st.divider()

    # Últimos 20 acessos
    st.markdown("#### Últimas execuções")
    df_recent = df.head(20)[["data_hora", "usuario", "tipo_evento", "maquina"]].copy()
    df_recent["data_hora"] = df_recent["data_hora"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_recent["tipo_evento"] = df_recent["tipo_evento"].map({"bacen": "Consulta BACEN", "pdf": "Extração PDF", "excel": "Exportação Excel"}).fillna(df_recent["tipo_evento"])
    df_recent.columns = ["Data/Hora", "Usuário", "Tipo de Evento", "Máquina"]
    st.dataframe(df_recent, hide_index=True, use_container_width=True)
    
    
# =====================================================
# STREAMLIT CONFIG
# =====================================================
st.set_page_config(page_title="Correção Monetária", layout="wide", page_icon="🏛️", initial_sidebar_state="expanded")
inicializar_banco()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"]  {
    font-family: 'Inter', sans-serif;
}

/* Fundo branco real */
.stApp {
    background-color: #FFFFFF;
}

/* Remove padding padrão do streamlit */
.block-container {
    padding-top: 3rem !important;
    padding-bottom: 0rem;
    max-width: 100% !important;
}

/* Centralização vertical */
.login-wrapper {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
}

/* Card SaaS */
.login-card {
    width: 100%;
    max-width: 380px;
    padding: 2.5rem;
    border-radius: 12px;
    border: 1px solid #E5E7EB;
    background-color: #FFFFFF;
}

/* Título */
.login-title {
    font-size: 1.6rem;
    font-weight: 600;
    color: #111827;
    margin-bottom: 0.3rem;
}

/* Subtítulo */
.login-subtitle {
    font-size: 0.9rem;
    color: #6B7280;
    margin-bottom: 1.8rem;
}

/* Inputs */
.stTextInput > div > div > input {
    border-radius: 8px !important;
    padding: 11px !important;
    border: 1px solid #D1D5DB !important;
}

.stTextInput > div > div > input:focus {
    border: 1px solid #111827 !important;
    box-shadow: none !important;
}

/* Botão */
.stFormSubmitButton > button {
    background-color: #111827;
    color: white;
    border-radius: 8px;
    padding: 11px;
    font-weight: 500;
    width: 100%;
    border: none;
}

.stFormSubmitButton > button:hover {
    background-color: #000000;
}

/* Cards de etapas — altura fixa e sem quebra de linha */
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] .stButton > button {
    height: 48px !important;
    min-height: 48px !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    font-size: 0.80rem !important;
    padding: 0 8px !important;
    width: 100% !important;
    border-radius: 8px !important;
}

/* ===================== BOTÕES — identidade visual única em todo o app =====================
   Substitui as cores padrão do Streamlit (vermelho/azul) pela paleta dourada da marca,
   garantindo a mesma aparência em qualquer navegador/ambiente (local ou publicado). */
.stButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"] {
    background-color: #9C7A33 !important;
    color: #FFFFFF !important;
    border: 1px solid #9C7A33 !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover {
    background-color: #7A5F28 !important;
    border-color: #7A5F28 !important;
}
.stButton > button[kind="primary"]:active,
.stButton > button[kind="primary"]:focus:not(:hover) {
    background-color: #7A5F28 !important;
    border-color: #7A5F28 !important;
    color: #FFFFFF !important;
    box-shadow: none !important;
}

.stButton > button[kind="secondary"] {
    background-color: #FFFFFF !important;
    color: #2C2C2C !important;
    border: 1px solid #E5E0D3 !important;
    box-shadow: none !important;
}
.stButton > button[kind="secondary"]:hover {
    background-color: #F8F5F0 !important;
    border-color: #C9A85C !important;
    color: #2C2C2C !important;
}

/* ===================== SELOS NUMÉRICOS DAS ETAPAS =====================
   Badge circular desenhado em CSS puro (não depende de fonte de emoji,
   então tem a mesma aparência em qualquer navegador/servidor). */
.st-key-steps .stButton > button {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0.5rem !important;
}
.st-key-steps .stButton > button::before {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background-color: #9C7A33;
    color: #FFFFFF;
    font-size: 0.72rem;
    font-weight: 700;
}
.st-key-steps .stButton > button[kind="primary"]::before {
    background-color: #FFFFFF;
    color: #7A5F28;
}
.st-key-steps div[data-testid="stColumn"]:nth-of-type(1) .stButton > button::before { content: "1"; }
.st-key-steps div[data-testid="stColumn"]:nth-of-type(2) .stButton > button::before { content: "2"; }
.st-key-steps div[data-testid="stColumn"]:nth-of-type(3) .stButton > button::before { content: "3"; }
.st-key-steps div[data-testid="stColumn"]:nth-of-type(4) .stButton > button::before { content: "4"; }
.st-key-steps div[data-testid="stColumn"]:nth-of-type(5) .stButton > button::before { content: "5"; }

/* ===================== MENU LATERAL (SIDEBAR) - minimalista ===================== */
[data-testid="stSidebar"] {
    background-color: #FAF8F4;
    border-right: 1px solid #ECE8DF;
}
[data-testid="stSidebar"] > div {
    padding-top: 1.6rem;
}

.sidebar-brand {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0 0.2rem 1rem 0.2rem;
}
.sidebar-brand img { display: block; }
.sidebar-brand-text {
    font-size: 1.05rem;
    font-weight: 700;
    color: #2C2C2C;
    letter-spacing: -0.01em;
    line-height: 1.2;
}

.sidebar-divider {
    border-top: 1px solid #ECE8DF;
    margin: 0 0 0.8rem 0;
}

/* Botões de navegação minimalistas: sem borda/caixa, só texto + hover/pill sutil */
[data-testid="stSidebar"] .stButton > button {
    width: 100% !important;
    height: auto !important;
    min-height: 0 !important;
    white-space: nowrap !important;
    font-size: 0.92rem !important;
    font-weight: 500 !important;
    padding: 0.55rem 0.8rem !important;
    border-radius: 8px !important;
    border: none !important;
    background-color: transparent !important;
    color: #6B6558 !important;
    box-shadow: none !important;
    text-align: left !important;
    justify-content: flex-start !important;
    transition: background-color 0.15s ease, color 0.15s ease;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #F1EDE5 !important;
    color: #2C2C2C !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background-color: #EFEAE0 !important;
    color: #2C2C2C !important;
    font-weight: 700 !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
    background-color: #EFEAE0 !important;
}

</style>
""", unsafe_allow_html=True)


# ---------- Autenticação ----------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user = ""

VALID_USERS = {
    "admin_sv": "admin_sv"
}

def verify_password(user, passwd):
    return VALID_USERS.get(user) == passwd


def login_screen():
    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    #st.markdown('<div class="login-card">', unsafe_allow_html=False)

    # Logo + Título
    st.markdown('<div class="logo-section">', unsafe_allow_html=True)
    
    logo_path = "logo_sv.png"   # ← Altere se o nome do arquivo for diferente
    if os.path.exists(logo_path):
        st.image(logo_path, width=78)
    else:
        st.warning("Logo não encontrada")

    st.markdown('<div class="login-title">Correção Monetária</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-subtitle">Automação de Correções Monetárias</div>', unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)

    with st.form("login_form", clear_on_submit=False):
        user = st.text_input("Usuário", placeholder="digite o usuário", key="login_user", value="")
        pwd = st.text_input("Senha", type="password", placeholder="digite a senha", key="login_pass", value="")
        
        submitted = st.form_submit_button("Entrar")

        if submitted:
            if verify_password(user.strip(), pwd.strip()):
                st.session_state.authenticated = True
                st.session_state.user = user.strip()
                st.success("✅ Login realizado com sucesso!")
                st.rerun()
            else:
                st.error("❌ Usuário ou senha incorretos.")

    st.markdown('</div></div>', unsafe_allow_html=True)
    st.stop()


# ====================== ICGJ UPLOAD ======================
def mostrar_icgj():
    st.markdown("## 📤 Atualizar ICGJ")
    st.caption("Faça upload de um novo arquivo ICGJ.csv para substituir o atual no sistema")

    arquivo = st.file_uploader("Selecione o arquivo ICGJ.csv", type=["csv"])

    if arquivo is not None:
        try:
            df_novo = pd.read_csv(arquivo)
            st.markdown("#### Preview do arquivo enviado")
            st.dataframe(df_novo.head(10), hide_index=True, use_container_width=True)

            if st.button("✅ Confirmar e Salvar", type="primary"):
                base_dir = os.path.dirname(__file__)
                caminho = os.path.join(base_dir, "ICGJ.csv")
                df_novo.to_csv(caminho, index=False)
                st.success("✅ ICGJ.csv atualizado com sucesso!")
                st.rerun()
        except Exception as e:
            st.error(f"Erro ao processar arquivo: {e}")

    st.divider()
    st.markdown("#### Arquivo ICGJ atual")
    base_dir = os.path.dirname(__file__)
    caminho = os.path.join(base_dir, "ICGJ.csv")
    if os.path.exists(caminho):
        df_atual = pd.read_csv(caminho)
        st.caption(f"{len(df_atual)} registros no arquivo atual")
        st.dataframe(df_atual, hide_index=True, use_container_width=True, height=400)
    else:
        st.warning("Arquivo ICGJ.csv não encontrado.")


# ====================== HEADER DO APP ======================
if not st.session_state.authenticated:
    login_screen()

# Session State
if "raw_df" not in st.session_state: st.session_state.raw_df = None
if "df" not in st.session_state: st.session_state.df = None
if "df_original" not in st.session_state: st.session_state.df_original = None
if "references" not in st.session_state: st.session_state.references = [""]
if "current_page" not in st.session_state: st.session_state.current_page = "dashboard"
if "current_step" not in st.session_state: st.session_state.current_step = "1"

# ====================== MENU LATERAL (SIDEBAR) ======================
logo_path = "logo_sv.png"
paginas = [
    ("📊 Dashboard", "dashboard"),
    ("⚙️ Processar", "processar"),
    ("📤 Subir ICGJ", "icgj"),
]

with st.sidebar:
    logo_html = ""
    if os.path.exists(logo_path):
        import base64
        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode()
        logo_html = f'<img src="data:image/png;base64,{logo_b64}" height="34"/>'
    st.markdown(
        f'<div class="sidebar-brand">{logo_html}<span class="sidebar-brand-text">Correção Monetária</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)

    for label, key in paginas:
        is_active = st.session_state.current_page == key
        if st.button(label, type="primary" if is_active else "secondary", key=f"nav_{key}"):
            st.session_state.current_page = key
            st.rerun()

current_page = st.session_state.current_page

# ====================== ROTEAMENTO ======================
if current_page == "dashboard":
    mostrar_dashboard()
    st.stop()

if current_page == "icgj":
    mostrar_icgj()
    st.stop()

# ====================== PROCESSAR - SUB-NAVEGAÇÃO ======================
st.markdown("### 📍 Etapas do Processo")

steps = [
    ("Extrair PDF", "1"),
    ("Processar Colunas", "2"),
    ("Projetar Parcelas", "3"),
    ("Parâmetros da Análise", "4"),
    ("BACEN & Exportar", "5"),
]

current_step = st.session_state.get("current_step", "1")

with st.container(key="steps"):
    col1, col2, col3, col4, col5 = st.columns(5)
    for i, (label, key) in enumerate(steps):
        with [col1, col2, col3, col4, col5][i]:
            if st.button(label, type="primary" if current_step == key else "secondary", key=f"step_{key}"):
                st.session_state.current_step = key
                st.rerun()

st.divider()

# ====================== PASSO 1 ======================
if current_step == "1":
    st.subheader("Passo 1 - Extrair PDF")
    uploaded_file = st.file_uploader("Selecione o arquivo PDF da planilha CEMIG", type=["pdf"])
    
    if st.button("Extrair Tabelas do PDF", type="primary"):
        if uploaded_file:
            try:
                with st.spinner("Extraindo tabelas do PDF..."):
                    st.session_state.raw_df = extrair_tabela_pdf(uploaded_file)
                    st.session_state.df = st.session_state.raw_df.copy()
                    st.session_state.df_original = st.session_state.raw_df.copy()
                registrar_log(st.session_state.user, tipo_evento="pdf")
                st.success(f"✅ {len(st.session_state.raw_df)} registros extraídos com sucesso!")
            except Exception as e:
                st.error(f"Erro na extração: {e}")

# ====================== PASSO 2 ======================
elif current_step == "2":
    st.subheader("Passo 2 - Processar Colunas")
    if st.session_state.raw_df is None:
        st.warning("Volte ao Passo 1 e extraia o PDF primeiro.")
    else:
        if st.button("Processar Colunas e Cálculos Iniciais", type="primary"):
            try:
                with st.spinner("Processando..."):
                    # (Todo o código de processamento permanece igual ao original)
                    df = st.session_state.raw_df.copy()

                    for standard, variants in COLUMN_VARIANTS.items():
                        for col in list(df.columns):
                            if col in variants:
                                df.rename(columns={col: standard}, inplace=True)
                                break

                    df["Número de Postes"] = df.get("Pontos", pd.Series()).apply(tratar_postes)
                    col_preco = "Preço Pto."
                    df["Preço que estava sendo cobrado pela CEMIG"] = df.get(col_preco, pd.Series()).apply(tratar_numero)
                    df["Preço conquistado na AÇÃO"] = df.get(col_preco, pd.Series()).apply(tratar_numero)
                    df["Valor conquistado na AÇÃO"] = (df["Número de Postes"] * df["Preço conquistado na AÇÃO"]).round(2)
                    df["Valor CEMIG"] = (df["Número de Postes"] * df["Preço que estava sendo cobrado pela CEMIG"]).round(2)
                    df["Benefício Econômico"] = (df["Valor CEMIG"] - df["Valor conquistado na AÇÃO"]).round(2)

                    df["referencia"] = df["Mês referência/Ano cobrança"].apply(converter_referencia_yyyymm)
                    df["referencia_pgto"] = df.get("Pagamento", pd.Series()).apply(converter_referencia_yyyymm_pagamento)
                    df["referencia_vcto"] = df.get("Vencimento", pd.Series()).apply(converter_referencia_yyyymm_pagamento)

                    df = df[df["Mês referência/Ano cobrança"].apply(eh_mes_ano) & (df["referencia"] != "")].reset_index(drop=True)

                    df = recalcular_icgj(df)

                    for c in ["IPCA","IGPM","IGPDI"]: df[c] = pd.Series(dtype=float)
                    for c in ["Corrigido IPCA","Corrigido IGPM","Corrigido IGPDI","Corrigido ICGJ"]: df[c] = 0.0
                    for c in ["Honorários IPCA","Honorários IGPM","Honorários IGPDI","Honorários ICGJ"]: df[c] = 0.0

                    col_order = ["Mês referência/Ano cobrança","referencia","Pagamento","referencia_pgto","Vencimento","referencia_vcto","Número de Postes",
                                 "Preço que estava sendo cobrado pela CEMIG","Valor CEMIG","Preço conquistado na AÇÃO",
                                 "Valor conquistado na AÇÃO","Benefício Econômico",
                                 "IPCA","IGPM","IGPDI","ICGJ","Corrigido IPCA","Corrigido IGPM","Corrigido IGPDI","Corrigido ICGJ",
                                 "Honorários IPCA","Honorários IGPM","Honorários IGPDI","Honorários ICGJ"]
                    df = df[[c for c in col_order if c in df.columns]]
                    df = df.sort_values("referencia").reset_index(drop=True)

                    st.session_state.df = corrigir_tipos(df)
                    st.session_state.df_original = st.session_state.df.copy()

                    refs = df.drop_duplicates(subset=["Mês referência/Ano cobrança"], keep="first")["Mês referência/Ano cobrança"].tolist()
                    st.session_state.references = [""] + refs

                st.success(f"✅ Processamento concluído! {len(df)} registros gerados.")
            except Exception as e:
                st.error(f"Erro no processamento: {e}")

# ====================== PASSO 3, 4 e 5 ======================
# ====================== PASSO 3 - PROJETAR PARCELAS ======================
elif current_step == "3":
    st.subheader("Passo 3 - Projetar Parcelas")

    if st.session_state.df is None or st.session_state.df.empty:
        st.warning("Conclua o Passo 2 primeiro.")
    else:
        st.markdown("##### Adicionar parcelas futuras automaticamente")

        st.write("**Data do Trânsito em Julgado:**")
        col_date, _ = st.columns([1.2, 2.8])
        with col_date:
            data_transito = st.date_input("Data do Trânsito em Julgado", label_visibility="collapsed")

        st.write("**Quantidade de novas parcelas a projetar:**")
        col_qty, col_add, col_del = st.columns([1, 1, 1])
        with col_qty:
            quantidade = st.number_input("Quantidade de novas parcelas", min_value=1, value=12, step=1, label_visibility="collapsed")
        with col_add:
            if st.button("➕ Adicionar", type="primary"):
                try:
                    # Converter data de trânsito para formato YYYYMM
                    referencia_transito = data_transito.strftime("%Y%m")
                    
                    ultima_linha = st.session_state.df.iloc[-1].copy()

                    novas_linhas = []
                    referencia_atual = referencia_transito

                    for i in range(quantidade):
                        if i == 0:
                            # Primeira parcela começa na data de trânsito
                            pass
                        else:
                            referencia_atual = add_meses_yyyymm(referencia_atual, 1)
                        nova_linha = ultima_linha.copy()
                        mes_nome = list(MESES.keys())[int(referencia_atual[4:]) - 1]
                        ano = referencia_atual[:4]
                        nova_linha["Mês referência/Ano cobrança"] = f"{mes_nome}/{ano}"
                        nova_linha["referencia"] = referencia_atual
                        nova_linha["Pagamento"] = ""
                        nova_linha["referencia_pgto"] = ""
                        nova_linha["Vencimento"] = ""
                        nova_linha["referencia_vcto"] = ""

                        for col in ["IPCA", "IGPM", "IGPDI", "Corrigido IPCA", "Corrigido IGPM", "Corrigido IGPDI", "Corrigido ICGJ",
                                    "Honorários IPCA", "Honorários IGPM", "Honorários IGPDI", "Honorários ICGJ"]:
                            if col in nova_linha:
                                nova_linha[col] = 0.0

                        novas_linhas.append(nova_linha)

                    df_novo = pd.DataFrame(novas_linhas)
                    st.session_state.df = pd.concat([st.session_state.df, df_novo], ignore_index=True)
                    st.session_state.df = st.session_state.df.sort_values("referencia").reset_index(drop=True)

                    st.session_state.df = recalcular_icgj(st.session_state.df)
                    st.session_state.df = corrigir_tipos(st.session_state.df)

                    st.success(f"✅ {quantidade} novas parcelas adicionadas com ICGJ atualizado!")
                    st.rerun()

                except Exception as e:
                    st.error(f"Erro ao adicionar parcelas: {e}")

        with col_del:
            if st.button("🗑️ Excluir Todas as Parcelas Projetadas", type="secondary"):
                if len(st.session_state.df) > len(st.session_state.df_original):
                    st.session_state.df = st.session_state.df_original.copy().reset_index(drop=True)
                    st.session_state.df = corrigir_tipos(st.session_state.df)
                    st.success("✅ Todas as parcelas projetadas foram excluídas!")
                    st.rerun()
                else:
                    st.info("Não há parcelas projetadas para excluir.")

# ====================== PASSO 4 - PARÂMETROS DA ANÁLISE ======================
elif current_step == "4":
    st.subheader("Passo 4 - Parâmetros da Análise")
    if st.session_state.df is None:
        st.warning("Conclua o Passo 2 primeiro.")
    else:
        st.divider()

        tab1, tab2, tab3 = st.tabs(["🔍 Filtro Geral", "💰 Fornecedor (CEMIG)", "✅ Valor Conquistado"])

        with tab1:
            st.markdown("##### Filtrar dados a partir de uma referência específica")
            col_select, _ = st.columns([1.2, 2.8])
            with col_select:
                filtro_referencia = st.selectbox("Referência inicial:", st.session_state.references, key="filtro_ref")

            col_button, _ = st.columns([1.2, 2.8])
            with col_button:
                if st.button("🔄 Aplicar Filtro", type="secondary"):
                    if filtro_referencia:
                        ref_base = converter_referencia_yyyymm(filtro_referencia)
                        df_filtrado = st.session_state.df[st.session_state.df["referencia"] >= ref_base].copy().reset_index(drop=True)

                        df_anterior = st.session_state.df[st.session_state.df["referencia"] < ref_base].sort_values("referencia")
                        if not df_anterior.empty:
                            preco_congelado = df_anterior.iloc[-1]["Preço que estava sendo cobrado pela CEMIG"]
                            df_filtrado["Preço que estava sendo cobrado pela CEMIG"] = preco_congelado
                            df_filtrado["Valor CEMIG"] = (df_filtrado["Número de Postes"] * preco_congelado).round(2)
                            df_filtrado["Benefício Econômico"] = (df_filtrado["Valor CEMIG"] - df_filtrado["Valor conquistado na AÇÃO"]).round(2)

                        st.session_state.df = df_filtrado.sort_values("referencia").reset_index(drop=True)
                        st.session_state.df = corrigir_tipos(st.session_state.df)
                        st.success(f"✅ Filtro aplicado!")

        with tab2:
            st.markdown("##### Configurar preço do fornecedor (CEMIG)")
            col_m, col_v, col_i = st.columns([1.2, 1, 1.3])
            with col_m:
                marco_fornecedor = st.selectbox("📅 Marco Fornecedor:", st.session_state.references, key="marco_forn")
            with col_v:
                valor_fornecedor = st.number_input("💵 Valor (R$):", value=0.0, format="%.4f", key="val_forn")
            with col_i:
                indice_fornecedor = st.selectbox("📊 Índice:", ["IPCA", "IGPM", "IGP-DI", "ICGJ", "Outros"], key="ind_forn")

            considerar_neg_forn = st.checkbox(
                "Padronizar índices negativos para 1.0 (Fornecedor)?",
                value=st.session_state.get("considerar_neg_forn", False),
                key="considerar_neg_forn"
            )

            if st.button("🚀 Atualizar Fornecedor", type="primary"):
                if valor_fornecedor <= 0:
                    st.warning("❌ Valor do Fornecedor deve ser maior que zero.")
                else:
                    marco_yyyymm = converter_referencia_yyyymm(marco_fornecedor)
                    preco_atual = valor_fornecedor
                    bloco_anterior = 0
                    with st.spinner("Atualizando preço FORNECEDOR..."):
                        for i, row in st.session_state.df.iterrows():
                            linha_yyyymm = row["referencia"]

                            meses_diff = calcular_meses_diff(marco_yyyymm, linha_yyyymm)
                            bloco_atual = meses_diff // 12
                            if bloco_atual > bloco_anterior:
                                inicio = add_meses_yyyymm(marco_yyyymm, bloco_anterior * 12)
                                fim = add_meses_yyyymm(marco_yyyymm, bloco_atual * 12)
                                data_inicio = yyyymm_para_data_bacen(inicio)
                                data_fim = yyyymm_para_data_bacen(fim)
                                if data_inicio and data_fim:
                                    if indice_fornecedor == "IPCA":
                                        fator = consultar_bacen_fator(433, data_inicio, data_fim, considerar_negativo=considerar_neg_forn)
                                    elif indice_fornecedor == "IGPM":
                                        fator = consultar_bacen_fator(189, data_inicio, data_fim, considerar_negativo=considerar_neg_forn)
                                    elif indice_fornecedor == "IGP-DI":
                                        fator = consultar_bacen_fator(190, data_inicio, data_fim, considerar_negativo=considerar_neg_forn)
                                    elif indice_fornecedor == "ICGJ":
                                        fator = 1 + (row.get("ICGJ", 0)/100)
                                        if considerar_neg_forn and fator < 1:
                                            fator = 1.0
                                    else:
                                        fator = 1.0

                                    preco_atual *= fator
                                    bloco_anterior = bloco_atual

                            st.session_state.df.at[i, "Preço que estava sendo cobrado pela CEMIG"] = round(preco_atual, 2)
                            rounded_preco_forn = round(preco_atual, 2)
                            st.session_state.df.at[i, "Valor CEMIG"] = round(st.session_state.df.at[i, "Número de Postes"] * rounded_preco_forn, 2)
                            st.session_state.df.at[i, "Benefício Econômico"] = round(st.session_state.df.at[i, "Valor CEMIG"] - st.session_state.df.at[i, "Valor conquistado na AÇÃO"], 2)
                    st.session_state.df = st.session_state.df.sort_values("referencia").reset_index(drop=True)
                    st.session_state.df = corrigir_tipos(st.session_state.df)
                    st.success("✅ Preço FORNECEDOR atualizado com sucesso!")

        with tab3:
            st.markdown("##### Configurar preço conquistado na ação")
            col_m2, col_v2, col_i2 = st.columns([1.2, 1, 1.3])
            with col_m2:
                marco_conquistado = st.selectbox("📅 Marco Conquistado:", st.session_state.references, key="marco_conq")
            with col_v2:
                valor_conquistado = st.number_input("💵 Valor (R$):", value=0.0, format="%.4f", key="val_conq")
            with col_i2:
                indice_contrato = st.selectbox("📊 Índice:", ["IPCA", "IGPM", "IGP-DI", "ICGJ", "Outros"], key="ind_conq")

            considerar_neg_conq = st.checkbox(
                "Padronizar índices negativos para 1.0 (Preço Conquistado)?",
                value=st.session_state.get("considerar_neg_conq", False),
                key="considerar_neg_conq"
            )

            if st.button("🚀 Atualizar Conquistado", type="primary"):
                if valor_conquistado <= 0:
                    st.warning("❌ Valor conquistado deve ser maior que zero.")
                else:
                    marco_yyyymm = converter_referencia_yyyymm(marco_conquistado)
                    preco_atual = valor_conquistado
                    bloco_anterior = 0
                    with st.spinner("Atualizando preço CONQUISTADO..."):
                        for i, row in st.session_state.df.iterrows():
                            linha_yyyymm = row["referencia"]

                            meses_diff = calcular_meses_diff(marco_yyyymm, linha_yyyymm)
                            bloco_atual = meses_diff // 12
                            if bloco_atual > bloco_anterior:
                                inicio = add_meses_yyyymm(marco_yyyymm, bloco_anterior * 12)
                                fim = add_meses_yyyymm(marco_yyyymm, bloco_atual * 12)
                                data_inicio = yyyymm_para_data_bacen(inicio)
                                data_fim = yyyymm_para_data_bacen(fim)
                                if data_inicio and data_fim:
                                    if indice_contrato == "IPCA":
                                        fator = consultar_bacen_fator(433, data_inicio, data_fim, considerar_negativo=considerar_neg_conq)
                                    elif indice_contrato == "IGPM":
                                        fator = consultar_bacen_fator(189, data_inicio, data_fim, considerar_negativo=considerar_neg_conq)
                                    elif indice_contrato == "IGP-DI":
                                        fator = consultar_bacen_fator(190, data_inicio, data_fim, considerar_negativo=considerar_neg_conq)
                                    elif indice_contrato == "ICGJ":
                                        fator = 1 + (row.get("ICGJ", 0)/100)
                                        if considerar_neg_conq and fator < 1:
                                            fator = 1.0
                                    else:
                                        fator = 1.0

                                    preco_atual *= fator
                                    bloco_anterior = bloco_atual

                            st.session_state.df.at[i, "Preço conquistado na AÇÃO"] = round(preco_atual, 2)
                            rounded_preco_conq = round(preco_atual, 2)
                            st.session_state.df.at[i, "Valor conquistado na AÇÃO"] = round(st.session_state.df.at[i, "Número de Postes"] * rounded_preco_conq, 2)
                            st.session_state.df.at[i, "Benefício Econômico"] = round(st.session_state.df.at[i, "Valor CEMIG"] - st.session_state.df.at[i, "Valor conquistado na AÇÃO"], 2)
                    st.session_state.df = st.session_state.df.sort_values("referencia").reset_index(drop=True)
                    st.session_state.df = corrigir_tipos(st.session_state.df)
                    st.success("✅ Preço CONQUISTADO atualizado com sucesso!")

# ====================== PASSO 5 ======================
elif current_step == "5":
    st.subheader("Passo 5 - Consultar BACEN & Exportar")

    col_num, col_cons, col_exp = st.columns([1, 1, 1])
    with col_num:
        st.write("**Honorários (%):**")
        percentual_honorarios = st.number_input("Percentual de Honorários", min_value=0.0, max_value=100.0, value=0.0, step=0.01, key="percentual_honorarios", label_visibility="collapsed")
    with col_cons:
        st.write("**Consultar dados no BACEN:**")
        if st.button("🔍 BACEN", type="secondary"):
            if st.session_state.df is not None:

                    #REGISTRA LOG AQUI
                    registrar_log_bacen(st.session_state.user)
                    # Criar placeholder para mostrar o progresso
                    status_placeholder = st.empty()
                    progress_placeholder = st.empty()
                    
                    percentual_honorarios = st.session_state.get("percentual_honorarios", 0.0)
                    total_linhas = len(st.session_state.df)

                    for i, row in st.session_state.df.iterrows():
                        # Mostrar qual linha está sendo processada
                        ref = row.get("Mês referência/Ano cobrança", "N/A")
                        progresso = (i + 1) / total_linhas
                        
                        # Atualizar o placeholder com o status atual
                        status_placeholder.info(f"📊 Processando: {ref} ({i+1}/{total_linhas})")
                        progress_placeholder.progress(progresso, text=f"Consultando índices... {int(progresso*100)}%")
                        
                        ref_pgto = row.get("referencia_pgto")
                        if pd.isna(ref_pgto) or ref_pgto == "" or ref_pgto == 0:
                            data_para_consulta = yyyymm_para_data_bacen(row["referencia"])
                        else:
                            data_para_consulta = row.get("Pagamento")

                        # Consultar cada índice e mostrar o que está sendo consultado
                        status_placeholder.info(f"📊 {ref} - Consultando **IPCA**...")
                        ipca  = obter_ipca(data_para_consulta)

                        status_placeholder.info(f"📊 {ref} - Consultando **IGPM**...")
                        igpm  = obter_igpm(data_para_consulta)

                        status_placeholder.info(f"📊 {ref} - Consultando **IGP-DI**...")
                        igpdi = obter_igpdi(data_para_consulta)

                        ipca_final  = ipca
                        igpm_final  = igpm
                        igpdi_final = igpdi

                        st.session_state.df.at[i, "IPCA"]  = ipca_final
                        st.session_state.df.at[i, "IGPM"]  = igpm_final
                        st.session_state.df.at[i, "IGPDI"] = igpdi_final

                        benef = st.session_state.df.at[i, "Benefício Econômico"]

                        if ipca_final is not None:
                            st.session_state.df.at[i, "Corrigido IPCA"] = round(benef * ipca_final, 2)
                        if igpm_final is not None:
                            st.session_state.df.at[i, "Corrigido IGPM"] = round(benef * igpm_final, 2)
                        if igpdi_final is not None:
                            st.session_state.df.at[i, "Corrigido IGPDI"] = round(benef * igpdi_final, 2)

                        icgj = st.session_state.df.at[i, "ICGJ"]
                        if pd.notna(icgj) and icgj != 0:
                            st.session_state.df.at[i, "Corrigido ICGJ"] = round(benef * icgj, 2)

                        # Calcular Honorários
                        percentual = percentual_honorarios / 100
                        st.session_state.df.at[i, "Honorários IPCA"] = round(st.session_state.df.at[i, "Corrigido IPCA"] * percentual, 2) if pd.notna(st.session_state.df.at[i, "Corrigido IPCA"]) else 0.0
                        st.session_state.df.at[i, "Honorários IGPM"] = round(st.session_state.df.at[i, "Corrigido IGPM"] * percentual, 2) if pd.notna(st.session_state.df.at[i, "Corrigido IGPM"]) else 0.0
                        st.session_state.df.at[i, "Honorários IGPDI"] = round(st.session_state.df.at[i, "Corrigido IGPDI"] * percentual, 2) if pd.notna(st.session_state.df.at[i, "Corrigido IGPDI"]) else 0.0
                        st.session_state.df.at[i, "Honorários ICGJ"] = round(st.session_state.df.at[i, "Corrigido ICGJ"] * percentual, 2) if pd.notna(st.session_state.df.at[i, "Corrigido ICGJ"]) else 0.0
                    
                    # Limpar os placeholders após conclusão
                    status_placeholder.empty()
                    progress_placeholder.empty()

                    st.session_state.df = corrigir_tipos(st.session_state.df)

                    # TOTAL NO FINAL
                    total_row = {col: "" for col in st.session_state.df.columns}
                    total_row["Mês referência/Ano cobrança"] = "TOTAL"
                    for col in ["Valor conquistado na AÇÃO", "Valor CEMIG", "Benefício Econômico",
                                "Corrigido IPCA", "Corrigido IGPM", "Corrigido IGPDI", "Corrigido ICGJ",
                                "Honorários IPCA", "Honorários IGPM", "Honorários IGPDI", "Honorários ICGJ"]:
                        if col in st.session_state.df.columns:
                            total_row[col] = round(st.session_state.df[col].sum(), 2)

                    df_sem_total = st.session_state.df[st.session_state.df["Mês referência/Ano cobrança"] != "TOTAL"].copy()
                    st.session_state.df = pd.concat([df_sem_total, pd.DataFrame([total_row])], ignore_index=True)
                    st.session_state.df = corrigir_tipos(st.session_state.df)

                    st.success("✅ Consulta BACEN concluída!")

    with col_exp:
        st.write("**Exportar tabela final para excel:**")
        if st.button("📥 Excel", type="primary"):
            if st.session_state.df is not None:
                output = BytesIO()
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    st.session_state.df.to_excel(writer, index=False)
                output.seek(0)
                registrar_log(st.session_state.user, tipo_evento="excel")
                st.download_button("📥 Baixar Excel", output, "resultado_correcao_monetaria.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ====================== TABELA FINAL ======================
st.divider()
st.subheader("📋 Tabela Atual (atualizada em tempo real)")

if st.session_state.get("df") is not None and not st.session_state.df.empty:
    df = st.session_state.df
    colunas_indices = ["IPCA", "IGPM", "IGPDI", "ICGJ"]
    cols_exist = [c for c in colunas_indices if c in df.columns]
    
    styler = df.style
    
    if cols_exist:
        def pintar_vermelho(v):
            try:
                if float(v) < 1:
                    return "background-color: #4a2a2a; color: #ffaaaa;"
                return ""
            except:
                return ""
        
        styler = styler.map(pintar_vermelho, subset=cols_exist)
        styler = styler.format(precision=4, subset=cols_exist, na_rep="-")
    
    def destacar_total(row):
        if row.get("Mês referência/Ano cobrança") == "TOTAL":
            return ["font-weight: bold; background-color: #3a2f1f; color: #f0e6d2;"] * len(row)
        return [""] * len(row)
    
    styler = styler.apply(destacar_total, axis=1)
    
    st.dataframe(styler, use_container_width=True, height=720, hide_index=True)

elif st.session_state.get("raw_df") is not None and not st.session_state.raw_df.empty:
    st.dataframe(st.session_state.raw_df, use_container_width=True, height=720, hide_index=True)
else:
    st.info("Extraia o PDF no Passo 1 para começar.")

st.caption("Versão: v.1.3 • Design atualizado com identidade visual da marca")
