import streamlit as st
import json
import os
import time
import base64
import datetime
from urllib.parse import quote
from io import BytesIO
from PIL import Image

# --- KÜTÜPHANE KONTROLLERİ ---
try:
    from google import genai
    from google.genai import types
except ImportError:
    st.error("Google GenAI eksik. Terminale şunu yaz: pip install google-genai")
    st.stop()

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    st.error("Firebase Admin eksik. Terminale şunu yaz: pip install firebase-admin")
    st.stop()

# ==========================================
# ⚙️ AYARLAR
# ==========================================
FIREBASE_DB_URL = "https://geminiborsa-f9a80-default-rtdb.firebaseio.com/"
LOCAL_KEY_FILE = "api_keys.txt"

BOT_CONFIGS = {
    "xFinans": {"username": "@xFinans_bot", "buttons": [("📊 Derinlik", "derinlik"), ("🔢 Teorik", "teorik"), ("🏢 AKD", "akd"), ("📈 Yükselen/Düşen", "yukselendusen"), ("📜 Teorik Liste", "teorikliste"), ("📡 Sinyal", "sinyal")]},
    "BorsaBilgi": {"username": "@borsabilgibot", "buttons": [("📊 Derinlik", "derinlik"), ("🏢 AKD", "akd"), ("🔄 Takas", "takas"), ("🔢 Teorik", "teorik"), ("📉 Endeks Alan - Satan", "endeks"), ("🏦 Kurum Analizi", "kurumlar"), ("🇺🇸 BOFA Analiz", "bofa"), ("📰 Haberler", "haber")]},
    "BorsaBuzz": {"username": "@BorsaBuzzBot", "buttons": [("📊 Derinlik", "derinlik"), ("🏢 AKD", "akd"), ("🌟 AKD Pro", "akdpro"), ("🔝 AKD 20", "akd20"), ("📏 Kademe", "kademe"), ("🐳 Balina", "balina"), ("📐 Teknik", "teknik")]},
    "b0pt": {"username": "@b0pt_bot", "buttons": [("📊 Derinlik", "derinlik"), ("🏢 AKD", "akd"), ("🔢 Teorik", "teorik"), ("📚 Tüm Veriler", "tumu"), ("🔄 Takas", "takas"), ("📏 Kademe", "kademe"), ("📉 Grafik", "grafik"), ("🏦 Genel AKD", "genelakd"), ("🏢 Kurum Analizi", "kurum"), ("🔢 Teorik Yükselen - Düşen", "teorikyd"), ("📈 Piyasa Yükselen - Düşen", "piyasayd"), ("🇺🇸 Bofa Analizi", "bofa")]}
}

# --- SESSION BAŞLATMA ---
if 'telegram_flow' not in st.session_state: st.session_state['telegram_flow'] = {'step': 'idle', 'symbol': '', 'options': []}
if 'telegram_images' not in st.session_state: st.session_state['telegram_images'] = []
if 'key_index' not in st.session_state: st.session_state['key_index'] = 0
if 'dynamic_key_pool' not in st.session_state: st.session_state['dynamic_key_pool'] = []
if 'selected_bot_key' not in st.session_state: st.session_state['selected_bot_key'] = "xFinans"

# ==========================================
# 🔑 KEY YÖNETİMİ (PC + CLOUD UYUMLU)
# ==========================================
def load_keys():
    keys = []
    # 1. Önce PC'deki dosyaya bak
    if os.path.exists(LOCAL_KEY_FILE):
        with open(LOCAL_KEY_FILE, "r", encoding="utf-8") as f:
            keys = [k.strip() for k in f.read().split('\n') if k.strip()]
    
    # 2. Dosya boşsa veya yoksa Secrets'a bak (Cloud için)
    if not keys and "gemini" in st.secrets and "api_keys" in st.secrets["gemini"]:
        keys = st.secrets["gemini"]["api_keys"]
    
    return keys

