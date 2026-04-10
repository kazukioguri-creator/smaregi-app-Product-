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
# 定数・ユーティリティ
# ============================================================
def safe_int(v, d=0):
    if v is None: return d
    try: return int(v)
    except (ValueError, TypeError): return d

def safe_float(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except (ValueError, TypeError): return d

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
            headers={"Authorization": f"Basic {cred}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "pos.products:read pos.products:write"})
        if r.status_code == 200:
            d = r.json()
            t = d.get("access_token")
            st.session_state[ck] = {"at": t, "ea": time.time() + d.get("expires_in", 3600) - 60}
            return t
    except Exception:
        pass
    return None

# ============================================================
# バーコードリーダー (常時マウント・権限保持・枠CSS固定)
# ============================================================
def persistent_barcode_scanner():
    """
    - iframe を1回だけマウントし、JS側で start/pause/resume を制御
    - カメラストリームを stop せず pause するだけなので権限再要求なし
    - 読取枠は CSS の固定オーバーレイで描画 (html5-qrcode の qrbox に頼らない)
    - 読取成功時: ビープ + バイブ + フラッシュ → 値を送信 → 自動で再開待機
    """

    html_code = r"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
        <style>
            *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
            body,html{background:transparent;overflow:hidden;
                font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}

            .wrap{position:relative;width:100%;height:280px;border-radius:16px;
                overflow:hidden;background:#0f172a}

            /* ===== カメラ映像 ===== */
            #reader{width:100%;height:100%}
            #reader video{width:100%!important;height:100%!important;object-fit:cover!important;
                display:block!important;border-radius:16px}
            /* html5-qrcode の内蔵 UI を完全非表示 */
            #reader img,#reader__scan_region>img,
            #reader__dashboard,#reader__header_message,
            #qr-shaded-region{display:none!important}
            #reader__scan_region{
                position:absolute!important;inset:0!important;
                width:100%!important;height:100%!important;
                min-height:0!important;overflow:hidden!important}

            /* ===== 読取枠オーバーレイ (CSS固定) ===== */
            .scan-overlay{
                position:absolute;inset:0;z-index:5;pointer-events:none;
                display:flex;align-items:center;justify-content:center}
            .scan-frame{
                width:280px;height:90px;
                border:2.5px solid rgba(255,255,255,0.85);
                border-radius:12px;
                box-shadow:0 0 0 4000px rgba(0,0,0,0.45);
                transition:border-color .15s}
            .scan-frame.hit{border-color:#22c55e;box-shadow:0 0 0 4000px rgba(0,0,0,0.45),
                0 0 20px rgba(34,197,94,0.5)}
            .scan-hint{
                position:absolute;bottom:18px;left:0;right:0;
                text-align:center;color:rgba(255,255,255,0.7);
                font-size:12px;font-weight:600;letter-spacing:.03em}

            /* ===== ローディング ===== */
            .loading{position:absolute;inset:0;z-index:10;
                display:flex;flex-direction:column;align-items:center;justify-content:center;
                background:#0f172a;color:#94a3b8;font-size:14px;gap:12px;
                transition:opacity .4s}
            .loading.hidden{opacity:0;pointer-events:none}
            .spinner{width:36px;height:36px;border:3px solid #334155;
                border-top-color:#3b82f6;border-radius:50%;animation:spin .8s linear infinite}
            @keyframes spin{to{transform:rotate(360deg)}}

            /* ===== 成功フラッシュ ===== */
            .flash{position:absolute;inset:0;z-index:15;
                background:rgba(34,197,94,0.3);opacity:0;pointer-events:none;
                transition:opacity .12s}
            .flash.on{opacity:1}

            /* ===== 成功バナー ===== */
            .done-banner{position:absolute;inset:0;z-index:20;
                display:flex;flex-direction:column;align-items:center;justify-content:center;
                background:rgba(15,23,42,0.88);color:#fff;
                opacity:0;pointer-events:none;transition:opacity .25s}
            .done-banner.show{opacity:1;pointer-events:auto}
            .done-code{font-size:18px;font-weight:700;letter-spacing:1px;
                background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);
                padding:6px 18px;border-radius:8px;margin-top:6px}

            /* ===== 一時停止マスク ===== */
            .paused-mask{position:absolute;inset:0;z-index:25;
                display:flex;align-items:center;justify-content:center;
                background:rgba(15,23,42,0.75);color:#94a3b8;
                font-size:14px;font-weight:600;
                opacity:0;pointer-events:none;transition:opacity .25s}
            .paused-mask.show{opacity:1;pointer-events:auto}

            /* ===== エラー ===== */
            .cam-error{position:absolute;inset:0;z-index:10;
                display:none;flex-direction:column;align-items:center;justify-content:center;
                background:#0f172a;color:#f87171;font-size:14px;text-align:center;padding:20px}
            .cam-error.show{display:flex}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="loading" id="loading"><div class="spinner"></div><span>カメラ起動中...</span></div>
            <div class="flash" id="flash"></div>
            <div class="done-banner" id="doneBanner">
                <div style="font-size:44px">✅</div>
                <div style="font-size:12px;color:#94a3b8;margin-top:2px">読み取り完了</div>
                <div class="done-code" id="doneCode"></div>
            </div>
            <div class="paused-mask" id="pausedMask">スキャン一時停止中</div>
            <div class="cam-error" id="camError">
                <div style="font-size:32px;margin-bottom:8px">📷</div>
                <div style="font-weight:700">カメラにアクセスできません</div>
                <div style="color:#94a3b8;font-size:12px;margin-top:4px">ブラウザ設定でカメラを許可してください</div>
            </div>
            <div class="scan-overlay" id="overlayWrap">
                <div class="scan-frame" id="scanFrame"></div>
                <div class="scan-hint" id="scanHint">バーコードを枠に合わせてください</div>
            </div>
            <div id="reader"></div>
        </div>

        <script>
        /* ---- Streamlit 通信 ---- */
        const ST={
            send(v){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:setComponentValue",value:v},"*")},
            height(h){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:setFrameHeight",height:h},"*")},
            ready(){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:componentReady",apiVersion:1},"*")}
        };

        /* ---- 効果音 ---- */
        function beep(){try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.type="sine";o.frequency.value=1200;g.gain.value=.3;o.start();o.stop(c.currentTime+.12)}catch(e){}}
        function vibrate(){try{navigator.vibrate&&navigator.vibrate([60,30,60])}catch(e){}}
        function flash(){const e=document.getElementById("flash");e.classList.add("on");setTimeout(()=>e.classList.remove("on"),250)}
        function frameHit(){const f=document.getElementById("scanFrame");f.classList.add("hit");setTimeout(()=>f.classList.remove("hit"),600)}

        /* ---- 状態管理 ---- */
        let scanner=null;
        let state="idle";   // idle | scanning | paused | done
        let cameraStarted=false;

        const $=id=>document.getElementById(id);

        function showBanner(code){
            $("doneCode").textContent=code;
            $("doneBanner").classList.add("show");
        }
        function hideBanner(){$("doneBanner").classList.remove("show")}
        function showPaused(){$("pausedMask").classList.add("show")}
        function hidePaused(){$("pausedMask").classList.remove("show")}

        /* ---- スキャナ開始 (1回だけカメラ権限取得) ---- */
        function initScanner(){
            if(scanner) return Promise.resolve();
            scanner=new Html5Qrcode("reader");

            const cfg={
                fps:15,
                qrbox:{width:9999,height:9999},  /* 画面全体をデコード対象にする */
                aspectRatio:1.333,
                formatsToSupport:[
                    Html5QrcodeSupportedFormats.EAN_13,Html5QrcodeSupportedFormats.EAN_8,
                    Html5QrcodeSupportedFormats.UPC_A,Html5QrcodeSupportedFormats.UPC_E,
                    Html5QrcodeSupportedFormats.CODE_128,Html5QrcodeSupportedFormats.CODE_39,
                    Html5QrcodeSupportedFormats.QR_CODE
                ]
            };

            return scanner.start(
                {facingMode:"environment"},cfg,
                (text)=>{
                    if(state!=="scanning") return;   /* pause 中は無視 */
                    state="done";
                    beep();vibrate();flash();frameHit();
                    showBanner(text);
                    /* pause: カメラは止めず decode だけ停止 */
                    try{scanner.pause(true)}catch(e){}
                    setTimeout(()=>{ ST.send(text) },650);
                },
                ()=>{}
            ).then(()=>{
                cameraStarted=true;
                $("loading").classList.add("hidden");
                state="scanning";
            }).catch(()=>{
                $("loading").classList.add("hidden");
                $("camError").classList.add("show");
            });
        }

        /* ---- 外部コマンド受信 ---- */
        function handleCommand(cmd){
            if(cmd==="start"||cmd==="resume"){
                hideBanner();hidePaused();
                if(!cameraStarted){
                    $("loading").classList.remove("hidden");
                    initScanner();
                }else{
                    try{scanner.resume()}catch(e){}
                    state="scanning";
                }
                ST.height(280);
            }else if(cmd==="pause"){
                if(state==="scanning"){
                    try{scanner.pause(true)}catch(e){}
                    state="paused";
                    showPaused();
                }
            }else if(cmd==="hide"){
                if(state==="scanning"){try{scanner.pause(true)}catch(e){}}
                state="paused";
                ST.height(0);
            }
        }

        /* ---- Streamlit lifecycle ---- */
        let firstRender=true;
        window.onload=function(){ST.ready();ST.height(0)};

        window.addEventListener("message",function(ev){
            if(!ev.data||ev.data.type!=="streamlit:render") return;
            const args=ev.data.args||{};
            const cmd=args.command||"";

            if(firstRender){
                firstRender=false;
                if(cmd) handleCommand(cmd);
                return;
            }
            if(cmd) handleCommand(cmd);
        });
        </script>
    </body>
    </html>
    """

    component_dir = os.path.abspath("barcode_component_dir")
    os.makedirs(component_dir, exist_ok=True)
    with open(os.path.join(component_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)

    _comp = components.declare_component("persistent_barcode_scanner", path=component_dir)
    return _comp


# グローバルでコンポーネント関数を1回だけ生成
_scanner_component = persistent_barcode_scanner()


def render_scanner(command="hide", key="scanner_main"):
    """
    command: "start" | "resume" | "pause" | "hide"
    常に同じ key でレンダリングするため iframe は再生成されない
    """
    return _scanner_component(command=command, key=key)


# ============================================================
# フィールド定義
# ============================================================
FIELD_DEFS = OrderedDict([
    ("商品名",       {"api":"productName",          "type":"text",    "default":"",          "required":True, "core":True, "max":85,  "send_empty":True, "post":True}),
    ("商品価格",     {"api":"price",                "type":"number",  "default":0,           "required":True, "core":True, "max":None,"send_empty":True, "post":True}),
    ("部門ID",       {"api":"categoryId",           "type":"category","default":"",          "required":True, "core":True, "max":None,"send_empty":False,"post":True}),
    ("原価",         {"api":"cost",                 "type":"number",  "default":0,           "required":False,"core":False,"max":None,"send_empty":False,"post":True}),
    ("税区分",       {"api":"taxDivision",          "type":"select",  "default":"0:税込",    "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:税込","1:税抜","2:非課税"]}),
    ("在庫管理区分", {"api":"stockControlDivision", "type":"select",  "default":"0:対象",    "required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:対象","1:対象外"]}),
    ("売上区分",     {"api":"salesDivision",        "type":"select",  "default":"0:売上対象","required":False,"core":False,"max":None,"send_empty":False,"post":True,
        "options":["0:売上対象","1:売上対象外"]}),
    ("説明",         {"api":"description",          "type":"text",    "default":"",          "required":False,"core":False,"max":1000,"send_empty":False,"post":True}),
    ("カラー",       {"api":"color",                "type":"text",    "default":"",          "required":False,"core":False,"max":85,  "send_empty":False,"post":True}),
    ("サイズ",       {"api":"size",                 "type":"text",    "default":"",          "required":False,"core":False,"max":85,  "send_empty":False,"post":True}),
    ("端末表示",     {"api":"displayFlag",          "type":"select",  "default":"1:表示する","required":False,"core":False,"max":None,"send_empty":False,"post":True,
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
    st.session_state.pop("cat_options_cache", None)


# ============================================================
# CSS
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    :root{--brand:#2563eb;--brand-light:#dbeafe;--success:#16a34a;
        --danger:#dc2626;--surface:#fff;--bg:#f8fafc;
        --text1:#0f172a;--text2:#64748b;--border:#e2e8f0;--radius:14px}
    *{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
    input,select,textarea,.stSelectbox div,
    [data-baseweb="select"] span,[data-baseweb="input"] input{font-size:16px!important}
    .stApp{background:var(--bg)}
    .block-container{padding:.75rem .75rem 6rem .75rem!important;max-width:640px!important;margin:0 auto}

    .ui-card{background:var(--surface);padding:1.25rem;border-radius:var(--radius);
        box-shadow:0 1px 3px rgba(0,0,0,.04),0 4px 12px rgba(0,0,0,.03);
        margin-bottom:1rem;border:1px solid var(--border)}
    .ui-card-title{font-size:.85rem;font-weight:700;color:var(--text2);
        text-transform:uppercase;letter-spacing:.04em;margin-bottom:.75rem}

    .status-badge{display:inline-flex;align-items:center;gap:6px;
        padding:6px 14px;border-radius:999px;font-size:.85rem;font-weight:600}
    .status-new{background:var(--brand-light);color:var(--brand)}
    .status-exist{background:#fef3c7;color:#92400e}

    .stButton>button{border-radius:12px!important;font-weight:700!important;
        padding:.75rem 1rem!important;font-size:1rem!important;width:100%;
        transition:transform .1s,box-shadow .15s}
    .stButton>button:active{transform:scale(.97)}
    .stButton>button[kind="primary"]{background:var(--brand)!important;color:#fff!important;
        border:none!important;box-shadow:0 2px 8px rgba(37,99,235,.25)}
    .stButton>button[kind="secondary"]{background:var(--surface)!important;color:var(--text1)!important;
        border:1.5px solid var(--border)!important}

    .r-row{padding:.85rem 1rem;border-radius:12px;margin:.35rem 0;font-size:.92rem;
        font-weight:600;display:flex;align-items:center;gap:8px}
    .r-ok{background:#f0fdf4;color:#166534;border-left:5px solid #22c55e}
    .r-err{background:#fef2f2;color:#991b1b;border-left:5px solid #ef4444}

    div[data-testid="stVerticalBlock"]>div{padding-bottom:.15rem!important}
    header[data-testid="stHeader"]{background:var(--bg)}
    </style>""", unsafe_allow_html=True)

def sr(kind, name, msg):
    cls = {"ok":"r-ok","err":"r-err"}.get(kind,"r-ok")
    icon = {"ok":"✅","err":"❌"}.get(kind,"●")
    st.markdown(f'<div class="r-row {cls}"><span>{icon}</span><strong>{name}</strong>'
                f'<span style="opacity:.4;margin:0 4px;">|</span>{msg}</div>', unsafe_allow_html=True)


# ============================================================
# GCS 画像
# ============================================================
def get_gcp_credentials():
    gcp_dict = json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
    return service_account.Credentials.from_service_account_info(gcp_dict)

def upload_and_link_image(token, product_id, file_obj):
    try:
        img = Image.open(file_obj)
        if img.mode not in ("RGB","L"): img = img.convert("RGB")
        img.thumbnail((800,800), Image.LANCZOS)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=85); buf.seek(0)

        creds = get_gcp_credentials()
        client = storage.Client(credentials=creds, project=creds.project_id)
        bucket = client.bucket(st.secrets["GCP_BUCKET_NAME"])
        fname = f"products/{product_id}_{int(time.time()*1000)}.jpg"
        blob = bucket.blob(fname)
        blob.upload_from_file(buf, content_type="image/jpeg")

        signed_url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(minutes=15), method="GET")
        try:
            short_res = requests.get(f"https://tinyurl.com/api-create.php?url={requests.utils.quote(signed_url)}", timeout=5)
            final_url = short_res.text if short_res.status_code == 200 else signed_url
        except: final_url = signed_url

        headers = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
        payload = {"imageUrl": final_url}

        ok_img = ok_icon = False
        for _ in range(3):
            try:
                r = requests.put(f"{get_api_base()}/products/{product_id}/image", headers=headers, json=payload, timeout=15)
                if r.status_code in (200,201,204): ok_img=True; break
                if r.status_code==404: time.sleep(1); continue
            except: time.sleep(1)
        for _ in range(3):
            try:
                r = requests.put(f"{get_api_base()}/products/{product_id}/icon_image", headers=headers, json=payload, timeout=15)
                if r.status_code in (200,201,204): ok_icon=True; break
                if r.status_code==404: time.sleep(1); continue
            except: time.sleep(1)

        return (ok_img and ok_icon), ("画像登録完了" if ok_img and ok_icon else "画像の一部連携に失敗")
    except Exception as e:
        return False, f"エラー: {e}"


# ============================================================
# API: データ取得
# ============================================================
@st.cache_data(ttl=120)
def get_categories(token):
    cats, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/categories", headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code!=200: break
        d = r.json()
        if not isinstance(d, list): break
        cats.extend(d)
        if len(d)<1000: break
        p+=1
    return cats

@st.cache_data(ttl=60)
def get_products(token):
    prods, p = [], 1
    while True:
        r = requests.get(f"{get_api_base()}/products", headers={"Authorization":f"Bearer {token}"}, params={"limit":1000,"page":p})
        if r.status_code!=200: break
        d = r.json()
        if not isinstance(d, list): break
        prods.extend(d)
        if len(d)<1000: break
        p+=1
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
        if d["type"]=="select": v=sel2api(v)
        elif d["type"]=="category": v=safe_str(v).split(":")[0] if v and ":" in safe_str(v) else safe_str(v)
        if (v=="" or v is None or v==0) and not d.get("send_empty",False): continue
        payload[d["api"]] = safe_str(v)
    return payload


# ============================================================
# ページ 1: スキャン＆登録
# ============================================================
def _init_state():
    defaults = {
        "scan_phase": "idle",
        "final_code": "",
        "code_source": None,
        "last_registered": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def page_scanner_form():
    inject_css()
    _init_state()
    token = get_token()
    if not token:
        st.error("スマレジ認証エラー。設定を確認してください。")
        st.stop()

    prods = get_products(token)
    cat_opts = _cat_options(token)
    phase = st.session_state.scan_phase

    # --- 直前の登録結果 ---
    last = st.session_state.last_registered
    if last:
        sr("ok" if last["ok"] else "err", last["name"], "登録完了" if last["ok"] else last.get("detail","エラー"))
        st.session_state.last_registered = None

    # ===========================================
    # カメラコンポーネント (常時マウント)
    # phase に応じてコマンドを切り替えるだけ
    # ===========================================
    if phase == "scanning":
        scanner_cmd = "start"
    elif phase == "scanned":
        scanner_cmd = "pause"
    else:
        scanner_cmd = "hide"

    scanned_value = render_scanner(command=scanner_cmd, key="scanner_main")

    # スキャン結果を受け取ったら phase 遷移
    if scanned_value and phase == "scanning":
        st.session_state.final_code = scanned_value
        st.session_state.scan_phase = "scanned"
        st.rerun()

    # ===========================================
    # idle: 入力方法を選択
    # ===========================================
    if phase == "idle":
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品コードを入力</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("📸 スキャン", type="primary", use_container_width=True):
                st.session_state.scan_phase = "scanning"
                st.session_state.code_source = "scan"
                st.rerun()
        with c2:
            if st.button("⌨️ 手入力", type="secondary", use_container_width=True):
                st.session_state.scan_phase = "manual_input"
                st.session_state.code_source = "manual"
                st.rerun()
        with c3:
            if st.button("⚙️ 自動採番", type="secondary", use_container_width=True):
                st.session_state.final_code = generate_auto_code()
                st.session_state.scan_phase = "scanned"
                st.session_state.code_source = "auto"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ===========================================
    # scanning: カメラアクティブ
    # ===========================================
    elif phase == "scanning":
        if st.button("← 戻る", type="secondary"):
            st.session_state.scan_phase = "idle"
            st.rerun()

    # ===========================================
    # manual_input: 手動入力
    # ===========================================
    elif phase == "manual_input":
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品コードを入力</div>', unsafe_allow_html=True)
        manual_code = st.text_input("商品コード", placeholder="例: 4901234567890", label_visibility="collapsed")
        ca, cb = st.columns(2)
        with ca:
            if st.button("決定", type="primary", disabled=not manual_code):
                st.session_state.final_code = manual_code.strip()
                st.session_state.scan_phase = "scanned"
                st.rerun()
        with cb:
            if st.button("← 戻る", type="secondary"):
                st.session_state.scan_phase = "idle"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ===========================================
    # scanned: コード確定 → フォーム
    # ===========================================
    elif phase == "scanned":
        code_input = st.session_state.final_code
        target_prod = find_product_by_code(prods, code_input)
        is_new = target_prod is None

        # コード表示
        st.markdown(
            f'<div class="ui-card" style="padding:.85rem 1.25rem">'
            f'<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<span style="font-size:.8rem;color:var(--text2)">商品コード</span>'
            f'<code style="font-size:1.05rem;font-weight:700;letter-spacing:.5px">{code_input}</code>'
            f'</div></div>', unsafe_allow_html=True)

        if is_new:
            st.markdown('<span class="status-badge status-new">✨ 新規登録</span>', unsafe_allow_html=True)
            default_data = {k: d["default"] for k, d in FIELD_DEFS.items()}
        else:
            st.markdown(f'<span class="status-badge status-exist">🔄 更新: {target_prod.get("productName","")}</span>', unsafe_allow_html=True)
            default_data = {}
            for k, d in FIELD_DEFS.items():
                val = target_prod.get(d["api"], d["default"])
                if d["type"]=="number": val=safe_float(val, d["default"])
                elif d["type"]=="select": val=api2sel(safe_str(val), d["options"])
                elif d["type"]=="category":
                    cid=safe_str(val)
                    val=next((o for o in cat_opts if o.startswith(cid+":")),""  ) if cid else ""
                default_data[k] = val

        # フォーム
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品情報</div>', unsafe_allow_html=True)
        form_vals = {}
        form_vals["商品名"] = st.text_input("商品名 *", value=default_data.get("商品名",""), placeholder="例: オーガニックコーヒー豆 200g")
        form_vals["商品価格"] = st.number_input("価格 *", value=int(default_data.get("商品価格",0)), step=10, min_value=0)
        cat_default = default_data.get("部門ID","")
        cat_index = cat_opts.index(cat_default) if cat_default in cat_opts else 0
        form_vals["部門ID"] = st.selectbox("部門 *", cat_opts, index=cat_index)

        with st.expander("詳細設定（原価・税区分など）", expanded=False):
            for k, d in FIELD_DEFS.items():
                if d["core"]: continue
                dv = default_data.get(k, d["default"])
                if d["type"]=="select":
                    idx = d["options"].index(dv) if dv in d["options"] else 0
                    form_vals[k] = st.selectbox(k, d["options"], index=idx)
                elif d["type"]=="number":
                    form_vals[k] = st.number_input(k, value=int(dv), step=1)
                else:
                    form_vals[k] = st.text_input(k, value=dv)
        st.markdown('</div>', unsafe_allow_html=True)

        # 写真
        st.markdown('<div class="ui-card"><div class="ui-card-title">写真（任意）</div>', unsafe_allow_html=True)
        photo_tab, upload_tab = st.tabs(["📷 撮影","📁 ファイル選択"])
        with photo_tab:
            camera_img = st.camera_input("商品を撮影", label_visibility="collapsed")
        with upload_tab:
            upload_img = st.file_uploader("画像を選択", type=["jpg","jpeg","png"], label_visibility="collapsed")
        img_file = camera_img or upload_img
        st.markdown('</div>', unsafe_allow_html=True)

        # ボタン
        col_s, col_c = st.columns([3,1])
        with col_s:
            submit_btn = st.button("🚀 登録して次へ" if is_new else "🔄 更新して次へ", type="primary", use_container_width=True)
        with col_c:
            if st.button("取消", type="secondary", use_container_width=True):
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""
                st.rerun()

        if submit_btn:
            if not form_vals["商品名"]:
                st.error("商品名は必須です。"); st.stop()
            if not form_vals["部門ID"]:
                st.error("部門を選択してください。"); st.stop()

            payload = create_payload(form_vals, code_input)
            headers = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
            ok, detail = False, ""

            with st.spinner("送信中..."):
                if is_new:
                    r = requests.post(f"{get_api_base()}/products", headers=headers, json=payload)
                    if r.status_code in (200,201):
                        pid = r.json().get("productId")
                        if img_file: upload_and_link_image(token, pid, img_file)
                        ok = True
                    else: detail = r.text[:80]
                else:
                    pid = target_prod.get("productId")
                    r = requests.patch(f"{get_api_base()}/products/{pid}", headers=headers, json=payload)
                    if r.status_code in (200,204):
                        if img_file: upload_and_link_image(token, pid, img_file)
                        ok = True
                    else: detail = r.text[:80]

            st.cache_data.clear()
            st.session_state.last_registered = {"name": form_vals["商品名"], "ok": ok, "detail": detail}

            source = st.session_state.code_source
            if source == "auto":
                st.session_state.final_code = generate_auto_code()
                st.session_state.scan_phase = "scanned"
            elif source == "scan":
                st.session_state.scan_phase = "scanning"
                st.session_state.final_code = ""
            else:
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""
            st.rerun()


# ============================================================
# ページ 2: 商品一括管理
# ============================================================
def page_spreadsheet():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()

    st.markdown("### 商品一括管理")

    with st.expander("表示列の設定"):
        optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
        cur_vis = st.session_state.get("visible_fields",[])
        sel_vis = st.multiselect("追加する項目", options=optional, default=[c for c in cur_vis if c in optional], label_visibility="collapsed")
        if sel_vis != cur_vis:
            st.session_state["visible_fields"] = sel_vis; st.rerun()

    visible = get_visible()
    prods = get_products(token)
    cat_map = {safe_str(c.get("categoryId","")): safe_str(c.get("categoryName","")) for c in get_categories(token)}

    rows = []
    for p in prods:
        row = {"productId": safe_str(p.get("productId","")), "商品コード": safe_str(p.get("productCode",""))}
        for k in visible:
            d = FIELD_DEFS[k]; v = p.get(d["api"], d["default"])
            if d["type"]=="select": v=api2sel(safe_str(v), d.get("options",[]))
            elif d["type"]=="category":
                cid=safe_str(v); cn=cat_map.get(cid,"")
                v=f"{cid}:{cn}" if cid and cn else cid
            elif d["type"]=="number": v=safe_float(v, d["default"])
            else: v=safe_str(v, d["default"])
            row[k]=v
        rows.append(row)

    df = pd.DataFrame(rows)
    display_cols = ["productId","商品コード"]+visible
    if df.empty: df = pd.DataFrame(columns=display_cols)

    btn_save = st.button("💾 変更をすべて保存", type="primary")

    cat_opts = _cat_options(token)
    ccfg = {"productId": st.column_config.TextColumn("商品ID", disabled=True), "商品コード": st.column_config.TextColumn("商品コード", disabled=True)}
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"]=="category": ccfg[k]=st.column_config.SelectboxColumn(k, options=cat_opts)
        elif d["type"]=="select": ccfg[k]=st.column_config.SelectboxColumn(k, options=d.get("options",[]))
        elif d["type"]=="number": ccfg[k]=st.column_config.NumberColumn(k)
        else: ccfg[k]=st.column_config.TextColumn(k, max_chars=d.get("max"))

    edited_df = st.data_editor(df[display_cols], column_config=ccfg, num_rows="fixed", use_container_width=True, height=600)

    if btn_save:
        results = []
        with st.spinner("同期中..."):
            for idx, nr in edited_df.iterrows():
                pid = str(nr.get("productId","")).strip()
                if not pid: continue
                orow = df[df['productId']==pid].iloc[0].to_dict()
                dp = {}
                for k,d in FIELD_DEFS.items():
                    if k not in orow or k not in nr: continue
                    ov,nv = orow[k],nr[k]
                    if d["type"]=="select": ov,nv=sel2api(ov),sel2api(nv)
                    elif d["type"]=="category":
                        ov=safe_str(ov).split(":")[0] if ov and ":" in safe_str(ov) else safe_str(ov)
                        nv=safe_str(nv).split(":")[0] if nv and ":" in safe_str(nv) else safe_str(nv)
                    if safe_str(ov)!=safe_str(nv):
                        if (nv=="" or nv is None or nv==0) and not d.get("send_empty",False): continue
                        dp[d["api"]]=safe_str(nv)
                if dp:
                    r = requests.patch(f"{get_api_base()}/products/{pid}",
                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, json=dp)
                    pn=str(nr.get("商品名","不明"))
                    results.append(("ok",pn,"更新完了") if r.status_code in (200,204) else ("err",pn,"更新エラー"))
            st.cache_data.clear()
        for k,n,m in results: sr(k,n,m)


# ============================================================
# ページ 3: 部門マスター
# ============================================================
def page_categories():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()

    st.markdown("### 部門マスター")
    cats = get_categories(token)
    cat_df = pd.DataFrame([{
        "部門ID": safe_str(c.get("categoryId","")),
        "部門名": safe_str(c.get("categoryName","")),
        "表示順": safe_int(c.get("displaySequence"),0),
    } for c in cats]) if cats else pd.DataFrame(columns=["部門ID","部門名","表示順"])

    btn_save_cat = st.button("💾 部門データを保存", type="primary")
    edited_cats = st.data_editor(cat_df, use_container_width=True, num_rows="dynamic", height=500,
        column_config={"部門ID": st.column_config.TextColumn("部門ID (自動)", disabled=True),
                       "部門名": st.column_config.TextColumn("部門名", required=True),
                       "表示順": st.column_config.NumberColumn("表示順", default=0)})

    if btn_save_cat:
        results = []
        with st.spinner("同期中..."):
            for idx, row in edited_cats.iterrows():
                cid=str(row.get("部門ID","")).strip()
                if cid in ["nan","None","<NA>",""]: cid=None
                cname=str(row.get("部門名","")).strip()
                if not cname or cname in ["nan","None","<NA>"]: continue
                cseq=str(safe_int(row.get("表示順",0)))
                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}

                if not cid:
                    r=requests.post(f"{get_api_base()}/categories", headers=headers, json={"categoryName":cname,"displaySequence":cseq})
                    results.append(("ok",cname,"追加完了") if r.status_code in (200,201) else ("err",cname,"追加エラー"))
                else:
                    old=cats[idx] if idx<len(cats) else None
                    if old:
                        ch={}
                        if cname!=safe_str(old.get("categoryName","")): ch["categoryName"]=cname
                        if cseq!=str(safe_int(old.get("displaySequence",0))): ch["displaySequence"]=cseq
                        if ch:
                            r=requests.patch(f"{get_api_base()}/categories/{cid}", headers=headers, json=ch)
                            results.append(("ok",cname,"更新完了") if r.status_code in (200,204) else ("err",cname,"更新エラー"))
            st.cache_data.clear(); _refresh_cat_options()
        for k,n,m in results: sr(k,n,m)


# ============================================================
# ページ 4: 設定
# ============================================================
def page_settings():
    inject_css()
    st.markdown("### 設定")
    st.markdown('<div class="ui-card"><div class="ui-card-title">自動採番ルール</div>', unsafe_allow_html=True)
    st.caption("中央には日時が自動挿入されます。")
    pfx = st.text_input("接頭辞", value=st.session_state.auto_rule_prefix)
    sfx = st.text_input("接尾辞", value=st.session_state.auto_rule_suffix)
    st.code(f"{pfx}20260410134221{sfx}", language=None)
    if st.button("保存", type="primary"):
        st.session_state.auto_rule_prefix = pfx
        st.session_state.auto_rule_suffix = sfx
        st.success("保存しました。")
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
