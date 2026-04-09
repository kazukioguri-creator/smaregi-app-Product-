import streamlit as st
import requests
import json
import time
import base64
import datetime
import pandas as pd
from collections import OrderedDict
from io import BytesIO
from PIL import Image
from pathlib import Path
from google.oauth2 import service_account
from google.cloud import storage

# ============================================================
# 定数
# ============================================================
CONFIG_PATH = Path("smaregi_config.json")
DEFAULT_CONFIG = {
    "contract_id": "", "client_id": "", "client_secret": "",
    "visible_fields": [], "use_sandbox": True,
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
# 設定管理
# ============================================================
def _load_file_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try: cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except: pass
    try:
        if "CONTRACT_ID" in st.secrets: cfg["contract_id"] = st.secrets["CONTRACT_ID"]
        if "CLIENT_ID" in st.secrets: cfg["client_id"] = st.secrets["CLIENT_ID"]
        if "CLIENT_SECRET" in st.secrets: cfg["client_secret"] = st.secrets["CLIENT_SECRET"]
    except Exception:
        pass
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
    if "cat_options_cache" in st.session_state:
        return st.session_state["cat_options_cache"]
    cats = get_categories()
    opts = [""] + [f"{safe_str(c.get('categoryId',''))}:{safe_str(c.get('categoryName',''))}" for c in cats]
    st.session_state["cat_options_cache"] = opts
    return opts

def _refresh_cat_options():
    if "cat_options_cache" in st.session_state:
        del st.session_state["cat_options_cache"]

# ============================================================
# CSS — Legacy Enterprise SaaS スタイル
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    /* ---- Base ---- */
    * {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue',
                     Arial, 'Hiragino Kaku Gothic ProN', 'Hiragino Sans', Meiryo, sans-serif;
        font-size: 13px;
    }

    /* ---- App background ---- */
    .stApp, [data-testid="stAppViewContainer"] { background: #f0f2f5; }
    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 2rem !important;
        max-width: 1280px !important;
    }

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] {
        background: #1c2b45 !important;
        border-right: 1px solid #0d1929;
    }
    section[data-testid="stSidebar"] * { color: #b8cfe0 !important; }
    section[data-testid="stSidebar"] a,
    section[data-testid="stSidebar"] [data-testid="stSidebarNav"] span {
        color: #ccdaeb !important;
        font-weight: 500 !important;
        letter-spacing: .01em;
    }
    section[data-testid="stSidebar"] [aria-selected="true"] span,
    section[data-testid="stSidebar"] [data-testid="stSidebarNav"] [aria-current] span {
        color: #ffffff !important;
        background: rgba(255,255,255,.1) !important;
        border-radius: 4px;
    }
    section[data-testid="stSidebar"] .stSelectbox > div > div,
    section[data-testid="stSidebar"] input {
        background: rgba(255,255,255,.1) !important;
        border: 1px solid rgba(255,255,255,.2) !important;
        color: #fff !important;
    }

    /* ---- Page header ---- */
    .main-header {
        background: #ffffff;
        color: #1c2b45;
        padding: .7rem 1.1rem;
        border-radius: 3px;
        margin-bottom: 1.1rem;
        font-size: 1.05rem;
        font-weight: 700;
        letter-spacing: .02em;
        border-left: 4px solid #2563eb;
        border-bottom: 1px solid #dde1e7;
        box-shadow: 0 1px 3px rgba(0,0,0,.07), 0 1px 2px rgba(0,0,0,.04);
    }

    /* ---- Section label ---- */
    .section-label {
        font-size: .7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .08em;
        color: #6b7a8d;
        border-bottom: 1px solid #dde1e7;
        padding-bottom: .35rem;
        margin-bottom: .8rem;
    }

    /* ---- Tabs ---- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0 !important;
        background: #ffffff;
        border: 1px solid #dde1e7;
        border-radius: 3px 3px 0 0;
        border-bottom: none;
        padding: 0;
    }
    .stTabs [data-baseweb="tab"] {
        padding: .55rem 1.2rem !important;
        font-size: .8rem !important;
        font-weight: 600 !important;
        color: #4a5568 !important;
        border-right: 1px solid #dde1e7 !important;
        border-radius: 0 !important;
        letter-spacing: .02em;
        text-transform: uppercase;
    }
    .stTabs [aria-selected="true"] {
        color: #2563eb !important;
        background: #eff6ff !important;
        border-bottom: 2px solid #2563eb !important;
    }
    .stTabs [data-baseweb="tab-panel"] {
        background: #ffffff;
        border: 1px solid #dde1e7;
        border-top: none;
        border-radius: 0 0 3px 3px;
        padding: 1rem 1rem !important;
        box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }

    /* ---- Buttons ---- */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="stBaseButton-primary"] {
        background: #2563eb !important;
        color: #fff !important;
        border: 1px solid #1d4ed8 !important;
        border-radius: 3px !important;
        font-weight: 600 !important;
        font-size: .8rem !important;
        padding: .4rem 1rem !important;
        letter-spacing: .02em;
        box-shadow: 0 1px 2px rgba(0,0,0,.12) !important;
        transition: background .12s, box-shadow .12s !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="stBaseButton-primary"]:hover {
        background: #1d4ed8 !important;
        box-shadow: 0 2px 6px rgba(37,99,235,.35) !important;
    }
    .stButton > button[kind="secondary"],
    .stButton > button[data-testid="stBaseButton-secondary"] {
        background: #ffffff !important;
        color: #374151 !important;
        border: 1px solid #ced4da !important;
        border-radius: 3px !important;
        font-size: .8rem !important;
        font-weight: 500 !important;
        box-shadow: 0 1px 2px rgba(0,0,0,.06) !important;
    }
    .stButton > button[kind="secondary"]:hover,
    .stButton > button[data-testid="stBaseButton-secondary"]:hover {
        background: #f3f4f6 !important;
        border-color: #adb5bd !important;
    }

    /* ---- Result rows ---- */
    .r-row {
        padding: .38rem .75rem;
        border-radius: 3px;
        margin: .2rem 0;
        font-size: .8rem;
        display: flex;
        align-items: center;
        gap: .5rem;
        border: 1px solid transparent;
        font-weight: 500;
    }
    .r-ok   { background: #d1fae5; color: #065f46; border-color: #6ee7b7; }
    .r-err  { background: #fee2e2; color: #7f1d1d; border-color: #fca5a5; }
    .r-warn { background: #fef9c3; color: #713f12; border-color: #fde047; }
    .r-skip { background: #f3f4f6; color: #6b7280; border-color: #d1d5db; }
    .r-del  { background: #fce7f3; color: #831843; border-color: #f9a8d4; }

    /* ---- Info card ---- */
    .info-card {
        background: #fff;
        border: 1px solid #dde1e7;
        border-radius: 3px;
        padding: .75rem 1rem;
        margin-bottom: .9rem;
        box-shadow: 0 1px 3px rgba(0,0,0,.05);
    }
    .info-card h4 {
        margin: 0 0 .35rem 0;
        color: #1c2b45;
        font-size: .72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .07em;
    }
    .info-card p {
        margin: 0;
        color: #4a5568;
        font-size: .8rem;
        line-height: 1.5;
    }

    /* ---- Summary bar ---- */
    .summary-bar {
        display: flex;
        gap: .8rem;
        margin-bottom: 1rem;
    }
    .summary-item {
        background: #fff;
        border: 1px solid #dde1e7;
        border-radius: 3px;
        padding: .5rem .9rem;
        min-width: 100px;
        box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }
    .summary-item .si-label {
        font-size: .65rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .08em;
        color: #6b7a8d;
        display: block;
        margin-bottom: .15rem;
    }
    .summary-item .si-value {
        font-size: 1.1rem;
        font-weight: 700;
        color: #1c2b45;
    }

    /* ---- Badge ---- */
    .badge {
        display: inline-block;
        padding: .15rem .5rem;
        border-radius: 2px;
        font-size: .65rem;
        font-weight: 700;
        letter-spacing: .06em;
        text-transform: uppercase;
    }
    .badge-blue  { background: #dbeafe; color: #1d4ed8; }
    .badge-green { background: #d1fae5; color: #065f46; }
    .badge-gray  { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }

    /* ---- File uploader ---- */
    [data-testid="stFileUploader"] {
        border: 2px dashed #93c5fd !important;
        border-radius: 3px !important;
        background: #f8faff !important;
    }
    [data-testid="stFileUploader"] * { font-size: .8rem !important; }

    /* ---- Data editor ---- */
    [data-testid="stDataEditor"] {
        border: 1px solid #dde1e7 !important;
        border-radius: 3px !important;
        font-size: .8rem !important;
    }
    [data-testid="stDataEditor"] input { ime-mode: active !important; }
    </style>
    """, unsafe_allow_html=True)

def sr(kind, name, msg):
    cls  = {"ok":"r-ok","err":"r-err","warn":"r-warn","skip":"r-skip","del":"r-del"}.get(kind,"r-ok")
    icon = {"ok":"✓","err":"✕","warn":"△","skip":"―","del":"✕"}.get(kind,"●")
    st.markdown(
        f'<div class="r-row {cls}"><span>{icon}</span>'
        f'<strong>{name}</strong><span style="opacity:.6; margin: 0 4px;">|</span>{msg}</div>',
        unsafe_allow_html=True
    )

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
# API: 画像登録 (🌟Google Cloud Storage 署名付きURL版)
# ============================================================
def get_gcp_credentials():
    gcp_json_str = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_dict = json.loads(gcp_json_str)
    credentials = service_account.Credentials.from_service_account_info(gcp_dict)
    return credentials

def upload_and_link_image(token, product_id, file_obj):
    """
    Google Cloud Storage (GCS) に画像をアップロードし、
    15分限定の署名付きURLを発行してスマレジに連携する最強・安全・確実なロジック。
    """
    try:
        # 1. 画像の軽量化 (JPEG変換)
        img = Image.open(file_obj)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((800, 800), Image.LANCZOS)
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG", quality=85)
        img_bytes.seek(0)
        
        # 2. GCSへアップロード
        bucket_name = st.secrets["GCP_BUCKET_NAME"]
        credentials = get_gcp_credentials()
        client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = client.bucket(bucket_name)
        
        # ファイル名を一意にする（ミリ秒のタイムスタンプを使用）
        filename = f"products/{product_id}_{int(time.time() * 1000)}.jpg"
        blob = bucket.blob(filename)
        
        # アップロード実行
        blob.upload_from_file(img_bytes, content_type="image/jpeg")
        
        # 3. 15分間限定の「署名付きURL」を発行
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method="GET"
        )
        
        # 4. スマレジへ imageUrl を送信 (PUT)
        url = f"{get_api_base()}/products/{product_id}/image"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"imageUrl": signed_url}

        last_r = None
        for attempt in range(4):
            try:
                r = requests.put(url, headers=headers, json=payload, timeout=30)
                last_r = r
                if r.status_code in (200, 201, 204):
                    return True, "画像登録完了（GCS経由）"
                if r.status_code == 404 and attempt < 3:
                    time.sleep(2 ** attempt) # 商品の非同期作成待ち
                    continue
                break
            except requests.exceptions.RequestException:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                break

        if last_r is not None:
            return False, f"スマレジ連携失敗 (HTTP {last_r.status_code}): {last_r.text[:100]}"
        return False, "スマレジ連携に失敗しました（タイムアウト）"

    except Exception as e:
        return False, f"システムエラー: {str(e)}"

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
# タブ1: session_state保持
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
    st.markdown('<div class="section-label">追加表示する項目を選択</div>', unsafe_allow_html=True)
    selected = st.multiselect("", options=optional, default=[c for c in current if c in optional])
    c1,c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", use_container_width=True):
            update_config("visible_fields", selected)
            if "tab1_data" in st.session_state: del st.session_state["tab1_data"]
            st.rerun()
    with c2:
        if st.button("キャンセル", use_container_width=True): st.rerun()

# ============================================================
# ダイアログ: 商品フォーム入力
# ============================================================
@st.dialog("商品を追加", width="large")
def add_form_dialog():
    visible = get_visible(); cat_opts = _cat_options(); vals = {}
    st.markdown('<div class="section-label">商品情報</div>', unsafe_allow_html=True)
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category": vals[k] = st.selectbox(k, cat_opts, index=0)
        elif d["type"] == "select": vals[k] = st.selectbox(k, d.get("options",[]), index=0)
        elif d["type"] == "number": vals[k] = st.number_input(k, value=d["default"], step=1)
        else: vals[k] = st.text_input(k, value=d["default"], max_chars=d.get("max"))
    st.markdown("---")
    st.markdown('<div class="section-label">画像（任意）</div>', unsafe_allow_html=True)
    img_file = st.file_uploader("JPG / PNG / GIF", type=["jpg","jpeg","png","gif"], key="form_img")

    if st.button("登録実行", type="primary", use_container_width=True):
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

        with st.spinner("登録中..."):
            token = get_token()
            if not token: st.error("認証に失敗しました"); return
            r = requests.post(f"{get_api_base()}/products",
                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
            if r.status_code in (200,201):
                pid = r.json().get("productId","")
                if img_file and pid:
                    img_file.seek(0)
                    ok, msg = upload_and_link_image(token, pid, img_file)
                    if ok: st.success(f"登録完了 (ID: {pid}) — 画像も登録しました")
                    else: st.warning(f"商品登録OK (ID: {pid}) — 画像: {msg}")
                else:
                    st.success(f"登録完了 (ID: {pid})")
                get_products.clear()
                time.sleep(1); st.rerun()
            else:
                st.error(f"登録失敗 (HTTP {r.status_code}): {r.text}")

# ============================================================
# ページ: 商品管理
# ============================================================
def page_main():
    inject_css()

    cfg = get_config()
    if not cfg.get("contract_id") or not cfg.get("client_id") or not cfg.get("client_secret"):
        st.markdown('<div class="main-header">商品管理</div>', unsafe_allow_html=True)
        st.warning("設定ページで API 接続情報を入力してください。")
        st.stop()

    # ---- ヘッダー + サマリーバー ----
    col_h, col_b = st.columns([3, 1])
    with col_h:
        env_badge = '<span class="badge badge-gray">Sandbox</span>' if cfg.get("use_sandbox") else '<span class="badge badge-green">本番</span>'
        st.markdown(f'<div class="main-header">商品管理 &nbsp;{env_badge}</div>', unsafe_allow_html=True)
    with col_b:
        st.markdown("<div style='padding-top:.4rem'></div>", unsafe_allow_html=True)

    prods_for_count = get_products()
    cats_for_count  = get_categories()
    st.markdown(f"""
    <div class="summary-bar">
      <div class="summary-item"><span class="si-label">登録商品数</span><span class="si-value">{len(prods_for_count)}</span></div>
      <div class="summary-item"><span class="si-label">部門数</span><span class="si-value">{len(cats_for_count)}</span></div>
      <div class="summary-item"><span class="si-label">環境</span><span class="si-value" style="font-size:.85rem">{'Sandbox' if cfg.get('use_sandbox') else '本番'}</span></div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["新規登録", "商品編集", "部門管理"])

    # ========== タブ1: 新規登録 ==========
    with tab1:
        visible = get_visible()

        # ツールバー
        tc1, tc2, tc3 = st.columns([1, 1, 4])
        with tc1:
            if st.button("項目設定", key="fs1"): field_settings_dialog()
        with tc2:
            if st.button("フォーム追加", key="form_add"): add_form_dialog()

        st.markdown('<div class="section-label" style="margin-top:.6rem">画像ファイル</div>', unsafe_allow_html=True)
        uploaded_files = st.file_uploader(
            "複数ファイル同時アップロード可 (JPG / PNG / GIF)",
            type=["jpg","jpeg","png","gif"],
            accept_multiple_files=True, key="tab1_imgs"
        )
        img_map = {}
        if uploaded_files:
            for f in uploaded_files: img_map[f.name] = f
            st.caption(f"{len(img_map)} 件選択 : {', '.join(img_map.keys())}")

        st.markdown('<div class="section-label" style="margin-top:.8rem">商品データ入力</div>', unsafe_allow_html=True)
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

        st.markdown("---")
        col_reg, col_clear = st.columns([4, 1])
        with col_reg:
            if st.button("一括登録を実行", type="primary", use_container_width=True, key="bulk_reg"):
                results = []
                with st.spinner("登録処理中..."):
                    token = get_token()
                    if not token: st.error("認証に失敗しました"); st.stop()
                    for idx, row in edited_df.iterrows():
                        payload = row_to_post_payload(row)
                        if not payload: continue
                        if not payload.get("categoryId"):
                            results.append(("err", safe_str(row.get("商品名",f"行{idx+1}")), "部門IDは必須です"))
                            continue
                        r = requests.post(f"{get_api_base()}/products",
                            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                        name = safe_str(row.get("商品名", f"行{idx+1}"))
                        if r.status_code in (200,201):
                            pid = r.json().get("productId","")
                            img_name = safe_str(row.get("画像",""))
                            if img_name and img_name in img_map and pid:
                                fo = img_map[img_name]; fo.seek(0)
                                ok, msg = upload_and_link_image(token, pid, fo)
                                if ok: results.append(("ok", name, f"登録完了・画像あり (ID:{pid})"))
                                else:  results.append(("warn", name, f"商品OK / 画像: {msg}"))
                            else:
                                results.append(("ok", name, f"登録完了 (ID:{pid})"))
                        else:
                            results.append(("err", name, f"登録失敗 (HTTP {r.status_code}): {r.text[:120]}"))
                    # 商品キャッシュのみクリア
                    get_products.clear()
                if results:
                    ok_cnt  = sum(1 for k,_,_ in results if k=="ok")
                    err_cnt = sum(1 for k,_,_ in results if k=="err")
                    st.markdown(
                        f'<div style="margin:.6rem 0 .3rem;font-size:.75rem;color:#6b7a8d">'
                        f'完了 {ok_cnt} 件 / エラー {err_cnt} 件</div>',
                        unsafe_allow_html=True
                    )
                    for k,n,m in results: sr(k,n,m)
        with col_clear:
            if st.button("クリア", use_container_width=True, key="clear_tab1"):
                if "tab1_data" in st.session_state: del st.session_state["tab1_data"]
                st.rerun()

    # ========== タブ2: 商品編集 ==========
    with tab2:
        visible = get_visible()
        tc1, tc2 = st.columns([1, 1])
        with tc1:
            if st.button("項目設定", key="fs2"): field_settings_dialog()
        with tc2:
            if st.button("データ再取得", key="reload_prod"):
                st.cache_data.clear()
                _refresh_cat_options()
                st.rerun()

        prods = get_products()
        if not prods:
            st.info("登録済み商品がありません。")
        else:
            st.caption(f"全 {len(prods)} 件")
            rows = [prod_row(p, visible) for p in prods]
            df_edit = pd.DataFrame(rows)
            display_cols = ["productId"] + visible
            ecfg = col_config(visible, mode="edit")
            ecfg["productId"] = st.column_config.TextColumn("商品ID", disabled=True)
            edited_prods = st.data_editor(df_edit[display_cols], column_config=ecfg,
                use_container_width=True, key="tab2_editor", disabled=["productId"])

            st.markdown("---")
            st.markdown('<div class="section-label">画像登録方法</div>', unsafe_allow_html=True)
            img_mode = st.radio("", ["一括アップロード", "個別アップロード"], horizontal=True, key="img_mode",
                                label_visibility="collapsed")

            if img_mode == "一括アップロード":
                bulk_files = st.file_uploader("画像ファイル（複数可）", type=["jpg","jpeg","png","gif"],
                    accept_multiple_files=True, key="tab2_bulk_imgs")
                bmap = {}
                if bulk_files:
                    for f in bulk_files: bmap[f.name] = f
                    st.caption(f"{len(bmap)} 件選択 : {', '.join(bmap.keys())}")
                assigns = {}
                if bmap:
                    st.markdown('<div class="section-label" style="margin-top:.7rem">画像の割り当て</div>', unsafe_allow_html=True)
                    for _, row in edited_prods.iterrows():
                        pid = safe_str(row.get("productId",""))
                        pn  = safe_str(row.get("商品名",""))
                        if pid:
                            sel = st.selectbox(f"{pn} (ID:{pid})", ["なし"]+list(bmap.keys()), key=f"asgn_{pid}")
                            if sel != "なし": assigns[pid] = sel

                cs, cd = st.columns(2)
                with cs:
                    if st.button("変更を保存", type="primary", use_container_width=True, key="save_edit"):
                        results = []
                        with st.spinner("処理中..."):
                            token = get_token()
                            if not token: st.error("認証に失敗しました"); st.stop()
                            for idx, nr in edited_prods.iterrows():
                                pid = safe_str(nr.get("productId",""))
                                pn  = safe_str(nr.get("商品名", f"ID:{pid}"))
                                if not pid: continue
                                hc   = False
                                orow = df_edit.iloc[idx] if idx < len(df_edit) else None
                                if orow is not None:
                                    dp = diff_payload(orow, nr)
                                    if dp:
                                        hc = True
                                        r = requests.patch(f"{get_api_base()}/products/{pid}",
                                            headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=dp)
                                        if r.status_code not in (200,204):
                                            results.append(("err", pn, f"更新失敗 (HTTP {r.status_code}): {r.text[:120]}")); continue
                                if pid in assigns:
                                    fo = bmap[assigns[pid]]; fo.seek(0)
                                    ok, msg = upload_and_link_image(token, pid, fo)
                                    if ok: results.append(("ok", pn, "更新＋画像登録完了"))
                                    else:  results.append(("warn", pn, f"更新OK / 画像: {msg}"))
                                elif hc:
                                    results.append(("ok", pn, "更新完了"))
                            get_products.clear()
                        if results:
                            for k,n,m in results: sr(k,n,m)
                with cd:
                    if st.button("選択商品を削除", type="secondary", use_container_width=True, key="del_bulk"):
                        st.warning("この機能は現在ロックされています。")

            else:  # 個別アップロード
                popts = {
                    f"{safe_str(p.get('productName',''))} (ID:{safe_str(p.get('productId',''))})":
                    safe_str(p.get("productId","")) for p in prods
                }
                sl   = st.selectbox("対象商品", list(popts.keys()), key="indiv_sel")
                spid = popts.get(sl, "")
                ifile = st.file_uploader("画像をドラッグ＆ドロップ", type=["jpg","jpeg","png","gif"], key="indiv_img")
                if ifile:
                    if st.button("この画像を登録", type="primary", key="indiv_reg"):
                        with st.spinner("アップロード中..."):
                            token = get_token()
                            if not token:
                                st.error("認証に失敗しました")
                            else:
                                ifile.seek(0)
                                ok, msg = upload_and_link_image(token, spid, ifile)
                                get_products.clear()
                                if ok: st.success(msg)
                                else:  st.error(msg)
                st.markdown("---")
                if st.button("テーブル変更を保存", type="primary", use_container_width=True, key="save_indiv"):
                    results = []
                    with st.spinner("処理中..."):
                        token = get_token()
                        if not token: st.error("認証に失敗しました"); st.stop()
                        for idx, nr in edited_prods.iterrows():
                            pid = safe_str(nr.get("productId",""))
                            pn  = safe_str(nr.get("商品名", f"ID:{pid}"))
                            if not pid: continue
                            orow = df_edit.iloc[idx] if idx < len(df_edit) else None
                            if orow is not None:
                                dp = diff_payload(orow, nr)
                                if dp:
                                    r = requests.patch(f"{get_api_base()}/products/{pid}",
                                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=dp)
                                    if r.status_code in (200,204): results.append(("ok", pn, "更新完了"))
                                    else: results.append(("err", pn, f"更新失敗 (HTTP {r.status_code}): {r.text[:120]}"))
                    get_products.clear()
                    if results:
                        for k,n,m in results: sr(k,n,m)

    # ========== タブ3: 部門管理 ==========
    with tab3:
        cats = get_categories()
        if cats:
            st.caption(f"全 {len(cats)} 部門")
            cat_df = pd.DataFrame([{
                "部門ID":   safe_str(c.get("categoryId","")),
                "部門名":   safe_str(c.get("categoryName","")),
                "表示順":   safe_int(c.get("displaySequence"), 0),
            } for c in cats])
            edited_cats = st.data_editor(cat_df, use_container_width=True, key="cat_editor", num_rows="dynamic",
                column_config={
                    "部門ID":   st.column_config.TextColumn("部門ID", disabled=True),
                    "部門名":   st.column_config.TextColumn("部門名"),
                    "表示順":   st.column_config.NumberColumn("表示順"),
                })
        else:
            st.info("部門データがありません。")
            edited_cats = pd.DataFrame(columns=["部門ID","部門名","表示順"])

        st.markdown("---")
        st.markdown('<div class="section-label">部門を追加</div>', unsafe_allow_html=True)
        nc1, nc2 = st.columns(2)
        with nc1: new_cn = st.text_input("部門名", key="new_cn")
        with nc2: new_cs = st.number_input("表示順", value=0, key="new_cs")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("部門を追加", type="primary", use_container_width=True, key="add_cat"):
                if new_cn.strip():
                    rv = None
                    with st.spinner("処理中..."):
                        token = get_token()
                        if token:
                            rv = requests.post(f"{get_api_base()}/categories",
                                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                                json={"categoryName": new_cn.strip(), "displaySequence": str(safe_int(new_cs))})
                            get_categories.clear()
                            _refresh_cat_options()
                    if rv and rv.status_code in (200,201):
                        st.success(f"部門「{new_cn}」を追加しました"); time.sleep(.5); st.rerun()
                    elif rv: st.error(f"追加失敗 (HTTP {rv.status_code}): {rv.text}")
                    else: st.error("認証に失敗しました")
                else:
                    st.warning("部門名を入力してください。")
        with c2:
            if st.button("部門変更を保存", type="secondary", use_container_width=True, key="save_cats"):
                results = []
                with st.spinner("処理中..."):
                    token = get_token()
                    if token and cats:
                        for idx, row in edited_cats.iterrows():
                            cid = safe_str(row.get("部門ID",""))
                            if not cid: continue
                            old = cats[idx] if idx < len(cats) else None
                            if old:
                                ch = {}
                                if safe_str(row.get("部門名","")) != safe_str(old.get("categoryName","")): ch["categoryName"] = safe_str(row["部門名"])
                                if str(safe_int(row.get("表示順",0))) != safe_str(old.get("displaySequence","0")): ch["displaySequence"] = str(safe_int(row["表示順"]))
                                if ch:
                                    r = requests.patch(f"{get_api_base()}/categories/{cid}",
                                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=ch)
                                    if r.status_code in (200,204): results.append(("ok", safe_str(row.get("部門名","")), "更新完了"))
                                    else: results.append(("err", safe_str(row.get("部門名","")), "更新失敗: " + r.text[:100]))
                    get_categories.clear()
                    _refresh_cat_options()
                if results:
                    for k,n,m in results: sr(k,n,m)

# ============================================================
# ページ: 設定
# ============================================================
def page_settings():
    inject_css()
    st.markdown('<div class="main-header">設定</div>', unsafe_allow_html=True)
    cfg = get_config()
    for k,v in [("s_cid",cfg.get("contract_id","")), ("s_cli",cfg.get("client_id","")),
                ("s_sec",cfg.get("client_secret","")), ("s_sb",cfg.get("use_sandbox",True))]:
        if k not in st.session_state: st.session_state[k] = v

    try:
        is_secret_cid = "CONTRACT_ID" in st.secrets
        is_secret_cli = "CLIENT_ID" in st.secrets
        is_secret_sec = "CLIENT_SECRET" in st.secrets
    except Exception:
        is_secret_cid = is_secret_cli = is_secret_sec = False

    st.markdown('<div class="info-card"><h4>API 接続設定</h4><p>スマレジ デベロッパーズのアプリ環境設定から取得した値を入力してください。</p></div>', unsafe_allow_html=True)

    use_sb = st.toggle("サンドボックス環境を使用", value=st.session_state.s_sb, key="t_sb")
    st.markdown("---")
    cid = st.text_input(
        "契約ID" + ("　　※クラウド設定 (Secrets) から読み込み中" if is_secret_cid else ""),
        value=st.session_state.s_cid, key="i_cid", disabled=is_secret_cid
    )
    cli = st.text_input(
        "クライアントID" + ("　　※クラウド設定から読み込み中" if is_secret_cli else ""),
        value=st.session_state.s_cli, key="i_cli", disabled=is_secret_cli
    )
    sec = st.text_input(
        "クライアントシークレット" + ("　　※クラウド設定から読み込み中" if is_secret_sec else ""),
        value=st.session_state.s_sec, type="password", key="i_sec", disabled=is_secret_sec
    )

    st.markdown("---")
    st.markdown('<div class="info-card"><h4>デフォルト表示項目</h4><p>商品テーブルに追加表示する項目を選択します（コア項目は常に表示されます）。</p></div>', unsafe_allow_html=True)
    optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
    cur_vis  = cfg.get("visible_fields",[])
    sel_vis  = st.multiselect("追加表示項目", options=optional, default=[c for c in cur_vis if c in optional], key="s_vis")

    st.markdown("---")
    if st.button("設定を保存", type="primary", use_container_width=True):
        st.session_state.s_cid = cid; st.session_state.s_cli = cli
        st.session_state.s_sec = sec; st.session_state.s_sb  = use_sb
        update_config_bulk({"contract_id":cid,"client_id":cli,"client_secret":sec,
            "use_sandbox":use_sb,"visible_fields":sel_vis})
        for k in [k for k in st.session_state if k.startswith("_tc_")]: del st.session_state[k]
        st.cache_data.clear()
        _refresh_cat_options()
        st.success("設定を保存しました。")

    st.markdown("---")
    st.markdown('<div class="info-card"><h4>接続テスト</h4><p>保存した設定で API 認証を確認します。</p></div>', unsafe_allow_html=True)
    st.caption(f"環境: {'Sandbox' if use_sb else '本番'}　／　契約ID: {cid if cid else '(未設定)'}")
    if st.button("接続テストを実行", use_container_width=True, key="test_conn"):
        if not cid or not cli or not sec:
            st.error("全項目を入力してください。")
        else:
            with st.spinner("確認中..."):
                try:
                    cr = base64.b64encode(f"{cli}:{sec}".encode()).decode()
                    tu = f"https://id.smaregi.{'dev' if use_sb else 'jp'}/app/{cid}/token"
                    r  = requests.post(tu,
                        headers={"Authorization":f"Basic {cr}","Content-Type":"application/x-www-form-urlencoded"},
                        data={"grant_type":"client_credentials","scope":"pos.products:read pos.products:write"})
                    if r.status_code == 200:
                        st.success(f"接続成功  (有効期限: {r.json().get('expires_in','?')} 秒)")
                    else:
                        st.error(f"接続失敗 (HTTP {r.status_code}): {r.text}")
                except Exception as e:
                    st.error(f"接続エラー: {e}")

# ============================================================
# ナビゲーション
# ============================================================
nav = st.navigation([
    st.Page(page_main,     title="商品管理", icon="📦"),
    st.Page(page_settings, title="設定",     icon="⚙"),
])
nav.run()
