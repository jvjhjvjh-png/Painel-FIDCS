import streamlit as st
import pandas as pd
import requests
import zipfile
import io
import re
from datetime import datetime

# Configuração Avançada da Página do Streamlit
st.set_page_config(
    page_title="Inteligência de Mercado - FIDCs & Investidores",
    page_icon="📊",
    layout="wide"
)

# Estilização visual profissional via CSS customizado
st.markdown("""
    <style>
        .main { background-color: #F8F9FA; }
        .stMetric { background-color: #FFFFFF; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-left: 5px solid #1B365D; }
        .footer { text-align: center; padding: 25px; color: #6C757D; font-size: 14px; border-top: 1px solid #E9ECEF; margin-top: 50px; }
        .update-box { background-color: #E8EEF5; padding: 12px; border-radius: 6px; font-size: 13px; color: #1B365D; margin-bottom: 20px; border: 1px solid #CBD5E1; }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# DICIONÁRIO DE TRADUÇÃO DE COLUNAS (PADRÃO PROFISSIONAL)
# ---------------------------------------------------------
COLUNAS_AMIGAVEIS = {
    'NOME_FIDC_INVESTIDO': 'FIDC Investido',
    'PL_FIDC_INVESTIDO_RS': 'PL do FIDC',
    'CNPJ_FIDC_INVESTIDO': 'CNPJ do FIDC',
    'GESTOR_FIDC_INVESTIDO': 'Gestor do FIDC',
    'ADMINISTRADOR_FIDC_INVESTIDO': 'Adm. do FIDC',
    'COTISTAS_FIDC_INVESTIDO': 'Cotistas do FIDC',
    'NOME_FUNDO_INVESTIDOR': 'Fundo Investidor',
    'PL_FUNDO_INVESTIDOR_RS': 'PL do Fundo Investidor',
    'CNPJ_FUNDO_INVESTIDOR': 'CNPJ do Fundo',
    'GESTOR_FUNDO_INVESTIDOR': 'Gestor do Fundo',
    'ADMINISTRADOR_FUNDO_INVESTIDOR': 'Adm. do Fundo',
    'TIPO_FUNDO_INVESTIDOR': 'Tipo de Fundo',
    'PUBLICO_ALVO_INVESTIDOR': 'Público Alvo',
    'COTISTAS_FUNDO_INVESTIDOR': 'Cotistas do Fundo',
    'VALOR_ALOCADO_RS': 'Valor Alocado',
    'PARTICIPACAO_PL_INVESTIDOR': '% Part. PL Fundo',
    'PARTICIPACAO_PL_FIDC': '% Part. PL FIDC',
    'ORIGEM_CAPTACAO': 'Origem da Captação',
    'MESMA_GESTORA': 'Mesma Gestora',
    'MES': 'Mês de Referência'
}

# ---------------------------------------------------------
# FUNÇÕES DE FORMATAÇÃO E NORMALIZAÇÃO (PADRÃO BR)
# ---------------------------------------------------------
def formatar_brl(val):
    if pd.isna(val) or val == 0:
        return "R$ 0,00"
    s = f"R$ {val:,.2f}"
    return s.replace(",", "TEMP").replace(".", ",").replace("TEMP", ".")

def formatar_pct(val):
    if pd.isna(val):
        return "0,00%"
    s = f"{val * 100:,.2f}%"
    return s.replace(",", "TEMP").replace(".", ",").replace("TEMP", ".")

def obter_nome_mes(ano_mes):
    ano = ano_mes[:4]
    mes_num = ano_mes[4:]
    nomes = {
        "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
        "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
        "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
    }
    return f"{nomes.get(mes_num, mes_num)} de {ano}"

def normalizar_cnpj(val):
    if pd.isna(val): return ""
    s = str(val).strip()
    s = re.sub(r'\.0$', '', s)
    s = re.sub(r'\D', '', s)
    if 0 < len(s) <= 14: return s.zfill(14)
    return ""

def formatar_mascara_cnpj(val):
    cnpj = normalizar_cnpj(val)
    if len(cnpj) == 14:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    return str(val) if pd.notna(val) else ""

def converter_numero_cvm(series):
    s = series.astype(str).str.strip()
    mask_ambos = s.str.contains(r'\.', regex=True) & s.str.contains(r',', regex=True)
    s.loc[mask_ambos] = s.loc[mask_ambos].str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    mask_virgula = ~s.str.contains(r'\.', regex=True) & s.str.contains(r',', regex=True)
    s.loc[mask_virgula] = s.loc[mask_virgula].str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='coerce').fillna(0.0)

def obter_valor_coluna(row, candidatos):
    for col in candidatos:
        if col in row and pd.notna(row[col]):
            v = str(row[col]).strip()
            if v and v.lower() != 'nan' and v != 'None': return v
    return ""

def obter_cotistas(row, candidatos):
    for col in candidatos:
        if col in row and pd.notna(row[col]):
            val_raw = row[col]
            try:
                val = float(val_raw)
                if val > 0: return int(val)
            except (ValueError, TypeError):
                v = str(val_raw).strip()
                if v and v.lower() != 'nan' and v != 'None':
                    v_clean = v.replace('.', '').replace(',', '.') if ('.' in v and ',' in v) else v.replace(',', '.')
                    try:
                        val = float(v_clean)
                        if val > 0: return int(val)
                    except ValueError: continue
    return 0

def obter_numero_float(row, candidatos):
    for col in candidatos:
        if col in row and pd.notna(row[col]):
            v = str(row[col]).strip()
            if v and v.lower() != 'nan' and v != 'None':
                try:
                    val = float(v.replace('.', '').replace(',', '.')) if ('.' in v and ',' in v) else float(v.replace(',', '.'))
                    if val > 0: return val
                except ValueError: continue
    return 0.0

def processar_mes_cda(ano_mes, cad_info, id_fundo_map, fidc_cnpjs_set):
    res = requests.get(f"https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{ano_mes}.zip")
    if res.status_code != 200: return None

    pl_info = {}
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        arq_pl = next((f for f in z.namelist() if "cda_fi_PL_" in f), None)
        if arq_pl:
            with z.open(arq_pl) as f:
                df_pl = pd.read_csv(f, sep=";", encoding="latin1", low_memory=False)
                df_pl.columns = df_pl.columns.str.upper().str.strip()
                col_cnpj_pl = next((c for c in ['CNPJ_FUNDO_CLASSE', 'CNPJ_FUNDO', 'CNPJ_CLASSE'] if c in df_pl.columns), None)
                col_vl_pl = next((c for c in ['VL_PATRIM_LIQ', 'VL_PL', 'PATRIMONIO_LIQUIDO'] if c in df_pl.columns), None)
                col_cotistas = next((c for c in ['NR_COTST', 'QT_COTST', 'NR_COTISTAS', 'QT_COTISTAS', 'NR_COTST_CLASSE'] if c in df_pl.columns), None)
                if col_cnpj_pl and col_vl_pl:
                    df_pl['CNPJ_14'] = df_pl[col_cnpj_pl].apply(normalizar_cnpj)
                    df_pl['PL_NUM'] = converter_numero_cvm(df_pl[col_vl_pl])
                    df_pl['COTISTAS_NUM'] = converter_numero_cvm(df_pl[col_cotistas]) if col_cotistas else 0
                    for _, r in df_pl.iterrows():
                        cnpj = r['CNPJ_14']
                        if cnpj: pl_info[cnpj] = {'pl': float(r['PL_NUM']), 'cotistas': int(r['COTISTAS_NUM'])}

    try:
        url_inf = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ano_mes}.zip"
        resp_inf = requests.get(url_inf)
        if resp_inf.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(resp_inf.content)) as z_inf:
                arq_inf = next((f for f in z_inf.namelist() if "inf_diario_fi_" in f), None)
                if arq_inf:
                    with z_inf.open(arq_inf) as f_inf:
                        df_inf = pd.read_csv(f_inf, sep=";", encoding="latin1", low_memory=False)
                        df_inf.columns = df_inf.columns.str.upper().str.strip()
                        col_cnpj_inf = next((c for c in ['CNPJ_FUNDO_CLASSE', 'CNPJ_FUNDO', 'CNPJ_CLASSE'] if c in df_inf.columns), None)
                        col_vl_inf = next((c for c in ['VL_PATRIM_LIQ', 'VL_PL'] if c in df_inf.columns), None)
                        col_cotst_inf = next((c for c in ['NR_COTST', 'QT_COTST', 'NR_COTISTAS', 'QT_COTISTAS', 'NR_COTST_CLASSE'] if c in df_inf.columns), None)
                        col_dt_inf = next((c for c in ['DT_COMPTC', 'DT_COMPT'] if c in df_inf.columns), None)
                        if col_cnpj_inf and col_cotst_inf:
                            df_inf['CNPJ_14'] = df_inf[col_cnpj_inf].apply(normalizar_cnpj)
                            df_inf['COTISTAS_NUM'] = converter_numero_cvm(df_inf[col_cotst_inf])
                            df_inf['PL_NUM'] = converter_numero_cvm(df_inf[col_vl_inf]) if col_vl_inf else 0.0
                            if col_dt_inf: df_inf = df_inf.sort_values(by=col_dt_inf)
                            df_ultimo = df_inf.groupby('CNPJ_14').last().reset_index()
                            for _, r in df_ultimo.iterrows():
                                cnpj = r['CNPJ_14']
                                if cnpj:
                                    cots = int(r['COTISTAS_NUM'])
                                    pl_val = float(r['PL_NUM'])
                                    if cnpj not in pl_info:
                                        pl_info[cnpj] = {'pl': pl_val, 'cotistas': cots}
                                    else:
                                        if cots > 0: pl_info[cnpj]['cotistas'] = cots
                                        if pl_info[cnpj]['pl'] == 0 and pl_val > 0: pl_info[cnpj]['pl'] = pl_val
    except Exception: pass

    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        arq_blc2 = next((f for f in z.namelist() if "cda_fi_BLC_2" in f), None)
        if not arq_blc2: return None
        with z.open(arq_blc2) as f:
            df_blc2 = pd.read_csv(f, sep=";", encoding="latin1", low_memory=False)
            df_blc2.columns = df_blc2.columns.str.upper().str.strip()

    col_inv_cnpj = next((c for c in ['CNPJ_FUNDO_CLASSE', 'CNPJ_FUNDO', 'CNPJ_CLASSE'] if c in df_blc2.columns), None)
    col_inv_nome = next((c for c in ['DENOM_SOCIAL_CLASSE', 'DENOM_SOCIAL', 'NM_FUNDO'] if c in df_blc2.columns), None)
    col_inv_tipo = next((c for c in ['TP_FUNDO', 'TP_CLASSE', 'TP_APLIC'] if c in df_blc2.columns), None)
    col_target_cnpj = next((c for c in ['CNPJ_FUNDO_COTA', 'CNPJ_FUNDO_CLASSE_COTA', 'CNPJ_COTA', 'CNPJ_EMISSOR'] if c in df_blc2.columns), None)
    col_target_nome = next((c for c in ['NM_FUNDO_COTA', 'NM_FUNDO_CLASSE_COTA', 'DENOM_SOCIAL_COTA', 'NM_EMISSOR'] if c in df_blc2.columns), None)
    col_valor = next((c for c in ['VL_MERC_POS_FINAL', 'VL_POS_FINAL', 'VALOR_MERCADO'] if c in df_blc2.columns), None)
    col_pl = next((c for c in ['PR_PART_PL', 'PR_PARTIC_PL', 'PERCENT_PL', 'PR_PL', 'PR_PART_PL_CLASSE'] if c in df_blc2.columns), None)

    df_blc2['CNPJ_ALVO_14'] = df_blc2[col_target_cnpj].apply(normalizar_cnpj)
    df_blc2['CNPJ_INV_14'] = df_blc2[col_inv_cnpj].apply(normalizar_cnpj)
    df_blc2['VALOR_RS'] = converter_numero_cvm(df_blc2[col_valor]) if col_valor else 0.0
    df_blc2['PERCENT_PL_INV'] = converter_numero_cvm(df_blc2[col_pl]) if col_pl else 0.0

    cond_cnpj = df_blc2['CNPJ_ALVO_14'].isin(fidc_cnpjs_set)
    cond_nome = df_blc2[col_target_nome].astype(str).str.contains('FIDC|DIREITO|CREDITO|CREDITÓRIO', case=False, na=False) if col_target_nome else False

    df_filtrado = df_blc2[(cond_cnpj | cond_nome) & (df_blc2['VALOR_RS'] > 0)].copy()
    soma_alocacoes_fidc = df_filtrado.groupby('CNPJ_ALVO_14')['VALOR_RS'].sum().to_dict()

    def resolver_pl_fidc(cnpj_target):
        pl = 0.0
        if cnpj_target in pl_info and pl_info[cnpj_target]['pl'] > 0: pl = pl_info[cnpj_target]['pl']
        elif cnpj_target in cad_info and cad_info[cnpj_target].get('pl', 0.0) > 0: pl = cad_info[cnpj_target]['pl']
        soma_conhecida = soma_alocacoes_fidc.get(cnpj_target, 0.0)
        return max(pl, soma_conhecida)

    def resolver_cotistas(cnpj_target):
        if not cnpj_target: return 0
        if cnpj_target in pl_info and pl_info[cnpj_target].get('cotistas', 0) > 0: return pl_info[cnpj_target]['cotistas']
        if cnpj_target in cad_info and cad_info[cnpj_target].get('cotistas', 0) > 0: return cad_info[cnpj_target]['cotistas']
        return 0

    registros = []
    for _, r in df_filtrado.iterrows():
        cnpj_alvo, cnpj_inv, val_investido = r['CNPJ_ALVO_14'], r['CNPJ_INV_14'], float(r['VALOR_RS'])
        info_alvo, info_inv = cad_info.get(cnpj_alvo, {}), cad_info.get(cnpj_inv, {})
        pl_inv_data = pl_info.get(cnpj_inv, {})

        nome_fidc = info_alvo.get('nome') or (str(r[col_target_nome]).strip() if col_target_nome and pd.notna(r[col_target_nome]) else "FIDC NÃO IDENTIFICADO")
        nome_inv = info_inv.get('nome') or (str(r[col_inv_nome]).strip() if col_inv_nome and pd.notna(r[col_inv_nome]) else "INVESTIDOR NÃO IDENTIFICADO")
        gestor_fidc, gestor_inv = info_alvo.get('gestor') or "NÃO INFORMADO", info_inv.get('gestor') or "NÃO INFORMADO"
        admin_fidc, admin_inv = info_alvo.get('admin') or "NÃO INFORMADO", info_inv.get('admin') or "NÃO INFORMADO"

        mesma_gestora = "Sim" if (gestor_fidc != "NÃO INFORMADO" and gestor_inv != "NÃO INFORMADO" and (gestor_fidc.upper() == gestor_inv.upper() or gestor_fidc.upper() in gestor_inv.upper() or gestor_inv.upper() in gestor_fidc.upper())) else "Não"
        origem_captacao = "Mercado (Terceiros)" if mesma_gestora == "Não" else "Proprietária (Mesma Casa)"

        pl_total_fidc = resolver_pl_fidc(cnpj_alvo)
        pl_total_inv = pl_inv_data.get('pl', 0.0) or info_inv.get('pl', 0.0)

        cotistas_fidc, cotistas_inv = resolver_cotistas(cnpj_alvo), resolver_cotistas(cnpj_inv)
        pct_detencao_fidc = (val_investido / pl_total_fidc) if pl_total_fidc > 0 else 0.0
        pct_pl_cvm = float(r['PERCENT_PL_INV'])
        pct_pl_inv = (pct_pl_cvm / 100.0 if pct_pl_cvm > 1.0 else pct_pl_cvm) if pct_pl_cvm > 0 else ((val_investido / pl_total_inv) if pl_total_inv > 0 else 0.0)

        registros.append({
            'NOME_FIDC_INVESTIDO': nome_fidc,
            'PL_FIDC_INVESTIDO_RS': float(pl_total_fidc),
            'CNPJ_FIDC_INVESTIDO': formatar_mascara_cnpj(cnpj_alvo),
            'GESTOR_FIDC_INVESTIDO': gestor_fidc,
            'ADMINISTRADOR_FIDC_INVESTIDO': admin_fidc,
            'COTISTAS_FIDC_INVESTIDO': int(cotistas_fidc),
            'NOME_FUNDO_INVESTIDOR': nome_inv,
            'PL_FUNDO_INVESTIDOR_RS': float(pl_total_inv),
            'CNPJ_FUNDO_INVESTIDOR': formatar_mascara_cnpj(cnpj_inv),
            'GESTOR_FUNDO_INVESTIDOR': gestor_inv,
            'ADMINISTRADOR_FUNDO_INVESTIDOR': admin_inv,
            'TIPO_FUNDO_INVESTIDOR': info_inv.get('tp') or (r[col_inv_tipo] if col_inv_tipo else "N/A"),
            'PUBLICO_ALVO_INVESTIDOR': info_inv.get('publico', 'N/A'),
            'COTISTAS_FUNDO_INVESTIDOR': int(cotistas_inv),
            'VALOR_ALOCADO_RS': float(val_investido),
            'PARTICIPACAO_PL_INVESTIDOR': float(pct_pl_inv),
            'PARTICIPACAO_PL_FIDC': float(pct_detencao_fidc),
            'ORIGEM_CAPTACAO': origem_captacao,
            'MESMA_GESTORA': mesma_gestora
        })

    df_final = pd.DataFrame(registros)
    return df_final.drop_duplicates(subset=['CNPJ_FIDC_INVESTIDO', 'CNPJ_FUNDO_INVESTIDOR', 'VALOR_ALOCADO_RS']).sort_values(by=['NOME_FIDC_INVESTIDO', 'VALOR_ALOCADO_RS'], ascending=[True, False])

@st.cache_data(ttl=604800, show_spinner="Baixando e processando bases de cadastros e CDA da CVM...")
def carregar_dados_completos():
    cad_info = {}
    id_fundo_map = {}
    fidc_cnpjs_set = set()
    cols_cotistas_cad = ['NR_COTST', 'QT_COTST', 'NR_COTISTAS', 'QT_COTISTAS', 'NR_COTST_CLASSE', 'QT_COTST_CLASSE']
    cols_pl_cad = ['VL_PATRIM_LIQ', 'VL_PL', 'PATRIMONIO_LIQUIDO', 'PL', 'VL_PATRIM_LIQ_CLASSE']

    try:
        url_175 = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
        resp_175 = requests.get(url_175)
        if resp_175.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(resp_175.content)) as z:
                if "registro_fundo.csv" in z.namelist():
                    with z.open("registro_fundo.csv") as f:
                        df_f = pd.read_csv(f, sep=";", encoding="latin1", low_memory=False)
                        df_f.columns = df_f.columns.str.upper().str.strip()
                        for _, row in df_f.iterrows():
                            cnpj = normalizar_cnpj(row.get('CNPJ_FUNDO'))
                            nome = obter_valor_coluna(row, ['DENOMINACAO_SOCIAL', 'DENOM_SOCIAL', 'NM_FUNDO'])
                            gestor = obter_valor_coluna(row, ['GESTOR', 'NM_GESTOR', 'CPF_CNPJ_GESTOR'])
                            admin = obter_valor_coluna(row, ['ADMINISTRADOR', 'ADMIN', 'NM_ADMIN'])
                            tp = str(row.get('TP_FUNDO', '')).upper()
                            cots = obter_cotistas(row, cols_cotistas_cad)
                            pl_cad = obter_numero_float(row, cols_pl_cad)
                            dados = {'cnpj': cnpj, 'nome': nome, 'gestor': gestor, 'admin': admin, 'tp': tp, 'publico': 'N/A', 'cotistas': cots, 'pl': pl_cad}
                            if cnpj:
                                cad_info[cnpj] = dados
                                if any(k in tp or k in nome.upper() for k in ['FIDC', 'DIREITO', 'CREDITO', 'CREDITÓRIO']):
                                    fidc_cnpjs_set.add(cnpj)
    except Exception: pass

    hoje = datetime.now()
    meses_para_processar = [f"{hoje.year}{m:02d}" for m in range(1, hoje.month + 1)]
    
    dados_por_mes = {}
    master_list = []
    for m in meses_para_processar:
        df_mes = processar_mes_cda(m, cad_info, id_fundo_map, fidc_cnpjs_set)
        if df_mes is not None and not df_mes.empty:
            dados_por_mes[m] = df_mes
            df_temp = df_mes.copy()
            df_temp.insert(0, 'MES', obter_nome_mes(m))
            master_list.append(df_temp)

    df_master = pd.concat(master_list, ignore_index=True) if master_list else pd.DataFrame()
    return dados_por_mes, df_master

def preparar_df_exibicao(df):
    df_disp = df.copy()
    for col in ['PL_FIDC_INVESTIDO_RS', 'PL_FUNDO_INVESTIDOR_RS', 'VALOR_ALOCADO_RS']:
        if col in df_disp.columns:
            df_disp[col] = df_disp[col].apply(formatar_brl)
    for col in ['PARTICIPACAO_PL_INVESTIDOR', 'PARTICIPACAO_PL_FIDC']:
        if col in df_disp.columns:
            df_disp[col] = df_disp[col].apply(formatar_pct)
    # Renomeia para colunas amigáveis
    df_disp = df_disp.rename(columns=COLUNAS_AMIGAVEIS)
    return df_disp

# ---------------------------------------------------------
# INTERFACE VISUAL DO APLICATIVO
# ---------------------------------------------------------
st.title("📊 Inteligência de Mercado: Alocações em FIDCs")
st.markdown("Painel analítico executivo estruturado com base nos dados públicos da CVM (Resolução CVM 175 e CDA).")

dados_por_mes, df_master = carregar_dados_completos()

if not df_master.empty:
    meses_disponiveis = list(dados_por_mes.keys())
    
    # Barra lateral de filtros e informações de atualização
    st.sidebar.header("⚙️ Configurações & Filtros")
    
    st.sidebar.markdown("""
        <div class="update-box">
            <b>📅 Cronograma de Atualização:</b><br>
            A base de dados é atualizada automaticamente toda <b>segunda-feira</b> com os novos arquivos disponibilizados pela CVM.
        </div>
    """, unsafe_allow_html=True)

    mes_escolhido = st.sidebar.selectbox("Selecione o Mês Base:", meses_disponiveis, format_func=obter_nome_mes)
    
    df_filtrado = dados_por_mes[mes_escolhido]

    # Filtros Avançados na Barra Lateral
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔍 Filtros Avançados")
    
    gestores_disponiveis = sorted(df_filtrado['GESTOR_FUNDO_INVESTIDOR'].dropna().unique())
    gestor_filtro = st.sidebar.multiselect("Filtrar por Gestora:", gestores_disponiveis)

    origens_disponiveis = sorted(df_filtrado['ORIGEM_CAPTACAO'].dropna().unique())
    origem_filtro = st.sidebar.multiselect("Origem da Captação:", origens_disponiveis)

    # Aplicar filtros ao dataframe do mês
    if gestor_filtro:
        df_filtrado = df_filtrado[df_filtrado['GESTOR_FUNDO_INVESTIDOR'].isin(gestor_filtro)]
    if origem_filtro:
        df_filtrado = df_filtrado[df_filtrado['ORIGEM_CAPTACAO'].isin(origem_filtro)]

    aba_dash, aba_mensal, aba_consolidada, aba_manual = st.tabs([
        "📈 Painel Executivo (Dashboard)", 
        f"📅 Detalhado ({obter_nome_mes(mes_escolhido)})", 
        "📚 Base Consolidada",
        "📖 Manual de Utilização"
    ])

    with aba_dash:
        st.subheader(f"Visão Executiva do Período: {obter_nome_mes(mes_escolhido)}")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Volume Total Alocado", formatar_brl(df_filtrado['VALOR_ALOCADO_RS'].sum()))
        col2.metric("Total de Alocações", f"{len(df_filtrado):,}".replace(",", "."))
        col3.metric("FIDCs Distintos", f"{df_filtrado['CNPJ_FIDC_INVESTIDO'].nunique():,}".replace(",", "."))
        col4.metric("Fundos Investidores", f"{df_filtrado['CNPJ_FUNDO_INVESTIDOR'].nunique():,}".replace(",", "."))

        st.markdown("---")
        
        col_g1, col_g2 = st.columns(2)
        
        with col_g1:
            st.markdown("### 🏆 Top Fundos Investidores por Volume")
            top_fundos = df_filtrado.groupby('NOME_FUNDO_INVESTIDOR')['VALOR_ALOCADO_RS'].sum().reset_index()
            top_fundos = top_fundos.sort_values(by='VALOR_ALOCADO_RS', ascending=False).head(10)
            top_fundos.insert(0, 'Pos.', range(1, len(top_fundos) + 1))
            top_fundos['VALOR_ALOCADO_RS'] = top_fundos['VALOR_ALOCADO_RS'].apply(formatar_brl)
            top_fundos.columns = ['Pos.', 'Fundo Investidor', 'Valor Total Alocado']
            st.dataframe(top_fundos, use_container_width=True, hide_index=True, height=400)

        with col_g2:
            st.markdown("### 🏢 Top Gestoras Investidoras por Volume")
            top_gestoras = df_filtrado.groupby('GESTOR_FUNDO_INVESTIDOR')['VALOR_ALOCADO_RS'].sum().reset_index()
            top_gestoras = top_gestoras.sort_values(by='VALOR_ALOCADO_RS', ascending=False).head(10)
            top_gestoras.insert(0, 'Pos.', range(1, len(top_gestoras) + 1))
            top_gestoras['VALOR_ALOCADO_RS'] = top_gestoras['VALOR_ALOCADO_RS'].apply(formatar_brl)
            top_gestoras.columns = ['Pos.', 'Gestora Investidora', 'Valor Total Alocado']
            st.dataframe(top_gestoras, use_container_width=True, hide_index=True, height=400)

    with aba_mensal:
        st.subheader(f"Detalhamento Completo - {obter_nome_mes(mes_escolhido)}")
        st.markdown("Tabela analítica completa contendo todas as alocações cruzadas, filtros aplicados e formatação monetária padrão BR.")
        st.dataframe(preparar_df_exibicao(df_filtrado), use_container_width=True, hide_index=True, height=600)

    with aba_consolidada:
        st.subheader("📚 Série Histórica Consolidada")
        st.markdown("Base de dados histórica completa de todos os meses processados no ano corrente.")
        st.dataframe(preparar_df_exibicao(df_master), use_container_width=True, hide_index=True, height=600)

    with aba_manual:
        st.subheader("📖 Manual de Utilização e Metodologia")
        st.markdown("""
        Bem-vindo ao **Painel de Inteligência de Mercado de FIDCs**. Esta ferramenta foi desenvolvida para automatizar a extração, tratamento e consolidação de dados públicos disponibilizados pela **Comissão de Valores Mobiliários (CVM)**.

        ### 🔍 1. Fontes de Dados Utilizadas
        * **Documentação de Carteiras e Ativos (CDA):** Dados mensais detalhando as posições e alocações de ativos dentro dos Fundos de Investimento.
        * **Informe Diário:** Posições atualizadas de Patrimônio Líquido (PL) e quantidade de cotistas.
        * **Cadastros CVM (Resolução CVM 175):** Informações cadastrais de fundos, classes, gestores e administradores.

        ### 📅 2. Frequência de Atualização
        * A base possui cache inteligente e é atualizada automaticamente **toda segunda-feira** para refletir os envios mais recentes por parte dos administradores regulados pela CVM.

        ### ⚙️ 3. Lógica e Processamento
        * **Cruzamento Automático:** O sistema cruza os blocos de carteira (Bloco 2 do CDA) para identificar quais fundos possuem cotas ou alocações em FIDCs.
        * **Detecção de Mesma Gestora:** Identifica se a captação ocorreu no mercado aberto (terceiros) ou de forma proprietária (mesma casa / gestora).
        * **Formatação Brasileira:** Valores monetários e percentuais seguem rigorosamente o padrão nacional (`R$` com pontos nos milhares e vírgulas nos decimais).

        ### 🚀 4. Como Navegar
        * Use o seletor na barra lateral esquerda para alternar o **Mês Base** da análise e utilize os **Filtros Avançados** de gestora e origem.
        * Utilize as abas superiores para alternar entre o **Dashboard Executivo**, a **Tabela Detalhada do Mês** e a **Série Histórica**.
        """)

else:
    st.error("Não foram encontrados dados de CDA disponíveis na CVM para o período atual.")

# Rodapé oficial
st.markdown("""
    <div class="footer">
        Desenvolvido por <b>João Victor Helito</b> &copy; 2026 — Todos os direitos reservados.
    </div>
""", unsafe_allow_html=True)
