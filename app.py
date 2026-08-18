import streamlit as st
import json
import fitz  # PyMuPDF
import pandas as pd
from PIL import Image
import io
from typing import Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import time

# Sayfa Ayarları
st.set_page_config(page_title="PDF BOM & Çizim Analizi", layout="wide")
st.title("PDF BOM & Teknik Çizim Analiz Aracı")

# ==========================================
# 1. AI ÇIKTI ŞEMALARI (STRUCTURED OUTPUTS)
# ==========================================

class BomSatiri(BaseModel):
    cizim_numarasi: str = Field(description="Açıklamadaki çizim no (Örn: 301107-3073)")
    kullanim_adedi: int = Field(description="Quantity / Piece sütunundaki adet (Örn: 2 NR için 2)")

class BomVerisi(BaseModel):
    satirlar: list[BomSatiri] = Field(description="BOM tablosundaki alt montaj/çizim referansları ve adetleri")

class Malzeme(BaseModel):
    pos_no: Optional[str] = Field(None, description="Poz numarası (Item no)")
    birim_miktar: Optional[int] = Field(None, description="Çizimdeki miktar (QTY)")
    name_of_item: Optional[str] = Field(None, description="Parça adı (Plate, Flat Bar vb.)")
    en_mm: Optional[float] = Field(None, description="Genişlik veya En (Width) - mm")
    boy_mm: Optional[float] = Field(None, description="Uzunluk veya Boy (Length) - mm")
    thickness_mm: Optional[float] = Field(None, description="Kalınlık (t, Thickness) - mm")
    diameter_mm: Optional[float] = Field(None, description="Varsa Çap (Ø, Diameter) - mm")
    material: Optional[str] = Field(None, description="Malzeme türü (S355 vb.)")
    weight_kg: Optional[float] = Field(None, description="Birim Ağırlık (Weight/Item) - kg")

class CizimVerisi(BaseModel):
    cizim_numarasi: Optional[str] = Field(None, description="Sağ alt antetten çizim numarası")
    malzemeler: list[Malzeme] = Field(default=[], description="Malzeme listesi tablosundaki satırlar")

# ==========================================
# 2. YARDIMCI FONKSİYONLAR
# ==========================================

def sayfa_resme_cevir(sayfa, dpi=200):
    pix = sayfa.get_pixmap(dpi=dpi)
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data))

def bom_pdf_oku(client, resim):
    prompt = """
    Bu bir ana malzeme listesi (Bill of Material) sayfasıdır. 
    Lütfen tablodaki satırları incele ve alt montaj/çizimlerin 'Çizim Numarası' ile 'Kullanım Adedini' çıkar.
    - Çizim numaraları genellikle 'Descriptions' kısmında alt tireden sonra yer alır.
    - Adetleri 'Quantity' veya 'Piece' sütunundan al.
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash', # Model ismi güncellendi
        contents=[resim, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BomVerisi,
            temperature=0.0
        ),
    )
    return json.loads(response.text)

def cizim_pdf_oku(client, resim):
    prompt = """
    Bu teknik çizim sayfasını incele:
    1. Sağ alttaki antetten 'Çizim Numarasını' bul.
    2. Malzeme Listesi (BOM) tablosunu oku. Sadece hammaddeleri al.
    3. Boyut stringlerini En, Boy, Kalınlık (t) ve Çap (Ø) olarak sayısal (float) ayrıştır.
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[resim, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CizimVerisi,
            temperature=0.0
        ),
    )
    return json.loads(response.text)

# ==========================================
# 3. STREAMLIT ARAYÜZÜ VE ANA AKIŞ
# ==========================================

st.sidebar.header("API Ayarları")
api_key = st.sidebar.text_input("Gemini API Anahtarı", type="password")

st.sidebar.markdown("---")
st.sidebar.header("Dosya Yükleme")
bom_dosyalari = st.sidebar.file_uploader("BOM PDF'lerini Yükleyin", accept_multiple_files=True, type=['pdf'])
cizim_dosyalari = st.sidebar.file_uploader("Çizim PDF'lerini Yükleyin", accept_multiple_files=True, type=['pdf'])

