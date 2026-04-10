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
    if v is None:
        return d
    try:
        return int(v)
    except (ValueError, TypeError):
        return d

def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (ValueError, TypeError):
        return d

def safe_str(v, d=""):
    if pd.isna(v) or v is None:
        return d
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

# --- 自動採番ルール ---
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
        r = requests.post(
            get_auth_url(),
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "pos.products:read pos.products:write",
            },
        )
        if r.status_code == 200:
            d = r.json()
            t = d.get("access_token")
            st.session_state[ck] = {
                "at": t,
                "ea": time.time() + d.get("expires_in", 3600) - 60,
            }
            return t
    except Exception:
        pass
    return None


# ============================================================
# 🌟 バーコードリーダー (インライン埋め込み版・大幅改善)
# ============================================================
def inline_barcode_scanner(key="scanner"):
    """
    改善点:
    - 読取成功時にバイブレーション + 効果音 + 画面フラッシュで即座にフィードバック
    - カメラ起動中のローディングインジケーター表示
    - ピンチズームによる拡大が効かないよう viewport 固定
    - 読取領域をバーコード横長に最適化 (280x100)
    - 連続スキャン: 読取後2秒で自動的にカメラ再起動するモード対応
    - カメラ権限エラー時のフォールバックメッセージ
    """
    html_code = """
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <style>
            *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
            body, html {
                background: transparent;
                overflow: hidden;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }

            .scanner-wrap {
                position: relative;
                width: 100%;
                border-radius: 16px;
                overflow: hidden;
                background: #0f172a;
            }

            /* ローディングオーバーレイ */
            .loading-overlay {
                position: absolute; inset: 0;
                display: flex; flex-direction: column;
                align-items: center; justify-content: center;
                background: #0f172a; z-index: 10;
                color: #94a3b8; font-size: 14px; gap: 12px;
                transition: opacity 0.4s ease;
            }
            .loading-overlay.hidden { opacity: 0; pointer-events: none; }

            .spinner {
                width: 36px; height: 36px;
                border: 3px solid #334155;
                border-top-color: #3b82f6;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
            }
            @keyframes spin { to { transform: rotate(360deg); } }

            /* 読取成功フラッシュ */
            .flash-overlay {
                position: absolute; inset: 0;
                background: rgba(34, 197, 94, 0.35);
                z-index: 20; opacity: 0;
                pointer-events: none;
                transition: opacity 0.15s ease;
            }
            .flash-overlay.active { opacity: 1; }

            /* 成功バナー */
            .success-banner {
                position: absolute; inset: 0;
                display: flex; flex-direction: column;
                align-items: center; justify-content: center;
                background: rgba(15, 23, 42, 0.92);
                z-index: 30; color: white;
                opacity: 0; pointer-events: none;
                transition: opacity 0.3s ease;
            }
            .success-banner.show { opacity: 1; pointer-events: auto; }
            .success-banner .check-icon { font-size: 48px; margin-bottom: 8px; }
            .success-banner .scanned-code {
                font-size: 18px; font-weight: 700;
                background: rgba(34, 197, 94, 0.2);
                border: 1px solid rgba(34, 197, 94, 0.4);
                padding: 6px 16px; border-radius: 8px;
                margin-top: 4px; letter-spacing: 1px;
            }

            /* エラーメッセージ */
            .error-msg {
                position: absolute; inset: 0;
                display: flex; flex-direction: column;
                align-items: center; justify-content: center;
                background: #0f172a; z-index: 10;
                color: #f87171; font-size: 14px;
                text-align: center; padding: 20px;
                display: none;
            }
            .error-msg.show { display: flex; }

            #reader { width: 100%; min-height: 280px; }
            #reader video { object-fit: cover; border-radius: 16px; }

            /* html5-qrcode の内蔵UIを非表示 */
            #reader__scan_region > img { display: none !important; }
            #reader__dashboard { display: none !important; }
        </style>
    </head>
    <body>
        <div class="scanner-wrap">
            <div class="loading-overlay" id="loadingOverlay">
                <div class="spinner"></div>
                <span>カメラを起動中...</span>
            </div>
            <div class="flash-overlay" id="flashOverlay"></div>
            <div class="success-banner" id="successBanner">
                <div class="check-icon">✅</div>
                <div style="font-size:13px; color:#94a3b8;">読み取り完了</div>
                <div class="scanned-code" id="scannedCodeDisplay"></div>
            </div>
            <div class="error-msg" id="errorMsg">
                <div style="font-size:32px; margin-bottom:8px;">📷</div>
                <div style="font-weight:700; margin-bottom:4px;">カメラにアクセスできません</div>
                <div style="color:#94a3b8; font-size:12px;">ブラウザの設定でカメラを許可してください</div>
            </div>
            <div id="reader"></div>
        </div>

        <script>
            /* --- Streamlit 通信ヘルパー --- */
            function sendToStreamlit(value) {
                window.parent.postMessage({
                    isStreamlitMessage: true,
                    type: "streamlit:setComponentValue",
                    value: value
                }, "*");
            }
            function setHeight(h) {
                window.parent.postMessage({
                    isStreamlitMessage: true,
                    type: "streamlit:setFrameHeight",
                    height: h
                }, "*");
            }

            /* --- 効果音 (Web Audio API) --- */
            function playBeep() {
                try {
                    const ctx = new (window.AudioContext || window.webkitAudioContext)();
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.type = "sine";
                    osc.frequency.value = 1200;
                    gain.gain.value = 0.3;
                    osc.start();
                    osc.stop(ctx.currentTime + 0.12);
                } catch(e) {}
            }

            /* --- バイブレーション --- */
            function vibrate() {
                try { navigator.vibrate && navigator.vibrate([80, 40, 80]); } catch(e) {}
            }

            /* --- フラッシュ演出 --- */
            function flashScreen() {
                const el = document.getElementById("flashOverlay");
                el.classList.add("active");
                setTimeout(() => el.classList.remove("active"), 300);
            }

            /* --- 成功バナー --- */
            function showSuccess(code) {
                document.getElementById("scannedCodeDisplay").textContent = code;
                document.getElementById("successBanner").classList.add("show");
            }

            const FRAME_HEIGHT = 300;
            let html5QrCode = null;
            let started = false;

            window.onload = function() {
                window.parent.postMessage({
                    isStreamlitMessage: true,
                    type: "streamlit:componentReady",
                    apiVersion: 1
                }, "*");
                setHeight(FRAME_HEIGHT);
            };

            function startScanner() {
                if (html5QrCode) {
                    try { html5QrCode.stop(); } catch(e) {}
                }
                document.getElementById("loadingOverlay").classList.remove("hidden");
                document.getElementById("successBanner").classList.remove("show");
                document.getElementById("errorMsg").classList.remove("show");

                html5QrCode = new Html5Qrcode("reader");
                const config = {
                    fps: 15,
                    qrbox: { width: 280, height: 100 },
                    aspectRatio: 1.3333,
                    formatsToSupport: [
                        Html5QrcodeSupportedFormats.EAN_13,
                        Html5QrcodeSupportedFormats.EAN_8,
                        Html5QrcodeSupportedFormats.UPC_A,
                        Html5QrcodeSupportedFormats.UPC_E,
                        Html5QrcodeSupportedFormats.CODE_128,
                        Html5QrcodeSupportedFormats.CODE_39,
                        Html5QrcodeSupportedFormats.QR_CODE
                    ]
                };

                html5QrCode.start(
                    { facingMode: "environment" },
                    config,
                    (decodedText) => {
                        /* 成功時の演出 */
                        playBeep();
                        vibrate();
                        flashScreen();

                        html5QrCode.stop().then(() => {
                            showSuccess(decodedText);
                            /* 少し見せてからStreamlitに送る */
                            setTimeout(() => {
                                sendToStreamlit(decodedText);
                            }, 600);
                        }).catch(() => {
                            sendToStreamlit(decodedText);
                        });
                    },
                    (errorMessage) => { /* 読取中のノイズは無視 */ }
                ).then(() => {
                    /* カメラ起動成功 → ローディングを消す */
                    document.getElementById("loadingOverlay").classList.add("hidden");
                }).catch((err) => {
                    /* カメラ起動失敗 */
                    document.getElementById("loadingOverlay").classList.add("hidden");
                    document.getElementById("errorMsg").classList.add("show");
                });
            }

            window.addEventListener("message", function(event) {
                if (event.data.type === "streamlit:render" && !started) {
                    started = true;
                    startScanner();
                }
            });
        </script>
    </body>
    </html>
    """
    component_dir = os.path.abspath("barcode_component_dir")
    os.makedirs(component_dir, exist_ok=True)
    with open(os.path.join(component_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_code)

    _scanner = components.declare_component("custom_barcode_scanner", path=component_dir)
    return _scanner(key=key, default=None)


# ============================================================
# フィールド定義
# ============================================================
FIELD_DEFS = OrderedDict([
    ("商品名",       {"api": "productName",          "type": "text",     "default": "",          "required": True,  "core": True,  "max": 85,   "send_empty": True,  "post": True}),
    ("商品価格",     {"api": "price",                "type": "number",   "default": 0,           "required": True,  "core": True,  "max": None, "send_empty": True,  "post": True}),
    ("部門ID",       {"api": "categoryId",           "type": "category", "default": "",          "required": True,  "core": True,  "max": None, "send_empty": False, "post": True}),
    ("原価",         {"api": "cost",                 "type": "number",   "default": 0,           "required": False, "core": False, "max": None, "send_empty": False, "post": True}),
    ("税区分",       {"api": "taxDivision",          "type": "select",   "default": "0:税込",    "required": False, "core": False, "max": None, "send_empty": False, "post": True,
        "options": ["0:税込", "1:税抜", "2:非課税"]}),
    ("在庫管理区分", {"api": "stockControlDivision", "type": "select",   "default": "0:対象",    "required": False, "core": False, "max": None, "send_empty": False, "post": True,
        "options": ["0:対象", "1:対象外"]}),
    ("売上区分",     {"api": "salesDivision",        "type": "select",   "default": "0:売上対象","required": False, "core": False, "max": None, "send_empty": False, "post": True,
        "options": ["0:売上対象", "1:売上対象外"]}),
    ("説明",         {"api": "description",          "type": "text",     "default": "",          "required": False, "core": False, "max": 1000, "send_empty": False, "post": True}),
    ("カラー",       {"api": "color",                "type": "text",     "default": "",          "required": False, "core": False, "max": 85,   "send_empty": False, "post": True}),
    ("サイズ",       {"api": "size",                 "type": "text",     "default": "",          "required": False, "core": False, "max": 85,   "send_empty": False, "post": True}),
    ("端末表示",     {"api": "displayFlag",          "type": "select",   "default": "1:表示する","required": False, "core": False, "max": None, "send_empty": False, "post": True,
        "options": ["0:表示しない", "1:表示する"]}),
])

def sel2api(v):
    if not v or ":" not in safe_str(v):
        return safe_str(v)
    return safe_str(v).split(":")[0]

def api2sel(val, opts):
    s = safe_str(val)
    for o in opts:
        if o.split(":")[0] == s:
            return o
    return opts[0] if opts else s

def get_visible():
    extra = st.session_state.get("visible_fields", [])
    core = [k for k, d in FIELD_DEFS.items() if d["core"]]
    return core + [k for k in extra if k in FIELD_DEFS and not FIELD_DEFS[k]["core"]]

def _cat_options(token):
    if "cat_options_cache" in st.session_state:
        return st.session_state["cat_options_cache"]
    cats = get_categories(token)
    opts = [""] + [
        f"{safe_str(c.get('categoryId',''))}:{safe_str(c.get('categoryName',''))}"
        for c in cats
    ]
    st.session_state["cat_options_cache"] = opts
    return opts

def _refresh_cat_options():
    st.session_state.pop("cat_options_cache", None)


# ============================================================
# CSS (フラット・モバイルネイティブ風)
# ============================================================
def inject_css():
    st.markdown("""
    <style>
    :root {
        --brand: #2563eb;
        --brand-light: #dbeafe;
        --success: #16a34a;
        --danger: #dc2626;
        --surface: #ffffff;
        --bg: #f8fafc;
        --text-primary: #0f172a;
        --text-secondary: #64748b;
        --border: #e2e8f0;
        --radius: 14px;
    }

    * { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }

    /* 16px 未満だと iOS Safari がフォームをズームするので厳守 */
    input, select, textarea, .stSelectbox div,
    [data-baseweb="select"] span,
    [data-baseweb="input"] input { font-size: 16px !important; }

    .stApp { background: var(--bg); }
    .block-container {
        padding: 0.75rem 0.75rem 6rem 0.75rem !important;
        max-width: 640px !important;
        margin: 0 auto;
    }

    /* ---- カード ---- */
    .ui-card {
        background: var(--surface);
        padding: 1.25rem;
        border-radius: var(--radius);
        box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03);
        margin-bottom: 1rem;
        border: 1px solid var(--border);
    }
    .ui-card-title {
        font-size: 0.85rem;
        font-weight: 700;
        color: var(--text-secondary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.75rem;
    }

    /* ---- ステータスバッジ ---- */
    .status-badge {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 6px 14px; border-radius: 999px;
        font-size: 0.85rem; font-weight: 600;
    }
    .status-new   { background: var(--brand-light); color: var(--brand); }
    .status-exist { background: #fef3c7; color: #92400e; }

    /* ---- ボタン ---- */
    .stButton > button {
        border-radius: 12px !important;
        font-weight: 700 !important;
        padding: 0.75rem 1rem !important;
        font-size: 1rem !important;
        width: 100%;
        transition: transform 0.1s ease, box-shadow 0.15s ease;
    }
    .stButton > button:active {
        transform: scale(0.97);
    }
    .stButton > button[kind="primary"] {
        background: var(--brand) !important;
        color: white !important;
        border: none !important;
        box-shadow: 0 2px 8px rgba(37,99,235,0.25);
    }
    .stButton > button[kind="secondary"] {
        background: var(--surface) !important;
        color: var(--text-primary) !important;
        border: 1.5px solid var(--border) !important;
    }

    /* ---- 結果行 ---- */
    .r-row {
        padding: 0.85rem 1rem;
        border-radius: 12px;
        margin: 0.35rem 0;
        font-size: 0.92rem;
        font-weight: 600;
        display: flex; align-items: center; gap: 8px;
    }
    .r-ok  { background: #f0fdf4; color: #166534; border-left: 5px solid #22c55e; }
    .r-err { background: #fef2f2; color: #991b1b; border-left: 5px solid #ef4444; }

    /* ---- Streamlit 雑音除去 ---- */
    div[data-testid="stVerticalBlock"] > div { padding-bottom: 0.15rem !important; }
    header[data-testid="stHeader"] { background: var(--bg); }

    /* ---- フローティング登録ボタン (モバイル) ---- */
    .fab-wrap {
        position: fixed; bottom: 0; left: 0; right: 0;
        padding: 0.75rem 1rem; padding-bottom: max(0.75rem, env(safe-area-inset-bottom));
        background: linear-gradient(transparent, var(--bg) 30%);
        z-index: 100;
        display: flex; justify-content: center;
    }
    .fab-wrap .inner { max-width: 640px; width: 100%; }
    </style>
    """, unsafe_allow_html=True)


def sr(kind, name, msg):
    cls = {"ok": "r-ok", "err": "r-err"}.get(kind, "r-ok")
    icon = {"ok": "✅", "err": "❌"}.get(kind, "●")
    st.markdown(
        f'<div class="r-row {cls}">'
        f'<span>{icon}</span><strong>{name}</strong>'
        f'<span style="opacity:.4;margin:0 4px;">|</span>{msg}</div>',
        unsafe_allow_html=True,
    )


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
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
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

        signed_url = blob.generate_signed_url(
            version="v4", expiration=datetime.timedelta(minutes=15), method="GET"
        )
        try:
            safe_url = requests.utils.quote(signed_url)
            short_res = requests.get(
                f"https://tinyurl.com/api-create.php?url={safe_url}", timeout=5
            )
            final_url = short_res.text if short_res.status_code == 200 else signed_url
        except Exception:
            final_url = signed_url

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"imageUrl": final_url}

        url_img = f"{get_api_base()}/products/{product_id}/image"
        ok_img = False
        for _ in range(3):
            try:
                r1 = requests.put(url_img, headers=headers, json=payload, timeout=15)
                if r1.status_code in (200, 201, 204):
                    ok_img = True
                    break
                if r1.status_code == 404:
                    time.sleep(1)
                    continue
            except Exception:
                time.sleep(1)
                continue

        url_icon = f"{get_api_base()}/products/{product_id}/icon_image"
        ok_icon = False
        for _ in range(3):
            try:
                r2 = requests.put(url_icon, headers=headers, json=payload, timeout=15)
                if r2.status_code in (200, 201, 204):
                    ok_icon = True
                    break
                if r2.status_code == 404:
                    time.sleep(1)
                    continue
            except Exception:
                time.sleep(1)
                continue

        if ok_img and ok_icon:
            return True, "画像・アイコン登録完了"
        return False, "画像の一部連携に失敗"
    except Exception as e:
        return False, f"システムエラー: {str(e)}"


