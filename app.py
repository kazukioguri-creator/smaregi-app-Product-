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
# ユーティリティ
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
    dom = "smaregi.dev" if st.secrets.get("USE_SANDBOX", True) else "smaregi.jp"
    return f"https://api.{dom}/{cid}/pos"

def get_auth_url():
    cid = st.secrets["CONTRACT_ID"]
    dom = "smaregi.dev" if st.secrets.get("USE_SANDBOX", True) else "smaregi.jp"
    return f"https://id.{dom}/app/{cid}/token"

if "auto_rule_prefix" not in st.session_state:
    st.session_state.auto_rule_prefix = "AUTO-"
if "auto_rule_suffix" not in st.session_state:
    st.session_state.auto_rule_suffix = ""

def generate_auto_code():
    pfx = st.session_state.auto_rule_prefix
    sfx = st.session_state.auto_rule_suffix
    return f"{pfx}{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{sfx}"

# ============================================================
# 認証
# ============================================================
def get_token():
    try:
        ci, cs = st.secrets["CLIENT_ID"], st.secrets["CLIENT_SECRET"]
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
            d = r.json(); t = d["access_token"]
            st.session_state[ck] = {"at": t, "ea": time.time() + d.get("expires_in", 3600) - 60}
            return t
    except Exception:
        pass
    return None

