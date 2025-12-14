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
    from google.api_core.exceptions import ResourceExhausted, NotFound, InvalidArgument, Unauthenticated
except ImportError:
    st.error("Google GenAI eksik.")
    st.stop()

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    st.error("Firebase Admin eksik.")
    st.stop()

# ==========================================
# ⚙️ AYARLAR
# ==========================================
FIREBASE_DB_URL = "https://geminiborsa-f9a80-default-rtdb.firebaseio.com/"
MODEL_FLASH = 'gemini-2.5-flash' 
MODEL_LITE  = 'gemini-2.5-flash-lite'
LOCAL_KEY_FILE = "api_keys.txt"

# BOT YAPILANDIRMASI
BOT_CONFIGS = {
    "xFinans": {
        "username": "@xFinans_bot",
        "buttons": [
            ("📊 Derinlik", "derinlik"),
            ("🔢 Teorik", "teorik"),
            ("🏢 AKD", "akd"),
            ("📈 Yükselen/Düşen", "yukselendusen"),
            ("📜 Teorik Liste", "teorikliste"),
            ("📡 Sinyal", "sinyal")
        ]
    },
    "BorsaBilgi": {
        "username": "@borsabilgibot",
        "buttons": [
            ("📊 Derinlik", "derinlik"),
            ("🏢 AKD", "akd"),
            ("🔄 Takas", "takas"),
            ("🔢 Teorik", "teorik"),
            ("📉 Endeks Alan - Satan", "endeks"),
            ("🏦 Kurum Analizi", "kurumlar"),
            ("🇺🇸 BOFA Analiz", "bofa"),
            ("📰 Haberler", "haber")
        ]
    },
    "BorsaBuzz": {
        "username": "@BorsaBuzzBot",
        "buttons": [
            ("📊 Derinlik", "derinlik"),
            ("🏢 AKD", "akd"),
            ("🌟 AKD Pro", "akdpro"),
            ("🔝 AKD 20", "akd20"),
            ("📏 Kademe", "kademe"),
            ("🐳 Balina", "balina"),
            ("📐 Teknik", "teknik")
        ]
    },
    "b0pt": {
        "username": "@b0pt_bot",
        "buttons": [
            ("📊 Derinlik", "derinlik"),
            ("🏢 AKD", "akd"),
            ("🔢 Teorik", "teorik"),
            ("📚 Tüm Veriler", "tumu"),
            ("🔄 Takas", "takas"),
            ("📏 Kademe", "kademe"),
            ("📉 Grafik", "grafik"),
            ("🏦 Genel AKD", "genelakd"),
            ("🏢 Kurum Analizi", "kurum"),
            ("🔢 Teorik Yükselen - Düşen", "teorikyd"),
            ("📈 Piyasa Yükselen - Düşen", "piyasayd"),
            ("🇺🇸 Bofa Analizi", "bofa")
        ]
    }
}

# ==========================================
# 🔧 SESSION
# ==========================================
if 'telegram_flow' not in st.session_state: st.session_state['telegram_flow'] = {'step': 'idle', 'symbol': '', 'options': []}
if 'telegram_images' not in st.session_state: st.session_state['telegram_images'] = []
if 'key_index' not in st.session_state: st.session_state['key_index'] = 0
if 'dynamic_key_pool' not in st.session_state: st.session_state['dynamic_key_pool'] = []
if 'selected_bot_key' not in st.session_state: st.session_state['selected_bot_key'] = "xFinans"

