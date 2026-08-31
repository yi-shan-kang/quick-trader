# -*- coding: utf-8 -*-
"""读取本地《A股小市值策略手册（公开版）》.docx 全部文本（含图片OCR不可用，仅文本）"""
from docx import Document

doc = Document(r'strategies/small_cap_strategy/optimization/《A股小市值策略手册（公开版）》.docx')
paras = [p.text for p in doc.paragraphs if p.text.strip()]
for i, p in enumerate(paras):
    print(f'[{i:03d}] {p}')