def save_keys_to_disk(keys_list):
    clean_keys = [k.strip() for k in keys_list if k.strip()]
    # Sadece PC'de dosyaya yazar
    try:
        with open(LOCAL_KEY_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(clean_keys))
        st.session_state['dynamic_key_pool'] = clean_keys
        st.toast("Keyler api_keys.txt dosyasına kaydedildi!", icon="💾")
    except Exception as e:
        st.error(f"Kaydetme hatası: {e}")

# Uygulama açılışında keyleri yükle
if not st.session_state['dynamic_key_pool']:
    st.session_state['dynamic_key_pool'] = load_keys()

# ==========================================
# 🔥 FIREBASE INIT (HARDCODED - SENİN KEY)
# ==========================================
def init_firebase():
    if len(firebase_admin._apps) > 0: return
    try:
        # GÜVENLİK NOTU: Bu key kodun içinde. GitHub'a atarsan repo PRIVATE olsun.
        key_dict = {
          "type": "service_account",
          "project_id": "geminiborsa-f9a80",
          "private_key_id": "48b5d78f516302263727053dc5a870bd5f920ac4",
          "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCow86o1O7cUyWI\nCUFF23sEG7fEMbUt+Owahc14TfLiucZAbe0MyWtrvYX64tAC8JEFwpnzMdz3Cxq+\n0N3Kv/k6DopGgUo6PIPwwRDOqrvRA37FKvo0iV+QgG/kx9rL5xelaeXZcxTDdjWw\nTO9FbDhsJThGFVnTpkqWYEtUx6j6IIRTtBRSMA85suusKJqUpIWlX3Rjb/xPVbgU\nPlQK6Kd00AYGNFgEqqsFr0JzjR1ttmwlkR6h1l3N34DjcDW4mjgR6qToBzrFQYx5\niuObh9nlODi8lGo2S9c9RJJyJJyNwfsJ+33JEIY0bWtv14Yd5jiwiWsHMYG0Tgjf\nU9HStrU3AgMBAAECggEACAz3Mc9JPxIUVe34bX2v251QNzgWfYVEthXbRw7o3u9K\ndCO0+DQvlKnWLLFfMj9e8QMR1wEc77Ku6Zq1yd3oj2AcMTV/tfwnCyLFS3vnjmv3\n7diZVkzrVc2wCMjj43A4YjgFXUnZHA3qjRN5IpBWt19Qf5S10/E4g6iU+gwK6oue\nAV9CuefdxjPmUWaXXG295NBt+YpYoQ+XD7P2KtRwSX1o+vMSSQg3BpMfvsUKYBAe\nE2XzfXMYGD+OXitlUDI1ydb2GO1dQoK/te0c51LEvm0TpmesiUdlFaag5OW8+arS\nbZ+jrxJqwkP0tWoYkYTSY7nrOWo+kkxsq1+xQ70SeQKBgQDgB+ccOOskDjcq2T84\ngdreAuHZADCvin4rlZrpL5snbwpyRE6sWkmMmqe3S2QIPA7JpMSf4x2k+oypCcsi\nQkQzInBqHNKbbupqmIcJlBfNrfQOqq9p0G8MjwBjqGwi9SaYqTWOjdXvB41dF6jy\niJeM0qK7QGSCcHI+Yi8Yx7r1AwKBgQDA2PnchGGF0VrK8/kt2zvip9dey5Hhd1Ve\nbIo1GkABu/2oQPqBNpuGgt+AvSd84RgbL8/xg9pcBYw4sCYBZilrpH3BgPrZ0D09\nEGnk90dRa40JKtDO3H4FXHyVkLRBMt4j3Py+uXXHrWBRRNGHd+7mj5EC+MRRg92f\nUxtV7TtGvQKBgH1PrlQ4+j4WvYD4N8axy+z3C8FHu/PUsbJLYnUgrdam4976mk8J\nya4eK8X5I6D/hv3/bgRJE6Hei6NZ2Qf2rRM1JlAUgzFyHyk03APdlFr1/Fff3XKA\npj0OGBemc6YyHj6yF0T/zTSAsu/pdhUDllGs2F2JLS9RGnYOkW14+vhlAoGAG7jV\nKkMJdeAjihtKTbI/SJTSG/ltjhjGd91ofLu6ScWJcD9vA1YjQ1Ha6TnHzGbbPUVB\nQjmvER1nC9sei4LxH101CrUM2nTZ6MZMQrLdWLH6Q0AZZjNCFmk2K5Xyo5C5aDRj\nTNOCP+MHfodDC5NND23B7chvCDzJhha/TjndFI0CgYEAspHPEXIs6pA6T4idbI4f\n9WISoqmvRlzs/Z8FWJbx5bqx2+xRgkgR5muCsXeFvF4Tna87iR1cLixbIpQuMqO4\nHOkJUafdWoU7fhAOghLffjfoBLvjSW487T9LiUPUwWq/CXhN/C/KZhw4IXmaTg18\nCxzdYHfdIjV7q7QP0hEhF9w=\n-----END PRIVATE KEY-----\n",
          "client_email": "firebase-adminsdk-fbsvc@geminiborsa-f9a80.iam.gserviceaccount.com",
          "client_id": "111579891187704340858",
          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
          "token_uri": "https://oauth2.googleapis.com/token",
          "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
          "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40geminiborsa-f9a80.iam.gserviceaccount.com",
          "universe_domain": "googleapis.com"
        }
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    except Exception as e:
        st.error(f"Firebase Bağlantı Hatası: {e}")
        st.stop()