# ============================================================
# バーコードリーダー
#
# 連続スキャン対応の要点:
#   1. 読取成功 → uid付きJSONを送信 → 即座にnullで上書き (古い値の残留を防止)
#   2. 1.2秒後に自動resume (カメラストリームは維持)
#   3. Python側は uid の一致で重複を弾く
# ============================================================
def build_scanner_component():
    html_code = r"""<!DOCTYPE html>
<html>
<head>
<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body,html{background:transparent;overflow:hidden;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{position:relative;width:100%;height:280px;border-radius:16px;overflow:hidden;background:#0f172a}
#reader{width:100%;height:100%}
#reader video{width:100%!important;height:100%!important;object-fit:cover!important;display:block!important;border-radius:16px}
#reader img,#reader__scan_region>img,#reader__dashboard,
#reader__header_message,#qr-shaded-region{display:none!important}
#reader__scan_region{position:absolute!important;inset:0!important;
    width:100%!important;height:100%!important;min-height:0!important;overflow:hidden!important}
.scan-overlay{position:absolute;inset:0;z-index:5;pointer-events:none;
    display:flex;align-items:center;justify-content:center}
.scan-frame{width:280px;height:90px;border:2.5px solid rgba(255,255,255,.85);
    border-radius:12px;box-shadow:0 0 0 4000px rgba(0,0,0,.45);transition:border-color .2s,box-shadow .2s}
.scan-frame.hit{border-color:#22c55e;box-shadow:0 0 0 4000px rgba(0,0,0,.45),0 0 24px rgba(34,197,94,.6)}
.scan-hint{position:absolute;bottom:16px;left:0;right:0;text-align:center;
    color:rgba(255,255,255,.7);font-size:12px;font-weight:600}
.loading{position:absolute;inset:0;z-index:10;display:flex;flex-direction:column;
    align-items:center;justify-content:center;background:#0f172a;color:#94a3b8;
    font-size:14px;gap:12px;transition:opacity .4s}
.loading.hidden{opacity:0;pointer-events:none}
.spinner{width:36px;height:36px;border:3px solid #334155;border-top-color:#3b82f6;
    border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.flash{position:absolute;inset:0;z-index:15;background:rgba(34,197,94,.3);
    opacity:0;pointer-events:none;transition:opacity .12s}
.flash.on{opacity:1}
.done-banner{position:absolute;inset:0;z-index:20;display:flex;flex-direction:column;
    align-items:center;justify-content:center;background:rgba(15,23,42,.88);color:#fff;
    opacity:0;pointer-events:none;transition:opacity .25s}
.done-banner.show{opacity:1}
.done-code{font-size:18px;font-weight:700;letter-spacing:1px;
    background:rgba(34,197,94,.15);border:1px solid rgba(34,197,94,.4);
    padding:6px 18px;border-radius:8px;margin-top:6px}
.done-sub{font-size:11px;color:#94a3b8;margin-top:8px}
.cam-error{position:absolute;inset:0;z-index:10;display:none;flex-direction:column;
    align-items:center;justify-content:center;background:#0f172a;color:#f87171;
    font-size:14px;text-align:center;padding:20px}
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
        <div class="done-sub">次のバーコードをかざしてください</div>
    </div>
    <div class="cam-error" id="camError">
        <div style="font-size:32px;margin-bottom:8px">📷</div>
        <div style="font-weight:700">カメラにアクセスできません</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:4px">ブラウザ設定でカメラを許可してください</div>
    </div>
    <div class="scan-overlay"><div class="scan-frame" id="scanFrame"></div>
        <div class="scan-hint" id="scanHint">バーコードを枠に合わせてください</div></div>
    <div id="reader"></div>
</div>
<script>
const ST={
    send(v){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:setComponentValue",value:v},"*")},
    height(h){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:setFrameHeight",height:h},"*")},
    ready(){window.parent.postMessage({isStreamlitMessage:true,type:"streamlit:componentReady",apiVersion:1},"*")}
};
function beep(){try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.type="sine";o.frequency.value=1200;g.gain.value=.3;o.start();o.stop(c.currentTime+.12)}catch(e){}}
function vibrate(){try{navigator.vibrate&&navigator.vibrate([60,30,60])}catch(e){}}

const $=id=>document.getElementById(id);
let scanner=null, cameraReady=false, scanActive=false, lastCmdSeq=-1;
let autoResumeTimer=null;

function showBanner(code){$("doneCode").textContent=code;$("doneBanner").classList.add("show")}
function hideBanner(){$("doneBanner").classList.remove("show")}
function doFlash(){const e=$("flash");e.classList.add("on");setTimeout(()=>e.classList.remove("on"),250)}
function doFrameHit(){const f=$("scanFrame");f.classList.add("hit");setTimeout(()=>f.classList.remove("hit"),800)}

function initCamera(){
    if(scanner) return Promise.resolve();
    scanner=new Html5Qrcode("reader");
    return scanner.start(
        {facingMode:"environment"},
        {fps:15,qrbox:{width:9999,height:9999},aspectRatio:1.333,
         formatsToSupport:[
            Html5QrcodeSupportedFormats.EAN_13,Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.UPC_A,Html5QrcodeSupportedFormats.UPC_E,
            Html5QrcodeSupportedFormats.CODE_128,Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.QR_CODE]},
        (text)=>{
            if(!scanActive) return;
            scanActive=false;
            beep();vibrate();doFlash();doFrameHit();
            showBanner(text);
            try{scanner.pause(true)}catch(e){}

            /* ★ uid付きで送信し、50ms後にnullで上書き → rerunで古い値が返らない */
            const uid=Date.now()+"_"+Math.random().toString(36).slice(2,8);
            ST.send(JSON.stringify({code:text,uid:uid}));
            setTimeout(()=>{ ST.send(null) }, 50);

            /* 1.2秒後に自動resume */
            if(autoResumeTimer) clearTimeout(autoResumeTimer);
            autoResumeTimer=setTimeout(()=>{
                hideBanner();
                try{scanner.resume()}catch(e){}
                scanActive=true;
            },1200);
        },
        ()=>{}
    ).then(()=>{
        cameraReady=true;
        $("loading").classList.add("hidden");
        scanActive=true;
    }).catch(()=>{
        $("loading").classList.add("hidden");
        $("camError").classList.add("show");
    });
}

function handleCommand(cmd,seq){
    if(seq<=lastCmdSeq) return;
    lastCmdSeq=seq;
    if(cmd==="start"){
        hideBanner();
        if(autoResumeTimer){clearTimeout(autoResumeTimer);autoResumeTimer=null}
        if(!cameraReady){
            $("loading").classList.remove("hidden");
            initCamera();
        }else{
            try{scanner.resume()}catch(e){}
            scanActive=true;
        }
        ST.height(280);
    }else if(cmd==="hide"){
        if(autoResumeTimer){clearTimeout(autoResumeTimer);autoResumeTimer=null}
        hideBanner();
        if(cameraReady&&scanActive){try{scanner.pause(true)}catch(e){}}
        scanActive=false;
        ST.height(0);
    }
}

window.onload=function(){ST.ready();ST.height(0)};
window.addEventListener("message",function(ev){
    if(!ev.data||ev.data.type!=="streamlit:render") return;
    const a=ev.data.args||{};
    if(a.command) handleCommand(a.command, a.seq||0);
});
</script>
</body>
</html>"""
    d = os.path.abspath("barcode_component_dir")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)
    return components.declare_component("persistent_scanner", path=d)

