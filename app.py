import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import time
import base64
import datetime
import os
import pandas as pd
from collections import OrderedDict
from io import BytesIO
from PIL import Image
from google.oauth2 import service_account
from google.cloud import storage

# ============================================================
# 定数・ユーティリティ・設定管理
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
    if pd.isna(v) or v is None: return d
    return str(v)

def get_api_base():
    cid = st.secrets["CONTRACT_ID"]
    use_sandbox = st.secrets.get("USE_SANDBOX", True)
    dom = "smaregi.dev" if use_sandbox else "smaregi.jp"
    return f"https://api.{dom}/{cid}/pos"

def get_auth_url():
    cid = st.secrets["CONTRACT_ID"]
    use_sandbox = st.secrets.get("USE_SANDBOX", True)
    dom = "smaregi.dev" if use_sandbox else "smaregi.jp"
    return f"https://id.{dom}/app/{cid}/token"

# --- 自動採番ルールの初期化 ---
if "auto_rule_prefix" not in st.session_state:
    st.session_state.auto_rule_prefix = "AUTO-"
if "auto_rule_suffix" not in st.session_state:
    st.session_state.auto_rule_suffix = ""

def generate_auto_code():
    prefix = st.session_state.auto_rule_prefix
    suffix = st.session_state.auto_rule_suffix
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{timestamp}{suffix}"

# ============================================================
# API: 認証
# ============================================================
def get_token():
    try:
        ci = st.secrets["CLIENT_ID"]
        cs = st.secrets["CLIENT_SECRET"]
    except KeyError:
        return None

    ck = "server_auth_token"
    cached = st.session_state.get(ck)
    if cached and cached.get("ea", 0) > time.time():
        return cached["at"]

    try:
        cred = base64.b64encode(f"{ci}:{cs}".encode()).decode()
        r = requests.post(get_auth_url(),
            headers={"Authorization":f"Basic {cred}","Content-Type":"application/x-www-form-urlencoded"},
            data={"grant_type":"client_credentials","scope":"pos.products:read pos.products:write"})
        if r.status_code == 200:
            d = r.json()
            t = d.get("access_token")
            st.session_state[ck] = {"at": t, "ea": time.time() + d.get("expires_in", 3600) - 60}
            return t
    except Exception:
        pass
    return None