# ==========================================
# 📡 TELEGRAM FLOW
# ==========================================
def start_telegram_request(symbol, rtype):
    if not firebase_admin._apps: return
    bot_key = st.session_state['selected_bot_key']
    st.session_state['telegram_flow'] = {'step': 'processing', 'symbol': symbol, 'options': []}
    
    db.reference('bridge/response').delete()
    db.reference('bridge/request').set({
        'symbol': symbol.upper() if symbol else "",
        'type': rtype,
        'target_bot': BOT_CONFIGS[bot_key]["username"],
        'status': 'pending',
        'timestamp': time.time()
    })
    st.rerun()

def send_user_selection(selection):
    db.reference('bridge/request').update({'status': 'selection_made', 'selection': selection, 'timestamp': time.time()})
    st.session_state['telegram_flow']['step'] = 'processing'
    st.session_state['telegram_flow']['options'] = []
    st.toast(f"Seçim: {selection}", icon="📨")
    time.sleep(0.5)
    st.rerun()

def send_restart_command():
    if not firebase_admin._apps: return
    db.reference('bridge/system_command').set({'command': 'restart', 'timestamp': time.time()})
    st.toast("🔄 Yeniden Başlatma Komutu!", icon="🔄")

def check_firebase_status():
    if not firebase_admin._apps: return
    flow = st.session_state['telegram_flow']
    if flow['step'] == 'processing':
        req = db.reference('bridge/request').get()
        if not req: return
        status = req.get('status')
        
        if status == 'waiting_user_selection':
            res = db.reference('bridge/response').get()
            if res and 'options' in res:
                st.session_state['telegram_flow']['options'] = res['options']
                st.session_state['telegram_flow']['step'] = 'show_buttons'
                st.rerun()
        elif status == 'completed':
            res = db.reference('bridge/response').get()
            if res and 'image_base64' in res:
                img = Image.open(BytesIO(base64.b64decode(res['image_base64'])))
                st.session_state['telegram_images'].append(img)
                st.session_state['telegram_flow']['step'] = 'idle'
                st.toast("Görsel Geldi!", icon="📸")
                st.rerun()
        elif status == 'miniapp_waiting_upload':
            st.session_state['telegram_flow']['step'] = 'upload_wait'
            st.rerun()
        elif status == 'timeout':
            st.error("Zaman aşımı!")
            st.session_state['telegram_flow']['step'] = 'idle'
            st.rerun()

