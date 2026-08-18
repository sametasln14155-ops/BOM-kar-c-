# -*- coding: utf-8 -*-
"""
Created on Tue Aug 18 14:09:17 2026

@author: Samet
"""

import streamlit as st
import fitz  # PyMuPDF
import pandas as pd
from PIL import Image
import io
import json
import time
import datetime
from typing import Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# ==========================================
# 0. SAYFA VE AI AYARLARI
# ==========================================
st.set_page_config(page_title="BOM & Çizim Konsolidasyon", page_icon="⚙️", layout="centered")

# API Anahtarı
API_KEY = st.secrets["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

# Token Takip Sistemi
if 'token_kullanimi' not in st.session_state:
    st.session_state.token_kullanimi = {"input": 0, "output": 0, "total": 0}

def token_hesaba_kat(usage_metadata):
    if usage_metadata:
        st.session_state.token_kullanimi["input"] += getattr(usage_metadata, 'prompt_token_count', 0)
        st.session_state.token_kullanimi["output"] += getattr(usage_metadata, 'candidates_token_count', 0)
        st.session_state.token_kullanimi["total"] += getattr(usage_metadata, 'total_token_count', 0)

# ==========================================
# 1. AI ÇIKTI ŞEMALARI
# ==========================================
class BomSatiri(BaseModel):
    cizim_numarasi: str = Field(description="Açıklamadaki çizim no")
    kullanim_adedi: int = Field(description="Quantity / Piece sütunundaki adet")

class BomVerisi(BaseModel):
    satirlar: list[BomSatiri] = Field(description="BOM tablosundaki satırlar")

class Malzeme(BaseModel):
    pos_no: Optional[str] = Field(None, description="Poz numarası")
    birim_miktar: Optional[int] = Field(None, description="Çizimdeki miktar")
    name_of_item: Optional[str] = Field(None, description="Parça adı")
    en_mm: Optional[float] = Field(None, description="Genişlik (mm)")
    boy_mm: Optional[float] = Field(None, description="Uzunluk (mm)")
    thickness_mm: Optional[float] = Field(None, description="Kalınlık (mm)")
    diameter_mm: Optional[float] = Field(None, description="Varsa Çap (mm)")
    material: Optional[str] = Field(None, description="Malzeme türü")
    weight_kg: Optional[float] = Field(None, description="Ağırlık (kg)")

class CizimVerisi(BaseModel):
    cizim_numarasi: Optional[str] = Field(None, description="Çizim numarası")
    malzemeler: list[Malzeme] = Field(default=[], description="Malzemeler")

# ==========================================
# 2. YARDIMCI FONKSİYONLAR
# ==========================================
def sayfa_resme_cevir(sayfa, dpi=200):
    pix = sayfa.get_pixmap(dpi=dpi)
    img_data = pix.tobytes("png")
    return Image.open(io.BytesIO(img_data))

def pdf_analiz_et(resim, prompt, sema):
    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=[resim, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=sema,
            temperature=0.0
        ),
    )
    token_hesaba_kat(response.usage_metadata)
    return json.loads(response.text)

# ==========================================
# 3. KULLANICI ARAYÜZÜ (UI)
# ==========================================
st.title("⚙️ Üretim Malzeme Listesi (BOM) Oluşturucu")
st.markdown("Ana montaj (BOM) listelerinizdeki parça adetleri ile teknik çizimlerdeki ölçüleri konsolide ederek üretime hazır tek bir Excel dosyası oluşturun.")

# Sekmeler
tab1, tab2 = st.tabs(["📑 1. BOM Listelerini Yükle", "📐 2. Teknik Çizimleri Yükle"])

with tab1:
    st.info("İçerisinde alt parçaların adetlerini belirten Ana Montaj (Assembly) PDF'lerini buraya sürükleyip bırakın.")
    bom_files = st.file_uploader("BOM PDF'leri", type=["pdf"], accept_multiple_files=True, key="bom", label_visibility="hidden")

with tab2:
    st.info("Ölçüleri ve kalınlıkları (Dimensions, t) içeren alt parça teknik çizim PDF'lerini buraya sürükleyip bırakın.")
    cizim_files = st.file_uploader("Teknik Çizim PDF'leri", type=["pdf"], accept_multiple_files=True, key="cizim", label_visibility="hidden")

