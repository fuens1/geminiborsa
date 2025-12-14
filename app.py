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

# --- SESSION ---
if 'telegram_flow' not in st.session_state: st.session_state['telegram_flow'] = {'step': 'idle', 'symbol': '', 'options': []}
if 'telegram_images' not in st.session_state: st.session_state['telegram_images'] = []
if 'key_index' not in st.session_state: st.session_state['key_index'] = 0
if 'dynamic_key_pool' not in st.session_state: st.session_state['dynamic_key_pool'] = []
if 'selected_bot_key' not in st.session_state: st.session_state['selected_bot_key'] = "xFinans"

# ==========================================
# 🔑 KEY YÖNETİMİ
# ==========================================
def load_keys():
    keys = []
    if os.path.exists(LOCAL_KEY_FILE):
        with open(LOCAL_KEY_FILE, "r", encoding="utf-8") as f:
            keys = [k.strip() for k in f.read().split('\n') if k.strip()]
    if not keys and "gemini" in st.secrets and "api_keys" in st.secrets["gemini"]:
        keys = st.secrets["gemini"]["api_keys"]
    return keys

def save_keys_to_disk(keys_list):
    clean_keys = [k.strip() for k in keys_list if k.strip()]
    if os.path.exists(LOCAL_KEY_FILE):
        with open(LOCAL_KEY_FILE, "w", encoding="utf-8") as f: f.write("\n".join(clean_keys))
    st.session_state['dynamic_key_pool'] = clean_keys

if not st.session_state['dynamic_key_pool']:
    st.session_state['dynamic_key_pool'] = load_keys()

