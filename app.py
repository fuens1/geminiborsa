import streamlit as st
import json
import os
import time
import base64
import datetime
import re
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
        "username": 7704383636,
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
        "username": 7337864804,
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
        "username": 7697855307,
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
        "username": 7991185550,
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
if 'analysis_result' not in st.session_state: st.session_state['analysis_result'] = None 

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
        elif "firebase" in st.secrets and "json_content" in st.secrets["firebase"]:
            json_str = st.secrets["firebase"]["json_content"]
            cred_info = json.loads(json_str)
            if "private_key" in cred_info:
                cred_info["private_key"] = cred_info["private_key"].replace("\\n", "\n")
            cred = credentials.Certificate(cred_info)
        else:
            st.error("⚠️ Firebase Anahtarı Bulunamadı!")
            st.stop()
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    except Exception as e:
        st.error(f"Firebase Bağlantı Hatası: {e}")
        st.stop()

# ==========================================
# 📡 TELEGRAM İŞLEMLERİ
# ==========================================
def start_telegram_request(symbol, rtype):
    if not firebase_admin._apps: return
    bot_key = st.session_state['selected_bot_key']
    target_bot_username = BOT_CONFIGS[bot_key]["username"]
    no_symbol_needed = ["yukselendusen", "teorikliste", "sinyal", "endeks", "haber", "balina", "tum", "genelakd", "piyasayd", "teorikyd", "kurum", "kurumlar", "bofa"]
    
    if rtype not in no_symbol_needed and not symbol:
        st.toast(f"⚠️ Bu işlem için hisse kodu gerekli!", icon="⚠️")
        return

    st.session_state['telegram_flow'] = {'step': 'processing', 'symbol': symbol, 'options': []}
    st.session_state['analysis_result'] = None 
    
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
    ref_req = db.reference('bridge/request')
    ref_req.update({'status': 'selection_made', 'selection': selection, 'timestamp': time.time()})
    st.session_state['telegram_flow']['step'] = 'processing'
    st.session_state['telegram_flow']['options'] = []
    st.toast(f"Seçim İletildi: {selection}", icon="📨")
    time.sleep(0.5) 
    st.rerun()

def send_restart_command():
    if not firebase_admin._apps: return
    db.reference('bridge/system_command').set({'command': 'restart', 'timestamp': time.time()})
    st.toast("🔄 Yeniden Başlatma Komutu Gönderildi!", icon="🔄")

def check_firebase_status():
    try:
        if not firebase_admin._apps: return
        flow = st.session_state['telegram_flow']
        
        if flow['step'] == 'processing':
            status_data = db.reference('bridge/request').get()
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
                st.error("Zaman aşımı.")
                st.session_state['telegram_flow']['step'] = 'idle'
                st.rerun()
    except Exception: pass

# ==========================================
# 🤖 GEMINI ANALİZ
# ==========================================
def get_current_key():
    pool = st.session_state['dynamic_key_pool']
    if not pool: return None
    return pool[st.session_state['key_index'] % len(pool)]