# ============================================================
# 🌟 バーコードリーダー コンポーネント (UI最適化)
# ============================================================
def custom_barcode_scanner(key="scanner"):
    component_dir = os.path.abspath("barcode_component_dir")
    if not os.path.exists(component_dir):
        os.makedirs(component_dir)
    
    html_code = """
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://unpkg.com/html5-qrcode"></script>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <style>
            body, html { margin: 0; padding: 0; background-color: transparent; overflow: hidden; }
            #reader { width: 100vw; height: 100%; border-radius: 12px; background: #000; overflow: hidden;}
            #reader video { object-fit: cover; border-radius: 12px; }
            #qr-shaded-region { border-color: rgba(0,0,0,0.6) !important; }
        </style>
    </head>
    <body>
        <div id="reader"></div>
        <script>
            function sendToStreamlit(value) {
                window.parent.postMessage({ isStreamlitMessage: true, type: "streamlit:setComponentValue", value: value }, "*");
            }
            function setHeight(h) {
                window.parent.postMessage({ isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h }, "*");
            }
            window.onload = function() {
                window.parent.postMessage({ isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1 }, "*");
                setHeight(260); // スマホ画面にフィットする高さ
            };

            let started = false;
            window.addEventListener("message", function(event) {
                if (event.data.type === "streamlit:render" && !started) {
                    started = true;
                    const html5QrCode = new Html5Qrcode("reader");
                    const config = { 
                        fps: 15, 
                        qrbox: { width: 260, height: 80 }, 
                        aspectRatio: 1.0, 
                        formatsToSupport: [ 
                            Html5QrcodeSupportedFormats.EAN_13, Html5QrcodeSupportedFormats.EAN_8,
                            Html5QrcodeSupportedFormats.UPC_A, Html5QrcodeSupportedFormats.UPC_E,
                            Html5QrcodeSupportedFormats.CODE_128, Html5QrcodeSupportedFormats.CODE_39
                        ]
                    };
                    
                    html5QrCode.start({ facingMode: "environment" }, config, 
                        (decodedText) => {
                            html5QrCode.stop().then(() => {
                                setHeight(0); // 読取完了と同時に枠を消す
                                sendToStreamlit(decodedText);
                            });
                        },
                        (errorMessage) => {}
                    );
                }
            });
        </script>
    </body>
    </html>
    """
    with open(os.path.join(component_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)
    
    _scanner = components.declare_component("custom_barcode_scanner", path=component_dir)
    return _scanner(key=key)

# ============================================================
# フィールド定義
# ============================================================
FIELD_DEFS = OrderedDict([
    ("商品名",        {"api":"productName",          "type":"text",    "default":"",         "required":True, "core":True, "max":85, "send_empty":True, "post":True}),
    ("商品価格",      {"api":"price",                "type":"number",  "default":0,          "required":True, "core":True, "max":None,"send_empty":True,"post":True}),
    ("部門ID",        {"api":"categoryId",           "type":"category","default":"",         "required":True, "core":True, "max":None,"send_empty":False,"post":True}),
    ("原価",          {"api":"cost",                 "type":"number",  "default":0,          "required":False,"core":False,"max":None,"send_empty":False,"post":True}),
    ("税区分",        {"api":"taxDivision",          "type":"select",  "default":"0:税込",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:税込","1:税抜","2:非課税"]}),
    ("在庫管理区分",  {"api":"stockControlDivision", "type":"select",  "default":"0:対象",   "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:対象","1:対象外"]}),
    ("売上区分",      {"api":"salesDivision",        "type":"select",  "default":"0:売上対象","required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:売上対象","1:売上対象外"]}),
    ("説明",          {"api":"description",          "type":"text",    "default":"",         "required":False,"core":False,"max":1000,"send_empty":False,"post":True}),
    ("カラー",        {"api":"color",                "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("サイズ",        {"api":"size",                 "type":"text",    "default":"",         "required":False,"core":False,"max":85, "send_empty":False,"post":True}),
    ("端末表示",      {"api":"displayFlag",          "type":"select",  "default":"1:表示する","required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:表示しない","1:表示する"]}),
])

def sel2api(v):
    if not v or ":" not in safe_str(v): return safe_str(v)
    return safe_str(v).split(":")[0]

def api2sel(val, opts):
    s = safe_str(val)
    for o in opts:
        if o.split(":")[0] == s: return o
    return opts[0] if opts else s

def get_visible():
    extra = st.session_state.get("visible_fields", [])
    core = [k for k, d in FIELD_DEFS.items() if d["core"]]
    return core + [k for k in extra if k in FIELD_DEFS and not FIELD_DEFS[k]["core"]]

def _cat_options(token):
    if "cat_options_cache" in st.session_state:
        return st.session_state["cat_options_cache"]
    cats = get_categories(token)
    opts = [""] + [f"{safe_str(c.get('categoryId',''))}:{safe_str(c.get('categoryName',''))}" for c in cats]
    st.session_state["cat_options_cache"] = opts
    return opts

def _refresh_cat_options():
    if "cat_options_cache" in st.session_state:
        del st.session_state["cat_options_cache"]

# ============================================================
# CSS (徹底的なフラット＆モバイル最適化)
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    * { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
    input, select, textarea, .stSelectbox div { font-size: 16px !important; }
    
    /* 余白を極限まで消す */
    .stApp { background: #f8fafc; }
    .block-container { padding: 1rem 1rem 5rem 1rem !important; max-width: 600px !important; margin: 0 auto;}
    
    /* フォームのカード化 */
    .input-card { background: #ffffff; padding: 1.5rem; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.04); margin-bottom: 1.5rem; border: 1px solid #f1f5f9; }
    .card-title { color: #0f172a; font-size: 1.1rem; font-weight: 800; margin-bottom: 1rem; border-bottom: 2px solid #f1f5f9; padding-bottom: 0.5rem;}
    
    /* ボタンのネイティブアプリ化 */
    .stButton > button { border-radius: 12px !important; font-weight: bold !important; padding: 0.8rem !important; font-size: 1.1rem !important; width: 100%; }
    .stButton > button[kind="primary"] { background: #2563eb !important; color: white !important; border: none !important; box-shadow: 0 4px 10px rgba(37,99,235,0.2); }
    .stButton > button[kind="secondary"] { background: #ffffff !important; color: #334155 !important; border: 1px solid #cbd5e1 !important; }
    .stButton > button:active { transform: scale(0.96); transition: 0.1s; }
    
    /* 読取結果などのアラート */
    .r-row { padding: 1rem; border-radius: 12px; margin: 0.5rem 0; font-size: 1rem; font-weight: bold; display: flex; align-items: center; gap: 0.5rem; }
    .r-ok   { background: #ecfdf5; color: #166534; border-left: 6px solid #22c55e; }
    .r-err  { background: #fef2f2; color: #991b1b; border-left: 6px solid #ef4444; }
    
    /* Streamlit特有の隙間を消す */
    div[data-testid="stVerticalBlock"] > div { padding-bottom: 0.2rem !important; }
    </style>
    """, unsafe_allow_html=True)

def sr(kind, name, msg):
    cls  = {"ok":"r-ok","err":"r-err"}.get(kind,"r-ok")
    icon = {"ok":"✅","err":"❌"}.get(kind,"●")
    st.markdown(f'<div class="r-row {cls}"><span>{icon}</span><strong>{name}</strong><span style="opacity:.5; margin:0 4px;">|</span>{msg}</div>', unsafe_allow_html=True)

# ============================================================
# API: GCS画像登録
# ============================================================
def get_gcp_credentials():
    gcp_json_str = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_dict = json.loads(gcp_json_str)
    return service_account.Credentials.from_service_account_info(gcp_dict)

def upload_and_link_image(token, product_id, file_obj):
    try:
        img = Image.open(file_obj)
        if img.mode not in ("RGB", "L"): img = img.convert("RGB")
        img.thumbnail((800, 800), Image.LANCZOS)
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG", quality=85)
        img_bytes.seek(0)

        bucket_name = st.secrets["GCP_BUCKET_NAME"]
        credentials = get_gcp_credentials()
        client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = client.bucket(bucket_name)

        filename = f"products/{product_id}_{int(time.time() * 1000)}.jpg"
        blob = bucket.blob(filename)
        blob.upload_from_file(img_bytes, content_type="image/jpeg")

        signed_url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(minutes=15), method="GET")
        try:
            safe_url = requests.utils.quote(signed_url)
            short_res = requests.get(f"https://tinyurl.com/api-create.php?url={safe_url}", timeout=5)
            final_url = short_res.text if short_res.status_code == 200 else signed_url
        except: final_url = signed_url

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"imageUrl": final_url}

        url_img = f"{get_api_base()}/products/{product_id}/image"
        ok_img = False
        for _ in range(3):
            try:
                r1 = requests.put(url_img, headers=headers, json=payload, timeout=15)
                if r1.status_code in (200, 201, 204): ok_img = True; break
                if r1.status_code == 404: time.sleep(1); continue
            except: time.sleep(1); continue

        url_icon = f"{get_api_base()}/products/{product_id}/icon_image"
        ok_icon = False
        for _ in range(3):
            try:
                r2 = requests.put(url_icon, headers=headers, json=payload, timeout=15)
                if r2.status_code in (200, 201, 204): ok_icon = True; break
                if r2.status_code == 404: time.sleep(1); continue
            except: time.sleep(1); continue

        if ok_img and ok_icon: return True, "画像・アイコン登録完了"
        else: return False, "画像の一部連携に失敗"
    except Exception as e: return False, f"システムエラー: {str(e)}"

# ============================================================
# API: データ取得・ペイロード生成
# ============================================================
@st.cache_data(ttl=120)
def get_categories(token):
    cats, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/categories", headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code != 200: break
        d = r.json()
        if not isinstance(d, list): break
        cats.extend(d)
        if len(d) < 1000: break
        p += 1
    return cats

@st.cache_data(ttl=60)
def get_products(token):
    prods, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/products", headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code != 200: break
        d = r.json()
        if not isinstance(d, list): break
        prods.extend(d)
        if len(d) < 1000: break
        p += 1
    return prods

def find_product_by_code(prods, code):
    if not code: return None
    for p in prods:
        if p.get("productCode") == code: return p
    return None

def create_payload(form_data, code):
    payload = {"productCode": code}
    for k, d in FIELD_DEFS.items():
        if k not in form_data: continue
        v = form_data[k]
        if d["type"] == "select": v = sel2api(v)
        elif d["type"] == "category": v = safe_str(v).split(":")[0] if v and ":" in safe_str(v) else safe_str(v)
        if (v == "" or v is None or v == 0) and not d.get("send_empty",False): continue
        payload[d["api"]] = safe_str(v)
    return payload

# ============================================================
# ダイアログ: 📸 バーコードスキャン (ポップアップ)
# ============================================================
@st.dialog("📸 スキャン")
def scanner_modal():
    st.write("バーコードを枠に合わせてください")
    scanned_result = custom_barcode_scanner(key="popup_scanner")
    if scanned_result:
        st.session_state.input_mode = "scanned_success"
        st.session_state.final_code = scanned_result
        st.session_state.show_scanner_modal = False
        st.rerun()

# ============================================================
# ページ 1: 📱 スキャン＆登録 (スマホ最適化)
# ============================================================
def page_scanner_form():
    inject_css()
    token = get_token()
    if not token:
        st.error("スマレジ認証エラー。設定を確認してください。")
        st.stop()

    # ステート管理
    if "input_mode" not in st.session_state: st.session_state.input_mode = None
    if "final_code" not in st.session_state: st.session_state.final_code = ""
    if "was_auto" not in st.session_state: st.session_state.was_auto = False
    if "show_scanner_modal" not in st.session_state: st.session_state.show_scanner_modal = False

    # モーダルを開く処理
    if st.session_state.show_scanner_modal:
        scanner_modal()

    prods = get_products(token)
    cat_opts = _cat_options(token)

    st.markdown('<div class="input-card"><div class="card-title">1️⃣ 商品コードを準備</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📸 スキャン", type="primary" if st.session_state.input_mode in ["scan", "scanned_success"] else "secondary"):
            st.session_state.show_scanner_modal = True
            st.session_state.was_auto = False
            st.rerun()
    with col2:
        if st.button("⚙️ 自動採番", type="primary" if st.session_state.input_mode == "auto" else "secondary"):
            st.session_state.input_mode = "auto"
            st.session_state.final_code = generate_auto_code()
            st.session_state.was_auto = True
            st.rerun()

    code_input = ""
    if st.session_state.input_mode == "auto":
        code_input = st.session_state.final_code
        st.success(f"✅ 自動採番: **{code_input}**")
    elif st.session_state.input_mode == "scanned_success":
        code_input = st.session_state.final_code
        code_input = st.text_input("読取結果 (修正可)", value=code_input)

    st.markdown('</div>', unsafe_allow_html=True)

    # --- フォーム部分 ---
    if code_input:
        target_prod = find_product_by_code(prods, code_input)
        is_new = target_prod is None

        st.markdown('<div class="input-card"><div class="card-title">2️⃣ 商品情報</div>', unsafe_allow_html=True)

        if is_new:
            st.caption("✨ 新規登録になります")
            default_data = {k: d["default"] for k, d in FIELD_DEFS.items()}
        else:
            st.warning(f"🔄 既存商品「{target_prod.get('productName')}」を更新します")
            default_data = {}
            for k, d in FIELD_DEFS.items():
                val = target_prod.get(d["api"], d["default"])
                if d["type"] == "number": val = safe_float(val, d["default"])
                elif d["type"] == "select": val = api2sel(safe_str(val), d["options"])
                elif d["type"] == "category":
                    cid = safe_str(val)
                    val = next((o for o in cat_opts if o.startswith(cid+":")), "") if cid else ""
                default_data[k] = val

        form_vals = {}
        form_vals["商品名"] = st.text_input("商品名 (必須)", value=default_data["商品名"])
        form_vals["商品価格"] = st.number_input("価格 (必須)", value=int(default_data["商品価格"]), step=10)

        cat_index = cat_opts.index(default_data["部門ID"]) if default_data["部門ID"] in cat_opts else 0
        form_vals["部門ID"] = st.selectbox("部門 (必須)", cat_opts, index=cat_index)

        with st.expander("⚙️ その他の設定（原価、税など）"):
            for k, d in FIELD_DEFS.items():
                if d["core"]: continue
                if d["type"] == "select":
                    idx = d["options"].index(default_data[k]) if default_data[k] in d["options"] else 0
                    form_vals[k] = st.selectbox(k, d["options"], index=idx)
                elif d["type"] == "number":
                    form_vals[k] = st.number_input(k, value=int(default_data[k]), step=1)
                else:
                    form_vals[k] = st.text_input(k, value=default_data[k])
                    
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="input-card"><div class="card-title">3️⃣ 写真 (任意)</div>', unsafe_allow_html=True)
        img_file = st.file_uploader("写真を撮影、または選択", type=["jpg","jpeg","png"], label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)

        submit_btn = st.button("🚀 登録して次の商品へ", type="primary")

        if submit_btn:
            if not form_vals["商品名"] or not form_vals["部門ID"]:
                st.error("商品名と部門は必須です。")
                st.stop()

            payload = create_payload(form_vals, code_input)

            with st.spinner("スマレジに送信中..."):
                if is_new:
                    r = requests.post(f"{get_api_base()}/products", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                    if r.status_code in (200, 201):
                        pid = r.json().get("productId")
                        if img_file: upload_and_link_image(token, pid, img_file)
                        st.success(f"✅ {form_vals['商品名']} を登録しました！")
                    else: st.error(f"登録失敗: {r.text[:50]}")
                else:
                    pid = target_prod.get("productId")
                    r = requests.patch(f"{get_api_base()}/products/{pid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                    if r.status_code in (200, 204):
                        if img_file: upload_and_link_image(token, pid, img_file)
                        st.success(f"✅ {form_vals['商品名']} を更新しました！")
                    else: st.error(f"更新失敗: {r.text[:50]}")

                st.cache_data.clear()
                time.sleep(1.0)
                
                # 無限ループ処理
                if st.session_state.was_auto:
                    st.session_state.input_mode = "auto"
                    st.session_state.final_code = generate_auto_code()
                else:
                    st.session_state.input_mode = "scan"
                    st.session_state.final_code = ""
                    st.session_state.show_scanner_modal = True
                
                st.rerun()

# ============================================================
# ページ 2: 💻 商品一括管理
# ============================================================
def page_spreadsheet():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()

    st.markdown("### 💻 商品一括管理 (PC用)")
    st.info("PCでの価格一括変更などに特化した画面です。")

    with st.expander("👁️ スプレッドシートの表示列を設定"):
        optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
        cur_vis  = st.session_state.get("visible_fields", [])
        sel_vis  = st.multiselect("表に追加する項目", options=optional, default=[c for c in cur_vis if c in optional], label_visibility="collapsed")
        if sel_vis != cur_vis:
            st.session_state["visible_fields"] = sel_vis
            st.rerun()

    visible = get_visible()
    prods = get_products(token)

    cat_map = {safe_str(c.get("categoryId","")): safe_str(c.get("categoryName","")) for c in get_categories(token)}
    rows = []
    for p in prods:
        row = {"productId": safe_str(p.get("productId","")), "商品コード": safe_str(p.get("productCode",""))}
        for k in visible:
            d = FIELD_DEFS[k]; v = p.get(d["api"], d["default"])
            if d["type"] == "select": v = api2sel(safe_str(v), d.get("options",[]))
            elif d["type"] == "category":
                cid = safe_str(v); cn = cat_map.get(cid,"")
                v = f"{cid}:{cn}" if cid and cn else cid
            elif d["type"] == "number": v = safe_float(v, d["default"])
            else: v = safe_str(v, d["default"])
            row[k] = v
        rows.append(row)

    df = pd.DataFrame(rows)
    display_cols = ["productId", "商品コード"] + visible
    if df.empty: df = pd.DataFrame(columns=display_cols)

    btn_save = st.button("💾 表の変更をすべて保存する", type="primary")

    cat_opts = _cat_options(token)
    ccfg = {"productId": st.column_config.TextColumn("商品ID", disabled=True), "商品コード": st.column_config.TextColumn("商品コード", disabled=True)}
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category": ccfg[k] = st.column_config.SelectboxColumn(k, options=cat_opts)
        elif d["type"] == "select": ccfg[k] = st.column_config.SelectboxColumn(k, options=d.get("options",[]))
        elif d["type"] == "number": ccfg[k] = st.column_config.NumberColumn(k)
        else: ccfg[k] = st.column_config.TextColumn(k, max_chars=d.get("max"))

    edited_df = st.data_editor(df[display_cols], column_config=ccfg, num_rows="fixed", use_container_width=True, height=600)

    if btn_save:
        results = []
        with st.spinner("データを同期中..."):
            for idx, nr in edited_df.iterrows():
                pid = str(nr.get("productId", "")).strip()
                if not pid: continue
                orow = df[df['productId'] == pid].iloc[0].to_dict()
                dp = {}
                for k,d in FIELD_DEFS.items():
                    if k not in orow or k not in nr: continue
                    ov, nv = orow[k], nr[k]
                    if d["type"] == "select": ov,nv = sel2api(ov),sel2api(nv)
                    elif d["type"] == "category":
                        ov = safe_str(ov).split(":")[0] if ov and ":" in safe_str(ov) else safe_str(ov)
                        nv = safe_str(nv).split(":")[0] if nv and ":" in safe_str(nv) else safe_str(nv)
                    if safe_str(ov) != safe_str(nv):
                        if (nv == "" or nv is None or nv == 0) and not d.get("send_empty",False): continue
                        dp[d["api"]] = safe_str(nv)
                if dp:
                    r = requests.patch(f"{get_api_base()}/products/{pid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=dp)
                    pn = str(nr.get("商品名", "不明"))
                    if r.status_code in (200, 204): results.append(("ok", pn, "データ更新完了"))
                    else: results.append(("err", pn, f"更新エラー"))
            st.cache_data.clear()
        if results:
            for k, n, m in results: sr(k, n, m)

# ============================================================
# ページ 3: 📁 部門マスター
# ============================================================
def page_categories():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()

    st.markdown("### 📁 部門マスター")
    cats = get_categories(token)
    cat_df = pd.DataFrame([{
        "部門ID": safe_str(c.get("categoryId","")), "部門名": safe_str(c.get("categoryName","")), "表示順": safe_int(c.get("displaySequence"), 0),
    } for c in cats]) if cats else pd.DataFrame(columns=["部門ID","部門名","表示順"])

    btn_save_cat = st.button("💾 部門データの変更・追加を保存する", type="primary")
    edited_cats = st.data_editor(cat_df, use_container_width=True, num_rows="dynamic", height=500,
        column_config={"部門ID": st.column_config.TextColumn("部門ID (空欄=新規)", disabled=True), "部門名": st.column_config.TextColumn("部門名", required=True), "表示順": st.column_config.NumberColumn("表示順", default=0)})

    if btn_save_cat:
        results = []
        with st.spinner("部門データを同期中..."):
            for idx, row in edited_cats.iterrows():
                cid = str(row.get("部門ID","")).strip()
                if cid in ["nan", "None", "<NA>", ""]: cid = None
                cname = str(row.get("部門名","")).strip()
                if not cname or cname in ["nan", "None", "<NA>"]: continue
                cseq = str(safe_int(row.get("表示順", 0)))

                if not cid:
                    r = requests.post(f"{get_api_base()}/categories", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json={"categoryName": cname, "displaySequence": cseq})
                    if r.status_code in (200, 201): results.append(("ok", cname, "新規追加完了"))
                    else: results.append(("err", cname, f"追加エラー"))
                else:
                    old = cats[idx] if idx < len(cats) else None
                    if old:
                        ch = {}
                        if cname != safe_str(old.get("categoryName","")): ch["categoryName"] = cname
                        if cseq != str(safe_int(old.get("displaySequence",0))): ch["displaySequence"] = cseq
                        if ch:
                            r = requests.patch(f"{get_api_base()}/categories/{cid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=ch)
                            if r.status_code in (200, 204): results.append(("ok", cname, "更新完了"))
                            else: results.append(("err", cname, f"更新エラー"))
            st.cache_data.clear(); _refresh_cat_options()
        if results:
            for k, n, m in results: sr(k, n, m)

# ============================================================
# ページ 4: ⚙️ アプリ設定 (自動採番ルールの変更など)
# ============================================================
def page_settings():
    inject_css()
    st.markdown("### ⚙️ アプリ設定")
    
    st.markdown('<div class="input-card"><div class="card-title">🔢 自動採番ルール設定</div>', unsafe_allow_html=True)
    st.write("「自動採番」ボタンを押したときに作られる商品コードのルールを設定します。（※真ん中には自動で年月日の数字が入ります）")
    
    pfx = st.text_input("前につける文字 (接頭辞)", value=st.session_state.auto_rule_prefix)
    sfx = st.text_input("後ろにつける文字 (接尾辞)", value=st.session_state.auto_rule_suffix)
    
    st.info(f"💡 プレビュー: **{pfx}20260410134221{sfx}**")
    
    if st.button("ルールを保存する", type="primary"):
        st.session_state.auto_rule_prefix = pfx
        st.session_state.auto_rule_suffix = sfx
        st.success("保存しました！「スキャン＆登録」画面で反映されます。")
    st.markdown('</div>', unsafe_allow_html=True)

# ============================================================
# ナビゲーション
# ============================================================
nav = st.navigation([
    st.Page(page_scanner_form, title="スキャン＆登録", icon="📱"),
    st.Page(page_spreadsheet,  title="商品一括管理",   icon="💻"),
    st.Page(page_categories,   title="部門マスター",   icon="📁"),
    st.Page(page_settings,     title="設定",           icon="⚙️"),
])
nav.run()