# ============================================================
# API: データ取得・ペイロード
# ============================================================
@st.cache_data(ttl=120)
def get_categories(token):
    cats, p = [], 1
    while True:
        r = requests.get(
            f"{get_api_base()}/categories",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1000, "page": p},
        )
        if r.status_code != 200:
            break
        d = r.json()
        if not isinstance(d, list):
            break
        cats.extend(d)
        if len(d) < 1000:
            break
        p += 1
    return cats

@st.cache_data(ttl=60)
def get_products(token):
    prods, p = [], 1
    while True:
        r = requests.get(
            f"{get_api_base()}/products",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1000, "page": p},
        )
        if r.status_code != 200:
            break
        d = r.json()
        if not isinstance(d, list):
            break
        prods.extend(d)
        if len(d) < 1000:
            break
        p += 1
    return prods

def find_product_by_code(prods, code):
    if not code:
        return None
    for p in prods:
        if p.get("productCode") == code:
            return p
    return None

def create_payload(form_data, code):
    payload = {"productCode": code}
    for k, d in FIELD_DEFS.items():
        if k not in form_data:
            continue
        v = form_data[k]
        if d["type"] == "select":
            v = sel2api(v)
        elif d["type"] == "category":
            v = safe_str(v).split(":")[0] if v and ":" in safe_str(v) else safe_str(v)
        if (v == "" or v is None or v == 0) and not d.get("send_empty", False):
            continue
        payload[d["api"]] = safe_str(v)
    return payload


