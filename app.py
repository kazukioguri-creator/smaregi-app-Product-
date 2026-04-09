import streamlit as st
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
# API: 認証 (裏側で自動的に鍵を取得する機能)
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
    * { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Meiryo, sans-serif; font-size: 13px; }
    .stApp { background: #ffffff; }
    .block-container { padding-top: 1.5rem !important; padding-bottom: 2rem !important; max-width: 98% !important; }
    section[data-testid="stSidebar"] { background: #f8f9fa !important; border-right: 1px solid #dee2e6; }
    .main-header { color: #1e293b; font-size: 1.4rem; font-weight: 700; margin-bottom: 1rem; padding-bottom: .5rem; border-bottom: 2px solid #e2e8f0; }
    .stButton > button[kind="primary"] { background: #10b981 !important; color: white !important; border: none !important; font-weight: 600 !important; border-radius: 4px !important; padding: .5rem 1rem !important; }
    .stButton > button[kind="primary"]:hover { background: #059669 !important; }
    [data-testid="stDataEditor"] { border: 1px solid #cbd5e1 !important; border-radius: 4px !important; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    [data-testid="stDataEditor"] input { ime-mode: active !important; }
    .r-row { padding: .4rem .8rem; border-radius: 4px; margin: .2rem 0; font-size: .85rem; font-weight: 500; display: flex; align-items: center; gap: .5rem; }
    .r-ok   { background: #ecfdf5; color: #065f46; border-left: 4px solid #10b981; }
    .r-warn { background: #fffbeb; color: #b45309; border-left: 4px solid #f59e0b; }
    .r-err  { background: #fef2f2; color: #991b1b; border-left: 4px solid #ef4444; }
    </style>
    """, unsafe_allow_html=True)

def sr(kind, name, msg):
    cls  = {"ok":"r-ok","warn":"r-warn","err":"r-err"}.get(kind,"r-ok")
    icon = {"ok":"✓","warn":"△","err":"✕"}.get(kind,"●")
    st.markdown(f'<div class="r-row {cls}"><span>{icon}</span><strong>{name}</strong><span style="opacity:.5; margin:0 4px;">|</span>{msg}</div>', unsafe_allow_html=True)

# ============================================================
# API: GCS画像登録 (画像・アイコン同時設定)
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
        except:
            final_url = signed_url

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"imageUrl": final_url}
        
        # 1. 商品画像の登録
        url_img = f"{get_api_base()}/products/{product_id}/image"
        ok_img, msg_img = False, ""
        for attempt in range(4):
            try:
                r1 = requests.put(url_img, headers=headers, json=payload, timeout=30)
                if r1.status_code in (200, 201, 204): ok_img = True; break
                if r1.status_code == 404 and attempt < 3: time.sleep(2 ** attempt); continue
                msg_img = r1.text[:50]; break
            except Exception as e:
                if attempt < 3: time.sleep(2 ** attempt); continue
                msg_img = str(e); break

        # 2. アイコン画像の登録
        url_icon = f"{get_api_base()}/products/{product_id}/icon_image"
        ok_icon, msg_icon = False, ""
        for attempt in range(4):
            try:
                r2 = requests.put(url_icon, headers=headers, json=payload, timeout=30)
                if r2.status_code in (200, 201, 204): ok_icon = True; break
                if r2.status_code == 404 and attempt < 3: time.sleep(2 ** attempt); continue
                msg_icon = r2.text[:50]; break
            except Exception as e:
                if attempt < 3: time.sleep(2 ** attempt); continue
                msg_icon = str(e); break

        if ok_img and ok_icon: return True, "画像・アイコンの登録完了"
        elif ok_img: return False, f"画像OK / アイコン失敗: {msg_icon}"
        elif ok_icon: return False, f"アイコンOK / 画像失敗: {msg_img}"
        else: return False, f"画像連携エラー (IMG:{msg_img} / ICON:{msg_icon})"
        
    except Exception as e:
        return False, f"システムエラー: {str(e)}"

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

@st.cache_data(ttl=120)
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

# ============================================================
# データ比較・ペイロード生成
# ============================================================
def prod_row(p, token, visible):
    row = {}
    cat_map = {safe_str(c.get("categoryId","")): safe_str(c.get("categoryName","")) for c in get_categories(token)}
    for k in visible:
        d = FIELD_DEFS[k]; v = p.get(d["api"], d["default"])
        if d["type"] == "select": v = api2sel(safe_str(v), d.get("options",[]))
        elif d["type"] == "category":
            cid = safe_str(v); cn = cat_map.get(cid,"")
            v = f"{cid}:{cn}" if cid and cn else cid
        elif d["type"] == "number": v = safe_float(v, d["default"])
        else: v = safe_str(v, d["default"])
        row[k] = v
    row["productId"] = safe_str(p.get("productId",""))
    row["画像セット"] = ""
    return row

def row_to_post_payload(row):
    payload = {}
    for k,d in FIELD_DEFS.items():
        if k not in row or not d.get("post",True): continue
        v = row[k]
        if d["type"] == "select": v = sel2api(v)
        elif d["type"] == "category": v = safe_str(v).split(":")[0] if v and ":" in safe_str(v) else safe_str(v)
        if (v == "" or v is None or v == 0) and not d.get("send_empty",False): continue
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
            if (nv == "" or nv is None or nv == 0) and not d.get("send_empty",False): continue
            diff[d["api"]] = safe_str(nv)
    return diff

def get_original_row(original_df, pid):
    if not pid: return None
    res = original_df[original_df['productId'] == pid]
    if not res.empty: return res.iloc[0].to_dict()
    return None

# ============================================================
# ページ: 商品マスター
# ============================================================
def page_main():
    inject_css()
    token = get_token()
    if not token:
        st.error("スマレジとの認証に失敗しました。Streamlit CloudのSecrets設定（ID・シークレット）を確認してください。")
        st.stop()

    # 🌟 画面左側のサイドバーで、いつでも表示項目をポチポチ切り替えられます！
    st.sidebar.markdown("### 👁️ 表示項目の追加")
    st.sidebar.caption("表に表示したい項目を選んでください。")
    optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
    cur_vis  = st.session_state.get("visible_fields", [])
    sel_vis  = st.sidebar.multiselect("", options=optional, default=[c for c in cur_vis if c in optional], label_visibility="collapsed")
    if sel_vis != cur_vis:
        st.session_state["visible_fields"] = sel_vis
        st.rerun()

    st.markdown('<div class="main-header">📦 商品マスター (Spreadsheet)</div>', unsafe_allow_html=True)
    st.info("💡 **【使い方】** URLを開くだけで最新のデータが表示されます。表を直接編集して「保存する」ボタンを押せば、スマレジに即座に反映されます。")

    visible = get_visible()
    prods = get_products(token)
    
    original_rows = [prod_row(p, token, visible) for p in prods]
    original_df = pd.DataFrame(original_rows)
    display_cols = ["productId"] + visible + ["画像セット"]
    
    if original_df.empty: original_df = pd.DataFrame(columns=display_cols)

    c1, c2, c3 = st.columns([1.5, 1.5, 1])
    with c1:
        st.write("##")
        btn_save = st.button("💾 表の変更をすべて保存する", type="primary", use_container_width=True)
    with c2:
        st.caption("🖼️ 表で紐付けたい画像をドロップ")
        bulk_files = st.file_uploader("", type=["jpg","jpeg","png","gif"], accept_multiple_files=True, label_visibility="collapsed")
        bmap = {f.name: f for f in bulk_files} if bulk_files else {}
    with c3:
        st.write("##")
        if st.button("🔄 最新データに更新", use_container_width=True):
            st.cache_data.clear(); _refresh_cat_options(); st.rerun()

    cat_opts = _cat_options(token)
    ccfg = {}
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category": ccfg[k] = st.column_config.SelectboxColumn(k, options=cat_opts)
        elif d["type"] == "select": ccfg[k] = st.column_config.SelectboxColumn(k, options=d.get("options",[]))
        elif d["type"] == "number": ccfg[k] = st.column_config.NumberColumn(k)
        else: ccfg[k] = st.column_config.TextColumn(k, max_chars=d.get("max"))
    
    ccfg["productId"] = st.column_config.TextColumn("商品ID (空欄=新規)", disabled=True)
    if bmap: ccfg["画像セット"] = st.column_config.SelectboxColumn("画像セット", options=[""]+list(bmap.keys()), default="")
    else: ccfg["画像セット"] = st.column_config.TextColumn("画像セット", default="", disabled=True)

    edited_df = st.data_editor(original_df[display_cols], column_config=ccfg, num_rows="dynamic", use_container_width=True, height=600)

    if btn_save:
        results = []
        with st.spinner("データをスマレジに同期中..."):
            for idx, nr in edited_df.iterrows():
                pid = str(nr.get("productId", "")).strip()
                if pid in ["nan", "None", "<NA>", ""]: pid = None
                pn = str(nr.get("商品名", "")).strip()
                if not pn or pn in ["nan", "None", "<NA>"]: continue
                
                img_name = str(nr.get("画像セット", "")).strip()
                has_img = img_name and img_name in bmap
                
                if not pid:
                    payload = row_to_post_payload(nr)
                    if not payload.get("categoryId"): results.append(("err", pn, "部門未設定スキップ")); continue
                    r = requests.post(f"{get_api_base()}/products", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=payload)
                    if r.status_code in (200, 201):
                        new_pid = r.json().get("productId")
                        if has_img:
                            fo = bmap[img_name]; fo.seek(0)
                            ok, msg = upload_and_link_image(token, new_pid, fo)
                            results.append(("ok", pn, f"新規登録 & {msg}")) if ok else results.append(("warn", pn, f"登録OK / {msg}"))
                        else: results.append(("ok", pn, "新規登録完了"))
                    else: results.append(("err", pn, f"新規エラー: {r.text[:80]}"))
                else:
                    orow = get_original_row(original_df, pid)
                    if orow:
                        dp = diff_payload(orow, nr)
                        if dp:
                            r = requests.patch(f"{get_api_base()}/products/{pid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=dp)
                            if r.status_code not in (200, 204): results.append(("err", pn, f"更新エラー: {r.text[:80]}")); continue
                        
                        if has_img:
                            fo = bmap[img_name]; fo.seek(0)
                            ok, msg = upload_and_link_image(token, pid, fo)
                            results.append(("ok", pn, f"更新 & {msg}")) if ok else results.append(("warn", pn, f"更新OK / {msg}"))
                        elif dp: results.append(("ok", pn, "データ更新完了"))
            st.cache_data.clear(); _refresh_cat_options()
        if results:
            st.markdown("### 処理結果")
            for k, n, m in results: sr(k, n, m)
        else: st.success("変更されたデータはありませんでした。")

# ============================================================
# ページ: 部門マスター
# ============================================================
def page_categories():
    inject_css()
    token = get_token()
    if not token:
        st.error("スマレジとの認証に失敗しました。")
        st.stop()

    st.markdown('<div class="main-header">📁 部門マスター (Spreadsheet)</div>', unsafe_allow_html=True)
    cats = get_categories(token)
    cat_df = pd.DataFrame([{
        "部門ID": safe_str(c.get("categoryId","")), "部門名": safe_str(c.get("categoryName","")), "表示順": safe_int(c.get("displaySequence"), 0),
    } for c in cats]) if cats else pd.DataFrame(columns=["部門ID","部門名","表示順"])

    st.write("##")
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
                    else: results.append(("err", cname, f"追加エラー: {r.text[:80]}"))
                else:
                    old = cats[idx] if idx < len(cats) else None
                    if old:
                        ch = {}
                        if cname != safe_str(old.get("categoryName","")): ch["categoryName"] = cname
                        if cseq != str(safe_int(old.get("displaySequence",0))): ch["displaySequence"] = cseq
                        if ch:
                            r = requests.patch(f"{get_api_base()}/categories/{cid}", headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=ch)
                            if r.status_code in (200, 204): results.append(("ok", cname, "更新完了"))
                            else: results.append(("err", cname, f"更新エラー: {r.text[:80]}"))
            st.cache_data.clear(); _refresh_cat_options()
        if results:
            for k, n, m in results: sr(k, n, m)

# ============================================================
# ナビゲーション
# ============================================================
nav = st.navigation([
    st.Page(page_main,       title="商品マスター", icon="📦"),
    st.Page(page_categories, title="部門マスター", icon="📁"),
])
nav.run()