_scanner_func = build_scanner_component()

def render_scanner(command="hide"):
    if "scanner_cmd_seq" not in st.session_state:
        st.session_state.scanner_cmd_seq = 0
    st.session_state.scanner_cmd_seq += 1
    raw = _scanner_func(
        command=command,
        seq=st.session_state.scanner_cmd_seq,
        key="__persistent_scanner__",
        default=None,
    )
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        code = parsed.get("code", "")
        uid  = parsed.get("uid", "")
    except (json.JSONDecodeError, TypeError):
        return None  # null や不正な値は無視
    if not uid:
        return None
    if uid == st.session_state.get("_last_scan_uid"):
        return None
    st.session_state["_last_scan_uid"] = uid
    return code if code else None


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
    st.markdown("""<style>
:root{--brand:#2563eb;--brand-light:#dbeafe;--success:#16a34a;
    --surface:#fff;--bg:#f8fafc;--text1:#0f172a;--text2:#64748b;--border:#e2e8f0;--radius:14px}
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
.status-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;
    border-radius:999px;font-size:.85rem;font-weight:600;margin-bottom:.5rem}
.status-new{background:var(--brand-light);color:var(--brand)}
.status-exist{background:#fef3c7;color:#92400e}
.stButton>button{border-radius:12px!important;font-weight:700!important;
    padding:.75rem 1rem!important;font-size:1rem!important;width:100%;transition:transform .1s}
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
.hist{display:flex;align-items:center;gap:8px;padding:8px 12px;
    border-radius:10px;margin:4px 0;font-size:.85rem;font-weight:500}
.hist-ok{background:#f0fdf4;color:#166534}
.hist-err{background:#fef2f2;color:#991b1b}
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
    return service_account.Credentials.from_service_account_info(json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"]))

def upload_and_link_image(token, product_id, file_obj):
    try:
        img = Image.open(file_obj)
        if img.mode not in ("RGB","L"): img = img.convert("RGB")
        img.thumbnail((800,800), Image.LANCZOS)
        buf = BytesIO(); img.save(buf, format="JPEG", quality=85); buf.seek(0)
        creds = get_gcp_credentials()
        client = storage.Client(credentials=creds, project=creds.project_id)
        bucket = client.bucket(st.secrets["GCP_BUCKET_NAME"])
        blob = bucket.blob(f"products/{product_id}_{int(time.time()*1000)}.jpg")
        blob.upload_from_file(buf, content_type="image/jpeg")
        signed_url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(minutes=15), method="GET")
        try:
            r = requests.get(f"https://tinyurl.com/api-create.php?url={requests.utils.quote(signed_url)}", timeout=5)
            final_url = r.text if r.status_code == 200 else signed_url
        except: final_url = signed_url
        headers = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
        payload = {"imageUrl": final_url}
        ok_img = ok_icon = False
        for _ in range(3):
            try:
                r = requests.put(f"{get_api_base()}/products/{product_id}/image", headers=headers, json=payload, timeout=15)
                if r.status_code in (200,201,204): ok_img=True; break
            except: pass
            time.sleep(1)
        for _ in range(3):
            try:
                r = requests.put(f"{get_api_base()}/products/{product_id}/icon_image", headers=headers, json=payload, timeout=15)
                if r.status_code in (200,201,204): ok_icon=True; break
            except: pass
            time.sleep(1)
        return (ok_img and ok_icon), "画像登録完了" if ok_img and ok_icon else "画像一部失敗"
    except Exception as e:
        return False, str(e)


# ============================================================
# API
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
        if (v == "" or v is None or v == 0) and not d.get("send_empty", False): continue
        payload[d["api"]] = safe_str(v)
    return payload


# ============================================================
# ページ 1: スキャン＆登録 (連続登録)
# ============================================================
def _init_state():
    for k, v in {
        "scan_phase": "idle",
        "final_code": "",
        "code_source": None,
        "register_history": [],
        # ★ 登録直後に追加したコードを「既知」として記録し、
        #   次の get_products で返ってきても新規扱いにしない
        "just_registered_codes": set(),
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _add_history(name, code, ok):
    h = st.session_state.register_history
    h.insert(0, {"name": name, "code": code, "ok": ok,
                 "time": datetime.datetime.now().strftime("%H:%M:%S")})
    if len(h) > 50:
        st.session_state.register_history = h[:50]

def page_scanner_form():
    inject_css()
    _init_state()
    token = get_token()
    if not token:
        st.error("スマレジ認証エラー。")
        st.stop()

    prods = get_products(token)
    cat_opts = _cat_options(token)
    phase = st.session_state.scan_phase

    # ======== カメラ (常時マウント・コマンド切替のみ) ========
    scanner_cmd = "start" if phase == "scanning" else "hide"
    scanned_value = render_scanner(command=scanner_cmd)

    # ★ scanning フェーズのときだけ、新しいスキャン値を受け付ける
    if scanned_value and phase == "scanning":
        st.session_state.final_code = scanned_value
        st.session_state.scan_phase = "scanned"
        st.rerun()

    # ======== 登録履歴 ========
    history = st.session_state.register_history
    if history:
        latest = history[0]
        icon = "✅" if latest["ok"] else "❌"
        cls = "hist-ok" if latest["ok"] else "hist-err"
        st.markdown(
            f'<div class="hist {cls}">'
            f'<span>{icon}</span><strong>{latest["name"]}</strong>'
            f'<span style="opacity:.5">({latest["code"]})</span>'
            f'<span style="margin-left:auto;font-size:.75rem;opacity:.5">{latest["time"]}</span>'
            f'</div>', unsafe_allow_html=True)
        if len(history) > 1:
            with st.expander(f"履歴 ({len(history)}件)"):
                for h in history[1:]:
                    ic = "✅" if h["ok"] else "❌"
                    cc = "hist-ok" if h["ok"] else "hist-err"
                    st.markdown(
                        f'<div class="hist {cc}"><span>{ic}</span><strong>{h["name"]}</strong>'
                        f'<span style="opacity:.5">({h["code"]})</span>'
                        f'<span style="margin-left:auto;font-size:.75rem;opacity:.5">{h["time"]}</span></div>',
                        unsafe_allow_html=True)

    # ======== idle ========
    if phase == "idle":
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品コードを入力</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("📸 スキャン", type="primary", use_container_width=True):
                st.session_state.scan_phase = "scanning"
                st.session_state.code_source = "scan"
                st.session_state.final_code = ""
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

    # ======== scanning ========
    elif phase == "scanning":
        if st.button("← 戻る", type="secondary"):
            st.session_state.scan_phase = "idle"
            st.session_state.final_code = ""
            st.rerun()

    # ======== manual_input ========
    elif phase == "manual_input":
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品コードを入力</div>',
                    unsafe_allow_html=True)
        mc = st.text_input("商品コード", placeholder="例: 4901234567890",
                           label_visibility="collapsed")
        ca, cb = st.columns(2)
        with ca:
            if st.button("決定", type="primary", disabled=not mc):
                st.session_state.final_code = mc.strip()
                st.session_state.scan_phase = "scanned"
                st.rerun()
        with cb:
            if st.button("← 戻る", type="secondary"):
                st.session_state.scan_phase = "idle"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ======== scanned → フォーム ========
    elif phase == "scanned":
        code_input = st.session_state.final_code

        # ★ 今回のセッションで登録済みのコードは「既存」判定から除外
        just_registered = st.session_state.just_registered_codes
        target_prod = find_product_by_code(prods, code_input)
        if target_prod and code_input in just_registered:
            # 直前に自分が登録したばかり → 既存扱いにするか確認
            # (同じコードを意図的に再編集したい場合もあるのでそのまま既存扱い)
            pass
        is_new = target_prod is None

        # コード表示
        st.markdown(
            f'<div class="ui-card" style="padding:.85rem 1.25rem">'
            f'<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<span style="font-size:.8rem;color:var(--text2)">商品コード</span>'
            f'<code style="font-size:1.05rem;font-weight:700;letter-spacing:.5px">{code_input}</code>'
            f'</div></div>', unsafe_allow_html=True)

        if is_new:
            st.markdown('<span class="status-badge status-new">✨ 新規登録</span>',
                        unsafe_allow_html=True)
            dd = {k: d["default"] for k, d in FIELD_DEFS.items()}
        else:
            st.markdown(
                f'<span class="status-badge status-exist">'
                f'🔄 更新: {target_prod.get("productName","")}</span>',
                unsafe_allow_html=True)
            dd = {}
            for k, d in FIELD_DEFS.items():
                val = target_prod.get(d["api"], d["default"])
                if d["type"] == "number":
                    val = safe_float(val, d["default"])
                elif d["type"] == "select":
                    val = api2sel(safe_str(val), d["options"])
                elif d["type"] == "category":
                    cid = safe_str(val)
                    val = next((o for o in cat_opts if o.startswith(cid + ":")), "") if cid else ""
                dd[k] = val

        # フォーム
        st.markdown('<div class="ui-card"><div class="ui-card-title">商品情報</div>',
                    unsafe_allow_html=True)
        fv = {}
        fv["商品名"] = st.text_input("商品名 *", value=dd.get("商品名", ""),
                                      placeholder="例: オーガニックコーヒー豆 200g")
        fv["商品価格"] = st.number_input("価格 *", value=int(dd.get("商品価格", 0)),
                                         step=10, min_value=0)
        cat_def = dd.get("部門ID", "")
        ci = cat_opts.index(cat_def) if cat_def in cat_opts else 0
        fv["部門ID"] = st.selectbox("部門 *", cat_opts, index=ci)

        with st.expander("詳細設定", expanded=False):
            for k, d in FIELD_DEFS.items():
                if d["core"]: continue
                dv = dd.get(k, d["default"])
                if d["type"] == "select":
                    idx = d["options"].index(dv) if dv in d["options"] else 0
                    fv[k] = st.selectbox(k, d["options"], index=idx)
                elif d["type"] == "number":
                    fv[k] = st.number_input(k, value=int(dv), step=1)
                else:
                    fv[k] = st.text_input(k, value=dv)
        st.markdown('</div>', unsafe_allow_html=True)

        # 写真
        st.markdown('<div class="ui-card"><div class="ui-card-title">写真（任意）</div>',
                    unsafe_allow_html=True)
        pt, ut = st.tabs(["📷 撮影", "📁 ファイル選択"])
        with pt:
            camera_img = st.camera_input("撮影", label_visibility="collapsed")
        with ut:
            upload_img = st.file_uploader("選択", type=["jpg","jpeg","png"],
                                          label_visibility="collapsed")
        img_file = camera_img or upload_img
        st.markdown('</div>', unsafe_allow_html=True)

        # ボタン
        cs, cc = st.columns([3, 1])
        with cs:
            submit = st.button(
                "🚀 登録して次へ" if is_new else "🔄 更新して次へ",
                type="primary", use_container_width=True)
        with cc:
            if st.button("取消", type="secondary", use_container_width=True):
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""
                st.rerun()

        if submit:
            if not fv["商品名"]:
                st.error("商品名は必須です。")
                st.stop()
            if not fv["部門ID"]:
                st.error("部門を選択してください。")
                st.stop()

            payload = create_payload(fv, code_input)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            ok, detail = False, ""

            with st.spinner("送信中..."):
                if is_new:
                    r = requests.post(f"{get_api_base()}/products",
                                      headers=headers, json=payload)
                    if r.status_code in (200, 201):
                        pid = r.json().get("productId")
                        if img_file:
                            upload_and_link_image(token, pid, img_file)
                        ok = True
                    else:
                        detail = r.text[:80]
                else:
                    pid = target_prod.get("productId")
                    r = requests.patch(f"{get_api_base()}/products/{pid}",
                                       headers=headers, json=payload)
                    if r.status_code in (200, 204):
                        if img_file:
                            upload_and_link_image(token, pid, img_file)
                        ok = True
                    else:
                        detail = r.text[:80]

            # ★ 登録済みコードを記録 + キャッシュクリア
            st.session_state.just_registered_codes.add(code_input)
            st.cache_data.clear()
            _add_history(fv["商品名"], code_input, ok)

            # ★ uid をリセットして、次の rerun で古いスキャンを拾わないようにする
            st.session_state.pop("_last_scan_uid", None)

            # 次のフローへ
            src = st.session_state.code_source
            if src == "auto":
                st.session_state.final_code = generate_auto_code()
                st.session_state.scan_phase = "scanned"
            elif src == "scan":
                st.session_state.scan_phase = "scanning"
                st.session_state.final_code = ""
            else:
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""
            st.rerun()


# ============================================================
# ページ 2: 一括管理
# ============================================================
def page_spreadsheet():
    inject_css()
    token = get_token()
    if not token: st.error("認証エラー"); st.stop()
    st.markdown("### 商品一括管理")
    with st.expander("表示列の設定"):
        optional = [k for k,d in FIELD_DEFS.items() if not d["core"]]
        cur = st.session_state.get("visible_fields",[])
        sel = st.multiselect("追加する項目", options=optional,
                             default=[c for c in cur if c in optional], label_visibility="collapsed")
        if sel != cur: st.session_state["visible_fields"]=sel; st.rerun()
    visible = get_visible(); prods = get_products(token)
    cat_map = {safe_str(c.get("categoryId","")): safe_str(c.get("categoryName","")) for c in get_categories(token)}
    rows = []
    for p in prods:
        row = {"productId": safe_str(p.get("productId","")), "商品コード": safe_str(p.get("productCode",""))}
        for k in visible:
            d=FIELD_DEFS[k]; v=p.get(d["api"],d["default"])
            if d["type"]=="select": v=api2sel(safe_str(v),d.get("options",[]))
            elif d["type"]=="category":
                cid=safe_str(v); cn=cat_map.get(cid,"")
                v=f"{cid}:{cn}" if cid and cn else cid
            elif d["type"]=="number": v=safe_float(v,d["default"])
            else: v=safe_str(v,d["default"])
            row[k]=v
        rows.append(row)
    df=pd.DataFrame(rows); dc=["productId","商品コード"]+visible
    if df.empty: df=pd.DataFrame(columns=dc)
    btn=st.button("💾 変更をすべて保存",type="primary")
    co=_cat_options(token)
    ccfg={"productId":st.column_config.TextColumn("商品ID",disabled=True),
          "商品コード":st.column_config.TextColumn("商品コード",disabled=True)}
    for k in visible:
        d=FIELD_DEFS[k]
        if d["type"]=="category": ccfg[k]=st.column_config.SelectboxColumn(k,options=co)
        elif d["type"]=="select": ccfg[k]=st.column_config.SelectboxColumn(k,options=d.get("options",[]))
        elif d["type"]=="number": ccfg[k]=st.column_config.NumberColumn(k)
        else: ccfg[k]=st.column_config.TextColumn(k,max_chars=d.get("max"))
    edf=st.data_editor(df[dc],column_config=ccfg,num_rows="fixed",use_container_width=True,height=600)
    if btn:
        results=[]
        with st.spinner("同期中..."):
            for idx,nr in edf.iterrows():
                pid=str(nr.get("productId","")).strip()
                if not pid: continue
                orow=df[df['productId']==pid].iloc[0].to_dict(); dp={}
                for k,d in FIELD_DEFS.items():
                    if k not in orow or k not in nr: continue
                    ov,nv=orow[k],nr[k]
                    if d["type"]=="select": ov,nv=sel2api(ov),sel2api(nv)
                    elif d["type"]=="category":
                        ov=safe_str(ov).split(":")[0] if ov and ":" in safe_str(ov) else safe_str(ov)
                        nv=safe_str(nv).split(":")[0] if nv and ":" in safe_str(nv) else safe_str(nv)
                    if safe_str(ov)!=safe_str(nv):
                        if(nv=="" or nv is None or nv==0) and not d.get("send_empty",False): continue
                        dp[d["api"]]=safe_str(nv)
                if dp:
                    r=requests.patch(f"{get_api_base()}/products/{pid}",
                        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=dp)
                    pn=str(nr.get("商品名","不明"))
                    results.append(("ok",pn,"更新完了") if r.status_code in (200,204) else ("err",pn,"更新エラー"))
            st.cache_data.clear()
        for k,n,m in results: sr(k,n,m)


# ============================================================
# ページ 3: 部門マスター
# ============================================================
def page_categories():
    inject_css()
    token=get_token()
    if not token: st.error("認証エラー"); st.stop()
    st.markdown("### 部門マスター")
    cats=get_categories(token)
    cdf=pd.DataFrame([{"部門ID":safe_str(c.get("categoryId","")),"部門名":safe_str(c.get("categoryName","")),"表示順":safe_int(c.get("displaySequence"),0)} for c in cats]) if cats else pd.DataFrame(columns=["部門ID","部門名","表示順"])
    btn=st.button("💾 部門データを保存",type="primary")
    ec=st.data_editor(cdf,use_container_width=True,num_rows="dynamic",height=500,
        column_config={"部門ID":st.column_config.TextColumn("部門ID (自動)",disabled=True),
                       "部門名":st.column_config.TextColumn("部門名",required=True),
                       "表示順":st.column_config.NumberColumn("表示順",default=0)})
    if btn:
        results=[]
        with st.spinner("同期中..."):
            for idx,row in ec.iterrows():
                cid=str(row.get("部門ID","")).strip()
                if cid in ["nan","None","<NA>",""]: cid=None
                cn=str(row.get("部門名","")).strip()
                if not cn or cn in ["nan","None","<NA>"]: continue
                cs_=str(safe_int(row.get("表示順",0)))
                hd={"Authorization":f"Bearer {token}","Content-Type":"application/json"}
                if not cid:
                    r=requests.post(f"{get_api_base()}/categories",headers=hd,json={"categoryName":cn,"displaySequence":cs_})
                    results.append(("ok",cn,"追加完了") if r.status_code in (200,201) else ("err",cn,"追加エラー"))
                else:
                    old=cats[idx] if idx<len(cats) else None
                    if old:
                        ch={}
                        if cn!=safe_str(old.get("categoryName","")): ch["categoryName"]=cn
                        if cs_!=str(safe_int(old.get("displaySequence",0))): ch["displaySequence"]=cs_
                        if ch:
                            r=requests.patch(f"{get_api_base()}/categories/{cid}",headers=hd,json=ch)
                            results.append(("ok",cn,"更新完了") if r.status_code in (200,204) else ("err",cn,"更新エラー"))
            st.cache_data.clear();_refresh_cat_options()
        for k,n,m in results: sr(k,n,m)


# ============================================================
# ページ 4: 設定
# ============================================================
def page_settings():
    inject_css()
    st.markdown("### 設定")
    st.markdown('<div class="ui-card"><div class="ui-card-title">自動採番ルール</div>',unsafe_allow_html=True)
    st.caption("中央には日時が自動挿入されます。")
    pfx=st.text_input("接頭辞",value=st.session_state.auto_rule_prefix)
    sfx=st.text_input("接尾辞",value=st.session_state.auto_rule_suffix)
    st.code(f"{pfx}20260410134221{sfx}",language=None)
    if st.button("保存",type="primary"):
        st.session_state.auto_rule_prefix=pfx; st.session_state.auto_rule_suffix=sfx
        st.success("保存しました。")
    st.markdown('</div>',unsafe_allow_html=True)


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