# --- KALICI HAFIZA ---
def load_keys_from_disk():
    if os.path.exists(LOCAL_KEY_FILE):
        with open(LOCAL_KEY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
            keys = [k.strip() for k in content.split('\n') if k.strip()]
            st.session_state['dynamic_key_pool'] = keys

def save_keys_to_disk(keys_list):
    clean_keys = [k.strip() for k in keys_list if k.strip()]
    with open(LOCAL_KEY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(clean_keys))
    st.session_state['dynamic_key_pool'] = clean_keys

if not st.session_state['dynamic_key_pool']:
    load_keys_from_disk()

# ==========================================
# 🔥 FIREBASE
# ==========================================
def init_firebase():
    if len(firebase_admin._apps) > 0: return
    try:
        if os.path.exists("firebase_key.json"):
            cred = credentials.Certificate("firebase_key.json")
        else:
            json_str = st.secrets["firebase"]["json_content"]
            cred = credentials.Certificate(json.loads(json_str))
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    except Exception as e:
        st.error(f"Firebase Bağlantı Hatası: {e}")

# ==========================================
# 📡 TELEGRAM İŞLEMLERİ (DÜZELTİLDİ)
# ==========================================
def start_telegram_request(symbol, rtype):
    if not firebase_admin._apps: return
    
    bot_key = st.session_state['selected_bot_key']
    target_bot_username = BOT_CONFIGS[bot_key]["username"]

    no_symbol_needed = [
        "yukselendusen", "teorikliste", "sinyal", "endeks", "haber", 
        "balina", "tum", "genelakd", "piyasayd", "teorikyd", 
        "kurum", "kurumlar", "bofa"
    ]
    
    if rtype not in no_symbol_needed and not symbol:
        st.toast(f"⚠️ Bu işlem için hisse kodu gerekli!", icon="⚠️")
        return

    st.session_state['telegram_flow'] = {'step': 'processing', 'symbol': symbol, 'options': []}
    
    ref_req = db.reference('bridge/request')
    db.reference('bridge/response').delete() 
    
    ref_req.set({
        'symbol': symbol.upper() if symbol else "",
        'type': rtype,
        'target_bot': target_bot_username,
        'status': 'pending',
        'timestamp': time.time()
    })
    st.rerun()

def send_user_selection(selection):
    """
    KULLANICI SEÇİMİNİ GÖNDERİR.
    DÜZELTME: Timestamp eklendi, böylece bot bunun yeni bir komut olduğunu anlar.
    """
    ref_req = db.reference('bridge/request')
    
    # Timestamp güncellemesi kritik!
    ref_req.update({
        'status': 'selection_made', 
        'selection': selection,
        'timestamp': time.time() 
    })
    
    st.session_state['telegram_flow']['step'] = 'processing'
    st.session_state['telegram_flow']['options'] = []
    
    st.toast(f"Seçim İletildi: {selection}", icon="📨")
    time.sleep(0.5) # Firebase yazma işlemi için kısa bekleme
    st.rerun()

def check_firebase_status():
    try:
        if not firebase_admin._apps: return
        flow = st.session_state['telegram_flow']
        
        if flow['step'] == 'processing':
            ref_req = db.reference('bridge/request')
            status_data = ref_req.get()
            if not status_data: return
            
            status = status_data.get('status')
            
            if status == 'waiting_user_selection':
                res_data = db.reference('bridge/response').get()
                if res_data and 'options' in res_data:
                    st.session_state['telegram_flow']['options'] = res_data['options']
                    st.session_state['telegram_flow']['step'] = 'show_buttons'
                    st.rerun()
            
            elif status == 'completed':
                res_data = db.reference('bridge/response').get()
                if res_data and 'image_base64' in res_data:
                    try:
                        img_data = base64.b64decode(res_data['image_base64'])
                        img = Image.open(BytesIO(img_data))
                        st.session_state['telegram_images'].append(img)
                        st.toast("Görsel Alındı!", icon="📸")
                        st.session_state['telegram_flow']['step'] = 'idle'
                        st.rerun()
                    except: pass
            
            elif status == 'miniapp_waiting_upload':
                st.session_state['telegram_flow']['step'] = 'upload_wait'
                st.rerun()

            elif status == 'timeout':
                st.error("Zaman aşımı. Bot yanıt vermedi.")
                st.session_state['telegram_flow']['step'] = 'idle'
                st.rerun()
    except Exception: pass

# ==========================================
# 🤖 GEMINI ANALİZ (STREAM MODU)
# ==========================================
def get_current_key():
    pool = st.session_state['dynamic_key_pool']
    if not pool: return None
    return pool[st.session_state['key_index'] % len(pool)]

def analyze_images_stream(all_images, model_name):
    key = get_current_key()
    if not key:
        yield "HATA: API Key bulunamadı!"
        return

    gemini_contents = [ "Aşağıdaki borsa görsellerini (Grafik, Liste, Derinlik, Takas vb.) en ince detayına kadar analiz et." ] + all_images
    
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
    (Görseldeki tüm hisse, fiyat ve oranları buraya dök. Satır satır işle.)

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
    *Bu derinlik ve gerçekleşen işlemler (Time & Sales) görüntüsüne bak. Kademedeki görünür lot sayısı az olmasına rağmen, o fiyattan sürekli işlem geçmesine rağmen fiyat aşağı/yukarı gitmiyor mu? 'Iceberg Emir' (Gizli Emir) veya Duvar Örme durumu var mı? Tahtacı fiyatı belli bir seviyede tutmaya mı çalışıyor? Bu seviye bir biriktirme (akümülasyon) bölgesi mi?
    
    ## 8. Boğa/Ayı Tuzağı (Fakeout) Dedektörü
    *Fiyat önemli bir direnci/desteği kırmış görünüyor. Ancak AKD (Aracı Kurum Dağılımı) ve Hacim bunu destekliyor mu? Kırılım anında Bofa, Yatırım Finansman gibi büyük oyuncular alıcı tarafta mı, yoksa küçük yatırımcıya mal mı devrediyorlar? Bu hareketin bir Fakeout (Sahte Kırılım) olma ihtimalini 10 üzerinden puanla.
    
    ## 9.⚖️ Agresif vs. Pasif Emir Analizi
    *Derinlikteki emirlerin niteliğini analiz et. Alıcılar 'Pasif'e mi (Kademeye) yazılıyor, yoksa 'Aktif'ten (Piyasa emriyle) mi alıyor? Satış kademeleri eriyor mu, yoksa sürekli yeni satış mı ekleniyor (Reloading)? Tahtadaki agresiflik (Market Buy/Sell) hangi yönde?
    
    ## 10.🏦 Maliyet ve Takas Baskısı
    *Bugün en çok net alım yapan ilk 3 kurumun ortalama maliyetine bak. Şu anki fiyat, bu kurumların maliyetinin ne kadar üzerinde veya altında? Eğer fiyat maliyetlerinin çok altındaysa Zararına Satış baskısı oluşabilir mi? Yoksa maliyetlerine çekmek için fiyatı yukarı mı sürecekler?
    
    ## 11.🌊 RVOL ve Hacim Anormalliği
    *Bu saatteki işlem hacmini, hissenin standart hacmiyle kıyasla (Göz kararı). Hacimde anormal bir patlama var mı? Eğer hacim yüksekse ama fiyat yerinde sayıyorsa (Doji/Spinning Top), bu bir 'Trend Dönüşü' sinyali olabilir mi? Hacim fiyatı destekliyor mu?
    
    ## 12. 🧱 Kademe Boşlukları ve Spread Analizi
    *Alış ve satış kademeleri arasındaki makas (spread) açık mı? Kademeler dolu mu yoksa boş mu (Sığ tahta)? Eğer kademeler boşsa, yüklü bir emirle fiyatın sert bir şekilde (Slippage) kayma ihtimali nedir? Bu tahtada 'Scalp' yapmak riskli mi?
    
    ## 13. 🔄 VWAP Dönüş (Mean Reversion)
    *Fiyatın gün içi ağırlıklı ortalamadan (VWAP) ne kadar uzaklaştığını tahmin et. Lastik çok mu gerildi? Fiyatın VWAP'a doğru bir düzeltme (Pullback) yapma olasılığı var mı? Aşırı alım veya aşırı satım bölgesinde miyiz?
    
    ## 14. 🎭 Piyasa Yapıcı Psikolojisi
    *Tahtanın genel görünümüne bakarak 'Piyasa Yapıcı'nın (Market Maker) niyetini yorumla. Satış tarafına korkutma amaçlı yüklü Fake lotlar yazılmış olabilir mi? Alıcı tarafı bilerek mi zayıf bırakılmış (Mal toplamak için)? Yoksa gerçekten alıcı mı yok?
    
    ## 15. 🛑 Şeytanın Avukatı (Risk Analizi)
    *Bana bu hisseyi almak için sebeplerimi sayma. NEDEN ALMAMALIYIM? Riskler neler? Görselde seni rahatsız eden, 'Gel Gel' operasyonu olabileceğine dair en ufak bir ipucu var mı? Eğer işler ters giderse, en mantıklı Stop Loss (Zarar Kes) seviyesi, hangi kademenin altıdır?
    
    ## 16. Likidite Avı (Liquidity Sweep)
    *Fiyat, belirgin bir destek veya direnç seviyesinin altına/üstüne 'iğne atıp' hemen geri döndü mü? Bu hareket, sadece oradaki stop emirlerini patlatıp likidite toplamak için mi yapıldı? Eğer öyleyse, bu 'Fake Kırılım' sonrası ters yöne sert bir hareket (Ralli/Çöküş) beklemeli miyim?
    
    ## 17. 📊 "Point of Control (POC) ve Hacim Profili
    *Görseldeki işlemlere bakarak, en çok hacmin döndüğü fiyat seviyesini (POC - Point of Control) tahmin et. Şu anki fiyat bu seviyenin üzerinde mi altında mı? Fiyat bu yoğun bölgeden hızla uzaklaşıyor mu (Kabul), yoksa sürekli oraya mı çekiliyor (Denge)? Fiyat POC'den uzaklaştıysa 'Dengesizlik' (Imbalance) trade'i fırsatı var mı?
    
    ## 18. 🏗️ "Adım Adım Mal Toplama (Step-Ladder)
    *Derinlik ve gerçekleşen işlemlere bak. Fiyat düşmüyor ama her kademeye sistematik olarak küçük küçük (örn: 50, 100 lot) alışlar giriliyor mu? Bu, dikkat çekmeden mal toplayan bir 'Algoritmik Robot' (TWAP/VWAP botu) izi olabilir mi? Tahtada sinsi bir 'Emme' hareketi var mı?"
    
    ## 19. 🚦 "Dominant Taraf ve Delta Analizi
    *Şu an tahtada gerçekleşen işlemlere bak (Time & Sales). İşlemler daha çok 'Satış Kademesinden' (Aktif Alış) mi geçiyor, yoksa 'Alış Kademesinden' (Aktif Satış) mi? Yani piyasa emri gönderenler ALICILAR mi SATICILAR mi? Delta (Net Alıcı - Net Satıcı) pozitif mi negatif mi? Kim daha agresif?
    """ 

    try:
        client = genai.Client(api_key=key)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.2, 
            max_output_tokens=99999 
        )
        
        response_stream = client.models.generate_content_stream(
            model=model_name, 
            contents=gemini_contents, 
            config=config
        )
        
        for chunk in response_stream:
            if chunk.text:
                yield chunk.text

    except Exception as e:
        yield f"HATA: {str(e)}"

# ==========================================
# 🖥️ ARAYÜZ (MAIN)
# ==========================================
def main():
    st.set_page_config(page_title="Scalper AI Ultra", layout="wide")
    init_firebase()
    check_firebase_status()

    # --- SIDEBAR ---
    with st.sidebar:
        st.header("⚙️ Ayarlar")
        
        if st.button("⚠️ SİSTEMİ SIFIRLA (RESET)", type="primary"):
            st.session_state.clear()
            st.rerun()
            
        st.divider()
        
        # --- BOT SEÇİMİ ---
        st.subheader("🤖 Kanal Seçimi")
        current_name = st.session_state.get('selected_bot_key', 'xFinans')
        if current_name not in BOT_CONFIGS: current_name = 'xFinans'
        idx = list(BOT_CONFIGS.keys()).index(current_name)
            
        selected_bot_name = st.selectbox(
            "Veri Kaynağı:", 
            list(BOT_CONFIGS.keys()),
            index=idx
        )
        
        if selected_bot_name != st.session_state.get('selected_bot_key'):
            st.session_state['selected_bot_key'] = selected_bot_name
            st.rerun()
            
        st.caption(f"Aktif: {BOT_CONFIGS[selected_bot_name]['username']}")
        st.divider()

        st.subheader("🔑 API Anahtarları")
        current_keys = "\n".join(st.session_state['dynamic_key_pool'])
        keys_input = st.text_area("Gemini Keyler", value=current_keys, height=100)
        
        if st.button("💾 Kaydet"):
            keys_list = keys_input.split('\n')
            save_keys_to_disk(keys_list)
            st.success("Kaydedildi!")
            st.rerun()

        if st.button("🔍 KEY TESTİ (2.5)"):
            pool = st.session_state['dynamic_key_pool']
            if not pool:
                st.error("Key yok!")
            else:
                st.info(f"Test Modelleri:\n{MODEL_FLASH}\n{MODEL_LITE}")
                res_box = st.container(border=True)
                for k in pool:
                    mk = f"{k[:5]}...{k[-3:]}"
                    try:
                        c = genai.Client(api_key=k)
                        try:
                            c.models.generate_content(model=MODEL_FLASH, contents="T", config=types.GenerateContentConfig(max_output_tokens=1))
                            f_status = "✅"
                        except: f_status = "❌"
                        try:
                            c.models.generate_content(model=MODEL_LITE, contents="T", config=types.GenerateContentConfig(max_output_tokens=1))
                            l_status = "✅"
                        except: l_status = "❌"
                        res_box.write(f"**{mk}** | F: {f_status} | L: {l_status}")
                    except Exception as e:
                        res_box.error(f"HATA: {e}")

    # --- MAIN ---
    st.title(f"⚡ Scalper AI: {selected_bot_name}")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader(f"📡 {selected_bot_name} Paneli")
        symbol = st.text_input("Hisse Kodu (Opsiyonel):", placeholder="THYAO").upper()
        
        buttons_list = BOT_CONFIGS[selected_bot_name]["buttons"]
        num_columns = 4
        columns = st.columns(num_columns)
        
        for i, (btn_label, btn_cmd) in enumerate(buttons_list):
            col_idx = i % num_columns
            if columns[col_idx].button(btn_label, use_container_width=True):
                start_telegram_request(symbol, btn_cmd)

        step = st.session_state['telegram_flow']['step']
        target_username = BOT_CONFIGS[selected_bot_name]["username"]

        if step == 'processing':
            st.info(f"⏳ {target_username} bekleniyor...")
            st.spinner("İşleniyor...")
            time.sleep(1)
            st.rerun()
            
        elif step == 'show_buttons':
            st.success("👇 Seçenekler:")
            opts = st.session_state['telegram_flow']['options']
            cols = st.columns(2)
            for i, opt in enumerate(opts):
                if cols[i%2].button(f"👉 {opt}", key=f"btn_{i}"):
                    send_user_selection(opt)

        elif step == 'upload_wait':
            st.warning("⚠️ MİNİ-APP LİSTESİ AÇILDI!")
            st.info("Lütfen telefondan listeyi açıp SS alın ve SAĞ TARAFA yükleyin.")
            if st.button("❌ İptal Et"):
                db.reference('bridge/request').update({'status': 'cancelled'})
                st.session_state['telegram_flow']['step'] = 'idle'
                st.rerun()

        # ==========================================
        # 🆕 EKLENEN KISIM: X TARAYICI
        # ==========================================
        st.divider()
        with st.container(border=True):
            st.header("𝕏 Tarayıcı")
            
            # Kod değişkeni yoksa default "THYAO" ata
            api_ticker_input = symbol if symbol else "THYAO"

            raw_ticker = st.text_input("Kod:", api_ticker_input, key="x_ticker_input").upper()
            clean_ticker = raw_ticker.replace("#", "").strip()
            search_mode = st.radio("Tip:", ("🔥 Geçmiş", "⏱️ Canlı"), key="x_search_mode")
            
            if search_mode == "🔥 Geçmiş":
                s_date = st.date_input("Tarih", datetime.date.today(), key="x_date_input")
                # X Arama Linki (Geçmiş)
                url = f"https://x.com/search?q={quote(f'#{clean_ticker} lang:tr until:{s_date + datetime.timedelta(days=1)} since:{s_date} min_faves:5')}&src=typed_query&f=top"
                btn_txt = f"🔥 <b>{s_date}</b> Popüler"
            else:
                # X Arama Linki (Canlı)
                url = f"https://x.com/search?q={quote(f'#{clean_ticker} lang:tr')}&src=typed_query&f=live"
                btn_txt = f"⏱️ Son Dakika"
            
            # CSS ile Buton Görünümü Kazandırma
            st.markdown(
                f"""
                <style>
                .x-btn {{
                    display: inline-block;
                    padding: 0.5em 1em;
                    color: white;
                    background-color: #000000; /* X Black */
                    border: 1px solid #333;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: bold;
                    text-align: center;
                    width: 100%;
                    margin-top: 10px;
                }}
                .x-btn:hover {{
                    background-color: #333;
                    color: white;
                    border-color: #555;
                }}
                </style>
                <a href="{url}" target="_blank" class="x-btn">{btn_txt}</a>
                """, 
                unsafe_allow_html=True
            )
        # ==========================================

    with col2:
        st.subheader("🧠 Detaylı Analiz")
        
        uploaded_files = st.file_uploader("Görsel Yükle (Mini-App / Ekran Görüntüsü)", accept_multiple_files=True)
        
        if uploaded_files and st.session_state['telegram_flow']['step'] == 'upload_wait':
            db.reference('bridge/request').update({'status': 'manual_completed'})
            st.session_state['telegram_flow']['step'] = 'idle'
            st.success("Manuel yükleme alındı!")
            time.sleep(1)
            st.rerun()

        all_imgs = (uploaded_files or []) + st.session_state['telegram_images']

        if all_imgs:
            st.write(f"{len(all_imgs)} Görsel Analize Hazır")
            cols = st.columns(3)
            for i, img in enumerate(all_imgs):
                cols[i%3].image(img, use_container_width=True)
            
            if st.button("TEMİZLE", type="secondary"):
                st.session_state['telegram_images'] = []
                st.rerun()

            st.divider()
            model_choice = st.radio("Model:", [MODEL_FLASH, MODEL_LITE], horizontal=True)

            # --- PROGRESS BAR İLE ANALİZ ---
            if st.button("ANALİZİ BAŞLAT 🚀", type="primary", use_container_width=True):
                # İlerleme Çubuğu Başlangıcı
                progress_bar = st.progress(0)
                status_text = st.empty()
                ESTIMATED_TOTAL_CHARS = 9000 
                
                response_container = st.empty()
                full_text = ""
                
                for chunk_text in analyze_images_stream(all_imgs, model_choice):
                    if chunk_text.startswith("HATA:"):
                        st.error(chunk_text)
                        break
                    else:
                        full_text += chunk_text
                        response_container.markdown(full_text)
                        
                        # İlerleme Hesabı
                        current_len = len(full_text)
                        progress = min(current_len / ESTIMATED_TOTAL_CHARS, 0.95)
                        progress_bar.progress(progress)
                        status_text.caption(f"Analiz yazılıyor... %{int(progress * 100)}")
                
                progress_bar.progress(1.0)
                status_text.caption("Analiz Tamamlandı! %100")
                st.success("Analiz Tamamlandı!")
        else:
            if step == 'upload_wait':
                st.markdown("### ⬅️ LÜTFEN GÖRSEL YÜKLEYİN")
                st.caption("Mini-App tespit edildi.")
            else:
                st.info("Veri bekleniyor.")

if __name__ == "__main__":
    main()