if st.button("Analizi Başlat", type="primary"):
    if not api_key:
        st.error("Lütfen sol menüden Gemini API anahtarınızı girin.")
    elif not bom_dosyalari or not cizim_dosyalari:
        st.error("Lütfen hem BOM hem de Çizim PDF'lerini yükleyin.")
    else:
        client = genai.Client(api_key=api_key)
        tum_veriler = []
        montaj_carpanlari = {}

        # ADIM 1: BOM PDF'lerini İşleme
        with st.status("Adım 1: BOM Listeleri İşleniyor...", expanded=True) as status:
            for dosya in bom_dosyalari:
                try:
                    doc = fitz.open(stream=dosya.read(), filetype="pdf")
                    for sayfa_idx in range(len(doc)):
                        resim = sayfa_resme_cevir(doc.load_page(sayfa_idx))
                        bom_yaniti = bom_pdf_oku(client, resim)
                        
                        for satir in bom_yaniti.get("satirlar", []):
                            ciz_no = satir.get("cizim_numarasi")
                            adet = satir.get("kullanim_adedi", 1)
                            if ciz_no:
                                montaj_carpanlari[ciz_no.strip()] = adet
                except Exception as e:
                    st.error(f"BOM Hatası ({dosya.name}): {e}")
            
            st.write(f"Bulunan alt montaj referansları: {len(montaj_carpanlari)} adet")
            status.update(label="Adım 1 Tamamlandı!", state="complete", expanded=False)

        # ADIM 2: Çizim PDF'lerini İşleme
        with st.status("Adım 2: Teknik Çizimler İşleniyor...", expanded=True) as status:
            ilerleme_cizgisi = st.progress(0)
            toplam_cizim = len(cizim_dosyalari)
            
            for idx, dosya in enumerate(cizim_dosyalari):
                try:
                    st.write(f"İşleniyor: {dosya.name}")
                    doc = fitz.open(stream=dosya.read(), filetype="pdf")
                    for sayfa_idx in range(len(doc)):
                        resim = sayfa_resme_cevir(doc.load_page(sayfa_idx))
                        ai_yaniti = cizim_pdf_oku(client, resim)
                        
                        cizim_no = ai_yaniti.get("cizim_numarasi", "")
                        malzemeler = ai_yaniti.get("malzemeler", [])
                        carpan = montaj_carpanlari.get(cizim_no, 1)
                        
                        for malzeme in malzemeler:
                            birim = malzeme.get("birim_miktar") or 1
                            malzeme_verisi = {
                                "dosya_adi": dosya.name,
                                "sayfa_no": sayfa_idx + 1,
                                "cizim_numarasi": cizim_no,
                                "pos_no": malzeme.get("pos_no"),
                                "birim_miktar": birim,
                                "montaj_carpani": carpan,
                                "TOPLAM_MIKTAR": birim * carpan,
                                "name_of_item": malzeme.get("name_of_item"),
                                "en_mm": malzeme.get("en_mm"),
                                "boy_mm": malzeme.get("boy_mm"),
                                "thickness_mm": malzeme.get("thickness_mm"),
                                "diameter_mm": malzeme.get("diameter_mm"),
                                "material": malzeme.get("material"),
                                "weight_kg": malzeme.get("weight_kg")
                            }
                            tum_veriler.append(malzeme_verisi)
                    
                    ilerleme_cizgisi.progress((idx + 1) / toplam_cizim)
                except Exception as e:
                    st.error(f"Çizim Hatası ({dosya.name}): {e}")
                    
            status.update(label="Adım 2 Tamamlandı!", state="complete", expanded=False)

        # ADIM 3: Excel'e Aktarma ve İndirme
        if tum_veriler:
            sutun_sirasi = [
                "dosya_adi", "sayfa_no", "cizim_numarasi", "pos_no", 
                "birim_miktar", "montaj_carpani", "TOPLAM_MIKTAR", 
                "name_of_item", "en_mm", "boy_mm", "thickness_mm", 
                "diameter_mm", "material", "weight_kg"
            ]
            df = pd.DataFrame(tum_veriler)[sutun_sirasi]
            
            st.success("Analiz başarıyla tamamlandı!")
            st.dataframe(df) # Önizleme
            
            # Excel dosyasını bellekte oluşturma
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            excel_data = output.getvalue()
            
            st.download_button(
                label="📥 Konsolide Üretim Listesini İndir (Excel)",
                data=excel_data,
                file_name="Konsolide_Uretim_Listesi.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.warning("Çıkarılabilecek parça verisi bulunamadı.")