# ==========================================
# 🔥 FIREBASE INIT (GÜVENLİ & DİNAMİK)
# ==========================================
def init_firebase():
    # Eğer zaten bağlantı varsa tekrar etme
    if len(firebase_admin._apps) > 0: return

    try:
        cred = None

        # 1. YÖNTEM: Streamlit Cloud Secrets (Bulut için)
        # Github'a yüklediğinde burayı kullanacak.
        if "firebase" in st.secrets and "text_key" in st.secrets["firebase"]:
            try:
                # Secrets'taki metni JSON'a çevir
                key_content = st.secrets["firebase"]["text_key"]
                cred_info = json.loads(key_content, strict=False)
                cred = credentials.Certificate(cred_info)
            except Exception as json_err:
                st.error(f"Secrets JSON Format Hatası: {json_err}")
                st.stop()

        # 2. YÖNTEM: Yerel Dosya (PC için)
        # Bilgisayarında çalıştırırken klasördeki dosyayı kullanacak.
        elif os.path.exists("firebase_key.json"):
            cred = credentials.Certificate("firebase_key.json")

        # Bağlantıyı Kur
        if cred:
            firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        else:
            st.error("⚠️ Firebase Anahtarı Bulunamadı!")
            st.info("Lütfen şunlardan birini yapın:\n1. Bilgisayardaysanız: 'firebase_key.json' dosyasını klasöre atın.\n2. Cloud'daysanız: Secrets ayarlarını [firebase] text_key=... şeklinde yapın.")
            st.stop()

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
# 🧠 GEMINI ANALIZ
# ==========================================
def analyze_images_stream(all_images, model_name):
    pool = st.session_state['dynamic_key_pool']
    if not pool: yield "⚠️ HATA: API Key yok! Ayarlardan ekleyin."; return
    key = pool[st.session_state['key_index'] % len(pool)]
    
    # SENİN GÖNDERDİĞİN (YARIM KALAN) PROMPT TAMAMLANDI:
    SYSTEM_INSTRUCTION = """
    Sen Kıdemli Borsa Stratejistisin.
    
    GÖREVİN:
    Ekteki görsellerdeki verileri (Derinlik, AKD, Takas, Mini-App Listeleri, Grafikler) oku ve YARIDA KESMEDEN detaylıca raporla.
    Görselde veri yoksa, o başlığın altına "Veri bulunamadı" yaz.

    🎨 RENK KODLARI:
    * :green[...] -> Yükseliş, Güçlü Alım, Destek Üstü, Pozitif.
    * :red[...] -> Düşüş, Satış Baskısı, Direnç Altı, Negatif.
    * :blue[...] -> Nötr Veri, Bilgi, Fiyat.

    📄 RAPOR FORMATI:

    ## 1. 🔍 GÖRSEL VERİ DÖKÜMÜ (Mini-App / Liste Varsa)
    (Görseldeki tüm hisse, fiyat ve oranları buraya dök. Satır satır işle. EN AZ 20 SATIR)

    ## 2. 📊 DERİNLİK ANALİZİ (Varsa)
    * **Alıcı/Satıcı Dengesi:** (:green[Alıcılar] mı :red[Satıcılar] mı güçlü?)
    * **Emir Yığılmaları:** (Hangi kademede ne kadar lot var?)

    ## 3. 🏢 KURUM VE PARA GİRİŞİ (AKD) (Varsa)
    * **Toplayanlar:** (Kim alıyor? Maliyetleri ne?)
    * **Satanlar:** (Kim satıyor? Para çıkışı var mı?)

    ## 4. 🧠 GENEL SENTEZ VE SKOR
    * **Piyasa Yönü:** (Yukarı/Aşağı/Yatay)
    * **Genel Puan:** 10 üzerinden X
    * **Yorum:** :blue[Piyasa yapıcı ne planlıyor?]

    ## 5. 🎯 İŞLEM PLANI
    * :green[**GÜVENLİ GİRİŞ:** ...] 
    * :red[**STOP LOSS:** ...]
    * :green[**HEDEF 1:** ...]
    * :green[**HEDEF 2:** ...]

    ## 6. 🔮 KAPANIŞ BEKLENTİSİ
    (Günün geri kalanı için tahmin.)
    
    ## 7.Gizli Balina / Iceberg Avcısı
    *Bu derinlik ve gerçekleşen işlemler (Time & Sales) görüntüsüne bak. Kademedeki görünür lot sayısı az olmasına rağmen, o fiyattan sürekli işlem geçmesine rağmen fiyat aşağı/yukarı gitmiyor mu?
    
    ## 8. Boğa/Ayı Tuzağı (Fakeout) Dedektörü
    *Fiyat önemli bir direnci/desteği kırmış görünüyor. Ancak AKD (Aracı Kurum Dağılımı) ve Hacim bunu destekliyor mu? Kırılım anında Bofa, Yatırım Finansman gibi büyük oyuncular alıcı tarafta mı, yoksa küçük yatırımcıya mal mı devrediyorlar?
    
    ## 9.⚖️ Agresif vs. Pasif Emir Analizi
    *Derinlikteki emirlerin niteliğini analiz et. Alıcılar 'Pasif'e mi yazılıyor, yoksa 'Aktif'ten mi alıyor?
    
    ## 10.🏦 Maliyet ve Takas Baskısı
    *Bugün en çok net alım yapan ilk 3 kurumun ortalama maliyeti nedir?
    
    ## 11.🌊 RVOL ve Hacim Anormalliği
    *Hacimde anormal bir patlama var mı?
    
    ## 12. 🧱 Kademe Boşlukları ve Spread Analizi
    *Alış ve satış kademeleri arasındaki makas (spread) açık mı?
    
    ## 13. 🔄 VWAP Dönüş (Mean Reversion)
    *Fiyatın gün içi ağırlıklı ortalamadan (VWAP) ne kadar uzakta?
    
    ## 14. 🎭 Piyasa Yapıcı Psikolojisi
    *Tahtanın genel görünümüne bakarak 'Piyasa Yapıcı'nın niyetini yorumla.
    
    ## 15. 🛑 Şeytanın Avukatı (Risk Analizi)
    *NEDEN ALMAMALIYIM? Riskler neler?
    
    ## 16. Likidite Avı (Liquidity Sweep)
    *Stop patlatma hareketi var mı?
    
    ## 17. 📊 "Point of Control (POC) ve Hacim Profili
    *En çok hacmin döndüğü fiyat seviyesi neresi?
    
    ## 18. 🏗️ "Adım Adım Mal Toplama (Step-Ladder)
    *Robotik, sistematik alımlar var mı?
    
    ## 19. 🚦 "Dominant Taraf ve Delta Analizi
    *Delta (Net Alıcı - Net Satıcı) pozitif mi negatif mi?
    
    ## 20. 📏 KADEME YORUMU (PRICE LEVEL COMMENTARY)
    *(Bu bölüm ZORUNLUDUR. Fiyat kademelerini tek tek incele. Hangi kademede duvar var, hangi kademe boş? En az 20 madde.)
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
        st.subheader("🔑 Gemini Keys")
        keys_val = "\n".join(st.session_state['dynamic_key_pool'])
        
        if os.path.exists(LOCAL_KEY_FILE):
            new_keys = st.text_area("Düzenle:", keys_val, height=150)
            col_save, col_test = st.columns(2)
            if col_save.button("💾 Kaydet"):
                save_keys_to_disk(new_keys.split('\n'))
                st.success("Kaydedildi!")
                st.rerun()
            if col_test.button("🧪 Test Et"):
                if not st.session_state['dynamic_key_pool']: st.error("Key yok!")
                else:
                    st.info("Test...")
                    con = st.empty(); rep = ""
                    for k in st.session_state['dynamic_key_pool']:
                        msk = f"{k[:5]}...{k[-4:]}"
                        try:
                            c = genai.Client(api_key=k)
                            c.models.generate_content(model="gemini-2.5-flash", contents="T", config=types.GenerateContentConfig(max_output_tokens=1))
                            res = "✅ OK"
                        except: res = "❌ ERR"
                        rep += f"**{msk}** -> {res}\n\n"
                    con.markdown(rep)
        else:
            st.info("Cloud Modu: Keyler Secrets'tan yönetiliyor.")

    st.title(f"⚡ Scalper AI: {sel}")
    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("📡 Bot Kontrol")
        sym = st.text_input("Hisse Kodu:", value=st.session_state['telegram_flow']['symbol'], placeholder="THYAO", key="main_sym_input").upper()
        if sym != st.session_state['telegram_flow']['symbol']: st.session_state['telegram_flow']['symbol'] = sym

        cols = st.columns(4)
        for i, (lbl, cmd) in enumerate(BOT_CONFIGS[sel]["buttons"]):
            if cols[i%4].button(lbl, use_container_width=True): start_telegram_request(sym, cmd)
        
        step = st.session_state['telegram_flow']['step']
        if step == 'processing':
            st.info("İşleniyor..."); st.spinner("Bekleniyor..."); time.sleep(1); st.rerun()
        elif step == 'show_buttons':
            st.success("Seçim Yapın:")
            opts = st.session_state['telegram_flow']['options']
            bc = st.columns(2)
            for i, o in enumerate(opts):
                if bc[i%2].button(o, key=f"b{i}"): send_user_selection(o)
        elif step == 'upload_wait':
            st.warning("⚠️ Mini-App! SS yükleyin.")
            if st.button("İptal"): db.reference('bridge/request').update({'status': 'cancelled'}); st.rerun()

        # --- X TARAYICI ---
        st.divider(); st.subheader("𝕏 Tarayıcı")
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
                for ch in analyze_images_stream(imgs, mdl): txt += ch; out.markdown(txt)

if __name__ == "__main__":
    main()