def analyze_images_stream(all_images, model_name):
    max_retries = 3
    key = get_current_key()
    if not key:
        yield "HATA: API Key bulunamadı!"
        return

    gemini_contents = [ "Aşağıdaki borsa görsellerini (Grafik, Liste, Derinlik, Takas vb.) en ince detayına kadar analiz et." ] + all_images
    
    SYSTEM_INSTRUCTION = """
    Sen Kıdemli Borsa Stratejistisin.
    
    GÖREVİN:
    Ekteki görsellerdeki verileri oku ve YARIDA KESMEDEN detaylıca raporla.
    Görselde veri yoksa, o başlığın altına "Veri bulunamadı" yaz.
    
    📄 RAPOR FORMATI VE ETİKETLEME KURALI (ÇOK ÖNEMLİ):
    1. Her başlık mutlaka "## [Sayı]. [Başlık]" formatında olmalı.
    2. Her başlığın HEMEN YANINA, o bölümdeki analizin genel sonucuna göre [OLUMLU], [OLUMSUZ] veya [NÖTR] etiketini EKLEMEK ZORUNDASIN.
    3. Bu etiketi belirlerken sadece sayısal verilere değil, gidişata ve riske bak.
    
    Örnek Doğru Başlıklar:
    "## 1. 📊 DERİNLİK ANALİZİ [OLUMLU]"
    "## 7. 🛑 Şeytanın Avukatı (Risk Analizi) [OLUMSUZ]"
    "## 3. 🏢 KURUM VE PARA GİRİŞİ (AKD) [NÖTR]"

    🎨 RENK KODLARI (Metin İçi):
    * :green[...] -> Yükseliş, Güçlü Alım, Pozitif.
    * :red[...] -> Düşüş, Satış Baskısı, Negatif.
    * :blue[...] -> Nötr Veri, Bilgi.


    ## 1. 📊 DERİNLİK ANALİZİ (Varsa)
    * **Alıcı/Satıcı Dengesi:** (:green[Alıcılar] mı :red[Satıcılar] mı güçlü?)
    * **Emir Yığılmaları:** * **KADEME YORUMU:** ## 2. 🏢 KURUM VE PARA GİRİŞİ (AKD) (Varsa)
    * **Toplayanlar:** * **Satanlar:** ## 3. 🧠 GENEL SENTEZ VE SKOR
    * **Genel Puan:** 10 üzerinden X
    * **Yorum:** ## 4. 🎯 İŞLEM PLANI
    * :green[**GÜVENLİ GİRİŞ:** ...] 
    * :red[**STOP LOSS:** ...]
    * :green[**HEDEF 1:** ...]
    * :green[**HEDEF 2:** ...]

    ## 5. 🔮 KAPANIŞ BEKLENTİSİ
    (Tahmin.)
    
    ## 6. Gizli Balina / Iceberg Avcısı
    *Iceberg Emir veya Duvar Örme durumu var mı?
    
    ## 7. Boğa/Ayı Tuzağı (Fakeout) Dedektörü
    *Fakeout (Sahte Kırılım) ihtimali?
    
    ## 8. ⚖️ Agresif vs. Pasif Emir Analizi
    *Aktif mi Pasif mi?
    
    ## 9. 🏦 Maliyet ve Takas Baskısı
    *Maliyetlerin altında mı üstünde mi?
    
    ## 10. 🌊 RVOL ve Hacim Anormalliği
    *Hacim patlaması var mı?
    
    ## 11. 🧱 Kademe Boşlukları ve Spread Analizi
    *Slippage riski var mı?
    
    ## 12. 🔄 VWAP Dönüş (Mean Reversion)
    *Lastik çok mu gerildi? Pullback ihtimali?
    
    ## 13. 🎭 Piyasa Yapıcı Psikolojisi
    *Market Maker niyeti ne?
    
    ## 14. 🛑 Şeytanın Avukatı (Risk Analizi)
    *NEDEN ALMAMALIYIM? Riskler neler?
    
    ## 15. Likidite Avı (Liquidity Sweep)
    *Stop patlatma hareketi mi?
    
    ## 16. 📊 Point of Control (POC) ve Hacim Profili
    *POC seviyesi nerede?
    
    ## 17. 🏗️ Adım Adım Mal Toplama (Step-Ladder)
    *Algoritmik Robot izi var mı?
    
    ## 18. 🚦 Dominant Taraf ve Delta Analizi
    *Delta pozitif mi negatif mi?

    ## 19. ↕ Destek - Direnç Analizi
    *Derinlik - Kademe - AKD verilerinden yararlanarak en doğru ve en potansiyelli destek ve direnç fiyatlarını göster. Destek ve direncin gücüne göre sırala.

    ## 20. 🗣️ SOHBET VE ANALİZ ÖZETİ (FİNAL)
    *Özet karar: :green[ALIM FIRSATI] mı :red[UZAK DUR] mu?
    *Slogan cümle.
    """ 

    for attempt in range(max_retries):
        try:
            client = genai.Client(api_key=key)
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2, 
                max_output_tokens=99999 
            )
            response_stream = client.models.generate_content_stream(
                model=model_name, contents=gemini_contents, config=config
            )
            for chunk in response_stream:
                if chunk.text: yield chunk.text
            break
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "overloaded" in error_msg.lower():
                if attempt < max_retries - 1:
                    yield f"⚠️ Sunucu yoğun ({model_name}), yeniden deneniyor... ({attempt+1}/{max_retries})\n\n"
                    time.sleep(2)
                    continue
                else:
                    yield f"❌ HATA: Google Sunucuları çok yoğun. Hata: {error_msg}"
            else:
                yield f"HATA: {error_msg}"
                break

