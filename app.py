import streamlit as st
import requests
import json
import time
import base64
import pandas as pd
from collections import OrderedDict
from io import BytesIO
from PIL import Image
from pathlib import Path

# ============================================================
# 定数
# ============================================================
CONFIG_PATH = Path("smaregi_config.json")
DEFAULT_CONFIG = {
    "contract_id": "", "client_id": "", "client_secret": "",
    "imgbb_api_key": "", "visible_fields": [], "use_sandbox": True,
}

# ============================================================
# ユーティリティ
# ============================================================
def safe_int(v, d=0):
    if v is None: return d
    try: return int(v)
    except: return d

def safe_float(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

def safe_str(v, d=""):
    if v is None: return d
    return str(v)

# ============================================================
# 設定管理 (🌟 Step2: Secrets対応化)
# ============================================================
def _load_file_config():
    cfg = dict(DEFAULT_CONFIG)
    
    # 1. ローカルのJSONファイルがあれば読み込む
    if CONFIG_PATH.exists():
        try: cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except: pass
        
    # 2. クラウドの金庫 (st.secrets) に設定があれば、それを最優先で上書きする
    try:
        if "CONTRACT_ID" in st.secrets: cfg["contract_id"] = st.secrets["CONTRACT_ID"]
        if "CLIENT_ID" in st.secrets: cfg["client_id"] = st.secrets["CLIENT_ID"]
        if "CLIENT_SECRET" in st.secrets: cfg["client_secret"] = st.secrets["CLIENT_SECRET"]
        if "IMGBB_API_KEY" in st.secrets: cfg["imgbb_api_key"] = st.secrets["IMGBB_API_KEY"]
    except Exception:
        pass # st.secretsが設定されていないローカル環境用
        
    return cfg

def get_config():
    if "app_config" not in st.session_state:
        st.session_state.app_config = _load_file_config()
    return st.session_state.app_config

def update_config_bulk(u):
    cfg = get_config(); cfg.update(u); st.session_state.app_config = cfg
    try: CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

def update_config(k, v): update_config_bulk({k: v})

def get_api_base():
    cfg = get_config(); cid = cfg.get("contract_id", "")
    dom = "smaregi.dev" if cfg.get("use_sandbox", True) else "smaregi.jp"
    return f"https://api.{dom}/{cid}/pos"

def get_auth_url():
    cfg = get_config(); cid = cfg.get("contract_id", "")
    dom = "smaregi.dev" if cfg.get("use_sandbox", True) else "smaregi.jp"
    return f"https://id.{dom}/app/{cid}/token"

# ============================================================
# フィールド定義
# ============================================================
FIELD_DEFS = OrderedDict([
    ("商品名",        {"api":"productName",          "type":"text",    "default":"",         "required":True, "core":True, "max":85, "send_empty":True, "post":True}),
    ("商品コード",    {"api":"productCode",          "type":"text",    "default":"",         "required":False,"core":True, "max":20, "send_empty":False,"post":True}),
    ("商品価格",      {"api":"price",                "type":"number",  "default":0,          "required":True, "core":True, "max":None,"send_empty":True,"post":True}),
    ("原価",          {"api":"cost",                 "type":"number",  "default":0,          "required":False,"core":False,"max":None,"send_empty":False,"post":True}),
    ("部門ID",        {"api":"categoryId",           "type":"category","default":"",         "required":True, "core":True, "max":None,"send_empty":False,"post":True}),
    ("税区分",        {"api":"taxDivision",          "type":"select",  "default":"0:税込",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:税込","1:税抜","2:非課税"]}),
    ("税率",          {"api":"taxRate",              "type":"number",  "default":10,         "required":False,"core":False,"max":None,"send_empty":False,"post":False}),
    ("在庫管理区分",  {"api":"stockControlDivision", "type":"select",  "default":"0:対象",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:対象","1:対象外"]}),
    ("表示順",        {"api":"displaySequence",      "type":"number",  "default":0,          "required":False,"core":False,"max":None,"send_empty":False,"post":True}),
    ("説明",          {"api":"description",          "type":"text",    "default":"",         "required":False,"core":False,"max":1000,"send_empty":False,"post":True}),
    ("カラー",        {"api":"color",                "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("サイズ",        {"api":"size",                 "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("売上区分",      {"api":"salesDivision",        "type":"select",  "default":"0:売上対象","required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:売上対象","1:売上対象外"]}),
    ("グループコード",{"api":"groupCode",            "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("商品カナ",      {"api":"productKana",          "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("端末表示",      {"api":"displayFlag",          "type":"select",  "default":"1:表示する","required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:表示しない","1:表示する"]}),
    ("会員価格",      {"api":"customerPrice",        "type":"number",  "default":0,          "required":False,"core":False,"max":None,"send_empty":False,"post":True}),
    ("ポイント対象区分",{"api":"pointNotApplicable", "type":"select",  "default":"0:対象",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:対象","1:対象外"]}),
    ("値引割引対象",  {"api":"calcDiscount",         "type":"select",  "default":"1:対象",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:対象外","1:対象"]}),
    ("キャッチコピー",{"api":"catchCopy",            "type":"text",    "default":"",         "required":False,"core":False,"max":1000,"send_empty":False,"post":True}),
    ("タグ",          {"api":"tag",                  "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
])

def sel2api(v):
    if not v or ":" not in safe_str(v): return safe_str(v)
    return safe_str(v).split(":")[0]

def api2sel(val, opts):
    s = safe_str(val)
    for o in opts:
        if o.split(":")[0] == s: return o
    return opts[0] if opts else s

def is_empty(v):
    if v is None: return True
    if isinstance(v, str) and v.strip() == "": return True
    if isinstance(v, (int, float)) and v == 0: return True
    return False

def get_visible():
    cfg = get_config(); extra = cfg.get("visible_fields", [])
    core = [k for k, d in FIELD_DEFS.items() if d["core"]]
    return core + [k for k in extra if k in FIELD_DEFS and not FIELD_DEFS[k]["core"]]

def _cat_options():
    cats = get_categories()
    return [""] + [f"{safe_str(c.get('categoryId',''))}:{safe_str(c.get('categoryName',''))}" for c in cats]

# ============================================================
# CSS
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;700&display=swap');
    *{font-family:'Noto Sans JP',sans-serif}
    section[data-testid="stSidebar"]{background:linear-gradient(180deg,#1565C0 0%,#1976D2 40%,#2196F3 100%)}
    section[data-testid="stSidebar"] *{color:#fff!important}
    section[data-testid="stSidebar"] .stSelectbox>div>div,section[data-testid="stSidebar"] input{background:rgba(255,255,255,.15)!important;border:1px solid rgba(255,255,255,.3)!important;color:#fff!important}
    .main-header{background:linear-gradient(135deg,#1976D2,#2196F3);color:#fff;padding:1.2rem 1.5rem;border-radius:12px;margin-bottom:1.5rem;font-size:1.4rem;font-weight:700;letter-spacing:.05em}
    .stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:2px solid #E3F2FD}
    .stTabs [data-baseweb="tab"]{padding:.6rem 1.5rem;font-weight:500;color:#666;border-bottom:3px solid transparent}
    .stTabs [aria-selected="true"]{color:#1976D2!important;border-bottom:3px solid #1976D2!important;background:#E3F2FD;border-radius:8px 8px 0 0}
    .stButton>button[kind="primary"],.stButton>button[data-testid="stBaseButton-primary"]{background:linear-gradient(135deg,#1976D2,#2196F3)!important;color:#fff!important;border:none!important;border-radius:8px!important;font-weight:600!important;padding:.5rem 1.5rem!important;transition:all .2s ease!important}
    .stButton>button[kind="primary"]:hover,.stButton>button[data-testid="stBaseButton-primary"]:hover{background:linear-gradient(135deg,#1565C0,#1976D2)!important;box-shadow:0 4px 12px rgba(25,118,210,.4)!important}
    .stButton>button[kind="secondary"],.stButton>button[data-testid="stBaseButton-secondary"]{border:2px solid #2196F3!important;color:#1976D2!important;background:#fff!important;border-radius:8px!important;font-weight:500!important}
    .r-row{padding:.5rem 1rem;border-radius:8px;margin:.3rem 0;font-size:.9rem;display:flex;align-items:center;gap:.5rem}
    .r-ok{background:#E8F5E9;color:#2E7D32;border-left:4px solid #4CAF50}
    .r-err{background:#FFEBEE;color:#C62828;border-left:4px solid #F44336}
    .r-warn{background:#FFF8E1;color:#F57F17;border-left:4px solid #FFC107}
    .r-skip{background:#F5F5F5;color:#757575;border-left:4px solid #BDBDBD}
    .r-del{background:#FCE4EC;color:#AD1457;border-left:4px solid #E91E63}
    [data-testid="stFileUploader"]{border:2px dashed #90CAF9!important;border-radius:12px!important;padding:.5rem!important}
    .info-card{background:#FAFAFA;border:1px solid #E0E0E0;border-radius:10px;padding:1rem 1.2rem;margin-bottom:1rem}
    .info-card h4{margin:0 0 .5rem 0;color:#1976D2;font-size:1rem}
    [data-testid="stDataEditor"] input{ime-mode:active!important}
    </style>
    """, unsafe_allow_html=True)

def sr(kind, name, msg):
    cls = {"ok":"r-ok","err":"r-err","warn":"r-warn","skip":"r-skip","del":"r-del"}.get(kind,"r-ok")
    icon = {"ok":"✓","err":"✕","warn":"△","skip":"―","del":"✕"}.get(kind,"●")
    st.markdown(f'<div class="r-row {cls}"><strong>{icon}</strong> <strong>{name}</strong>　{msg}</div>', unsafe_allow_html=True)

# ============================================================
# API: 認証
# ============================================================
def get_token():
    cfg = get_config()
    cid, ci, cs = cfg.get("contract_id",""), cfg.get("client_id",""), cfg.get("client_secret","")
    if not cid or not ci or not cs: return None
    ck = f"_tc_{cid}"
    cached = st.session_state.get(ck)
    if cached and cached.get("ea",0) > time.time(): return cached["at"]
    try:
        cred = base64.b64encode(f"{ci}:{cs}".encode()).decode()
        r = requests.post(get_auth_url(),
            headers={"Authorization":f"Basic {cred}","Content-Type":"application/x-www-form-urlencoded"},
            data={"grant_type":"client_credentials","scope":"pos.products:read pos.products:write"})
        if r.status_code == 200:
            d = r.json(); t = d.get("access_token")
            st.session_state[ck] = {"at":t,"ea":time.time()+d.get("expires_in",3600)-60}
            return t
    except: pass
    return None

# ============================================================
# API: 画像
# ============================================================
def img_upload(file_obj, api_key):
    try:
        img = Image.open(file_obj)
        if img.mode != "RGB": img = img.convert("RGB")
        img.thumbnail((1200,1200), Image.LANCZOS)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        r = requests.post("https://api.imgbb.com/1/upload", data={"key":api_key,"image":b64,"expiration":600})
        if r.status_code == 200:
            j = r.json()
            if j.get("success"): return j["data"]["url"], None
            return None, j.get("error",{}).get("message","不明")
        return None, f"HTTP {r.status_code}"
    except Exception as e: return None, str(e)

def img_register(token, pid, image_url):
    url = f"{get_api_base()}/products/{pid}/image"
    h = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    for a in range(5):
        r = requests.put(url, headers=h, json={"imageUrl":image_url})
        if r.status_code == 404 and a < 4: time.sleep(2); continue
        break
    return (True,"OK") if r.status_code == 200 else (False,f"HTTP {r.status_code}: {r.text}")

def icon_register(token, pid, image_url):
    url = f"{get_api_base()}/products/{pid}/icon_image"
    h = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    for a in range(5):
        r = requests.put(url, headers=h, json={"imageUrl":image_url})
        if r.status_code == 404 and a < 4: time.sleep(2); continue
        break
    return (True,"OK") if r.status_code == 200 else (False,f"HTTP {r.status_code}: {r.text}")

def img_link(token, pid, file_obj, api_key):
    url, err = img_upload(file_obj, api_key)
    if err: return False, f"imgBB失敗: {err}"
    ok1,m1 = img_register(token, pid, url)
    ok2,m2 = icon_register(token, pid, url)
    if ok1 and ok2: return True, "画像・アイコン登録完了"
    elif ok1: return True, f"画像OK / アイコン失敗: {m2}"
    elif ok2: return False, f"画像失敗: {m1} / アイコンOK"
    else: return False, f"画像失敗: {m1} / アイコン失敗: {m2}"

# ============================================================
# API: 部門・商品
# ============================================================
@st.cache_data(ttl=120)
def get_categories():
    token = get_token()
    if not token: return []
    cats, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/categories",
            headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code != 200: break
        d = r.json()
        if not isinstance(d, list): break
        cats.extend(d)
        if len(d) < 1000: break
        p += 1
    return cats

@st.cache_data(ttl=120)
def get_products():
    token = get_token()
    if not token: return []
    prods, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/products",
            headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code != 200: break
        d = r.json()
        if not isinstance(d, list): break
        prods.extend(d)
        if len(d) < 1000: break
        p += 1
    return prods

# ============================================================
# DataFrame ヘルパー
# ============================================================
def col_config(visible, mode="new"):
    cfg = {}; cat_opts = _cat_options()
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category":
            cfg[k] = st.column_config.SelectboxColumn(k, options=cat_opts, default="", required=d.get("required",False))
        elif d["type"] == "select":
            cfg[k] = st.column_config.SelectboxColumn(k, options=d.get("options",[]), default=d["default"], required=d.get("required",False))
        elif d["type"] == "number":
            cfg[k] = st.column_config.NumberColumn(k, default=d["default"], required=d.get("required",False))
        else:
            cfg[k] = st.column_config.TextColumn(k, default=d["default"], max_chars=d.get("max"), required=d.get("required",False))
    return cfg

def empty_row(visible):
    return {k: FIELD_DEFS[k]["default"] for k in visible}

def prod_row(p, visible):
    row = {}
    cat_map = {safe_str(c.get("categoryId","")): safe_str(c.get("categoryName","")) for c in get_categories()}
    for k in visible:
        d = FIELD_DEFS[k]; v = p.get(d["api"], d["default"])
        if d["type"] == "select": v = api2sel(safe_str(v), d.get("options",[]))
        elif d["type"] == "category":
            cid = safe_str(v); cn = cat_map.get(cid,"")
            v = f"{cid}:{cn}" if cid and cn else (cid if cid else "")
        elif d["type"] == "number": v = safe_float(v, d["default"])
        else: v = safe_str(v, d["default"])
        row[k] = v
    row["productId"] = safe_str(p.get("productId",""))
    return row

def row_to_post_payload(row):
    name = row.get("商品名","")
    if not name or safe_str(name).strip() == "": return None
    payload = {}
    for k,d in FIELD_DEFS.items():
        if k not in row or not d.get("post",True): continue
        v = row[k]
        if d["type"] == "select": v = sel2api(v)
        elif d["type"] == "category":
            v = safe_str(v).split(":")[0] if v and ":" in safe_str(v) else safe_str(v)
        if is_empty(v) and not d.get("send_empty",False): continue
        payload[d["api"]] = safe_str(v)
    return payload

def diff_payload(old_row, new_row):
    diff = {}
    for k,d in FIELD_DEFS.items():
        if k not in old_row or k not in new_row: continue
        ov,nv = old_row[k], new_row[k]
        if d["type"] == "select": ov,nv = sel2api(ov),sel2api(nv)
        elif d["type"] == "category":
            ov = safe_str(ov).split(":")[0] if ov and ":" in safe_str(ov) else safe_str(ov)
            nv = safe_str(nv).split(":")[0] if nv and ":" in safe_str(nv) else safe_str(nv)
        if safe_str(ov) != safe_str(nv):
            if is_empty(nv) and not d.get("send_empty",False): continue
            diff[d["api"]] = safe_str(nv)
    return diff

# ============================================================
# タブ1テーブル管理（session_state保持）
# ============================================================
def _get_tab1_df(visible):
    key = "tab1_data"
    display_cols = visible + ["画像"]
    if key not in st.session_state:
        rows = [{**empty_row(visible), "画像": ""} for _ in range(5)]
        st.session_state[key] = pd.DataFrame(rows)[display_cols]
    else:
        df = st.session_state[key]
        for col in display_cols:
            if col not in df.columns:
                df[col] = "" if col == "画像" else FIELD_DEFS.get(col,{}).get("default","")
        st.session_state[key] = df[display_cols]
    return st.session_state[key]

def _save_tab1_df(df):
    st.session_state["tab1_data"] = df.copy()

# ============================================================
# ダイアログ: 表示項目設定
# ============================================================
@st.dialog("表示項目の設定", width="large")
def field_settings_dialog():
    optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
    current = get_config().get("visible_fields",[])
    selected = st.multiselect("表示する追加項目", options=optional, default=[c for c in current if c in optional])
    c1,c2 = st.columns(2)
    with c1:
        if st.button("保存",type="primary",use_container_width=True):
            update_config("visible_fields",selected)
            if "tab1_data" in st.session_state: del st.session_state["tab1_data"]
            st.rerun()
    with c2:
        if st.button("キャンセル",use_container_width=True): st.rerun()

# ============================================================
# ダイアログ: 商品フォーム入力
# ============================================================
@st.dialog("商品を追加", width="large")
def add_form_dialog():
    visible = get_visible(); cat_opts = _cat_options(); vals = {}
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category": vals[k] = st.selectbox(k, cat_opts, index=0)
        elif d["type"] == "select": vals[k] = st.selectbox(k, d.get("options",[]), index=0)
        elif d["type"] == "number": vals[k] = st.number_input(k, value=d["default"], step=1)
        else: vals[k] = st.text_input(k, value=d["default"], max_chars=d.get("max"))
    img_file = st.file_uploader("画像（任意）", type=["jpg","jpeg","png","gif"], key="form_img")
    if st.button("登録", type="primary", use_container_width=True):
        payload = {}
        for k in visible:
            d = FIELD_DEFS[k]
            if not d.get("post",True): continue
            v = vals[k]
            if d["type"] == "select": v = sel2api(v)
            elif d["type"] == "category" and v and ":" in safe_str(v): v = safe_str(v).split(":")[0]
            if is_empty(v) and not d.get("send_empty",False): continue
            payload[d["api"]] = safe_str(v)
        if not payload.get("productName"): st.error("商品名は必須です"); return
        if not payload.get("categoryId"): st.error("部門IDは必須です"); return
        with st.spinner("処理中..."):
            token = get_token()
            if not token: st.error("認証に失敗しました"); return
            r = requests.post(f"{get_api_base()}/products",
                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
            if r.status_code in (200,201):
                pid = r.json().get("productId","")
                if img_file and pid:
                    ak = get_config().get("imgbb_api_key","")
                    if ak:
                        img_file.seek(0); ok,msg = img_link(token,pid,img_file,ak)
                        if ok: st.success(f"商品登録完了 (ID:{pid}) 画像・アイコンも登録")
                        else: st.warning(f"商品登録OK (ID:{pid}) / {msg}")
                    else: st.warning(f"商品登録OK (ID:{pid}) / imgBB APIキー未設定")
                else: st.success(f"商品登録完了 (ID:{pid})")
                st.cache_data.clear(); time.sleep(1); st.rerun()
            else: st.error(f"登録失敗 (HTTP {r.status_code}): {r.text}")

# ============================================================
# ページ: 商品管理
# ============================================================
def page_main():
    inject_css()
    st.markdown('<div class="main-header">商品管理</div>', unsafe_allow_html=True)
    cfg = get_config()
    if not cfg.get("contract_id") or not cfg.get("client_id") or not cfg.get("client_secret"):
        st.warning("設定ページでスマレジのAPI接続情報を入力してください。"); st.stop()

    tab1,tab2,tab3 = st.tabs(["商品登録","商品編集","部門管理"])

    # ========== タブ1: 新規登録 ==========
    with tab1:
        visible = get_visible()
        c1,c2 = st.columns([1,1])
        with c1:
            if st.button("⚙ 表示項目設定",key="fs1"): field_settings_dialog()
        with c2:
            if st.button("＋ フォームで追加",key="form_add"): add_form_dialog()
        st.markdown("---")

        uploaded_files = st.file_uploader("画像ファイル（複数可）", type=["jpg","jpeg","png","gif"],
            accept_multiple_files=True, key="tab1_imgs")
        img_map = {}
        if uploaded_files:
            for f in uploaded_files: img_map[f.name] = f
            st.caption(f"{len(img_map)} 件選択中: {', '.join(img_map.keys())}")

        source_df = _get_tab1_df(visible)
        display_cols = visible + ["画像"]
        ccfg = col_config(visible, mode="new")
        if img_map:
            ccfg["画像"] = st.column_config.SelectboxColumn("画像", options=[""]+list(img_map.keys()), default="")
        else:
            ccfg["画像"] = st.column_config.TextColumn("画像", default="", disabled=True)

        edited_df = st.data_editor(source_df, column_config=ccfg,
            num_rows="dynamic", use_container_width=True, key="tab1_editor")
        _save_tab1_df(edited_df)

        col_reg, col_clear = st.columns([3,1])
        with col_reg:
            if st.button("一括登録", type="primary", use_container_width=True, key="bulk_reg"):
                results = []
                with st.spinner("処理中..."):
                    token = get_token()
                    if not token: st.error("認証に失敗しました"); st.stop()
                    ak = get_config().get("imgbb_api_key","")
                    for idx,row in edited_df.iterrows():
                        payload = row_to_post_payload(row)
                        if not payload: continue
                        if not payload.get("categoryId"):
                            results.append(("err",safe_str(row.get("商品名",f"行{idx+1}")),"部門IDは必須です")); continue
                        r = requests.post(f"{get_api_base()}/products",
                            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                        name = safe_str(row.get("商品名",f"行{idx+1}"))
                        if r.status_code in (200,201):
                            pid = r.json().get("productId","")
                            img_name = safe_str(row.get("画像",""))
                            if img_name and img_name in img_map and pid and ak:
                                fo = img_map[img_name]; fo.seek(0)
                                ok,msg = img_link(token,pid,fo,ak)
                                if ok: results.append(("ok",name,f"登録完了 画像あり (ID:{pid})"))
                                else: results.append(("warn",name,f"商品OK / {msg}"))
                            else: results.append(("ok",name,f"登録完了 (ID:{pid})"))
                        else: results.append(("err",name,f"登録失敗 (HTTP {r.status_code}): {r.text}"))
                    st.cache_data.clear()
                if results:
                    st.markdown("#### 実行結果")
                    for k,n,m in results: sr(k,n,m)
        with col_clear:
            if st.button("クリア", use_container_width=True, key="clear_tab1"):
                if "tab1_data" in st.session_state: del st.session_state["tab1_data"]
                st.rerun()

    # ========== タブ2: 商品編集 ==========
    with tab2:
        visible = get_visible()
        c1,c2 = st.columns([1,1])
        with c1:
            if st.button("⚙ 表示項目設定",key="fs2"): field_settings_dialog()
        with c2:
            if st.button("🔄 データ再取得",key="reload_prod"): st.cache_data.clear(); st.rerun()
        prods = get_products()
        if not prods:
            st.info("登録済み商品がありません")
        else:
            rows = [prod_row(p,visible) for p in prods]
            df_edit = pd.DataFrame(rows)
            display_cols = ["productId"] + visible
            ecfg = col_config(visible,mode="edit")
            ecfg["productId"] = st.column_config.TextColumn("商品ID",disabled=True)
            edited_prods = st.data_editor(df_edit[display_cols], column_config=ecfg,
                use_container_width=True, key="tab2_editor", disabled=["productId"])
            st.markdown("---")
            img_mode = st.radio("画像登録方法",["一括アップロード","個別アップロード"],horizontal=True,key="img_mode")

            if img_mode == "一括アップロード":
                bulk_files = st.file_uploader("画像ファイル（複数可）", type=["jpg","jpeg","png","gif"],
                    accept_multiple_files=True, key="tab2_bulk_imgs")
                bmap = {}
                if bulk_files:
                    for f in bulk_files: bmap[f.name] = f
                    st.caption(f"{len(bmap)} 件選択中: {', '.join(bmap.keys())}")
                assigns = {}
                if bmap:
                    st.markdown("**画像の割り当て**")
                    for _,row in edited_prods.iterrows():
                        pid = safe_str(row.get("productId",""))
                        pn = safe_str(row.get("商品名",""))
                        if pid:
                            sel = st.selectbox(f"{pn} (ID:{pid})",["なし"]+list(bmap.keys()),key=f"asgn_{pid}")
                            if sel != "なし": assigns[pid] = sel
                cs,cd = st.columns(2)
                with cs:
                    if st.button("変更を保存",type="primary",use_container_width=True,key="save_edit"):
                        results = []
                        with st.spinner("処理中..."):
                            token = get_token()
                            if not token: st.error("認証に失敗しました"); st.stop()
                            ak = get_config().get("imgbb_api_key","")
                            for idx,nr in edited_prods.iterrows():
                                pid = safe_str(nr.get("productId",""))
                                pn = safe_str(nr.get("商品名",f"ID:{pid}"))
                                if not pid: continue
                                hc = False
                                orow = df_edit.iloc[idx] if idx < len(df_edit) else None
                                if orow is not None:
                                    dp = diff_payload(orow,nr)
                                    if dp:
                                        hc = True
                                        r = requests.patch(f"{get_api_base()}/products/{pid}",
                                            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=dp)
                                        if r.status_code not in (200,204):
                                            results.append(("err",pn,f"更新失敗 (HTTP {r.status_code}): {r.text}")); continue
                                if pid in assigns and ak:
                                    fo = bmap[assigns[pid]]; fo.seek(0)
                                    ok,msg = img_link(token,pid,fo,ak)
                                    if ok: results.append(("ok",pn,"更新＋画像登録完了"))
                                    else: results.append(("warn",pn,f"更新OK / {msg}"))
                                elif hc: results.append(("ok",pn,"更新完了"))
                            st.cache_data.clear()
                        if results:
                            st.markdown("#### 実行結果")
                            for k,n,m in results: sr(k,n,m)
                with cd:
                    if st.button("選択商品を削除",type="secondary",use_container_width=True,key="del_bulk"):
                        st.warning("削除する商品IDを指定してください")
            else:
                popts = {f"{safe_str(p.get('productName',''))} (ID:{safe_str(p.get('productId',''))})": safe_str(p.get("productId","")) for p in prods}
                sl = st.selectbox("対象商品",list(popts.keys()),key="indiv_sel")
                spid = popts.get(sl,"")
                ifile = st.file_uploader("画像をドラッグ＆ドロップ", type=["jpg","jpeg","png","gif"], key="indiv_img")
                if ifile:
                    if st.button("この画像を登録",type="primary",key="indiv_reg"):
                        ok_r = None
                        with st.spinner("処理中..."):
                            token = get_token(); ak = get_config().get("imgbb_api_key","")
                            if not token: st.error("認証に失敗しました")
                            elif not ak: st.error("imgBB APIキーが未設定です")
                            else:
                                ifile.seek(0); ok_r,msg = img_link(token,spid,ifile,ak); st.cache_data.clear()
                        if ok_r is True: st.success(msg)
                        elif ok_r is False: st.error(msg)
                st.markdown("---")
                if st.button("テーブル変更を保存",type="primary",use_container_width=True,key="save_indiv"):
                    results = []
                    with st.spinner("処理中..."):
                        token = get_token()
                        if not token: st.error("認証に失敗しました"); st.stop()
                        for idx,nr in edited_prods.iterrows():
                            pid = safe_str(nr.get("productId",""))
                            pn = safe_str(nr.get("商品名",f"ID:{pid}"))
                            if not pid: continue
                            orow = df_edit.iloc[idx] if idx < len(df_edit) else None
                            if orow is not None:
                                dp = diff_payload(orow,nr)
                                if dp:
                                    r = requests.patch(f"{get_api_base()}/products/{pid}",
                                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=dp)
                                    if r.status_code in (200,204): results.append(("ok",pn,"更新完了"))
                                    else: results.append(("err",pn,f"更新失敗 (HTTP {r.status_code}): {r.text}"))
                    st.cache_data.clear()
                    if results:
                        st.markdown("#### 実行結果")
                        for k,n,m in results: sr(k,n,m)

    # ========== タブ3: 部門管理 ==========
    with tab3:
        cats = get_categories()
        if cats:
            cat_df = pd.DataFrame([{
                "部門ID": safe_str(c.get("categoryId","")),
                "部門名": safe_str(c.get("categoryName","")),
                "表示順": safe_int(c.get("displaySequence"),0)
            } for c in cats])
            edited_cats = st.data_editor(cat_df, use_container_width=True, key="cat_editor", num_rows="dynamic",
                column_config={
                    "部門ID": st.column_config.TextColumn("部門ID",disabled=True),
                    "部門名": st.column_config.TextColumn("部門名"),
                    "表示順": st.column_config.NumberColumn("表示順"),})
        else:
            st.info("部門データがありません")
            edited_cats = pd.DataFrame(columns=["部門ID","部門名","表示順"])
        st.markdown("---")
        st.markdown("**部門を追加**")
        nc1,nc2 = st.columns(2)
        with nc1: new_cn = st.text_input("部門名",key="new_cn")
        with nc2: new_cs = st.number_input("表示順",value=0,key="new_cs")
        c1,c2 = st.columns(2)
        with c1:
            if st.button("部門を追加",type="primary",use_container_width=True,key="add_cat"):
                if new_cn.strip():
                    rv = None
                    with st.spinner("処理中..."):
                        token = get_token()
                        if token:
                            rv = requests.post(f"{get_api_base()}/categories",
                                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                                json={"categoryName":new_cn.strip(),"displaySequence":str(safe_int(new_cs))})
                            st.cache_data.clear()
                    if rv and rv.status_code in (200,201):
                        st.success(f"部門「{new_cn}」を追加"); time.sleep(.5); st.rerun()
                    elif rv: st.error(f"追加失敗 (HTTP {rv.status_code}): {rv.text}")
                    else: st.error("認証に失敗しました")
                else: st.warning("部門名を入力してください")
        with c2:
            if st.button("部門変更を保存",type="secondary",use_container_width=True,key="save_cats"):
                results = []
                with st.spinner("処理中..."):
                    token = get_token()
                    if token and cats:
                        for idx,row in edited_cats.iterrows():
                            cid = safe_str(row.get("部門ID",""))
                            if not cid: continue
                            old = cats[idx] if idx < len(cats) else None
                            if old:
                                ch = {}
                                if safe_str(row.get("部門名","")) != safe_str(old.get("categoryName","")): ch["categoryName"] = safe_str(row["部門名"])
                                if str(safe_int(row.get("表示順",0))) != safe_str(old.get("displaySequence","0")): ch["displaySequence"] = str(safe_int(row["表示順"]))
                                if ch:
                                    r = requests.patch(f"{get_api_base()}/categories/{cid}",
                                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=ch)
                                    if r.status_code in (200,204): results.append(("ok",safe_str(row.get("部門名","")),"更新OK"))
                                    else: results.append(("err",safe_str(row.get("部門名","")),"更新失敗: "+r.text))
                    st.cache_data.clear()
                if results:
                    st.markdown("#### 実行結果")
                    for k,n,m in results: sr(k,n,m)

# ============================================================
# ページ: 設定
# ============================================================
def page_settings():
    inject_css()
    st.markdown('<div class="main-header">設定</div>', unsafe_allow_html=True)
    cfg = get_config()
    for k,v in [("s_cid",cfg.get("contract_id","")),("s_cli",cfg.get("client_id","")),
                ("s_sec",cfg.get("client_secret","")),("s_img",cfg.get("imgbb_api_key","")),
                ("s_sb",cfg.get("use_sandbox",True))]:
        if k not in st.session_state: st.session_state[k] = v

    st.markdown('<div class="info-card"><h4>スマレジ API 接続設定</h4>デベロッパーズのアプリ環境設定から取得した値を入力してください。</div>', unsafe_allow_html=True)
    use_sb = st.toggle("サンドボックス環境",value=st.session_state.s_sb,key="t_sb")
    
    # 🌟 Secretsに値がある場合は入力をロックして「設定済み」であることを明示
    is_secret_cid = "CONTRACT_ID" in st.secrets
    is_secret_cli = "CLIENT_ID" in st.secrets
    is_secret_sec = "CLIENT_SECRET" in st.secrets
    
    cid = st.text_input("契約ID" + (" (クラウド設定読込中)" if is_secret_cid else ""), value=st.session_state.s_cid, key="i_cid", disabled=is_secret_cid)
    cli = st.text_input("クライアントID" + (" (クラウド設定読込中)" if is_secret_cli else ""), value=st.session_state.s_cli, key="i_cli", disabled=is_secret_cli)
    sec = st.text_input("クライアントシークレット" + (" (クラウド設定読込中)" if is_secret_sec else ""), value=st.session_state.s_sec, type="password", key="i_sec", disabled=is_secret_sec)
    
    st.markdown("---")
    st.markdown('<div class="info-card"><h4>imgBB API キー</h4>商品画像アップロード用。<a href="https://api.imgbb.com/" target="_blank">imgBB</a> で取得。</div>', unsafe_allow_html=True)
    
    is_secret_img = "IMGBB_API_KEY" in st.secrets
    imgk = st.text_input("imgBB APIキー" + (" (クラウド設定読込中)" if is_secret_img else ""), value=st.session_state.s_img, type="password", key="i_img", disabled=is_secret_img)
    
    st.markdown("---")
    st.markdown('<div class="info-card"><h4>デフォルト表示項目</h4></div>', unsafe_allow_html=True)
    optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
    cur_vis = cfg.get("visible_fields",[])
    sel_vis = st.multiselect("追加表示項目",options=optional,default=[c for c in cur_vis if c in optional],key="s_vis")
    st.markdown("---")
    if st.button("設定を保存",type="primary",use_container_width=True):
        st.session_state.s_cid=cid; st.session_state.s_cli=cli; st.session_state.s_sec=sec
        st.session_state.s_img=imgk; st.session_state.s_sb=use_sb
        update_config_bulk({"contract_id":cid,"client_id":cli,"client_secret":sec,
            "imgbb_api_key":imgk,"use_sandbox":use_sb,"visible_fields":sel_vis})
        for k in [k for k in st.session_state if k.startswith("_tc_")]: del st.session_state[k]
        st.cache_data.clear(); st.success("設定を保存しました")
    st.markdown("---")
    st.markdown('<div class="info-card"><h4>接続テスト</h4></div>', unsafe_allow_html=True)
    st.caption(f"環境: {'サンドボックス' if use_sb else '本番'} ／ 契約ID: {cid if cid else '(未設定)'}")
    if st.button("接続テスト実行",use_container_width=True,key="test_conn"):
        if not cid or not cli or not sec: st.error("全項目を入力してください")
        else:
            with st.spinner("処理中..."):
                try:
                    cr = base64.b64encode(f"{cli}:{sec}".encode()).decode()
                    tu = f"https://id.smaregi.{'dev' if use_sb else 'jp'}/app/{cid}/token"
                    r = requests.post(tu, headers={"Authorization":f"Basic {cr}","Content-Type":"application/x-www-form-urlencoded"},
                        data={"grant_type":"client_credentials","scope":"pos.products:read pos.products:write"})
                    if r.status_code == 200: st.success(f"接続成功！ (有効期限: {r.json().get('expires_in','?')}秒)")
                    else: st.error(f"接続失敗 (HTTP {r.status_code}): {r.text}")
                except Exception as e: st.error(f"接続エラー: {e}")

# ============================================================
# ナビゲーション
# ============================================================
nav = st.navigation([
    st.Page(page_main, title="商品管理", icon="📦"),
    st.Page(page_settings, title="設定", icon="⚙"),
])
nav.run()