# ==========================================
# 🧠 GEMINI ANALIZ (HAFIZA ENTEGRASYONU)
# ==========================================
def analyze_images_stream(all_images, model_name):
    pool = st.session_state['dynamic_key_pool']
    if not pool: yield "⚠️ HATA: API Key bulunamadı! Lütfen ayarlardan Gemini API Key ekleyin."; return
    key = pool[st.session_state['key_index'] % len(pool)]
    
    SYSTEM_INSTRUCTION = """
    Sen Kıdemli Borsa Stratejistisin.
    GÖREV: Görselleri analiz et.
    
    ⚠️ ÖNEMLİ KURALLAR:
    1. Her başlık altında EN AZ 20 MADDE/SATIR DETAYLI VERİ OLACAK.
    2. "KADEME YORUMU" (PRICE LEVEL COMMENTARY) BÖLÜMÜ MUTLAKA OLACAK ve çok detaylı olacak.
    
    RAPOR FORMATI:
    ## 1. 🔍 GÖRSEL VERİ DÖKÜMÜ (En az 20 satır, tek tek işle)
    ## 2. 📊 DERİNLİK ANALİZİ (Alıcı/Satıcı dengesi, yığılmalar)
    ## 3. 🏢 KURUM VE PARA GİRİŞİ (AKD) (Toplayanlar, Satanlar)
    ## 4. 🧠 GENEL SENTEZ VE SKOR
    ## 5. 🎯 İŞLEM PLANI (Güvenli Giriş, Stop Loss, Hedefler)
    ## 6. 🔮 KAPANIŞ BEKLENTİSİ
    ## 7. Gizli Balina / Iceberg Avcısı
    ## 8. Boğa/Ayı Tuzağı Dedektörü
    ## 9. Agresif vs. Pasif Emir Analizi
    ## 10. Maliyet ve Takas Baskısı
    ## ...
    ## 20. 📏 KADEME YORUMU (PRICE LEVEL COMMENTARY) - Zorunlu. Kademeleri tek tek analiz et.
    """
    
    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content_stream(
            model=model_name,
            contents=["Görselleri analiz et."] + all_images,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, max_output_tokens=8192)
        )
        for chunk in response:
            if chunk.text: yield chunk.text
    except Exception as e: yield f"HATA: {e}"