# ==========================================
# 🧩 METİN AYRIŞTIRICI VE FİLTRELEME (HİBRİT)
# ==========================================
def parse_markdown_sections(text):
    """
    Markdown metnini böler ve rengi belirler.
    STRATEJİ:
    1. Önce Yapay Zeka'nın koyduğu [ETİKET]'e bakar (En Kesin Yöntem).
    2. Eğer etiket yoksa, geniş kelime havuzundan (POS_KEYWORDS vb.) tarar.
    """
    if not text: return []
    
    raw_sections = text.split("## ")
    parsed_sections = []
    
    counter = 0 
    
    # --- YEDEK KELİME HAVUZU (Fallback) ---
    POS_KEYWORDS = ["OLUMLU", "POZİTİF", "POZITIF", "YEŞİL", "YESIL", "GÜÇLÜ", "GUCLU", "ALIM", "FIRSAT", "RALLİ", "RALLI", "GÜVENLİ", "GUVENLI", "YÜKSELİŞ", "YUKSELIS"]
    NEG_KEYWORDS = ["OLUMSUZ", "NEGATİF", "NEGATIF", "KIRMIZI", "ZAYIF", "RİSK", "RISK", "TUZAK", "UZAK", "SATIŞ", "SATIS", "DÜŞÜŞ", "DUSUS", "TEHLİKE", "TEHLIKE", "UÇURUM", "UCURUM"]
    NEU_KEYWORDS = ["NÖTR", "NOTR", "YATAY", "DENGELİ", "DENGELI", "KARARSIZ", "BELİRSİZ", "BELIRSIZ"]

    for i, section in enumerate(raw_sections):
        if not section.strip(): continue
        
        lines = section.split('\n')
        header_line = lines[0].strip()
        
        # Filtreleme: Sadece rakamla başlayanları al
        if not re.match(r'^\d+\.', header_line):
            continue
            
        body = "## " + section
        
        # --- RENK VE DUYGU ANALİZİ ---
        label_color = "blue" # Varsayılan: Nötr
        
        # Türkçe karakter temizliği yaparak uppercase
        clean_header = header_line.replace('İ', 'I').replace('ı', 'I').upper()
        
        # 1. YÖNTEM: AI TAG KONTROLÜ (Öncelikli)
        ai_pos = "[OLUMLU]" in clean_header or "[POZİTİF]" in clean_header
        ai_neg = "[OLUMSUZ]" in clean_header or "[NEGATİF]" in clean_header
        ai_neu = "[NÖTR]" in clean_header or "[NOTR]" in clean_header

        if ai_pos:
            label_color = "green"
        elif ai_neg:
            label_color = "red"
        elif ai_neu:
            label_color = "blue"
        else:
            # 2. YÖNTEM: KELİME HAVUZU (AI Etiket Koymayı Unuttuysa)
            kw_pos = any(k in clean_header for k in POS_KEYWORDS)
            kw_neg = any(k in clean_header for k in NEG_KEYWORDS)
            
            if kw_pos and not kw_neg:
                label_color = "green"
            elif kw_neg and not kw_pos:
                label_color = "red"
            # Çakışma varsa veya hiçbiri yoksa Blue kalır.

        parsed_sections.append({
            "id": counter,
            "header": header_line,
            "body": body,
            "color": label_color
        })
        counter += 1
        
    return parsed_sections

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
        if st.button("🔄 TELEGRAM İLETİŞİM BAĞLANTISINI YENİDEN BAŞLAT"):
            send_restart_command()
        if st.button("⚠️ SİSTEMİ SIFIRLA (RESET)", type="primary"):
            st.session_state.clear()
            st.rerun()
        st.divider()
        
        st.subheader("🤖 Kanal Seçimi")
        current_name = st.session_state.get('selected_bot_key', 'xFinans')
        if current_name not in BOT_CONFIGS: current_name = 'xFinans'
        idx = list(BOT_CONFIGS.keys()).index(current_name)
        selected_bot_name = st.selectbox("Veri Kaynağı:", list(BOT_CONFIGS.keys()), index=idx)
        if selected_bot_name != st.session_state.get('selected_bot_key'):
            st.session_state['selected_bot_key'] = selected_bot_name
            st.rerun()
        st.caption(f"Aktif ID: {BOT_CONFIGS[selected_bot_name]['username']}")
        st.divider()

        st.subheader("🔑 API Anahtarları")
        current_keys = "\n".join(st.session_state['dynamic_key_pool'])
        keys_input = st.text_area("Gemini Keyler", value=current_keys, height=100)
        if st.button("💾 Kaydet"):
            save_keys_to_disk(keys_input.split('\n'))
            st.success("Kaydedildi!")
            st.rerun()
        
        if st.button("🔍 KEY TESTİ (2.5)"):
            pool = st.session_state['dynamic_key_pool']
            if not pool: st.error("Key yok!")
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
                    except Exception as e: res_box.error(f"HATA: {e}")

    # --- MAIN CONTENT ---
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
        if step == 'processing':
            st.info(f"⏳ Veri Çekiliyor...")
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

        # 𝕏 TARAYICI
        st.divider()
        st.subheader("𝕏 Tarayıcı")
        x_symbol = st.text_input("Kod:", value=symbol if symbol else "THYAO", key="x_input_real").upper()
        search_type = st.radio("Tip:", ["🔥 Geçmiş", "⏱️ Canlı"], key="x_search_type")
        x_date = st.date_input("Tarih", datetime.date.today(), key="x_date_picker")
        
        final_url = ""
        btn_label = ""
        if search_type == "🔥 Geçmiş":
            next_day = x_date + datetime.timedelta(days=1)
            query = f"#{x_symbol} lang:tr until:{next_day} since:{x_date} min_faves:5"
            final_url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=top"
            btn_label = f"🔥 {x_date} Popüler"
        else:
            query = f"#{x_symbol} lang:tr"
            final_url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
            btn_label = f"⏱️ {x_symbol} Son Dakika"
        st.link_button(btn_label, url=final_url, use_container_width=True)

    with col2:
        st.subheader("🧠 Detaylı Analiz")
        uploaded_files = st.file_uploader("Görsel Yükle", accept_multiple_files=True)
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
                st.session_state['analysis_result'] = None 
                st.rerun()

            st.divider()
            model_choice = st.radio("Model:", [MODEL_FLASH, MODEL_LITE], horizontal=True)

            # --- ANALİZ BUTONU ---
            if st.button("ANALİZİ BAŞLAT 🚀", type="primary", use_container_width=True):
                progress_bar = st.progress(0)
                status_text = st.empty()
                response_container = st.empty()
                full_text = ""
                
                # Canlı Yayın (Streaming)
                for chunk_text in analyze_images_stream(all_imgs, model_choice):
                    if chunk_text.startswith("HATA:"):
                        st.error(chunk_text)
                        break
                    else:
                        full_text += chunk_text
                        response_container.markdown(full_text)
                        progress = min(len(full_text) / 9000, 0.95)
                        progress_bar.progress(progress)
                        status_text.caption(f"Analiz yazılıyor... %{int(progress * 100)}")
                
                progress_bar.progress(1.0)
                status_text.caption("Analiz Tamamlandı! %100")
                
                # Sonucu Hafızaya At ve Sayfayı Yenile
                st.session_state['analysis_result'] = full_text
                st.rerun() 

            # --- FİLTRELİ SONUÇ GÖSTERİMİ ---
            if st.session_state['analysis_result']:
                st.divider()
                st.subheader("🔍 Sonuç Filtresi")
                
                sections = parse_markdown_sections(st.session_state['analysis_result'])
                
                # --- SAYIMLARI YAP ---
                count_pos = sum(1 for s in sections if s['color'] == 'green')
                count_neg = sum(1 for s in sections if s['color'] == 'red')
                count_neu = sum(1 for s in sections if s['color'] == 'blue')

                with st.expander("📂 Analiz Başlıklarını Filtrele", expanded=True):
                    
                    # --- KATEGORİ BUTONLARI ---
                    c1, c2, c3 = st.columns(3)
                    
                    # OLUMLU (YEŞİL)
                    if c1.button(f"✅ OLUMLU ({count_pos})", use_container_width=True):
                        for s in sections:
                            st.session_state[f"chk_{s['id']}"] = (s['color'] == 'green')
                        st.rerun()

                    # OLUMSUZ (KIRMIZI)
                    if c2.button(f"🔻 OLUMSUZ ({count_neg})", use_container_width=True):
                        for s in sections:
                            st.session_state[f"chk_{s['id']}"] = (s['color'] == 'red')
                        st.rerun()

                    # NÖTR (MAVİ)
                    if c3.button(f"🔹 NÖTR ({count_neu})", use_container_width=True):
                        for s in sections:
                            st.session_state[f"chk_{s['id']}"] = (s['color'] == 'blue')
                        st.rerun()
                    
                    st.divider()

                    # --- TOPLU İŞLEM BUTONLARI ---
                    col_act1, col_act2 = st.columns(2)
                    if col_act1.button("Tümünü Seç", key="sel_all", use_container_width=True):
                        for s in sections:
                            st.session_state[f"chk_{s['id']}"] = True
                        st.rerun()
                    if col_act2.button("Tümünü Kaldır", key="desel_all", use_container_width=True):
                        for s in sections:
                            st.session_state[f"chk_{s['id']}"] = False
                        st.rerun()
                    
                    st.divider()
                    
                    f_cols = st.columns(2)
                    for i, s in enumerate(sections):
                        # Key tabanlı state yönetimi
                        chk_key = f"chk_{s['id']}"
                        if chk_key not in st.session_state:
                            st.session_state[chk_key] = True
                            
                        display_text = f":{s['color']}[{s['header']}]"
                        
                        f_cols[i % 2].checkbox(display_text, key=chk_key)

                st.markdown("---")
                # Filtrelenmiş içeriği göster
                for s in sections:
                    if st.session_state.get(f"chk_{s['id']}", True):
                        st.markdown(s['body'])
                        st.markdown("") 
                
                st.success("Analiz Gösterildi.")

        else:
            if step == 'upload_wait':
                st.markdown("### ⬅️ LÜTFEN GÖRSEL YÜKLEYİN")
                st.caption("Mini-App tespit edildi.")
            else:
                st.info("Veri bekleniyor.")

if __name__ == "__main__":
    main()