# ============================================================
# ページ 1: 📱 スキャン＆登録
# ============================================================
def _init_scanner_state():
    """セッションステートの初期化を1箇所にまとめる"""
    defaults = {
        "scan_phase": "idle",      # idle → scanning → scanned → submitting
        "final_code": "",
        "code_source": None,       # "scan" | "auto" | "manual"
        "last_registered": None,   # 直前の登録結果 {"name":..., "ok":bool}
        "scanner_key_counter": 0,  # カメラ再起動用カウンタ
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def page_scanner_form():
    inject_css()
    _init_scanner_state()
    token = get_token()
    if not token:
        st.error("スマレジ認証エラー。設定を確認してください。")
        st.stop()

    prods = get_products(token)
    cat_opts = _cat_options(token)

    # ---------- 直前の登録結果を表示 ----------
    last = st.session_state.last_registered
    if last:
        if last["ok"]:
            sr("ok", last["name"], "登録完了")
        else:
            sr("err", last["name"], last.get("detail", "エラー"))
        st.session_state.last_registered = None

    # ============================================
    # Phase: idle — 入力方法を選択
    # ============================================
    if st.session_state.scan_phase == "idle":
        st.markdown(
            '<div class="ui-card">'
            '<div class="ui-card-title">商品コードを入力</div>',
            unsafe_allow_html=True,
        )

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

        st.markdown("</div>", unsafe_allow_html=True)

    # ============================================
    # Phase: scanning — カメラ表示
    # ============================================
    elif st.session_state.scan_phase == "scanning":
        st.markdown(
            '<div class="ui-card">'
            '<div class="ui-card-title">バーコードを枠に合わせてください</div>',
            unsafe_allow_html=True,
        )

        # ユニークキーでカメラを確実に再マウント
        scanner_key = f"barcode_{st.session_state.scanner_key_counter}"
        scanned = inline_barcode_scanner(key=scanner_key)

        if scanned:
            st.session_state.final_code = scanned
            st.session_state.scan_phase = "scanned"
            st.session_state.scanner_key_counter += 1
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

        if st.button("← 戻る", type="secondary"):
            st.session_state.scan_phase = "idle"
            st.session_state.scanner_key_counter += 1
            st.rerun()

    # ============================================
    # Phase: manual_input — 手動入力
    # ============================================
    elif st.session_state.scan_phase == "manual_input":
        st.markdown(
            '<div class="ui-card">'
            '<div class="ui-card-title">商品コードを入力</div>',
            unsafe_allow_html=True,
        )
        manual_code = st.text_input(
            "商品コード",
            placeholder="例: 4901234567890",
            label_visibility="collapsed",
        )
        col_ok, col_back = st.columns(2)
        with col_ok:
            if st.button("決定", type="primary", disabled=not manual_code):
                st.session_state.final_code = manual_code.strip()
                st.session_state.scan_phase = "scanned"
                st.rerun()
        with col_back:
            if st.button("← 戻る", type="secondary"):
                st.session_state.scan_phase = "idle"
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ============================================
    # Phase: scanned — コード確定 → フォーム表示
    # ============================================
    elif st.session_state.scan_phase == "scanned":
        code_input = st.session_state.final_code
        target_prod = find_product_by_code(prods, code_input)
        is_new = target_prod is None

        # --- コード表示 + 変更リンク ---
        st.markdown(
            '<div class="ui-card" style="padding:0.85rem 1.25rem;">'
            f'<div style="display:flex; align-items:center; justify-content:space-between;">'
            f'<span style="font-size:0.8rem;color:var(--text-secondary);">商品コード</span>'
            f'<code style="font-size:1.05rem; font-weight:700; letter-spacing:0.5px;">{code_input}</code>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        if is_new:
            st.markdown(
                '<span class="status-badge status-new">✨ 新規登録</span>',
                unsafe_allow_html=True,
            )
            default_data = {k: d["default"] for k, d in FIELD_DEFS.items()}
        else:
            st.markdown(
                f'<span class="status-badge status-exist">🔄 更新: {target_prod.get("productName", "")}</span>',
                unsafe_allow_html=True,
            )
            default_data = {}
            for k, d in FIELD_DEFS.items():
                val = target_prod.get(d["api"], d["default"])
                if d["type"] == "number":
                    val = safe_float(val, d["default"])
                elif d["type"] == "select":
                    val = api2sel(safe_str(val), d["options"])
                elif d["type"] == "category":
                    cid = safe_str(val)
                    val = next((o for o in cat_opts if o.startswith(cid + ":")), "") if cid else ""
                default_data[k] = val

        # --- 商品情報フォーム ---
        st.markdown(
            '<div class="ui-card">'
            '<div class="ui-card-title">商品情報</div>',
            unsafe_allow_html=True,
        )

        form_vals = {}
        form_vals["商品名"] = st.text_input(
            "商品名 *", value=default_data.get("商品名", ""),
            placeholder="例: オーガニックコーヒー豆 200g",
        )
        form_vals["商品価格"] = st.number_input(
            "価格 *", value=int(default_data.get("商品価格", 0)), step=10, min_value=0,
        )

        cat_default = default_data.get("部門ID", "")
        cat_index = cat_opts.index(cat_default) if cat_default in cat_opts else 0
        form_vals["部門ID"] = st.selectbox("部門 *", cat_opts, index=cat_index)

        with st.expander("詳細設定（原価・税区分など）", expanded=False):
            for k, d in FIELD_DEFS.items():
                if d["core"]:
                    continue
                dv = default_data.get(k, d["default"])
                if d["type"] == "select":
                    idx = d["options"].index(dv) if dv in d["options"] else 0
                    form_vals[k] = st.selectbox(k, d["options"], index=idx)
                elif d["type"] == "number":
                    form_vals[k] = st.number_input(k, value=int(dv), step=1)
                else:
                    form_vals[k] = st.text_input(k, value=dv)

        st.markdown("</div>", unsafe_allow_html=True)

        # --- 写真 ---
        st.markdown(
            '<div class="ui-card">'
            '<div class="ui-card-title">写真（任意）</div>',
            unsafe_allow_html=True,
        )

        photo_tab, upload_tab = st.tabs(["📷 撮影", "📁 ファイル選択"])
        with photo_tab:
            camera_img = st.camera_input("商品を撮影", label_visibility="collapsed")
        with upload_tab:
            upload_img = st.file_uploader(
                "画像ファイルを選択",
                type=["jpg", "jpeg", "png"],
                label_visibility="collapsed",
            )
        img_file = camera_img or upload_img
        st.markdown("</div>", unsafe_allow_html=True)

        # --- 登録ボタン群 ---
        col_submit, col_cancel = st.columns([3, 1])
        with col_submit:
            submit_btn = st.button(
                "🚀 登録して次へ" if is_new else "🔄 更新して次へ",
                type="primary",
                use_container_width=True,
            )
        with col_cancel:
            if st.button("取消", type="secondary", use_container_width=True):
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""
                st.rerun()

        # --- 送信処理 ---
        if submit_btn:
            if not form_vals["商品名"]:
                st.error("商品名は必須です。")
                st.stop()
            if not form_vals["部門ID"]:
                st.error("部門を選択してください。")
                st.stop()

            payload = create_payload(form_vals, code_input)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            ok = False
            detail = ""
            with st.spinner("スマレジに送信中..."):
                if is_new:
                    r = requests.post(
                        f"{get_api_base()}/products",
                        headers=headers, json=payload,
                    )
                    if r.status_code in (200, 201):
                        pid = r.json().get("productId")
                        if img_file:
                            upload_and_link_image(token, pid, img_file)
                        ok = True
                    else:
                        detail = r.text[:80]
                else:
                    pid = target_prod.get("productId")
                    r = requests.patch(
                        f"{get_api_base()}/products/{pid}",
                        headers=headers, json=payload,
                    )
                    if r.status_code in (200, 204):
                        if img_file:
                            upload_and_link_image(token, pid, img_file)
                        ok = True
                    else:
                        detail = r.text[:80]

            st.cache_data.clear()

            st.session_state.last_registered = {
                "name": form_vals["商品名"],
                "ok": ok,
                "detail": detail,
            }

            # 次のフローへ自動遷移
            source = st.session_state.code_source
            if source == "auto":
                st.session_state.final_code = generate_auto_code()
                st.session_state.scan_phase = "scanned"
            elif source == "scan":
                st.session_state.scan_phase = "scanning"
                st.session_state.scanner_key_counter += 1
                st.session_state.final_code = ""
            else:
                st.session_state.scan_phase = "idle"
                st.session_state.final_code = ""

            st.rerun()


# ============================================================
# ページ 2: 💻 商品一括管理
# ============================================================
def page_spreadsheet():
    inject_css()
    token = get_token()
    if not token:
        st.error("認証エラー")
        st.stop()

    st.markdown("### 商品一括管理")

    with st.expander("表示列の設定"):
        optional = [k for k, d in FIELD_DEFS.items() if not d["core"]]
        cur_vis = st.session_state.get("visible_fields", [])
        sel_vis = st.multiselect(
            "表に追加する項目",
            options=optional,
            default=[c for c in cur_vis if c in optional],
            label_visibility="collapsed",
        )
        if sel_vis != cur_vis:
            st.session_state["visible_fields"] = sel_vis
            st.rerun()

    visible = get_visible()
    prods = get_products(token)
    cat_map = {
        safe_str(c.get("categoryId", "")): safe_str(c.get("categoryName", ""))
        for c in get_categories(token)
    }

    rows = []
    for p in prods:
        row = {
            "productId": safe_str(p.get("productId", "")),
            "商品コード": safe_str(p.get("productCode", "")),
        }
        for k in visible:
            d = FIELD_DEFS[k]
            v = p.get(d["api"], d["default"])
            if d["type"] == "select":
                v = api2sel(safe_str(v), d.get("options", []))
            elif d["type"] == "category":
                cid = safe_str(v)
                cn = cat_map.get(cid, "")
                v = f"{cid}:{cn}" if cid and cn else cid
            elif d["type"] == "number":
                v = safe_float(v, d["default"])
            else:
                v = safe_str(v, d["default"])
            row[k] = v
        rows.append(row)

    df = pd.DataFrame(rows)
    display_cols = ["productId", "商品コード"] + visible
    if df.empty:
        df = pd.DataFrame(columns=display_cols)

    btn_save = st.button("💾 変更をすべて保存", type="primary")

    cat_opts = _cat_options(token)
    ccfg = {
        "productId": st.column_config.TextColumn("商品ID", disabled=True),
        "商品コード": st.column_config.TextColumn("商品コード", disabled=True),
    }
    for k in visible:
        d = FIELD_DEFS[k]
        if d["type"] == "category":
            ccfg[k] = st.column_config.SelectboxColumn(k, options=cat_opts)
        elif d["type"] == "select":
            ccfg[k] = st.column_config.SelectboxColumn(k, options=d.get("options", []))
        elif d["type"] == "number":
            ccfg[k] = st.column_config.NumberColumn(k)
        else:
            ccfg[k] = st.column_config.TextColumn(k, max_chars=d.get("max"))

    edited_df = st.data_editor(
        df[display_cols],
        column_config=ccfg,
        num_rows="fixed",
        use_container_width=True,
        height=600,
    )

    if btn_save:
        results = []
        with st.spinner("データを同期中..."):
            for idx, nr in edited_df.iterrows():
                pid = str(nr.get("productId", "")).strip()
                if not pid:
                    continue
                orow = df[df["productId"] == pid].iloc[0].to_dict()
                dp = {}
                for k, d in FIELD_DEFS.items():
                    if k not in orow or k not in nr:
                        continue
                    ov, nv = orow[k], nr[k]
                    if d["type"] == "select":
                        ov, nv = sel2api(ov), sel2api(nv)
                    elif d["type"] == "category":
                        ov = (
                            safe_str(ov).split(":")[0]
                            if ov and ":" in safe_str(ov)
                            else safe_str(ov)
                        )
                        nv = (
                            safe_str(nv).split(":")[0]
                            if nv and ":" in safe_str(nv)
                            else safe_str(nv)
                        )
                    if safe_str(ov) != safe_str(nv):
                        if (nv == "" or nv is None or nv == 0) and not d.get(
                            "send_empty", False
                        ):
                            continue
                        dp[d["api"]] = safe_str(nv)
                if dp:
                    r = requests.patch(
                        f"{get_api_base()}/products/{pid}",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json=dp,
                    )
                    pn = str(nr.get("商品名", "不明"))
                    if r.status_code in (200, 204):
                        results.append(("ok", pn, "更新完了"))
                    else:
                        results.append(("err", pn, "更新エラー"))
            st.cache_data.clear()
        if results:
            for k, n, m in results:
                sr(k, n, m)


# ============================================================
# ページ 3: 📁 部門マスター
# ============================================================
def page_categories():
    inject_css()
    token = get_token()
    if not token:
        st.error("認証エラー")
        st.stop()

    st.markdown("### 部門マスター")
    cats = get_categories(token)
    cat_df = (
        pd.DataFrame(
            [
                {
                    "部門ID": safe_str(c.get("categoryId", "")),
                    "部門名": safe_str(c.get("categoryName", "")),
                    "表示順": safe_int(c.get("displaySequence"), 0),
                }
                for c in cats
            ]
        )
        if cats
        else pd.DataFrame(columns=["部門ID", "部門名", "表示順"])
    )

    btn_save_cat = st.button("💾 部門データを保存", type="primary")
    edited_cats = st.data_editor(
        cat_df,
        use_container_width=True,
        num_rows="dynamic",
        height=500,
        column_config={
            "部門ID": st.column_config.TextColumn("部門ID (自動)", disabled=True),
            "部門名": st.column_config.TextColumn("部門名", required=True),
            "表示順": st.column_config.NumberColumn("表示順", default=0),
        },
    )

    if btn_save_cat:
        results = []
        with st.spinner("部門データを同期中..."):
            for idx, row in edited_cats.iterrows():
                cid = str(row.get("部門ID", "")).strip()
                if cid in ["nan", "None", "<NA>", ""]:
                    cid = None
                cname = str(row.get("部門名", "")).strip()
                if not cname or cname in ["nan", "None", "<NA>"]:
                    continue
                cseq = str(safe_int(row.get("表示順", 0)))

                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }

                if not cid:
                    r = requests.post(
                        f"{get_api_base()}/categories",
                        headers=headers,
                        json={"categoryName": cname, "displaySequence": cseq},
                    )
                    if r.status_code in (200, 201):
                        results.append(("ok", cname, "新規追加完了"))
                    else:
                        results.append(("err", cname, "追加エラー"))
                else:
                    old = cats[idx] if idx < len(cats) else None
                    if old:
                        ch = {}
                        if cname != safe_str(old.get("categoryName", "")):
                            ch["categoryName"] = cname
                        if cseq != str(safe_int(old.get("displaySequence", 0))):
                            ch["displaySequence"] = cseq
                        if ch:
                            r = requests.patch(
                                f"{get_api_base()}/categories/{cid}",
                                headers=headers,
                                json=ch,
                            )
                            if r.status_code in (200, 204):
                                results.append(("ok", cname, "更新完了"))
                            else:
                                results.append(("err", cname, "更新エラー"))
            st.cache_data.clear()
            _refresh_cat_options()
        if results:
            for k, n, m in results:
                sr(k, n, m)


# ============================================================
# ページ 4: ⚙️ 設定
# ============================================================
def page_settings():
    inject_css()
    st.markdown("### 設定")

    st.markdown(
        '<div class="ui-card">'
        '<div class="ui-card-title">自動採番ルール</div>',
        unsafe_allow_html=True,
    )
    st.caption("自動採番ボタンで生成されるコードの形式を設定します。中央には日時が自動挿入されます。")

    pfx = st.text_input("接頭辞", value=st.session_state.auto_rule_prefix)
    sfx = st.text_input("接尾辞", value=st.session_state.auto_rule_suffix)

    preview = f"{pfx}20260410134221{sfx}"
    st.code(preview, language=None)

    if st.button("保存", type="primary"):
        st.session_state.auto_rule_prefix = pfx
        st.session_state.auto_rule_suffix = sfx
        st.success("保存しました。")
    st.markdown("</div>", unsafe_allow_html=True)


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