# ==========================================
# 🖥️ MAIN
# ==========================================
def main():
    st.set_page_config(page_title="Scalper AI", layout="wide")
    init_firebase()
    check_firebase_status()

    with st.sidebar:
        st.header("⚙️ Ayarlar")
        if st.button("🔄 SİSTEMİ RESETLE"): send_restart_command()
        
        current = st.session_state.get('selected_bot_key', 'xFinans')
        sel = st.selectbox("Bot:", list(BOT_CONFIGS.keys()), index=list(BOT_CONFIGS.keys()).index(current))
        if sel != current: st.session_state['selected_bot_key'] = sel; st.rerun()
        
        # --- GEMINI KEY YÖNETİMİ ---
        st.divider()
        st.subheader("🔑 Gemini API Keyler")
        
        # Mevcut keyleri göster
        current_keys = st.session_state['dynamic_key_pool']
        keys_str = "\n".join(current_keys)
        
        # Düzenlenebilir alan
        new_keys_str = st.text_area("Her satıra bir key:", value=keys_str, height=150)
        
        col_save, col_test = st.columns(2)
        
        if col_save.button("💾 Kaydet"):
            key_list = new_keys_str.split('\n')
            save_keys_to_disk(key_list)
            st.rerun()

        if col_test.button("🧪 Test Et"):
            if not current_keys:
                st.error("Test edilecek key yok!")
            else:
                st.info("Test Başlıyor...")
                console = st.empty()
                report = ""
                for k in current_keys:
                    mask = f"{k[:5]}...{k[-4:]}"
                    # Flash Test
                    try:
                        c = genai.Client(api_key=k)
                        c.models.generate_content(model="gemini-2.5-flash", contents="T", config=types.GenerateContentConfig(max_output_tokens=1))
                        f_res = "✅"
                    except: f_res = "❌"
                    # Lite Test
                    try:
                        c = genai.Client(api_key=k)
                        c.models.generate_content(model="gemini-2.5-flash-lite", contents="T", config=types.GenerateContentConfig(max_output_tokens=1))
                        l_res = "✅"
                    except: l_res = "❌"
                    
                    report += f"**{mask}** -> Flash: {f_res} | Lite: {l_res}\n\n"
                console.markdown(report)

    st.title(f"⚡ Scalper AI: {sel}")
    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("📡 Bot Kontrol")
        sym = st.text_input("Hisse Kodu:", value=st.session_state['telegram_flow']['symbol'], placeholder="THYAO", key="main_sym_input").upper()
        if sym != st.session_state['telegram_flow']['symbol']:
             st.session_state['telegram_flow']['symbol'] = sym

        cols = st.columns(4)
        for i, (lbl, cmd) in enumerate(BOT_CONFIGS[sel]["buttons"]):
            if cols[i%4].button(lbl, use_container_width=True): start_telegram_request(sym, cmd)
        
        step = st.session_state['telegram_flow']['step']
        if step == 'processing':
            st.info("İşleniyor...")
            st.spinner("Bekleniyor...")
            time.sleep(1)
            st.rerun()
            
        elif step == 'show_buttons':
            st.success("Seçim Yapın:")
            opts = st.session_state['telegram_flow']['options']
            bc = st.columns(2)
            for i, o in enumerate(opts):
                if bc[i%2].button(o, key=f"b{i}"): send_user_selection(o)
                
        elif step == 'upload_wait':
            st.warning("⚠️ Mini-App Açıldı! SS yükleyin.")
            if st.button("İptal"): db.reference('bridge/request').update({'status': 'cancelled'}); st.rerun()

        # --- X TARAYICI (GÖRSELDEKİ GİBİ) ---
        st.divider()
        st.subheader("𝕏 Tarayıcı")
        x_sym = st.text_input("Kod:", value=sym if sym else "THYAO", key="x_in").upper()
        x_type = st.radio("Tip:", ["🔥 Geçmiş", "⏱️ Canlı"], key="x_type")
        x_date = st.date_input("Tarih", datetime.date.today(), key="x_date")
        
        if x_type == "🔥 Geçmiş":
            nxt = x_date + datetime.timedelta(days=1)
            qry = f"#{x_sym} lang:tr until:{nxt} since:{x_date} min_faves:5"
            url = f"https://x.com/search?q={quote(qry)}&src=typed_query&f=top"
            lbl = f"🔥 {x_date} Popüler"
        else:
            qry = f"#{x_sym} lang:tr"
            url = f"https://x.com/search?q={quote(qry)}&src=typed_query&f=live"
            lbl = f"⏱️ {x_sym} Son Dakika"
            
        st.link_button(lbl, url=url, use_container_width=True)

    with c2:
        st.subheader("🧠 Analiz")
        up = st.file_uploader("Görsel Yükle", accept_multiple_files=True)
        if up and step == 'upload_wait':
             db.reference('bridge/request').update({'status': 'manual_completed'})
             st.session_state['telegram_flow']['step'] = 'idle'
             st.rerun()
        
        imgs = (up or []) + st.session_state['telegram_images']
        if imgs:
            st.image(imgs, width=150)
            if st.button("🧹 Temizle"): st.session_state['telegram_images'] = []; st.rerun()
            
            mdl = st.radio("Model:", ["gemini-2.5-flash", "gemini-2.5-flash-lite"], horizontal=True)
            if st.button("ANALİZİ BAŞLAT 🚀", type="primary", use_container_width=True):
                out = st.empty(); txt = ""
                for ch in analyze_images_stream(imgs, mdl):
                    txt += ch; out.markdown(txt)

if __name__ == "__main__":
    main()