st.divider()

# Excel Oluştur Butonu
if st.button("🚀 Excel Oluştur", use_container_width=True):
    if not bom_files or not cizim_files:
        st.warning("⚠️ Lütfen işleme başlamadan önce her iki sekmeye de ilgili PDF dosyalarını yüklediğinizden emin olun.")
    else:
        tum_veriler = []
        montaj_carpanlari = {}
        st.session_state.token_kullanimi = {"input": 0, "output": 0, "total": 0} 

        progress_text = "Dosyalar analiz ediliyor, lütfen bekleyin..."
        my_bar = st.progress(0, text=progress_text)
        toplam_dosya = len(bom_files) + len(cizim_files)

        # ADIM 1: BOM Okuma
        for idx, file in enumerate(bom_files):
            my_bar.progress((idx) / toplam_dosya, text=f"BOM Analiz Ediliyor: {file.name}")
            doc = fitz.open(stream=file.read(), filetype="pdf")
            for sayfa_idx in range(len(doc)):
                resim = sayfa_resme_cevir(doc.load_page(sayfa_idx))
                prompt = "BOM listesinden alt montajların 'Çizim Numarası' ile 'Kullanım Adedini' çıkar."
                bom_yaniti = pdf_analiz_et(resim, prompt, BomVerisi)
                
                for satir in bom_yaniti.get("satirlar", []):
                    if satir.get("cizim_numarasi"):
                        montaj_carpanlari[satir["cizim_numarasi"].strip()] = satir.get("kullanim_adedi", 1)
            time.sleep(0.5)

        # ADIM 2: Çizim Okuma
        for idx, file in enumerate(cizim_files):
            my_bar.progress((len(bom_files) + idx) / toplam_dosya, text=f"Çizim Analiz Ediliyor: {file.name}")
            doc = fitz.open(stream=file.read(), filetype="pdf")
            for sayfa_idx in range(len(doc)):
                resim = sayfa_resme_cevir(doc.load_page(sayfa_idx))
                prompt = "BOM tablosunu oku, assembly satırlarını alma. Boyut stringlerini En, Boy, Kalınlık (t) ve Çap (Ø) olarak sayısal (float) ayrıştır."
                ai_yaniti = pdf_analiz_et(resim, prompt, CizimVerisi)
                
                cizim_no = ai_yaniti.get("cizim_numarasi", "")
                carpan = montaj_carpanlari.get(cizim_no, 1)
                
                for malzeme in ai_yaniti.get("malzemeler", []):
                    birim = malzeme.get("birim_miktar") or 1
                    tum_veriler.append({
                        "Dosya_Adi": file.name,
                        "Cizim_Numarasi": cizim_no,
                        "Poz_No": malzeme.get("pos_no"),
                        "Birim_Miktar": birim,
                        "Montaj_Carpani": carpan,
                        "TOPLAM_URETIM_MIKTARI": birim * carpan,
                        "Parca_Adi": malzeme.get("name_of_item"),
                        "En_mm": malzeme.get("en_mm"),
                        "Boy_mm": malzeme.get("boy_mm"),
                        "Kalinlik_mm": malzeme.get("thickness_mm"),
                        "Cap_mm": malzeme.get("diameter_mm"),
                        "Malzeme": malzeme.get("material"),
                        "Agirlik_kg": malzeme.get("weight_kg")
                    })
            time.sleep(0.5)

        my_bar.progress(100, text="İşlem Tamamlandı!")

        # ADIM 3: İndirme ve Raporlama
        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Malzeme Listesi')
            excel_data = output.getvalue()
            
            zaman = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            st.success("✅ Excel başarıyla oluşturuldu!")
            
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="📥 İndir (Konsolide_Uretim_Listesi.xlsx)",
                    data=excel_data,
                    file_name=f"Konsolide_Uretim_Listesi_{zaman}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            with col2:
                st.info(f"🪙 Harcanan Token: {st.session_state.token_kullanimi['total']:,}")
        else:
            st.error("Yüklenen PDF'lerden anlamlı bir veri çıkarılamadı.")