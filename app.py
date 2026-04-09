import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import time
import base64
import datetime
import urllib.parse
import pandas as pd
from collections import OrderedDict
from io import BytesIO
from PIL import Image
from google.oauth2 import service_account
from google.cloud import storage

# ============================================================
# 定数・ユーティリティ
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
# CSS
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    * { font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif; }
    input, select, textarea, .stSelectbox div { font-size: 16px !important; }
    .stApp { background: #f1f5f9; }
    .block-container { padding-top: 1rem !important; padding-bottom: 5rem !important; max-width: 600px !important; margin: 0 auto;}
    .step-card { background: #ffffff; padding: 1.5rem; border-radius: 16px; box-shadow: 0 4px 15px rgba(0,0,0,0.03); margin-bottom: 1.2rem; border: 1px solid #e2e8f0; }
    .step-header { display: flex; align-items: center; gap: 10px; margin-bottom: 1rem; }
    .step-number { background: #2563eb; color: white; border-radius: 50%; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; }
    .step-title { color: #0f172a; font-size: 1.2rem; font-weight: 800; }
    .stButton > button { border-radius: 12px !important; font-weight: 700 !important; padding: 0.75rem 1rem !important; font-size: 1.05rem !important; width: 100%; transition: transform 0.1s; }
    .stButton > button:active { transform: scale(0.97); }
    .stButton > button[kind="primary"] { background: #2563eb !important; color: white !important; border: none !important; box-shadow: 0 4px 6px rgba(37,99,235,0.25); }
    .stButton > button[kind="primary"]:hover { background: #1d4ed8 !important; }
    .r-row { padding: 1rem; border-radius: 12px; margin: .5rem 0; font-size: 1.05rem; font-weight: bold; display: flex; align-items: center; gap: .5rem; }
    .r-ok   { background: #dcfce7; color: #166534; border-left: 6px solid #22c55e; }
    .r-err  { background: #fee2e2; color: #991b1b; border-left: 6px solid #ef4444; }
    </style>
    """, unsafe_allow_html=True)

def sr(kind, name, msg):
    cls  = {"ok":"r-ok","err":"r-err"}.get(kind,"r-ok")
    icon = {"ok":"✅","err":"❌"}.get(kind,"●")
    st.markdown(f'<div class="r-row {cls}"><span>{icon}</span><strong>{name}</strong><span style="opacity:.5; margin:0 6px;">|</span>{msg}</div>', unsafe_allow_html=True)

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
# API: データ取得系
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
# ページ 1: 📱 スキャン＆登録 
# ============================================================
def page_scanner_form():
    inject_css()
    token = get_token()
    if not token:
        st.error("スマレジとの認証に失敗しました。")
        st.stop()

    st.markdown('<div style="text-align:center; padding-bottom:1rem;"><h2 style="margin:0; color:#0f172a;">📱 現場登録ツール</h2><p style="color:#64748b; font-size:14px;">現場での商品追加・写真登録を行います</p></div>', unsafe_allow_html=True)

    if "input_mode" not in st.session_state:
        st.session_state.input_mode = None
    if "final_code" not in st.session_state:
        st.session_state.final_code = ""

    prods = get_products(token)
    cat_opts = _cat_options(token)

    # ---------------------------------------------------------
    # STEP 1: 商品コードの登録方法
    # ---------------------------------------------------------
    st.markdown("""
        <div class="step-card">
            <div class="step-header">
                <div class="step-number">1</div>
                <div class="step-title">商品コードをどうしますか？</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📸 スキャナ起動", type="primary" if st.session_state.input_mode == "scan" else "secondary"):
            st.session_state.input_mode = "scan"
            st.session_state.final_code = ""
            st.rerun()
    with col2:
        if st.button("⚙️ 自動採番", type="primary" if st.session_state.input_mode == "auto" else "secondary"):
            st.session_state.input_mode = "auto"
            st.session_state.final_code = f"AUTO-{int(time.time() * 1000)}"
            st.rerun()

    code_input = ""
    
    # 🌟 HTML5スキャナーの埋め込み (カメラ起動機能)
    if st.session_state.input_mode == "scan":
        st.info("👇 【初回のみカメラ許可が必要です】カメラにバーコードをかざし、下の枠に数字を入力してください")
        
        # Javascriptで動く本格的なバーコードリーダーをHTMLとして埋め込みます
        components.html("""
        <script src="https://unpkg.com/html5-qrcode"></script>
        <div id="qr-reader" style="width:100%; border-radius: 8px; overflow: hidden; border: 2px solid #cbd5e1;"></div>
        <div style="text-align:center; margin-top: 15px; font-family: sans-serif;">
            <p style="font-size: 14px; color: #64748b; margin:0;">読み取った数字</p>
            <div id="qr-reader-results" style="font-weight: bold; font-size: 24px; color: #2563eb;">---</div>
        </div>
        <script>
            function onScanSuccess(decodedText, decodedResult) {
                document.getElementById('qr-reader-results').innerText = decodedText;
            }
            // 背面カメラ(environment)を優先して起動
            var html5QrcodeScanner = new Html5QrcodeScanner(
                "qr-reader", { fps: 10, qrbox: {width: 250, height: 100}, facingMode: "environment" });
            html5QrcodeScanner.render(onScanSuccess);
        </script>
        """, height=380)
        
        # 読み取った数字をユーザーが手動で貼り付ける枠
        code_input = st.text_input("上の数字を入力（またはコピー＆ペースト）してください", placeholder="例: 4901234567890")

    elif st.session_state.input_mode == "auto":
        code_input = st.session_state.final_code
        st.success(f"✅ システムが自動で番号を割り当てました: **{code_input}**")

    # ---------------------------------------------------------
    # STEP 2 & 3: 情報入力と写真撮影 (コードが確定してから表示)
    # ---------------------------------------------------------
    if code_input:
        target_prod = find_product_by_code(prods, code_input)
        is_new = target_prod is None
        
        st.markdown("""
            <div class="step-card" style="margin-top:20px;">
                <div class="step-header">
                    <div class="step-number">2</div>
                    <div class="step-title">商品情報を入力</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        if is_new:
            st.caption("✨ 新規商品の登録です")
            default_data = {k: d["default"] for k, d in FIELD_DEFS.items()}
        else:
            st.warning(f"🔄 既存の商品が見つかりました。「{target_prod.get('productName')}」を上書き更新します。")
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
        form_vals["商品価格"] = st.number_input("価格 (必須)", value=int(default_data["商品価格"]), step=100)
        
        cat_index = cat_opts.index(default_data["部門ID"]) if default_data["部門ID"] in cat_opts else 0
        form_vals["部門ID"] = st.selectbox("部門 (必須)", cat_opts, index=cat_index)

        with st.expander("⚙️ 詳細設定（原価、税区分など）"):
            for k, d in FIELD_DEFS.items():
                if d["core"]: continue
                if d["type"] == "select":
                    idx = d["options"].index(default_data[k]) if default_data[k] in d["options"] else 0
                    form_vals[k] = st.selectbox(k, d["options"], index=idx)
                elif d["type"] == "number":
                    form_vals[k] = st.number_input(k, value=int(default_data[k]), step=1)
                else:
                    form_vals[k] = st.text_input(k, value=default_data[k])

        # 写真設定
        st.markdown("""
            <div class="step-card" style="margin-top:20px;">
                <div class="step-header">
                    <div class="step-number">3</div>
                    <div class="step-title">商品の写真を設定 (任意)</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        st.info("👇 枠をタップすると、スマホの「カメラを起動」が選べます")
        img_file = st.file_uploader("写真を撮影、または選択", type=["jpg","jpeg","png"], label_visibility="collapsed")
        
        st.write("##")
        submit_btn = st.button("🚀 この内容でスマレジに登録する", type="primary")

        # 送信処理
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
                        if img_file:
                            ok, msg = upload_and_link_image(token, pid, img_file)
                            sr("ok", form_vals["商品名"], "新規登録＆画像完了") if ok else sr("err", form_vals["商品名"], "画像エラー")
                        else:
                            sr("ok", form_vals["商品名"], "新規登録完了")
                    else: sr("err", "登録失敗", r.text[:50])
                else:
                    pid = target_prod.get("productId")
                    r = requests.patch(f"{get_api_base()}/products/{pid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                    if r.status_code in (200, 204):
                        if img_file:
                            ok, msg = upload_and_link_image(token, pid, img_file)
                            sr("ok", form_vals["商品名"], "更新＆画像完了") if ok else sr("err", form_vals["商品名"], "画像エラー")
                        else:
                            sr("ok", form_vals["商品名"], "データ更新完了")
                    else: sr("err", "更新失敗", r.text[:50])
                
                st.cache_data.clear()
                st.session_state.input_mode = None
                st.session_state.final_code = ""
                time.sleep(2)
                st.rerun()

# ============================================================
# ページ 2: 💻 商品一括管理
# ============================================================
def page_spreadsheet():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()

    st.markdown('<div class="main-header">💻 商品一括管理</div>', unsafe_allow_html=True)
    st.info("PCでの価格の一括変更などに特化した画面です。（※新規作成はスマホ用ページをご利用ください）")

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

    c1, c2 = st.columns([2, 1])
    with c1: btn_save = st.button("💾 表の変更をすべて保存する", type="primary")
    with c2: 
        if st.button("🔄 最新データに更新", type="secondary"): 
            st.cache_data.clear(); st.rerun()

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

    st.markdown('<div class="main-header">📁 部門マスター</div>', unsafe_allow_html=True)
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
# ナビゲーション
# ============================================================
nav = st.navigation([
    st.Page(page_scanner_form, title="スキャン＆登録 (スマホ)", icon="📱"),
    st.Page(page_spreadsheet,  title="商品一括管理 (PC)",     icon="💻"),
    st.Page(page_categories,   title="部門マスター",          icon="📁"),
])
nav.run()
